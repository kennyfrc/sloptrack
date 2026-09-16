#!/usr/bin/env python3
"""Measure code slop in a repository.

Reports the SlopCodeBench quality signals:

    verbosity   = |clone lines U ast-grep lines| / SLOC
    erosion     = sum(mass(f) for CC(f) > 10) / sum(mass(f))
    mass(f)     = CC(f) * sqrt(SLOC(f))
    granularity = single-use callables / callables with a call site

Granularity is a band, not a floor: too high means over-decomposition, too low
means too few named steps. It has a human band but no agent band.

Verbosity and erosion are computed from a Tree-sitter parse when the grammar
for the repo's language is importable. Without Tree-sitter the tool still
reports file inventory, SLOC, and git growth, and says so.

Never reports a confident zero: a language that parses but yields no functions
is marked unreliable and excluded from the aggregate.

Usage:
    sloptrack measure [PATH] [--json] [--since REF] [--top N] [--lang LANG]
                      [--scb] [--no-git] [--exclude GLOB] [--functions named|all]

This file is also runnable on its own, which is how the agent skill calls it:
`python3 slop_measure.py [flags]`. It needs Tree-sitter and the grammar packages
for the target's languages; `sloptrack measure` supplies them through uvx.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

HIGH_CC = 10  # HIGH_COMPLEXITY_THRESHOLD: strictly greater than this
MIN_CLONE_STATEMENTS = 2
MIN_CLONE_LINES = 4
# Locations listed per duplicate block; the occurrence count is never truncated.
DUPLICATE_LOCATIONS = 4  # a normalized clone must span at least this many SLOC

# Baseline bands from SlopCodeBench / earendil.com (mean +/- sd).
BANDS = {
    "verbosity": {"human": 0.15, "human_sd": 0.06, "agent": 0.33, "agent_sd": 0.10},
    "erosion": {"human": 0.31, "human_sd": 0.17, "agent": 0.68, "agent_sd": 0.20},
    # Granularity has a human reference band but no agent band: SlopCodeBench
    # never measured it, and our own agent sample is not comparable. Derived
    # from the same human panel at HEAD (44 of 48 repos measurable), mean +/- sd
    # over the panel. It is a band, not a floor: below it means too few named
    # steps, above it means too many single-use callables.
    "granularity": {"human": 0.27, "human_sd": 0.13, "agent": None, "agent_sd": None},
}
SCB_CHECK_SUPPORTED = {"python", "javascript", "typescript", "rust", "zig", "haskell", "cpp", "c"}


# --------------------------------------------------------------------------
# Language table
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Lang:
    """Tree-sitter vocabulary for one language."""

    name: str
    exts: frozenset[str]
    filenames: frozenset[str] = frozenset()
    grammar: str | None = None
    grammar_fn: str = "language"  # most grammars export language(); some do not
    functions: frozenset[str] = frozenset()
    # When set, a function node counts only under one of these parents. Guards
    # grammars that reuse a node name for something that is not a function.
    function_parents: frozenset[str] = frozenset()
    decisions: frozenset[str] = frozenset()
    # Node types eligible as clone candidates. Empty means functions | decisions,
    # the historical behavior. scb-check names this field `clone_node_types`;
    # declare it when that merge would enroll a node that is not a clone unit.
    clone_types: frozenset[str] = frozenset()
    # Node types that name an invocation, for the granularity use count. Empty
    # means DEFAULT_CALLS. Declare it when a grammar reuses a call-shaped node
    # for something that is not an invocation.
    calls: frozenset[str] = frozenset()
    binary_like: frozenset[str] = frozenset()
    bool_tokens: frozenset[str] = frozenset({"&&", "||", "and", "or"})
    comments: frozenset[str] = frozenset({"comment"})
    identifiers: frozenset[str] = frozenset()
    literals: frozenset[str] = frozenset()


def _L(**kw) -> Lang:  # noqa: N802 - compact table builder
    return Lang(**kw)


LANGS: tuple[Lang, ...] = (
    _L(
        name="python",
        exts=frozenset({".py", ".pyw"}),
        grammar="tree_sitter_python",
        functions=frozenset({"function_definition"}),
        decisions=frozenset(
            {
                "if_statement", "elif_clause", "for_statement", "while_statement",
                "except_clause", "assert_statement", "list_comprehension",
                "set_comprehension", "dictionary_comprehension",
                "generator_expression", "conditional_expression", "if_clause",
                "boolean_operator",
            }
        ),
        comments=frozenset({"comment"}),
        identifiers=frozenset({"identifier"}),
        literals=frozenset(
            {"string", "integer", "float", "imaginary", "true", "false", "none"}
        ),
    ),
    _L(
        name="javascript",
        exts=frozenset({".js", ".mjs", ".cjs", ".jsx"}),
        grammar="tree_sitter_javascript",
        functions=frozenset(
            {
                "function_declaration", "function_expression", "arrow_function",
                "method_definition", "generator_function_declaration",
                "generator_function",
            }
        ),
        decisions=frozenset(
            {
                "if_statement", "for_statement", "for_in_statement", "while_statement",
                "do_statement", "switch_case", "catch_clause",
                "conditional_expression", "ternary_expression",
            }
        ),
        binary_like=frozenset({"binary_expression"}),
        comments=frozenset({"comment"}),
        identifiers=frozenset({"identifier", "property_identifier", "shorthand_property_identifier"}),
        literals=frozenset(
            {"string", "template_string", "number", "true", "false", "null", "undefined", "regex"}
        ),
    ),
    _L(
        name="typescript",
        exts=frozenset({".ts", ".tsx", ".mts", ".cts"}),
        grammar="tree_sitter_typescript",
        grammar_fn="language_typescript",
        functions=frozenset(
            {
                "function_declaration", "function_expression", "arrow_function",
                "method_definition", "generator_function_declaration",
                "generator_function", "function_signature", "abstract_method_signature",
            }
        ),
        decisions=frozenset(
            {
                "if_statement", "for_statement", "for_in_statement", "while_statement",
                "do_statement", "switch_case", "catch_clause",
                "conditional_expression", "ternary_expression",
            }
        ),
        binary_like=frozenset({"binary_expression"}),
        comments=frozenset({"comment"}),
        identifiers=frozenset({"identifier", "property_identifier", "shorthand_property_identifier"}),
        literals=frozenset(
            {"string", "template_string", "number", "true", "false", "null", "undefined", "regex"}
        ),
    ),
    _L(
        name="ruby",
        exts=frozenset({".rb", ".rake", ".gemspec"}),
        filenames=frozenset({"Rakefile", "Gemfile", "Guardfile", "Capfile", "Vagrantfile"}),
        grammar="tree_sitter_ruby",
        functions=frozenset({"method", "singleton_method"}),
        decisions=frozenset(
            {
                "if", "if_modifier", "unless", "unless_modifier", "elsif",
                "while", "while_modifier", "until", "until_modifier", "for",
                "when", "rescue", "rescue_modifier", "conditional",
            }
        ),
        binary_like=frozenset({"binary"}),
        bool_tokens=frozenset({"&&", "||", "and", "or"}),
        comments=frozenset({"comment"}),
        identifiers=frozenset({"identifier", "constant", "instance_variable", "symbol"}),
        literals=frozenset({"string", "string_content", "integer", "float", "true", "false", "nil"}),
    ),
    _L(
        name="go",
        exts=frozenset({".go"}),
        grammar="tree_sitter_go",
        functions=frozenset({"function_declaration", "method_declaration", "func_literal"}),
        decisions=frozenset(
            {
                "if_statement", "for_statement", "expression_case", "type_case",
                "communication_case",
            }
        ),
        binary_like=frozenset({"binary_expression"}),
        comments=frozenset({"comment"}),
        identifiers=frozenset({"identifier", "field_identifier", "type_identifier", "package_identifier"}),
        literals=frozenset({"interpreted_string_literal", "raw_string_literal", "int_literal", "float_literal", "rune_literal", "true", "false", "nil"}),
    ),
    _L(
        name="rust",
        exts=frozenset({".rs"}),
        grammar="tree_sitter_rust",
        functions=frozenset({"function_item"}),
        decisions=frozenset(
            {"if_expression", "match_expression", "while_expression", "loop_expression", "for_expression"}
        ),
        binary_like=frozenset({"binary_expression"}),
        comments=frozenset({"line_comment", "block_comment"}),
        identifiers=frozenset({"identifier", "field_identifier", "type_identifier"}),
        literals=frozenset({"string_literal", "raw_string_literal", "integer_literal", "float_literal", "char_literal", "boolean_literal"}),
    ),
    _L(
        name="java",
        exts=frozenset({".java"}),
        grammar="tree_sitter_java",
        functions=frozenset({"method_declaration", "constructor_declaration"}),
        decisions=frozenset(
            {
                "if_statement", "for_statement", "enhanced_for_statement", "while_statement",
                "do_statement", "switch_label", "catch_clause", "ternary_expression",
            }
        ),
        binary_like=frozenset({"binary_expression"}),
        comments=frozenset({"line_comment", "block_comment"}),
        identifiers=frozenset({"identifier"}),
        literals=frozenset({"string_literal", "decimal_integer_literal", "decimal_floating_point_literal", "true", "false", "null"}),
    ),
    _L(
        name="c",
        exts=frozenset({".c", ".h"}),
        grammar="tree_sitter_c",
        functions=frozenset({"function_definition"}),
        decisions=frozenset(
            {
                "if_statement", "for_statement", "while_statement", "do_statement",
                "case_statement", "conditional_expression",
            }
        ),
        binary_like=frozenset({"binary_expression"}),
        comments=frozenset({"comment"}),
        identifiers=frozenset({"identifier", "field_identifier", "type_identifier"}),
        literals=frozenset({"string_literal", "number_literal", "char_literal"}),
    ),
    _L(
        name="cpp",
        exts=frozenset({".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h++"}),
        grammar="tree_sitter_cpp",
        functions=frozenset({"function_definition"}),
        decisions=frozenset(
            {
                "if_statement", "for_statement", "while_statement", "do_statement",
                "case_statement", "catch_clause", "conditional_expression",
            }
        ),
        binary_like=frozenset({"binary_expression"}),
        comments=frozenset({"comment"}),
        identifiers=frozenset({"identifier", "field_identifier", "type_identifier", "namespace_identifier"}),
        literals=frozenset({"string_literal", "raw_string_literal", "number_literal", "char_literal", "true", "false", "null", "nullptr"}),
    ),
    _L(
        name="csharp",
        exts=frozenset({".cs"}),
        grammar="tree_sitter_c_sharp",
        functions=frozenset({"method_declaration", "constructor_declaration", "local_function_statement"}),
        decisions=frozenset(
            {
                "if_statement", "for_statement", "foreach_statement", "while_statement",
                "do_statement", "switch_section", "catch_clause", "conditional_expression",
            }
        ),
        binary_like=frozenset({"binary_expression"}),
        comments=frozenset({"comment"}),
        identifiers=frozenset({"identifier"}),
        literals=frozenset({"string_literal", "integer_literal", "real_literal", "true", "false", "null"}),
    ),
    _L(
        name="php",
        exts=frozenset({".php"}),
        grammar="tree_sitter_php",
        grammar_fn="language_php",
        functions=frozenset({"function_definition", "method_declaration"}),
        decisions=frozenset(
            {
                "if_statement", "else_if_clause", "for_statement", "foreach_statement",
                "while_statement", "do_statement", "case_statement", "catch_clause",
                "conditional_expression",
            }
        ),
        binary_like=frozenset({"binary_expression"}),
        comments=frozenset({"comment"}),
        identifiers=frozenset({"name", "variable_name"}),
        literals=frozenset({"string", "string_content", "integer", "float", "true", "false", "null"}),
    ),
    _L(
        name="kotlin",
        exts=frozenset({".kt", ".kts"}),
        grammar="tree_sitter_kotlin",
        functions=frozenset({"function_declaration", "secondary_constructor"}),
        decisions=frozenset(
            {
                "if_expression", "for_statement", "while_statement", "do_while_statement",
                "when_entry", "catch_block", "guard_condition",
            }
        ),
        binary_like=frozenset({"binary_expression", "conjunction_expression", "disjunction_expression"}),
        comments=frozenset({"line_comment", "multiline_comment"}),
        # The PyPI tree-sitter-kotlin calls locals and parameters `identifier`;
        # `simple_identifier` is kept because other builds of the grammar use it.
        identifiers=frozenset({"identifier", "simple_identifier"}),
        # The PyPI tree-sitter-kotlin names numbers `number_literal` and
        # `float_literal`; `true` and `null` are plain `identifier`s there, so
        # normalization covers them. Older spellings are kept for other builds.
        literals=frozenset(
            {
                "float_literal", "integer_literal", "number_literal", "null",
                "real_literal", "string_content", "string_literal",
            }
        ),
    ),
    _L(
        name="swift",
        exts=frozenset({".swift"}),
        grammar="tree_sitter_swift",
        functions=frozenset({"function_declaration", "init_declaration"}),
        decisions=frozenset(
            {
                "if_statement", "for_statement", "while_statement",
                "repeat_while_statement", "switch_entry", "catch_block",
                "ternary_expression", "guard_statement",
            }
        ),
        binary_like=frozenset({"binary_expression"}),
        comments=frozenset({"comment", "multiline_comment"}),
        identifiers=frozenset({"simple_identifier"}),
        literals=frozenset({"line_string_literal", "integer_literal", "real_literal", "boolean_literal", "nil"}),
    ),
    _L(
        name="scala",
        exts=frozenset({".scala", ".sc"}),
        grammar="tree_sitter_scala",
        functions=frozenset({"function_definition"}),
        decisions=frozenset(
            {"if_expression", "for_expression", "while_expression", "case_clause", "catch_clause", "match_expression"}
        ),
        binary_like=frozenset({"binary_expression"}),
        comments=frozenset({"comment", "block_comment"}),
        identifiers=frozenset({"identifier"}),
        literals=frozenset({"string", "integer_literal", "floating_point_literal", "boolean_literal", "null"}),
    ),
    _L(
        name="lua",
        exts=frozenset({".lua"}),
        grammar="tree_sitter_lua",
        functions=frozenset({"function_declaration", "function_definition"}),
        decisions=frozenset(
            {"if_statement", "elseif_statement", "for_statement", "while_statement", "repeat_statement"}
        ),
        binary_like=frozenset({"binary_expression"}),
        comments=frozenset({"comment"}),
        identifiers=frozenset({"identifier"}),
        literals=frozenset({"string", "number", "true", "false", "nil"}),
    ),
    _L(
        name="haskell",
        exts=frozenset({".hs", ".lhs"}),
        grammar="tree_sitter_haskell",
        functions=frozenset({"function"}),
        function_parents=frozenset({"declarations", "haskell"}),
        decisions=frozenset({"conditional", "case", "alternative", "guard", "multi_way_if"}),
        binary_like=frozenset({"infix"}),
        comments=frozenset({"comment", "haddock"}),
        identifiers=frozenset({"variable", "constructor", "module_id"}),
        literals=frozenset({"integer", "float", "char", "string", "literal"}),
    ),
    _L(
        name="zig",
        exts=frozenset({".zig"}),
        grammar="tree_sitter_zig",
        functions=frozenset({"function_declaration"}),
        # tree-sitter-zig names branches and loops `*_statement`, not
        # `*_expression`, even though `if` is an expression in the language. The
        # old spelling matched nothing and read as CC 1. `catch_expression` is
        # counted like the catch and except clauses of the other languages.
        decisions=frozenset(
            {
                "catch_expression", "for_statement", "if_statement",
                "switch_expression", "while_statement",
            }
        ),
        binary_like=frozenset({"binary_expression"}),
        comments=frozenset({"comment"}),
        identifiers=frozenset({"identifier"}),
        literals=frozenset({"integer", "float", "string", "char_literal", "true", "false", "null"}),
    ),
    _L(
        name="bash",
        exts=frozenset({".sh", ".bash"}),
        grammar="tree_sitter_bash",
        functions=frozenset({"function_definition"}),
        # Declared explicitly, as scb-check does: an `elif_clause` or a single
        # `case_item` is never a clone unit of its own.
        clone_types=frozenset(
            {
                "function_definition", "if_statement", "for_statement",
                "c_style_for_statement", "while_statement", "case_statement",
                "ternary_expression",
            }
        ),
        # No `until_statement` node: tree-sitter-bash folds `until` into
        # `while_statement`, and has no `until` production of its own.
        decisions=frozenset(
            {
                "if_statement", "elif_clause", "for_statement",
                "c_style_for_statement", "while_statement", "case_item",
                "ternary_expression",
            }
        ),
        binary_like=frozenset({"binary_expression"}),
        # `and` and `or` are not operators in bash; the default set would count
        # a bare word argument that happened to spell one.
        bool_tokens=frozenset({"&&", "||"}),
        comments=frozenset({"comment"}),
        # `word` covers bare command names, path arguments, and the function
        # name itself (function_definition.name is a `word`), so normalizing it
        # is what makes two same-shaped shell blocks hash equal.
        identifiers=frozenset(
            {"command_name", "special_variable_name", "variable_name", "word"}
        ),
        literals=frozenset(
            {
                "ansi_c_string", "number", "raw_string", "string",
                "string_content", "translated_string",
            }
        ),
    ),
)

BY_EXT: dict[str, Lang] = {}
BY_FILENAME: dict[str, Lang] = {}
for _lang in LANGS:
    for _ext in _lang.exts:
        BY_EXT[_ext] = _lang
    for _fn in _lang.filenames:
        BY_FILENAME[_fn] = _lang

# Directories never worth walking.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "dist", "build", "out",
    "target", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".next", ".nuxt", "coverage", ".terraform",
    ".bundle", ".gradle", ".idea", ".vscode", "Pods", "Carthage", "tmp",
}

AGENT_TRAILER = re.compile(
    r"(co-authored-by|generated\s+(with|by)|assisted-by|authored-by)\s*:?.*?"
    r"(claude|copilot|cursor|codex|gpt-?\d|openai|anthropic|gemini|aider|"
    r"windsurf|devin|cline|cody|continue\.dev|sourcegraph|amp|factory|droid)",
    re.IGNORECASE,
)

MINIFIED_NAME = re.compile(r"\.min\.|[-.]bundle\.|[-.]chunk\.|\.packed\.", re.IGNORECASE)
LONG_LINE = 300
LONG_LINE_SHARE = 0.05


def is_bundled(entry: FileEntry) -> bool:
    """True only for files named as build artifacts, such as `app.min.js`.

    This is a name check on purpose. Line length cannot tell a minified bundle
    from hand-written code with inline HTML or SQL in it, and guessing wrong
    silently drops real source from the measurement.
    """
    return bool(MINIFIED_NAME.search(entry.path.name))


def long_line_share(entry: FileEntry) -> float:
    """Share of non-blank lines longer than LONG_LINE characters."""
    lines = [line for line in entry.text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    return sum(1 for line in lines if len(line) > LONG_LINE) / len(lines)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


@dataclass
class FileEntry:
    """One source file selected for analysis."""

    path: Path
    rel: str
    lang: Lang
    text: str = ""


def git_root(start: Path) -> Path | None:
    # `git -C` needs a directory; scanning a single file passes its path.
    probe = start.parent if start.is_file() else start
    try:
        out = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None


def compiled_excludes(excludes: list[str]) -> list[re.Pattern[str]]:
    """Compile the --exclude globs once, since every candidate path is tested."""
    return [re.compile(_glob_to_regex(pattern)) for pattern in excludes]


def excluded(rel: str, patterns: list[re.Pattern[str]]) -> bool:
    """True when an --exclude glob matches this scope-relative path."""
    return any(pattern.search(rel) for pattern in patterns)


def lang_for(path: Path) -> Lang | None:
    """The language a path belongs to, by exact filename first, then extension."""
    return BY_FILENAME.get(path.name) or BY_EXT.get(path.suffix.lower())


def git_entry(repo: Path, root: Path, tracked: str, patterns: list[re.Pattern[str]]) -> FileEntry | None:
    """One git-listed path as a FileEntry, or None when it is not measurable."""
    if not tracked:
        return None
    path = repo / tracked
    if path.is_symlink() or not path.is_file():
        return None
    resolved = path.resolve()
    # git lists the whole repository; the scope is `root`.
    if not resolved.is_relative_to(root):
        return None
    if any(part in SKIP_DIRS for part in Path(tracked).parts):
        return None
    # Paths are relative to the measured scope, so a report never names directories
    # the caller did not ask for, and an --exclude glob means the same thing with
    # and without a subtree.
    rel = str(resolved.relative_to(root))
    if excluded(rel, patterns):
        return None
    lang = lang_for(path)
    if lang is None:
        return None
    return FileEntry(path, rel, lang)


def git_listed(repo: Path, root: Path, patterns: list[re.Pattern[str]]) -> list[FileEntry] | None:
    """Tracked and untracked files under `root`, or None when git cannot list them."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    entries = [git_entry(repo, root, tracked, patterns) for tracked in out.stdout.split("\0")]
    return [entry for entry in entries if entry is not None]


