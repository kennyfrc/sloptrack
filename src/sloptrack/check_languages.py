#!/usr/bin/env python3
"""Verify every language in the LANGS table against a fixture with a known answer.

This is the test half of the language-extension procedure. The reference
implementation checks each parser config the same way: a small sample must
parse, emit named function symbols, score complexity above the base, and give
the clone detector something to find. A table entry that cannot do this would
otherwise surface in a report as a confident, flattering zero (`EXCLUDED`).

What this checks per language:

  parsed     the grammar parses the fixture with no ERROR node
  functions  at least the fixture's expected number of named callables is found
  cc         the worst function scores at least the fixture's expected CC,
             proving the decision vocabulary catches a branch and a loop
  clones     a duplicated block yields a clone group of at least two members,
             proving the clone vocabulary and identifier normalization work
  literals   the same clone group survives after one copy's numbers change,
             proving the `literals` vocabulary normalizes constants
  name       no function is named with a raw text blob (a fallback `_func_name`)
  calls      a base fixture calls none of its functions, so no declared name may
             have a counted call; and an appended two-call fixture counts the
             called function twice and the other once, proving the granularity
             call vocabulary detects `foo()` in that language

It does not check numbers against scb-check, and it does not prove the metric
is right. It catches vocabulary drift: node names that moved, a grammar that
renamed a node, or a function-name field that is not where the table says.

Run through the wrapper so uvx installs every grammar this file needs:

    sloptrack check-languages [--lang NAME] [--verbose]
    ~/.agents/skills/measure-then-fix-slop/scripts/check_languages.sh [flags]

Exit codes: 0 all pass, 1 at least one language failed, 2 at least one grammar
is not importable (the wrapper could not install it).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

try:
    from . import measure as m
except ImportError:  # run as a plain script, as an installed skill does
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import slop_measure as m
    except ImportError:
        import measure as m  # source checkout, next to the analyzer

# (filename, source, min functions, min CC) per language. Every sample holds a
# branch and a loop inside a duplicated block, so one fixture proves the
# function, decision, and clone vocabularies together.
SAMPLES: dict[str, tuple[str, str, int, int]] = {
    "python": (
        "sample.py",
        dedent(
            """\
            def alpha(values):
                total = 0
                if values:
                    for item in values:
                        total = total + item
                return total


            def beta(rows):
                total = 0
                if rows:
                    for item in rows:
                        total = total + item
                return total
            """
        ),
        2,
        3,
    ),
    "javascript": (
        "sample.js",
        dedent(
            """\
            function alpha(values) {
              let total = 0;
              if (values.length > 0) {
                for (const item of values) {
                  total = total + item;
                }
              }
              return total;
            }

            function beta(rows) {
              let total = 0;
              if (rows.length > 0) {
                for (const item of rows) {
                  total = total + item;
                }
              }
              return total;
            }
            """
        ),
        2,
        3,
    ),
    "typescript": (
        "sample.ts",
        dedent(
            """\
            function alpha(values: number[]): number {
              let total = 0;
              if (values.length > 0) {
                for (const item of values) {
                  total = total + item;
                }
              }
              return total;
            }

            function beta(rows: number[]): number {
              let total = 0;
              if (rows.length > 0) {
                for (const item of rows) {
                  total = total + item;
                }
              }
              return total;
            }
            """
        ),
        2,
        3,
    ),
    "ruby": (
        "sample.rb",
        dedent(
            """\
            def alpha(values)
              total = 0
              if values
                index = 0
                while index < 3
                  total = total + index
                  index = index + 1
                end
              end
              total
            end

            def beta(rows)
              total = 0
              if rows
                index = 0
                while index < 3
                  total = total + index
                  index = index + 1
                end
              end
              total
            end
            """
        ),
        2,
        3,
    ),
    "go": (
        "sample.go",
        dedent(
            """\
            package sample

            func alpha(values []int) int {
                total := 0
                if len(values) > 0 {
                    for _, item := range values {
                        total = total + item
                    }
                }
                return total
            }

            func beta(rows []int) int {
                total := 0
                if len(rows) > 0 {
                    for _, item := range rows {
                        total = total + item
                    }
                }
                return total
            }
            """
        ),
        2,
        3,
    ),
    "rust": (
        "sample.rs",
        dedent(
            """\
            fn alpha(values: &[i32]) -> i32 {
                let mut total = 0;
                if !values.is_empty() {
                    for item in values {
                        total = total + item;
                    }
                }
                total
            }

            fn beta(rows: &[i32]) -> i32 {
                let mut total = 0;
                if !rows.is_empty() {
                    for item in rows {
                        total = total + item;
                    }
                }
                total
            }
            """
        ),
        2,
        3,
    ),
    "java": (
        "Sample.java",
        dedent(
            """\
            class Sample {
              static int alpha(int[] values) {
                int total = 0;
                if (values.length > 0) {
                  for (int item : values) {
                    total = total + item;
                  }
                }
                return total;
              }

              static int beta(int[] rows) {
                int total = 0;
                if (rows.length > 0) {
                  for (int item : rows) {
                    total = total + item;
                  }
                }
                return total;
              }
            }
            """
        ),
        2,
        3,
    ),
    "c": (
        "sample.c",
        dedent(
            """\
            int alpha(int values[], int size) {
              int total = 0;
              if (size > 0) {
                for (int i = 0; i < size; i++) {
                  total = total + values[i];
                }
              }
              return total;
            }

            int beta(int rows[], int size) {
              int total = 0;
              if (size > 0) {
                for (int i = 0; i < size; i++) {
                  total = total + rows[i];
                }
              }
              return total;
            }
            """
        ),
        2,
        3,
    ),
    "cpp": (
        "sample.cpp",
        dedent(
            """\
            int alpha(int values[], int size) {
              int total = 0;
              if (size > 0) {
                for (int i = 0; i < size; i++) {
                  total = total + values[i];
                }
              }
              return total;
            }

            int beta(int rows[], int size) {
              int total = 0;
              if (size > 0) {
                for (int i = 0; i < size; i++) {
                  total = total + rows[i];
                }
              }
              return total;
            }
            """
        ),
        2,
        3,
    ),
    "csharp": (
        "Sample.cs",
        dedent(
            """\
            class Sample {
              static int Alpha(int[] values) {
                int total = 0;
                if (values.Length > 0) {
                  foreach (int item in values) {
                    total = total + item;
                  }
                }
                return total;
              }

              static int Beta(int[] rows) {
                int total = 0;
                if (rows.Length > 0) {
                  foreach (int item in rows) {
                    total = total + item;
                  }
                }
                return total;
              }
            }
            """
        ),
        2,
        3,
    ),
    "php": (
        "sample.php",
        dedent(
            """\
            <?php
            function alpha($values) {
                $total = 0;
                if (count($values) > 0) {
                    foreach ($values as $item) {
                        $total = $total + $item;
                    }
                }
                return $total;
            }

            function beta($rows) {
                $total = 0;
                if (count($rows) > 0) {
                    foreach ($rows as $item) {
                        $total = $total + $item;
                    }
                }
                return $total;
            }
            """
        ),
        2,
        3,
    ),
    "kotlin": (
        "sample.kt",
        dedent(
            """\
            fun alpha(values: List<Int>): Int {
                var total = 0
                if (values.isNotEmpty()) {
                    for (item in values) {
                        total = total + item
                    }
                }
                return total
            }

            fun beta(rows: List<Int>): Int {
                var total = 0
                if (rows.isNotEmpty()) {
                    for (item in rows) {
                        total = total + item
                    }
                }
                return total
            }
            """
        ),
        2,
        3,
    ),
    "swift": (
        "sample.swift",
        dedent(
            """\
            func alpha(values: [Int]) -> Int {
                var total = 0
                if !values.isEmpty {
                    for item in values {
                        total = total + item
                    }
                }
                return total
            }

            func beta(rows: [Int]) -> Int {
                var total = 0
                if !rows.isEmpty {
                    for item in rows {
                        total = total + item
                    }
                }
                return total
            }
            """
        ),
        2,
        3,
    ),
    "scala": (
        "sample.scala",
        dedent(
            """\
            object Sample {
              def alpha(values: List[Int]): Int = {
                var total = 0
                if (values.nonEmpty) {
                  var index = 0
                  while (index < 3) {
                    total = total + index
                    index = index + 1
                  }
                }
                total
              }

              def beta(rows: List[Int]): Int = {
                var total = 0
                if (rows.nonEmpty) {
                  var index = 0
                  while (index < 3) {
                    total = total + index
                    index = index + 1
                  }
                }
                total
              }
            }
            """
        ),
        2,
        3,
    ),
    "lua": (
        "sample.lua",
        dedent(
            """\
            function alpha(values)
              local total = 0
              if values then
                for i = 1, 3 do
                  total = total + i
                end
              end
              return total
            end

            function beta(rows)
              local total = 0
              if rows then
                for i = 1, 3 do
                  total = total + i
                end
              end
              return total
            end
            """
        ),
        2,
        3,
    ),
    "haskell": (
        "Sample.hs",
        dedent(
            """\
            module Sample where

            alpha values =
              if null values then
                0
              else
                case values of
                  [] -> 0
                  (item:_) -> item

            beta rows =
              if null rows then
                0
              else
                case rows of
                  [] -> 0
                  (item:_) -> item
            """
        ),
        2,
        3,
    ),
    "zig": (
        "sample.zig",
        dedent(
            """\
            fn alpha(values: []const i32) i32 {
                var total: i32 = 0;
                if (values.len > 0) {
                    for (values) |item| {
                        total = total + item;
                    }
                }
                return total;
            }

            fn beta(rows: []const i32) i32 {
                var total: i32 = 0;
                if (rows.len > 0) {
                    for (rows) |item| {
                        total = total + item;
                    }
                }
                return total;
            }
            """
        ),
        2,
        3,
    ),
    "bash": (
        "sample.sh",
        dedent(
            """\
            log() {
              echo "[log] $*"
            }

            alpha() {
              local total=0
              if [ -n "$1" ]; then
                for item in "$@"; do
                  total=$((total + 1))
                done
              fi
              echo "$total"
            }

            beta() {
              local total=0
              if [ -n "$1" ]; then
                for item in "$@"; do
                  total=$((total + 1))
                done
              fi
              echo "$total"
            }
            """
        ),
        3,
        3,
    ),
}


# Positively verify reference counting for the granularity metric. Each entry
# appends two calls to `alpha` and `beta` to that language's fixture (alpha
# once, beta twice), then checks the counts. A language absent here still gets
# the declaration-exclusion check, which runs on every fixture.
#
# alpha uses == 1 also proves the declaration name was excluded: if it counted,
# the value would be 2. beta uses >= 2 proves a real reference is counted.
USE_CALLS: dict[str, str] = {
    "python": "\nalpha([])\nbeta([])\nbeta([])\n",
    "javascript": "\nalpha([]);\nbeta([]);\nbeta([]);\n",
    "typescript": "\nalpha([]);\nbeta([]);\nbeta([]);\n",
    "ruby": "\nalpha([])\nbeta([])\nbeta([])\n",
    "go": "\nvar _ = []int{alpha(nil), beta(nil), beta(nil)}\n",
    "rust": "\nfn uses() { alpha(&[]); beta(&[]); beta(&[]); }\n",
    "java": "\nclass Uses { static void run() { Sample.alpha(new int[0]); Sample.beta(new int[0]); Sample.beta(new int[0]); } }\n",
    "c": "\nint uses(void) { alpha(0, 0); beta(0, 0); beta(0, 0); return 0; }\n",
    "cpp": "\nint uses() { alpha(nullptr, 0); beta(nullptr, 0); beta(nullptr, 0); return 0; }\n",
    "csharp": "\nclass Uses { static void Run() { Sample.Alpha(new int[0]); Sample.Beta(new int[0]); Sample.Beta(new int[0]); } }\n",
    "php": "\nalpha([]);\nbeta([]);\nbeta([]);\n",
    "kotlin": "\nval useA = alpha(listOf(1))\nval useB = beta(listOf(1))\nval useB2 = beta(listOf(2))\n",
    "swift": "\n_ = alpha([0])\n_ = beta([0])\n_ = beta([0])\n",
    "scala": "\nval useA = alpha(Nil)\nval useB = beta(Nil)\nval useB2 = beta(Nil)\n",
    "lua": "\nalpha({})\nbeta({})\nbeta({})\n",
    "haskell": "\nuseA = alpha []\nuseB = beta []\nuseB2 = beta []\n",
    "zig": "\nconst useA = alpha(&[_]i32{});\nconst useB = beta(&[_]i32{});\nconst useB2 = beta(&[_]i32{});\n",
    "bash": "\nalpha\nbeta\nbeta\n",
}

# Languages whose fixture names its functions Alpha/Beta rather than alpha/beta.
USE_CALL_NAMES: dict[str, tuple[str, str]] = {"csharp": ("Alpha", "Beta")}


# A clone group this long can only be the function body itself, not a nested
# block. Requiring it proves function-level clones fire, not just sub-blocks.
MIN_FUNCTION_CLONE_SPAN = 6

# What the re-analysis after changing one copy's numbers managed to do.
LITERAL_OK = "ok"
LITERAL_NONE = "no-literals"
LITERAL_ERROR = "error"


def clone_groups(analysis: m.FileAnalysis) -> list[list[tuple[int, int]]]:
    """Clone groups the report would count, which need two members of clone length."""
    groups: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for digest, start, end in analysis.clone_candidates:
        groups[digest].append((start, end))
    return [
        members
        for members in groups.values()
        if len(members) >= 2 and members[0][1] - members[0][0] + 1 >= m.MIN_CLONE_LINES
    ]


def largest_clone_span(analysis: m.FileAnalysis) -> int:
    """Longest span among clone groups with at least two members."""
    spans = (members[0][1] - members[0][0] + 1 for members in clone_groups(analysis))
    return max(spans, default=0)


def shortfall(label: str, got: int, want: int, why: str) -> str | None:
    """A finding when a fixture measured less than it should, else None.

    Returning None rather than a bool is what lets the findings be a list of calls
    instead of a ladder of append-after-if.
    """
    if got >= want:
        return None
    return f"{label} {got}, expected >= {want} ({why})"


def unexpected(condition: bool, message: str) -> str | None:
    """A finding when something that should never happen did."""
    return message if condition else None


def analyze_variant(parser, cfg: m.Lang, text: str, filename: str) -> m.FileAnalysis:
    """Analyze one fixture variant. Raises whatever the grammar or vocabulary raises."""
    entry = m.FileEntry(path=Path(filename), rel=filename, lang=cfg, text=text)
    return m.analyze_file(entry, parser, named_only=True)


def mutate_first_copy(source: str) -> str:
    """Change every number in the first half of the fixture.

    The samples are two copies of the same function. Changing the numbers in
    one copy only leaves two bodies that differ in nothing but literal values,
    which the clone detector should still match. That is the literal half of
    'identifiers and literals are normalized away'; a table whose `literals`
    node types are wrong fails here while still passing the name-based check.
    """
    lines = source.splitlines(keepends=True)
    half = len(lines) // 2
    number = re.compile(r"(?<![A-Za-z0-9_.])(\d+)(?![A-Za-z0-9_.])")

    def bump(match: re.Match[str]) -> str:
        return str(int(match.group(1)) + 7)

    return "".join(number.sub(bump, line) for line in lines[:half]) + "".join(lines[half:])


class FixtureError(RuntimeError):
    """A fixture could not be analyzed at all."""


@dataclass(frozen=True)
class Fixture:
    """What one language's fixtures measured, before anyone judges them."""

    name: str
    cfg: m.Lang
    filename: str
    base: m.FileAnalysis
    min_functions: int
    min_cc: int
    mutated_span: int = 0
    literal_status: str = LITERAL_NONE
    literal_detail: str = ""
    call_note: str = ""
    call_findings: tuple[str, ...] = ()


def fixture_parser(name: str, cfg: m.Lang, grammars: dict[str, object]) -> tuple[object | None, str]:
    """A parser for this fixture, or the reason there cannot be one."""
    ts_lang = grammars.get(name)
    if ts_lang is None:
        package = (cfg.grammar or "?").replace("_", "-")
        return None, f"MISSING GRAMMAR {cfg.grammar} (pip package: {package})"
    if cfg.functions == frozenset():
        return None, "no function node types declared in the LANGS table"
    try:
        import tree_sitter

        return tree_sitter.Parser(ts_lang), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"parser could not start: {exc}"


def syntax_error(parser, source: str) -> bool:
    """True when the grammar reports an ERROR node for this fixture."""
    return bool(parser.parse(source.encode("utf-8")).root_node.has_error)


def _literal_check(parser, cfg: m.Lang, filename: str, source: str) -> tuple[int, str, str]:
    """Re-analyze with the first copy's numbers changed. Returns (span, status, detail)."""
    mutated = mutate_first_copy(source)
    if mutated == source:
        return 0, LITERAL_NONE, ""
    try:
        span = largest_clone_span(analyze_variant(parser, cfg, mutated, filename))
    except Exception as exc:  # noqa: BLE001
        return -1, LITERAL_ERROR, f"re-analyze after literal change raised {type(exc).__name__}: {exc}"
    return span, LITERAL_OK, ""