def tree_walked(root: Path, patterns: list[re.Pattern[str]]) -> list[FileEntry]:
    """Files found by walking the tree, for when git cannot list them."""
    entries: list[FileEntry] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            rel = str(path.relative_to(root))
            if excluded(rel, patterns):
                continue
            lang = lang_for(path)
            if lang is None:
                continue
            entries.append(FileEntry(path, rel, lang))
    return entries


def read_texts(entries: list[FileEntry]) -> None:
    """Load each file's text. An unreadable file is left empty rather than fatal."""
    for entry in entries:
        try:
            entry.text = entry.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            entry.text = ""


def discover(root: Path, excludes: list[str], use_git: bool = True) -> list[FileEntry]:
    """Return analyzable files under `root`, honoring .gitignore when in a repo."""
    root = root.resolve()
    patterns = compiled_excludes(excludes)
    repo = git_root(root) if use_git else None
    entries = git_listed(repo, root, patterns) if repo is not None else None
    if not entries:
        entries = tree_walked(root, patterns)
    read_texts(entries)
    entries.sort(key=lambda entry: entry.rel)
    return entries


def _glob_to_regex(pattern: str) -> str:
    """Translate a gitignore-ish glob into a regex."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == ".":
            out.append(r"\.")
        elif c in "+()|^$@%{}":
            out.append("\\" + c)
        elif c == "/":
            out.append("/")
        else:
            out.append(c)
        i += 1
    return "".join(out)


# --------------------------------------------------------------------------
# Parse, SLOC, complexity
# --------------------------------------------------------------------------


def load_grammar(cfg: Lang, tree_sitter) -> object | None:
    """Import one grammar and wrap it, or None when it cannot be used.

    A missing package and an ABI mismatch are both ordinary here: a language whose
    grammar will not load is simply reported as having no grammar.
    """
    try:
        module = __import__(cfg.grammar)
    except ImportError:
        return None
    try:
        factory = getattr(module, cfg.grammar_fn, None) or getattr(module, "language", None)
        if factory is None:
            return None
        return tree_sitter.Language(factory())
    except Exception:  # noqa: BLE001 - grammar ABI mismatch is possible
        return None


def load_grammars(langs: set[str]) -> dict[str, object]:
    """Import Tree-sitter languages for `langs`. Returns {} when unavailable."""
    try:
        import tree_sitter
    except ImportError:
        return {}
    loaded: dict[str, object] = {}
    for name in sorted(langs):
        cfg = next((lang for lang in LANGS if lang.name == name), None)
        if cfg is None or cfg.grammar is None:
            continue
        language = load_grammar(cfg, tree_sitter)
        if language is not None:
            loaded[name] = language
    return loaded


@dataclass
class FileAnalysis:
    """Per-file parse result."""

    entry: FileEntry
    sloc: int
    comment_rows: set[int]
    functions: list["Func"] = field(default_factory=list)
    clone_candidates: list[tuple[str, int, int]] = field(default_factory=list)
    parsed: bool = False
    anonymous: int = 0
    # Call-site counts by callee name. The cross-file sum is the use count for
    # the granularity metric.
    references: dict[str, int] = field(default_factory=dict)


@dataclass
class Func:
    """One function with its complexity mass and cross-file use count."""

    name: str
    file: str
    lang: str
    line: int
    cc: int
    sloc: int
    uses: int = 0

    @property
    def mass(self) -> float:
        return self.cc * math.sqrt(self.sloc)


def analyze_file(entry: FileEntry, parser, named_only: bool = True) -> FileAnalysis:
    """Parse one file and extract SLOC, comment rows, functions, clone candidates."""
    tree = parser.parse(entry.text.encode("utf-8", errors="replace"))
    root = tree.root_node
    cfg = entry.lang
    lines = entry.text.splitlines()

    spans = comment_spans(root, cfg, lines)
    sloc_lines, comment_rows = sloc_stats(lines, spans)
    scan = scan_functions(entry, cfg, root, lines, spans, named_only)
    return FileAnalysis(
        entry=entry, sloc=sloc_lines, comment_rows=comment_rows,
        functions=scan.functions, clone_candidates=_clone_candidates(root, cfg),
        parsed=True, anonymous=scan.anonymous,
        references=_call_references(root, cfg, scan.declared_ids, scan.self_spans),
    )


def comment_spans(root, cfg: Lang, lines: list[str]) -> dict[int, list[tuple[int, int]]]:
    """Comment columns per row, which SLOC and clone counting both need."""
    spans: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for node in named_walk(root):
        if node.type not in cfg.comments:
            continue
        start_row, start_col = node.start_point
        end_row, end_col = node.end_point
        for row in range(start_row, end_row + 1):
            start = start_col if row == start_row else 0
            end = end_col if row == end_row else (len(lines[row]) if row < len(lines) else 0)
            spans[row].append((start, end))
    return spans


def sloc_stats(lines: list[str], spans: dict[int, list[tuple[int, int]]]) -> tuple[int, set[int]]:
    """(SLOC, comment-only rows) for one file.

    A trimmed row of punctuation is not SLOC even though a parser hands it over as
    a node: counting `}` as code inflates every language by its brace density.
    """
    comment_rows: set[int] = set()
    sloc_lines = 0
    for idx, raw in enumerate(lines):
        if not raw.strip():
            continue
        here = spans.get(idx, [])
        if not _is_sloc(raw, here):
            if here and _line_is_comment(raw, here):
                comment_rows.add(idx)
            continue
        sloc_lines += 1
    return sloc_lines, comment_rows


@dataclass
class FunctionScan:
    """What the function vocabulary found in one file."""

    functions: list[Func]
    anonymous: int
    declared_ids: set[int]
    self_spans: list[tuple[str, int, int]]


def is_callable(node, cfg: Lang) -> bool:
    """True when this node is a callable the table counts."""
    if node.type not in cfg.functions:
        return False
    if cfg.function_parents and node.parent is not None \
            and node.parent.type not in cfg.function_parents:
        return False
    return not (node.child_by_field_name("body") is None and node.child_count == 0)


def scan_functions(
    entry: FileEntry,
    cfg: Lang,
    root,
    lines: list[str],
    spans: dict[int, list[tuple[int, int]]],
    named_only: bool,
) -> FunctionScan:
    """Every callable in one file, with the ids and spans the call counter needs."""
    functions: list[Func] = []
    anonymous = 0
    declared_ids: set[int] = set()
    self_spans: list[tuple[str, int, int]] = []
    for node in named_walk(root):
        if not is_callable(node, cfg):
            continue
        declared = _func_name_node(node, cfg)
        if declared is not None:
            declared_ids.add(declared.id)
        name, is_named = _func_name(node, cfg)
        self_spans.append((name, node.start_byte, node.end_byte))
        if not is_named:
            anonymous += 1
            if named_only:
                continue
        body = node.child_by_field_name("body") or node
        code_lines = {
            row for row in range(node.start_point[0], node.end_point[0] + 1)
            if row < len(lines) and _is_sloc(lines[row], spans.get(row, []))
        }
        functions.append(
            Func(
                name=name,
                file=entry.rel,
                lang=cfg.name,
                line=node.start_point[0] + 1,
                cc=1 + _decision_count(body, cfg, fold_nested=named_only),
                sloc=max(1, len(code_lines)),
            )
        )
    return FunctionScan(functions, anonymous, declared_ids, self_spans)


def named_walk(node):
    """Pre-order traversal yielding named nodes only.

    Grammars reuse a keyword's name for the token that spells it: tree-sitter-ruby
    nests an anonymous `if` token inside every `if` statement. Matching decision
    types against anonymous tokens double-counts, so every type match uses this.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        if current.is_named:
            yield current
        stack.extend(reversed(current.children))