def _call_check(
    name: str, cfg: m.Lang, parser, source: str, filename: str
) -> tuple[str, tuple[str, ...]]:
    """Count uses on a fixture with known call sites. Returns (note, findings)."""
    calls = USE_CALLS.get(name)
    if not calls:
        return "", ()
    alpha, beta = USE_CALL_NAMES.get(name, ("alpha", "beta"))
    try:
        references = analyze_variant(parser, cfg, source + calls, filename).references
    except Exception as exc:  # noqa: BLE001
        return "", (f"use fixture raised {type(exc).__name__}: {exc}",)
    alpha_uses, beta_uses = references.get(alpha, 0), references.get(beta, 0)
    findings = [
        finding
        for finding in (
            unexpected(alpha_uses != 1, f"call counting: expected {alpha} called once, got {alpha_uses}"),
            unexpected(beta_uses < 2, f"call counting: expected {beta} called twice or more, got {beta_uses}"),
        )
        if finding is not None
    ]
    return f"  uses {alpha} {alpha_uses}/{beta} {beta_uses}", tuple(findings)


def run_fixture(name: str, cfg: m.Lang, parser) -> Fixture:
    """Analyze every variant of one fixture and record what each measured."""
    filename, source, min_functions, min_cc = SAMPLES[name]
    try:
        analysis = analyze_variant(parser, cfg, source, filename)
    except Exception as exc:  # noqa: BLE001
        raise FixtureError(f"analyze_file raised {type(exc).__name__}: {exc}") from exc
    span, status, detail = _literal_check(parser, cfg, filename, source)
    note, call_findings = _call_check(name, cfg, parser, source, filename)
    return Fixture(
        name=name, cfg=cfg, filename=filename, base=analysis,
        min_functions=min_functions, min_cc=min_cc,
        mutated_span=span, literal_status=status, literal_detail=detail,
        call_note=note, call_findings=call_findings,
    )


def worst_cc(fixture: Fixture) -> int:
    """The highest cyclomatic complexity any function in the fixture reached."""
    return max((f.cc for f in fixture.base.functions), default=0)


def declaration_calls(analysis: m.FileAnalysis) -> dict[str, int]:
    """Counts attributed to a declaration's own name, which are not uses.

    The base fixture calls none of its functions, so any count here means a
    declaration or a shadow was read as a use.
    """
    return {
        f.name: analysis.references.get(f.name, 0)
        for f in analysis.functions
        if analysis.references.get(f.name, 0)
    }


def blob_name(functions: list[m.Func]) -> str:
    """The first function whose name looks like a raw text blob, or ""."""
    for f in functions:
        if "{" in f.name or "\n" in f.name or len(f.name) > 40:
            return f.name[:40]
    return ""


def fixture_findings(fixture: Fixture) -> list[str]:
    """Every reason this fixture failed, in a fixed order."""
    functions = fixture.base.functions
    block = fixture.base
    blob = blob_name(functions)
    miscounted = declaration_calls(block)
    candidates = [
        unexpected(bool(fixture.literal_detail), fixture.literal_detail),
        unexpected(
            len(functions) < fixture.min_functions,
            f"found {len(functions)} named function(s), expected >= {fixture.min_functions}"
            f" ({block.anonymous} anonymous)",
        ),
        shortfall("max CC", worst_cc(fixture), fixture.min_cc, "decision vocabulary missed a branch"),
        shortfall(
            "clone group(s)", len(clone_groups(block)), 1,
            "no clone group fired on the duplicated block",
        ),
        shortfall(
            "largest clone span", largest_clone_span(block), MIN_FUNCTION_CLONE_SPAN,
            "function-level clones did not fire",
        ),
        unexpected(
            fixture.literal_status == LITERAL_NONE,
            "no numeric literal in the first copy; the literal check could not run",
        ),
        unexpected(
            fixture.literal_status == LITERAL_OK and fixture.mutated_span < MIN_FUNCTION_CLONE_SPAN,
            f"after changing one copy's literals, largest clone span {fixture.mutated_span}"
            f" (expected >= {MIN_FUNCTION_CLONE_SPAN}); literals are not normalized",
        ),
        unexpected(bool(blob), f"function name looks like a text blob: {blob!r}"),
        unexpected(bool(miscounted), f"declaration name(s) counted as calls: {miscounted}"),
        *fixture.call_findings,
    ]
    return [finding for finding in candidates if finding is not None]