def _line_is_comment(raw: str, spans: list[tuple[int, int]]) -> bool:
    """True when every non-whitespace character on the line sits in a comment."""
    covered = bytearray(len(raw))
    for start, end in spans:
        for i in range(max(0, start), min(len(raw), end)):
            covered[i] = 1
    return all(covered[i] or raw[i].isspace() for i in range(len(raw)))


PUNCT_ONLY = frozenset("{}[]();,:")


def _uncommented(raw: str, spans: list[tuple[int, int]]) -> str:
    """Blank out every comment span on one line."""
    if not spans:
        return raw
    chars = list(raw)
    for start, end in spans:
        for i in range(max(0, start), min(len(chars), end)):
            chars[i] = " "
    return "".join(chars)


def _is_sloc(raw: str, spans: list[tuple[int, int]]) -> bool:
    """True when a line counts as source.

    Matches the reference implementation: strip comments, then reject blanks and
    lines holding nothing but punctuation. A lone `}` or `);` is layout, not code.
    """
    text = _uncommented(raw, spans).strip()
    return bool(text) and not all(character in PUNCT_ONLY for character in text)


ASSIGNMENT_PARENTS = frozenset(
    {
        "assignment_expression", "assignment", "init_declarator",
        "lexical_declaration", "variable_declaration", "variable_declarator",
        "field_declaration", "property_declaration", "const_declaration",
    }
)