def fixture_detail(fixture: Fixture) -> str:
    """The one-line scorecard for a fixture, whether it passed or not."""
    return (
        f"functions {len(fixture.base.functions)}  max CC {worst_cc(fixture)}"
        f"  clones {len(clone_groups(fixture.base))}  span {largest_clone_span(fixture.base)}"
        f"  lit-span {fixture.mutated_span}  sloc {fixture.base.sloc}{fixture.call_note}"
    )


def check_language(
    name: str, cfg: m.Lang, grammars: dict[str, object]
) -> tuple[bool, str, m.FileAnalysis | None]:
    """Run one fixture. Returns (passed, detail, analysis)."""
    parser, problem = fixture_parser(name, cfg, grammars)
    if parser is None:
        return False, problem, None
    if syntax_error(parser, SAMPLES[name][1]):
        return False, "fixture does not parse: grammar reported an ERROR node", None
    try:
        fixture = run_fixture(name, cfg, parser)
    except FixtureError as exc:
        return False, str(exc), None
    findings = fixture_findings(fixture)
    detail = fixture_detail(fixture)
    if findings:
        return False, detail + "  |  " + "; ".join(findings), fixture.base
    return True, detail, fixture.base


PASS, FAIL, MISS = "PASS ", "FAIL ", "MISS "