def _clean_text(node) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace").strip().splitlines()[0][:120]


def _assigned_name(node, cfg: Lang) -> str:
    """Name inherited from the nearest enclosing assignment, or "" if none."""
    parent = node.parent
    while parent is not None:
        if parent.type in ASSIGNMENT_PARENTS:
            for child in parent.children:
                if child.type in cfg.identifiers:
                    name = _clean_text(child)
                    if name.strip():
                        return name
        parent = parent.parent
    return ""


def _func_name_node(node, cfg: Lang):
    """The node that spells a callable's declared name, or None.

    Mirrors the field lookup in `_func_name` so the granularity counter can
    exclude exactly the node it treats as a declaration.
    """
    for field_name in ("name", "declarator", "pattern"):
        child = node.child_by_field_name(field_name)
        if child is None:
            continue
        # C, C++, and Java wrap the name in a chain of declarators.
        for _ in range(3):
            if not child.type.endswith("declarator"):
                break
            inner = child.child_by_field_name("declarator")
            if inner is None or inner.id == child.id:
                break
            child = inner
        if _clean_text(child):
            return child
    return None


def _func_name(node, cfg: Lang) -> tuple[str, bool]:
    """Return (display name, is named).

    A callable counts toward the metrics only when it can be named: either from
    its own declaration, or from the variable it is assigned to. This matches the
    reference implementation, which drops anonymous callbacks from both the
    numerator and the denominator. `--functions all` keeps them instead.
    """
    declared = _func_name_node(node, cfg)
    if declared is not None:
        return _clean_text(declared), True

    assigned = _assigned_name(node, cfg)
    if assigned:
        return assigned, True

    head = _clean_text(node) or node.type
    return head, False


# A use is a call site. Node types that name an invocation, and the fields that
# hold the callee. A language whose grammar differs can override `Lang.calls`.
DEFAULT_CALLS = frozenset(
    {
        "call", "call_expression", "function_call", "method_invocation",
        "invocation_expression", "function_call_expression", "member_call_expression",
        "scoped_call_expression", "macro_invocation", "apply", "command",
    }
)
DEFAULT_CALLEE_FIELDS = ("function", "name", "method", "callee", "macro", "command_name", "constructor")
ARGUMENT_CONTAINERS = frozenset(
    {"arguments", "argument_list", "argument", "token_tree", "call_arguments", "type_arguments"}
)


def _callee_subtree(node):
    """The subtree that names what a call invokes, or None."""
    for field_name in DEFAULT_CALLEE_FIELDS:
        child = node.child_by_field_name(field_name)
        if child is not None:
            return child
    for child in node.named_children:
        if child.type not in ARGUMENT_CONTAINERS:
            return child
    return None


def _last_identifier(node, cfg: Lang):
    """The rightmost identifier in a subtree. For `obj.method` this is the
    method, which is the invoked name, not the receiver."""
    found = None
    for current in named_walk(node):
        if current.type in cfg.identifiers:
            found = current
    return found


def _is_self_reference(node, text: str, self_spans: list[tuple[str, int, int]]) -> bool:
    """True when the call sits inside a callable with the same name, so it is
    recursion rather than reuse of a helper."""
    start = node.start_byte
    return any(name == text and begin <= start < end for name, begin, end in self_spans)


def _call_references(
    root,
    cfg: Lang,
    declared_ids: set[int],
    self_spans: list[tuple[str, int, int]],
) -> dict[str, int]:
    """Count uses by name, where a use is a call site.

    Only a call or method call counts: `foo()` and `obj.foo()`. A name that is
    merely mentioned (a callback passed by name, a local variable that shares the
    name) is not a use. Recursion is not a use either. This is still name-based,
    so two callables that share a name share their count, but it does not let a
    shadowing local invent calls.
    """
    call_types = cfg.calls or DEFAULT_CALLS
    counts: dict[str, int] = defaultdict(int)
    for node in named_walk(root):
        if node.type not in call_types:
            continue
        callee = _callee_subtree(node)
        if callee is None:
            continue
        identifier = _last_identifier(callee, cfg)
        if identifier is None or identifier.id in declared_ids:
            continue
        text = _clean_text(identifier)
        if not text or _is_self_reference(identifier, text, self_spans):
            continue
        counts[text] += 1
    return dict(counts)


def _decision_count(node, cfg: Lang, fold_nested: bool = False) -> int:
    """Cyclomatic increments for one function.

    With `fold_nested`, branches inside nested functions count toward this
    function too, which is how the reference implementation attributes the cost
    of inline callbacks to the named function that holds them. Without it, every
    nested function is its own unit and counting its branches twice would be an
    overcount, so the walk stops at nested function boundaries.
    """
    total = 0
    stack = [node]
    while stack:
        current = stack.pop()
        nested = current.is_named and current.type in cfg.functions
        if not fold_nested and current is not node and nested:
            continue
        if counts_as_decision(current, cfg):
            total += 1
        stack.extend(reversed(current.children))
    return total


def counts_as_decision(node, cfg: Lang) -> bool:
    """True when this node is one cyclomatic increment.

    Grammars reuse a keyword's name for the token that spells it, so only named
    nodes count: an anonymous `if` token would otherwise double every branch.
    """
    if not node.is_named:
        return False
    if node.type in cfg.decisions:
        return True
    return node.type in cfg.binary_like and _has_bool_operator(node, cfg)


def _has_bool_operator(node, cfg: Lang) -> bool:
    for child in node.children:
        if child.child_count == 0 and child.text is not None:
            token = child.text.decode("utf-8", errors="replace")
            if token in cfg.bool_tokens:
                return True
    return False


def _clone_candidates(root, cfg: Lang) -> list[tuple[str, int, int]]:
    """Return (structural hash, start_row, end_row) for clone-sized subtrees.

    Candidate node types come from `clone_types` when the language declares it,
    and from the union of functions and decision points otherwise.
    """
    candidates: list[tuple[str, int, int]] = []
    types = cfg.clone_types or (cfg.functions | cfg.decisions)
    for node in named_walk(root):
        if node.type not in types:
            continue
        if _statement_count(node) < MIN_CLONE_STATEMENTS:
            continue
        start, end = node.start_point[0], node.end_point[0]
        if end - start + 1 < 2:
            continue
        candidates.append((_structural_hash(node, cfg), start, end))
    return candidates


# Single-statement body wrappers. A grammar can nest one inside another
# (Go: function_declaration -> block -> statement_list), so the unwrap repeats
# until the statements themselves are reached.
BODY_WRAPPERS = frozenset(
    {
        "block", "body_statement", "compound_statement", "do", "do_group",
        "function_body", "statement_block", "statement_list", "statements",
    }
)


def _statement_count(node) -> int:
    """Count statements in a node, looking through single body wrappers."""
    target = node
    for _ in range(4):
        inner = next((c for c in target.children if c.type in BODY_WRAPPERS), None)
        if inner is None:
            break
        target = inner
    return len(target.named_children)


def _structural_hash(node, cfg: Lang) -> str:
    """Hash a subtree with identifiers and literals normalized away."""
    parts: list[str] = []

    def emit(n) -> None:
        if n.child_count == 0:
            text = n.text.decode("utf-8", errors="replace") if n.text else ""
            if n.type in cfg.identifiers:
                parts.append("$ID")
            elif n.type in cfg.literals:
                parts.append("$LIT")
            elif text:
                parts.append(text)
            return
        if n.type in cfg.comments:
            return
        parts.append(n.type)
        for child in n.children:
            emit(child)
        parts.append(")")

    emit(node)
    return hashlib.blake2b("\x00".join(parts).encode("utf-8"), digest_size=12).hexdigest()


# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------


@dataclass
class GitStats:
    """Repository growth over a window."""

    available: bool = False
    window: str = ""
    skip_reason: str = ""
    commits: int = 0
    insertions: int = 0
    deletions: int = 0
    delta: int = 0
    total_commits: int = 0
    agent_commits: int = 0
    agent_insertions: int = 0
    largest: list[tuple[int, str]] = field(default_factory=list)


def git_stats(root: Path, since: str) -> GitStats:
    stats = GitStats(window=since)
    if git_root(root) is None:
        return stats
    stats.available = True

    def run(*args: str) -> str:
        try:
            out = subprocess.run(
                ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120
            )
            return out.stdout if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    stats.total_commits = _int(run("rev-list", "--count", "HEAD"))
    base = run("rev-list", "-1", f"--before={since}", "HEAD").strip()
    rev = f"{base}..HEAD" if base else "HEAD"

    # With no commit older than the window, diffing against `HEAD` compares the
    # tree to the working directory and reports zero. Use the empty tree so the
    # whole history counts as growth.
    if base:
        short = run("diff", "--shortstat", f"{base}..HEAD")
    else:
        empty = run("hash-object", "-t", "tree", "/dev/null").strip()
        short = run("diff", "--shortstat", f"{empty}..HEAD") if empty else ""
    stats.insertions = _int(_match(short, r"(\d+) insertion"))
    stats.deletions = _int(_match(short, r"(\d+) deletion"))
    stats.commits = _int(run("rev-list", "--count", rev))
    stats.delta = stats.insertions - stats.deletions

    log = run("log", rev, "--pretty=format:%x01%s%n%b", "--shortstat")
    for chunk in log.split("\x01"):
        if not chunk.strip():
            continue
        head, _, body = chunk.partition("\n")
        ins = _int(_match(chunk, r"(\d+) insertion"))
        if AGENT_TRAILER.search(chunk):
            stats.agent_commits += 1
            stats.agent_insertions += ins
        stats.largest.append((ins, head.strip()[:90]))
    stats.largest.sort(reverse=True)
    stats.largest = stats.largest[:5]
    return stats