USAGE_HINT = "  run through `sloptrack check-languages` so uvx installs the grammars"
FINAL_NOTE = "  every table entry yields functions, complexity, clones, and call counts on its fixture"


def select_languages(wanted: list[str]) -> dict[str, m.Lang]:
    """Map fixture names to language configs, dropping names with no fixture."""
    by_name = {lang.name: lang for lang in m.LANGS}
    return {name: by_name[name] for name in wanted if name in by_name}


def selection_problems(wanted: list[str]) -> list[str]:
    """The error lines that stop a selection, one line per problem."""
    table = {lang.name for lang in m.LANGS}
    unfixtured = [name for name in wanted if name not in SAMPLES]
    untabled = [name for name in wanted if name in SAMPLES and name not in table]
    problems = []
    if unfixtured:
        problems.append(
            f"error: no fixture for {', '.join(unfixtured)}\n"
            f"       add one to SAMPLES in {Path(__file__).name}"
        )
    if untabled:
        problems.append(f"error: {', '.join(untabled)} not in the LANGS table")
    return problems


def report_unverified(names: list[str]) -> int:
    """Print one line per table entry with no fixture. Returns how many there were."""
    for name in names:
        print(f"  {FAIL} {name:<11} in LANGS but has no fixture in SAMPLES")
    if names:
        print(f"  {len(names)} table entr(y/ies) are unverified; add a sample before trusting them")
    return len(names)


def report_fixture(name: str, cfg: m.Lang, grammars: dict[str, object], verbose: bool) -> str:
    """Print one fixture's scorecard. Returns its mark."""
    passed, detail, analysis = check_language(name, cfg, grammars)
    if detail.startswith("MISSING"):
        mark = MISS
    else:
        mark = PASS if passed else FAIL
    print(f"  {mark} {name:<11} {detail}")
    if verbose and analysis is not None:
        for f in analysis.functions:
            print(f"         {f.name:<24} CC {f.cc:<4} SLOC {f.sloc}")
    return mark


def unfixtured_languages() -> list[str]:
    """Table entries with no fixture, which no run can verify."""
    return [lang.name for lang in m.LANGS if lang.name not in SAMPLES]


def print_requirements(wanted: list[str]) -> int:
    """Print the grammar packages for these languages, without importing any."""
    known = {lang.name for lang in m.LANGS}
    print(" ".join(m.grammar_packages({name for name in wanted if name in known})))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify language vocabularies on known-answer fixtures.")
    ap.add_argument("--lang", action="append", default=[], help="check only this language (repeatable)")
    ap.add_argument("--verbose", action="store_true", help="list every function found per language")
    ap.add_argument(
        "--print-requirements", action="store_true",
        help="print the grammar packages the requested languages need, then exit",
    )
    args = ap.parse_args(argv)

    wanted = args.lang or list(SAMPLES)
    if args.print_requirements:
        return print_requirements(wanted)
    problems = selection_problems(wanted)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2

    grammars = m.load_grammars(set(wanted))
    configs = select_languages(wanted)
    print("LANGUAGE CHECK  fixtures with known answers")
    marks = [report_fixture(name, configs[name], grammars, args.verbose) for name in wanted]
    failures = marks.count(FAIL)
    missing = marks.count(MISS)
    print(f"\n  {len(wanted) - failures - missing} passed, {failures} failed,"
          f" {missing} missing grammar(s)")

    unverified = 0 if args.lang else report_unverified(unfixtured_languages())
    if missing:
        print(USAGE_HINT)
        return 2
    if failures or unverified:
        return 1
    print(FINAL_NOTE)
    return 0


if __name__ == "__main__":
    sys.exit(main())