def _match(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _int(value: str | None) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------
# scb-check (reference implementation)
# --------------------------------------------------------------------------


def run_scb_check(root: Path, timeout: int = 900) -> tuple[dict | None, str | None]:
    """Run the paper author's scb-check CLI.

    Returns (report, error). `error` is set when scb-check was reachable but
    produced nothing usable, so a silent failure never looks like a pass.
    """
    command = scb_command()
    if command is None:
        return None, "neither scb-check nor uvx is on PATH"
    try:
        out = subprocess.run(
            [*command, "check", str(root), "--output-format", "json"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"scb-check did not finish within {timeout}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"scb-check could not run: {exc}"

    report = json_object(out.stdout or "")
    if report is not None:
        return report, None
    detail = (out.stderr or "").strip().splitlines()
    tail = detail[-1][:120] if detail else "no output"
    return None, f"scb-check exited {out.returncode} without a JSON report ({tail})"


def scb_command() -> list[str] | None:
    """How to invoke the reference implementation, or None when it is out of reach."""
    if shutil.which("scb-check"):
        return ["scb-check"]
    if shutil.which("uvx"):
        return [
            "uvx", "--quiet", "--from", "git+https://github.com/gabeorlanski/scb-check",
            "scb-check",
        ]
    return None


def json_object(stdout: str) -> dict | None:
    """The last JSON object in some output, which is how scb-check reports."""
    for line in reversed(stdout.strip().splitlines()):
        if line.strip().startswith("{"):
            try:
                return json.loads(line.strip())
            except json.JSONDecodeError:
                continue
    return None


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def band(value: float, kind: str, higher_is_worse: bool = True) -> str:
    b = BANDS[kind]
    if higher_is_worse:
        if value <= b["human"]:
            return "at-or-below human baseline"
        if value >= b["agent"]:
            return "in or above agent band"
        return "between human and agent bands"
    return ""


def ratio(value: float, kind: str) -> str:
    b = BANDS[kind]
    return f"{value / b['human']:.2f}x the human baseline" if b["human"] else ""


def granularity_band(value: float) -> str:
    """Granularity is a band, not a floor. Sitting below it is not an
    improvement: it means fewer named steps and more mass in one function."""
    b = BANDS["granularity"]
    low = b["human"] - b["human_sd"]
    high = b["human"] + b["human_sd"]
    if value < low:
        return "below human band"
    if value > high:
        return "above human band"
    return "within human band"


@dataclass
class Signals:
    """The metric values plus the lists the payload entries are built from."""

    verbosity: float | None
    measured_sloc: int
    clone_lines: int
    groups: dict[str, list[tuple[str, int, int]]]
    functions: list[Func]
    used_functions: list[Func]
    reused_functions: list[Func]
    single_use_functions: list[Func]
    unused_functions: list[Func]
    granularity: float | None
    total_mass: float
    high_mass: float
    erosion: float | None
    high_cc: list[Func]


@dataclass(frozen=True)
class MassTotals:
    """Complexity mass overall, and the part sitting in eroded functions."""

    total: float
    high: float
    high_cc: list[Func]

    @property
    def erosion(self) -> float | None:
        """The share of mass inside functions over the CC cutoff."""
        return self.high / self.total if self.total else None


def use_counts(analyzed: list[FileAnalysis]) -> dict[str, int]:
    """Call sites summed by callee name across the corpus.

    A named callable invoked once is premature reuse (Muratori, "Semantic
    Compression"). Counts are summed by name, so same-name definitions share one.
    """
    counts: dict[str, int] = defaultdict(int)
    for result in analyzed:
        for name, count in result.references.items():
            counts[name] += count
    return dict(counts)


def apply_uses(functions: list[Func], counts: dict[str, int]) -> None:
    """Stamp every callable with how many call sites it has."""
    for func in functions:
        func.uses = counts.get(func.name, 0)


def clone_groups_by_digest(analyzed: list[FileAnalysis]) -> dict[str, list[tuple[str, int, int]]]:
    """Clone candidates grouped by structural hash, across every measured file."""
    groups: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    for result in analyzed:
        for digest, start, end in result.clone_candidates:
            groups[digest].append((result.entry.rel, start, end))
    return dict(groups)


def is_clone_group(members: list[tuple[str, int, int]]) -> bool:
    """True when a digest occurs in two places over at least MIN_CLONE_LINES rows."""
    return len(members) >= 2 and members[0][2] - members[0][1] + 1 >= MIN_CLONE_LINES


def duplicate_lines(analyzed: list[FileAnalysis], groups: dict[str, list[tuple[str, int, int]]]) -> int:
    """Rows that sit in a clone group, with comment rows taken back out.

    A digest seen in two places marks both spans as duplicate lines.
    """
    clone_rows: dict[str, set[int]] = defaultdict(set)
    for members in groups.values():
        if not is_clone_group(members):
            continue
        for rel, start, end in members:
            clone_rows[rel].update(range(start, end + 1))
    comments = {result.entry.rel: result.comment_rows for result in analyzed}
    return sum(len(rows - comments.get(rel, set())) for rel, rows in clone_rows.items())


def mass_totals(functions: list[Func]) -> MassTotals:
    """Complexity mass overall, and the mass in functions over the CC cutoff."""
    high_cc = [f for f in functions if f.cc > HIGH_CC]
    return MassTotals(
        total=sum(f.mass for f in functions),
        high=sum(f.mass for f in high_cc),
        high_cc=high_cc,
    )


def compute_signals(functions: list[Func], analyzed: list[FileAnalysis]) -> Signals:
    """Compute verbosity's numerator, erosion, and granularity from parsed files.

    Takes the callables and the trustworthy file analyses, and returns the values
    plus the candidate lists the report ranks. Split out of the pipeline so the
    metric math can be checked without a Tree-sitter grammar.

    Zero-use callables are excluded from the granularity ratio and listed
    separately: they are entry points, exports, or dead code, and the metric cannot
    tell which.
    """
    apply_uses(functions, use_counts(analyzed))
    split = split_by_use(functions)
    groups = clone_groups_by_digest(analyzed)
    clone_lines = duplicate_lines(analyzed, groups)
    sloc = measured_sloc(analyzed)
    mass = mass_totals(functions)
    return Signals(
        verbosity=verbosity_value(clone_lines, sloc),
        measured_sloc=sloc,
        clone_lines=clone_lines,
        groups=groups,
        functions=functions,
        used_functions=split.used,
        reused_functions=split.reused,
        single_use_functions=split.single_use,
        unused_functions=split.unused,
        granularity=split.granularity,
        total_mass=mass.total,
        high_mass=mass.high,
        erosion=mass.erosion,
        high_cc=mass.high_cc,
    )


@dataclass(frozen=True)
class UseSplit:
    """Callables split by how many call sites they have."""

    used: list[Func]
    reused: list[Func]
    single_use: list[Func]
    unused: list[Func]

    @property
    def granularity(self) -> float | None:
        """The share of used callables invoked exactly once."""
        return len(self.single_use) / len(self.used) if self.used else None


def split_by_use(functions: list[Func]) -> UseSplit:
    """Partition callables by use count, which is what granularity measures."""
    used = [f for f in functions if f.uses >= 1]
    return UseSplit(
        used=used,
        reused=[f for f in used if f.uses >= 2],
        single_use=[f for f in used if f.uses == 1],
        unused=[f for f in functions if f.uses == 0],
    )


def measured_sloc(analyzed: list[FileAnalysis]) -> int:
    """SLOC in the files the metrics trust."""
    return sum(result.sloc for result in analyzed)


def verbosity_value(clone_lines: int, sloc: int) -> float | None:
    """Duplicated lines per measured SLOC, or None when nothing was parsed."""
    return clone_lines / sloc if sloc else None


def grammar_packages(languages: set[str]) -> list[str]:
    """The pip packages that supply grammars for these languages."""
    return sorted(
        lang.grammar.replace("_", "-")
        for lang in LANGS
        if lang.name in languages and lang.grammar
    )


def _fallback_sloc(text: str) -> int:
    """Lines counted without a parse, for files no grammar could read."""
    return sum(1 for line in text.splitlines() if line.strip())


class InputError(RuntimeError):
    """A request that cannot be measured, with the lines that explain why."""

    def __init__(self, message: str, *hints: str) -> None:
        super().__init__(message)
        self.hints = list(hints)

    def lines(self) -> list[str]:
        """The message and its hints, formatted the way the tool prints them."""
        return [f"error: {self}", *(f"       {hint}" for hint in self.hints)]


@dataclass
class Target:
    """What the caller asked to measure: one file, or everything under a root."""

    scope: Path
    root: Path
    requested: FileEntry | None = None


def build_parser() -> argparse.ArgumentParser:
    """The measurement flags. Shared with the CLI, which reads them before measuring."""
    ap = argparse.ArgumentParser(description="Measure code slop (verbosity and structural erosion).")
    ap.add_argument("path", nargs="?", default=".", help="repository or directory to measure")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a text report")
    ap.add_argument("--since", default="30 days ago", help="git window for growth (default: '30 days ago')")
    ap.add_argument("--top", type=int, default=10, help="how many hotspots to list")
    ap.add_argument("--lang", action="append", default=[], help="restrict to a language (repeatable)")
    ap.add_argument("--exclude", action="append", default=[], help="glob to skip (repeatable)")
    ap.add_argument("--scb", action="store_true", help="also run scb-check (the reference implementation)")
    ap.add_argument("--no-git", action="store_true", help="skip git growth metrics")
    ap.add_argument(
        "--functions", choices=("named", "all"), default="named",
        help="count only named callables (default, matches scb-check) or every callable",
    )
    ap.add_argument(
        "--include-minified", action="store_true",
        help="measure minified and bundled files too (they are excluded by default)",
    )
    ap.add_argument(
        "--print-requirements", action="store_true",
        help="print the tree-sitter grammar packages the target needs, then exit",
    )
    return ap


def resolve_target(path: str) -> Target:
    """Locate the scope, or raise InputError naming what is wrong with the path."""
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise InputError(f"no such path: {root}")
    if not root.is_file():
        return Target(scope=root, root=root)
    lang = BY_FILENAME.get(root.name) or BY_EXT.get(root.suffix.lower())
    if lang is None:
        raise InputError(f"unsupported file type: {root.name}")
    try:
        text = root.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise InputError(f"cannot read {root}: {exc}")
    return Target(scope=root, root=root.parent, requested=FileEntry(root, root.name, lang, text))


def parse_request(argv: list[str] | None) -> tuple[argparse.Namespace, Target]:
    """Flags and scope, decided once, so every stage receives a resolved request."""
    args = build_parser().parse_args(argv)
    return args, resolve_target(args.path)


def no_files_error(scope: Path) -> InputError:
    """The error for a scope with nothing measurable in it."""
    return InputError(
        f"no recognized source files under {scope}",
        "recognized: " + " ".join(sorted(BY_EXT)) + " " + " ".join(sorted(BY_FILENAME)),
        "add the language to LANGS in this script, or run scb-check directly.",
    )


def only_languages(files: list[FileEntry], langs: list[str]) -> list[FileEntry]:
    """Keep only the files whose language was asked for."""
    if not langs:
        return files
    wanted = set(langs)
    return [f for f in files if f.lang.name in wanted]


def split_bundled(files: list[FileEntry]) -> tuple[list[FileEntry], list[FileEntry]]:
    """The files to measure and the build artifacts, which are excluded by default.

    A name check decides this, not a line-length guess: a generator's output would
    otherwise dominate erosion.
    """
    bundled = [f for f in files if is_bundled(f)]
    return [f for f in files if not is_bundled(f)], bundled


def select_inputs(
    target: Target,
    *,
    excludes: list[str],
    langs: list[str],
    use_git: bool,
    include_bundled: bool,
) -> tuple[list[FileEntry], list[FileEntry]]:
    """The files to measure and the build artifacts left out. Raises InputError when empty."""
    if target.requested is not None:
        files = [target.requested]
    else:
        files = discover(target.root, excludes, use_git=use_git)
    files = only_languages(files, langs)
    if not files:
        raise no_files_error(target.scope)
    if target.requested is not None or include_bundled:
        return files, []
    files, bundled = split_bundled(files)
    if not files:
        raise InputError(
            "every recognized file is named as a build artifact",
            "pass --include-minified to measure them anyway.",
        )
    return files, bundled


def required_grammars(args: argparse.Namespace, target: Target) -> list[str]:
    """Grammar packages this request needs, read from the request alone.

    The CLI asks for this before it measures, so it can install what the run needs
    without importing a grammar to find out.
    """
    files, _bundled = select_inputs(
        target,
        excludes=args.exclude,
        langs=args.lang,
        use_git=not args.no_git,
        include_bundled=args.include_minified,
    )
    return grammar_packages({f.lang.name for f in files})


def long_line_report(files: list[FileEntry]) -> list[tuple[str, float]]:
    """Files with an unusual share of very long lines, longest share first.

    Long lines suggest generated or templated code, but they are not proof. Report
    them so the reader can decide; never exclude on this alone.
    """
    shares = []
    for entry in files:
        share = long_line_share(entry)
        if share >= LONG_LINE_SHARE:
            shares.append((entry.rel, share))
    shares.sort(key=lambda pair: pair[1], reverse=True)
    return shares


def load_parsers(grammars: dict[str, object]) -> tuple[dict[str, object], bool]:
    """A parser per grammar that starts, and whether Tree-sitter imported at all."""
    try:
        import tree_sitter
    except ImportError:
        return {}, False
    parsers: dict[str, object] = {}
    for name, language in grammars.items():
        try:
            parsers[name] = tree_sitter.Parser(language)
        except Exception:  # noqa: BLE001 - a grammar ABI mismatch must not kill the run
            continue
    return parsers, True


def unparsed(entry: FileEntry) -> FileAnalysis:
    """A file that still counts for SLOC but contributed no parse."""
    return FileAnalysis(entry=entry, sloc=_fallback_sloc(entry.text), comment_rows=set())


def analyze_files(
    files: list[FileEntry], parsers: dict[str, object], named_only: bool
) -> tuple[list[FileAnalysis], dict[str, int]]:
    """Analyze every file, falling back to SLOC when a parser is missing or fails."""
    results: list[FileAnalysis] = []
    failures: dict[str, int] = defaultdict(int)
    for entry in files:
        if not entry.text.strip():
            continue
        parser = parsers.get(entry.lang.name)
        if parser is None:
            results.append(unparsed(entry))
            continue
        try:
            results.append(analyze_file(entry, parser, named_only=named_only))
        except Exception:  # noqa: BLE001 - a bad grammar must not kill the run
            failures[entry.lang.name] += 1
            results.append(unparsed(entry))
    return results, dict(failures)


def unreliable_languages(results: list[FileAnalysis], parsers: dict[str, object]) -> set[str]:
    """Languages that parsed but yielded no callable at all.

    Zero named functions is normal once anonymous callbacks are excluded, so only a
    language with no callable of any kind points at a broken vocabulary. Both make
    for a flattering zero, so neither metric includes them.
    """
    named: dict[str, int] = defaultdict(int)
    anonymous: dict[str, int] = defaultdict(int)
    for result in results:
        if result.parsed:
            named[result.entry.lang.name] += len(result.functions)
            anonymous[result.entry.lang.name] += result.anonymous
    return {
        name for name in parsers
        if named.get(name, 0) == 0 and anonymous.get(name, 0) == 0
    }


@dataclass
class Run:
    """One measurement, from the resolved request to the numbers the report prints."""

    args: argparse.Namespace
    target: Target
    scan_root: Path
    files: list[FileEntry]
    bundled: list[FileEntry]
    long_lines: list[tuple[str, float]]
    present: set[str]
    grammars: dict[str, object]
    parsers: dict[str, object]
    tree_sitter: bool
    results: list[FileAnalysis]
    analyzed: list[FileAnalysis]
    unreliable: set[str]
    failures: dict[str, int]
    signals: Signals
    scb: dict | None
    scb_error: str | None
    git: GitStats

    @property
    def total_sloc(self) -> int:
        return sum(result.sloc for result in self.results)

    @property
    def measured_sloc(self) -> int:
        return measured_sloc(self.analyzed)

    @property
    def anonymous_skipped(self) -> int:
        return sum(result.anonymous for result in self.analyzed)


def run_measurement(target: Target, args: argparse.Namespace) -> Run:
    """Measure the resolved request: discover, parse, then compute the signals."""
    files, bundled = select_inputs(
        target,
        excludes=args.exclude,
        langs=args.lang,
        use_git=not args.no_git,
        include_bundled=args.include_minified,
    )
    present = {f.lang.name for f in files}
    grammars = load_grammars(present)
    parsers, tree_sitter_available = load_parsers(grammars)
    results, failures = analyze_files(files, parsers, args.functions == "named")
    unreliable = unreliable_languages(results, parsers)
    # Only parsed, trustworthy files may enter the metrics. An unparsed language
    # would otherwise dilute the denominator and fake a low verbosity.
    analyzed = [r for r in results if r.parsed and r.entry.lang.name not in unreliable]
    # The git root holds history for the whole repository, even when a subtree is
    # the scope, so growth and scb-check both run against it.
    scan_root = git_root(target.root) or target.root
    scb, scb_error = run_scb_check(scan_root) if args.scb else (None, None)
    git = GitStats(skip_reason="--no-git") if args.no_git else git_stats(scan_root, args.since)
    return Run(
        args=args,
        target=target,
        scan_root=scan_root,
        files=files,
        bundled=bundled,
        long_lines=long_line_report(files),
        present=present,
        grammars=grammars,
        parsers=parsers,
        tree_sitter=bool(grammars) and tree_sitter_available,
        results=results,
        analyzed=analyzed,
        unreliable=unreliable,
        failures=failures,
        signals=compute_signals([f for r in analyzed for f in r.functions], analyzed),
        scb=scb,
        scb_error=scb_error,
        git=git,
    )


def band_fields(value: float | None, kind: str) -> dict:
    """The band a value lands in and its multiple of the human baseline.

    One place pairs a value with its band, so a reading cannot carry a stale band.
    """
    return {
        "band": band(value, kind) if value is not None else None,
        "vs_human": ratio(value, kind) if value is not None else None,
    }


def scb_coverage(run: Run) -> float | None:
    """How much of this repo's SLOC the reference implementation could see."""
    if not run.scb:
        return None
    scanned = run.scb.get("total_loc") or 0
    return scanned / run.total_sloc if run.total_sloc else 0.0


def language_sloc(results: list[FileAnalysis]) -> dict[str, dict[str, int]]:
    """File and SLOC counts per language, for the header line and the JSON."""
    by_lang: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "sloc": 0})
    for result in results:
        entry = by_lang[result.entry.lang.name]
        entry["files"] += 1
        entry["sloc"] += result.sloc
    return {name: dict(counts) for name, counts in sorted(by_lang.items())}


def engine_payload(run: Run) -> dict:
    """What the engine saw: grammars, exclusions, and what it could not parse."""
    return {
        "tree_sitter": run.tree_sitter,
        "grammars": sorted(run.grammars),
        "languages_present": sorted(run.present),
        "languages_without_grammar": sorted(run.present - set(run.grammars)),
        "unreliable_languages": sorted(run.unreliable),
        "bundled_files": [f.rel for f in run.bundled],
        "anonymous_skipped": run.anonymous_skipped,
        "functions_mode": run.args.functions,
        "long_line_files": [
            {"file": rel, "share": round(share, 3)} for rel, share in run.long_lines[:10]
        ],
        "parse_failures": run.failures,
        "scb_check": bool(run.scb),
        "scb_check_coverage": scb_coverage(run),
        "scb_check_error": run.scb_error,
    }


def sloc_payload(run: Run) -> dict:
    """Line counts: total, measured, and the difference the metrics ignore."""
    return {
        "total": run.total_sloc,
        "measured": run.measured_sloc,
        "unmeasured": run.total_sloc - run.measured_sloc,
        "files_total": len(run.results),
        "files_measured": len(run.analyzed),
        "by_language": language_sloc(run.results),
    }


def verbosity_payload(run: Run) -> dict:
    """Duplicated lines over measured SLOC, next to the reference figure."""
    value = run.signals.verbosity
    return {
        "value": value,
        "clone_lines": run.signals.clone_lines,
        "flagged_lines": run.signals.clone_lines,
        "ast_grep_lines": (run.scb or {}).get("ast_grep_flagged_loc"),
        **band_fields(value, "verbosity"),
    }


def erosion_payload(run: Run) -> dict:
    """Complexity mass above the CC cutoff over all complexity mass."""
    value = run.signals.erosion
    return {
        "value": value,
        "functions": len(run.signals.functions),
        "high_cc_functions": len(run.signals.high_cc),
        "total_mass": run.signals.total_mass,
        "high_cc_mass": run.signals.high_mass,
        "anonymous_skipped": run.anonymous_skipped,
        **band_fields(value, "erosion"),
    }


def granularity_payload(run: Run) -> dict:
    """The share of used callables with exactly one call site, plus its band."""
    signals = run.signals
    value = signals.granularity
    return {
        "value": value,
        "used_functions": len(signals.used_functions),
        "reused_functions": len(signals.reused_functions),
        "single_use_functions": len(signals.single_use_functions),
        "unused_functions": len(signals.unused_functions),
        "band": granularity_band(value) if value is not None else None,
        "human": BANDS["granularity"]["human"],
        "human_sd": BANDS["granularity"]["human_sd"],
        "single_use_top": [
            func_entry(f, with_uses=True) for f in rank_by(signals.single_use_functions, run.args.top)
        ],
    }


def func_entry(func: Func, *, with_uses: bool = False) -> dict:
    """One callable, as the hotspot and single-use lists report it."""
    entry = {
        "file": func.file, "line": func.line, "name": func.name,
    }
    if with_uses:
        entry["uses"] = func.uses
    entry.update({"cc": func.cc, "sloc": func.sloc})
    return entry


def rank_by(functions: list[Func], top: int) -> list[Func]:
    """The heaviest callables first, which is the order every list uses."""
    return sorted(functions, key=lambda f: f.mass, reverse=True)[:top]


def duplicate_payload(run: Run) -> list[dict]:
    """Clone groups worth compressing, largest saving first."""
    groups = (m for m in run.signals.groups.values() if is_clone_group(m))
    ranked = sorted(groups, key=lambda m: (m[0][2] - m[0][1] + 1) * (len(m) - 1), reverse=True)
    return [
        {
            "lines": members[0][2] - members[0][1] + 1,
            "occurrences": len(members),
            # Lines freed if the block were compressed into one place.
            "recoverable_lines": (members[0][2] - members[0][1] + 1) * (len(members) - 1),
            "locations": [f"{rel}:{start + 1}" for rel, start, _ in members[:DUPLICATE_LOCATIONS]],
        }
        for members in ranked[: run.args.top]
    ]


def git_payload(git: GitStats) -> dict:
    """Growth over the window, and the commits that carried an agent trailer."""
    return {
        "available": git.available,
        "window": git.window,
        "skip_reason": git.skip_reason,
        "commits": git.commits,
        "insertions": git.insertions,
        "deletions": git.deletions,
        "delta_loc": git.delta,
        "total_commits": git.total_commits,
        "agent_commits": git.agent_commits,
        "agent_insertions": git.agent_insertions,
        "largest_commits": git.largest,
    }


def build_payload(run: Run) -> dict:
    """The JSON report: every number the text report prints, plus the lists behind them."""
    return {
        "root": str(run.target.scope),
        "engine": engine_payload(run),
        "sloc": sloc_payload(run),
        "verbosity": verbosity_payload(run),
        "erosion": erosion_payload(run),
        "granularity": granularity_payload(run),
        "git": git_payload(run.git),
        "hotspots": [func_entry(f) for f in rank_by(run.signals.functions, run.args.top)],
        "duplicate_blocks": duplicate_payload(run),
        "scb_check": run.scb,
    }


def main(argv: list[str] | None = None) -> int:
    """Measure one request and write its report."""
    try:
        args, target = parse_request(argv)
        if args.print_requirements:
            print(" ".join(required_grammars(args, target)))
            return 0
        run = run_measurement(target, args)
    except InputError as exc:
        print("\n".join(exc.lines()), file=sys.stderr)
        return 2

    payload = build_payload(run)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print_report(payload)
    erosion = run.signals.erosion
    return 1 if (erosion is not None and erosion > BANDS["erosion"]["agent"]) else 0


def print_metric_row(label: str, value: float | None, band_name: str = "", detail: str = "",
                     unavailable: str = "") -> None:
    """One metric, its band, and its multiple of human. Or why it is not there."""
    if value is None:
        print(f"  {label:<12}n/a{unavailable}")
        return
    print(f"  {label:<12}{value:.3f}   {band_name}   ({detail})")


def print_metric_detail(text: str) -> None:
    """The indented line under a metric that carries its detail."""
    print(f"              {text}")


def print_inputs(p: dict) -> None:
    """The header: what was measured, and how many lines it came to."""
    languages = ", ".join(
        f"{name} {counts['files']}f/{counts['sloc']}sloc"
        for name, counts in p["sloc"]["by_language"].items()
    )
    print(f"  languages      {languages}")
    sloc = p["sloc"]
    if sloc["unmeasured"]:
        print(f"  total SLOC     {sloc['total']}  ({sloc['measured']} measured,"
              f" {sloc['unmeasured']} excluded)")
    else:
        print(f"  total SLOC     {sloc['total']}")


def print_engine_notes(engine: dict) -> None:
    """Everything a reader has to know before trusting a number below."""
    if not engine["tree_sitter"]:
        print("\n  NOTE: Tree-sitter is unavailable, so no metric below was measured.")
        print("        Run through `sloptrack measure`, or the skill's run.sh, which")
        print("        supply the grammars in a throwaway uvx environment.")
    if engine["bundled_files"]:
        shown = ", ".join(engine["bundled_files"][:3])
        extra = len(engine["bundled_files"]) - 3
        more = f" (+{extra} more)" if extra > 0 else ""
        print(f"  excluded {len(engine['bundled_files'])} build artifact(s): {shown}{more}")
        print("           Generators are excluded so they do not dominate erosion.")
        print("           Pass --include-minified to measure them anyway.")
    if engine["long_line_files"]:
        shown = ", ".join(
            f"{d['file']} ({d['share']:.0%})" for d in engine["long_line_files"][:3]
        )
        print(f"  note: {len(engine['long_line_files'])} file(s) have many long lines: {shown}")
        print("        Generated or templated code can inflate erosion. Check before")
        print("        trusting their numbers; --exclude them if they are not yours.")
    if engine["languages_without_grammar"]:
        print(f"  no grammar for {', '.join(engine['languages_without_grammar'])};"
              " excluded from both metrics")
    if engine["unreliable_languages"]:
        print(f"  EXCLUDED {', '.join(engine['unreliable_languages'])}: parsed, but found no"
              " functions.")
        print("           Either the node vocabulary is wrong for that grammar, or the")
        print("           files are not valid in the language their extension claims.")
        print("           Both make for a flattering zero, so neither metric includes them.")
    if engine["parse_failures"]:
        print(f"  parse failures {engine['parse_failures']}")


def print_verbosity(p: dict) -> None:
    v = p["verbosity"]
    print_metric_row(
        "VERBOSITY", v["value"], v["band"], v["vs_human"],
        "   nothing was parsed, so there is no denominator",
    )
    if v["value"] is None:
        return
    print_metric_detail(
        f"clone lines {v['clone_lines']} of {p['sloc']['measured']} SLOC"
        "   [human 0.15 +/- 0.06 | agent 0.33 +/- 0.10]"
    )
    if v.get("ast_grep_lines") is None:
        print("              clone component only. The published metric also counts")
        print("              ast-grep drops, so treat this as a lower bound.")


def print_erosion(p: dict) -> None:
    e = p["erosion"]
    print_metric_row("EROSION", e["value"], e["band"], e["vs_human"], " (no functions parsed)")
    if e["value"] is None:
        return
    print_metric_detail(
        f"{e['high_cc_functions']} of {e['functions']} functions have CC > {HIGH_CC}"
        "   [human 0.31 +/- 0.17 | agent 0.68 +/- 0.20]"
    )
    if e.get("anonymous_skipped"):
        print_metric_detail(
            f"{e['anonymous_skipped']} anonymous callable(s) not counted;"
            " use --functions all to include them"
        )


def print_granularity(p: dict) -> None:
    g = p["granularity"]
    print_metric_row(
        "GRANULARITY", g["value"], g["band"],
        f"human {g['human']:.2f} +/- {g['human_sd']:.2f}",
        " (no callable has a counted reference)",
    )
    if g["value"] is None:
        return
    print_metric_detail(
        f"{g['single_use_functions']} of {g['used_functions']} used callables invoked"
        f" once; {g['reused_functions']} invoked twice or more"
    )
    if g["unused_functions"]:
        print_metric_detail(
            f"{g['unused_functions']} callable(s) have no call site"
            " (entry points, public API, or dead code)"
        )


def print_metrics(p: dict) -> None:
    """The three metrics, in the order the bands are documented."""
    print_verbosity(p)
    print_erosion(p)
    print_granularity(p)


def print_growth(p: dict) -> None:
    """Line growth over the window, and whatever agent trailers were found."""
    git = p["git"]
    print()
    if not git["available"]:
        reason = git["skip_reason"]
        print(f"  GROWTH      skipped ({reason})" if reason
              else "  GROWTH      not a git repository; skipped")
        return
    print(f"  GROWTH      {git['delta_loc']:+d} LOC over '{git['window']}'"
          f"  (+{git['insertions']} / -{git['deletions']} in {git['commits']} commits)")
    if git["agent_commits"]:
        share = git["agent_insertions"] / git["insertions"] if git["insertions"] else 0
        print(f"  AGENT TRAILER  {git['agent_commits']} commits carried an agent trailer,"
              f" +{git['agent_insertions']} lines ({share:.0%} of insertions)")


def print_scb_check(p: dict) -> None:
    """The reference implementation's numbers, when it ran or was asked for."""
    engine = p["engine"]
    report = p["scb_check"]
    if not report:
        if engine.get("scb_check_error"):
            print(f"\n  scb-check   FAILED: {engine['scb_check_error']}")
        elif set(engine["languages_present"]) & SCB_CHECK_SUPPORTED:
            langs = ", ".join(sorted(set(engine["languages_present"]) & SCB_CHECK_SUPPORTED))
            print(f"\n  hint: scb-check covers {langs}. Re-run with --scb for the reference")
            print("        implementation's composites, which include the ast-grep rules.")
        return
    coverage = engine["scb_check_coverage"]
    print()
    if not report.get("total_loc"):
        print("  scb-check   ran, but the repo has no files in a language it can parse.")
        return
    print(f"  scb-check   verbosity {report.get('verbosity', 0):.3f}"
          f"  erosion {report.get('erosion', 0):.3f}"
          f"  cog_erosion {report.get('cog_erosion', 0):.3f}")
    covered = f" ({coverage:.0%} of this repo's SLOC)" if coverage is not None else ""
    print(f"              scanned {report.get('total_loc', 0)} SLOC{covered}")
    if coverage is not None and coverage < 0.6:
        print("              WARNING: scb-check saw under 60% of this repo, so its score")
        print("                       describes the files it can parse, not the codebase.")


def print_hotspots(p: dict) -> None:
    """The heaviest functions, flagged when complexity is over the cutoff."""
    if not p["hotspots"]:
        return
    print(f"\n  WORST FUNCTIONS BY MASS (CC > {HIGH_CC} = eroded)")
    for f in p["hotspots"]:
        flag = "  <-- eroded" if f["cc"] > HIGH_CC else ""
        print(f"    CC {f['cc']:>4}  {f['sloc']:>5} sloc  {f['file']}:{f['line']}"
              f"  {f['name']}{flag}")


def print_duplicates(p: dict) -> None:
    """Cloned blocks, by how many lines compressing them would free."""
    if not p["duplicate_blocks"]:
        return
    print("\n  DUPLICATE BLOCKS (a second instance is the signal to compress)")
    for block in p["duplicate_blocks"]:
        shown = " | ".join(block["locations"])
        extra = block["occurrences"] - len(block["locations"])
        more = f" (+{extra} more)" if extra > 0 else ""
        print(f"    {block['lines']:>4} lines x{block['occurrences']},"
              f" saves {block['recoverable_lines']:>4}  {shown}{more}")


def print_single_use(p: dict) -> None:
    """Callables with one call site, which is where granularity comes from."""
    if not p["granularity"]["single_use_top"]:
        return
    print("\n  SINGLE-USE CALLABLES (invoked once; inline it unless it names a step)")
    for f in p["granularity"]["single_use_top"]:
        print(f"    {f['sloc']:>5} sloc  CC {f['cc']:<3}  {f['file']}:{f['line']}  {f['name']}"
              f"  (uses {f['uses']})")


def print_report(p: dict) -> None:
    """The text report: the same numbers as the JSON, in reading order."""
    print(f"SLOP REPORT  {p['root']}")
    print_inputs(p)
    print_engine_notes(p["engine"])
    print()
    print_metrics(p)
    print_growth(p)
    print_scb_check(p)
    print_hotspots(p)
    print_duplicates(p)
    print_single_use(p)


if __name__ == "__main__":
    sys.exit(main())