"""A clang front end for C and C++, because a text grammar cannot parse them.

tree-sitter-c reads source text as tokens. A C compiler reads a preprocessed
translation unit, and the difference is not a bug in the grammar. Three things
follow that the text grammar refuses, all of them ordinary code:

    ST_DATA int rsym;                                     a macro in a declaration
    static const int t[] = { #if A 1, #else 2, #endif };  a conditional inside brackets
    goto *dispatch_table[opcode = *pc++];                 a GNU computed goto

Upstream will not close the gap: issue #265 (macro prefix) was closed as not
planned, PR #325 (conditional in an initializer) is unmerged, and computed goto
has no report. So for c and cpp this module runs the real front end. A file clang
rejects is refused, never measured, so the rule that a confident wrong number is
worse than an admitted gap still holds.

The AST is read as text rather than JSON. `-ast-dump=json` on quickjs.c is 506 MB
and takes seconds to materialize; the text form is 31 MB, arrives in half a
second, and streams line by line. Nodes from included headers are dropped as they
pass, so only the target file's own code is measured.

Metric definitions mirror the tree-sitter path, with one documented difference in
the structural hash. Both drop names and literals. The tree-sitter hash also
keeps punctuation tokens from the source; this one keeps node kinds and operators
and has nothing else to keep, because clang does not print operator tokens as
nodes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

try:  # the tool also runs as a script under uvx, where measure.py is a sibling
    from .measure import (
        FileAnalysis, FileEntry, Func, _is_sloc as is_sloc, sloc_stats, unparsed,
    )
except ImportError:  # pragma: no cover - installed layout, exercised by tests
    import sys
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parent))
    try:
        from slop_measure import (  # type: ignore[no-redef]
            FileAnalysis, FileEntry, Func, _is_sloc as is_sloc, sloc_stats, unparsed,
        )
    except ImportError:
        from measure import (  # type: ignore[no-redef]
            FileAnalysis, FileEntry, Func, _is_sloc as is_sloc, sloc_stats, unparsed,
        )

# Languages this engine takes over when a front end is available. Everything else
# stays on the tree-sitter path.
ENGINE_LANGS = frozenset({"c", "cpp"})

CLANG_ENV = "SLOPTRACK_CLANG"
CFLAGS_ENV = "SLOPTRACK_CFLAGS"
DUMP_TIMEOUT = 300

# One declaration's worth of kinds that carry a body and therefore count as a unit.
FUNCTION_KINDS = frozenset(
    {
        "FunctionDecl", "CXXMethodDecl", "CXXConstructorDecl", "CXXDestructorDecl",
        "CXXConversionDecl", "ObjCMethodDecl",
    }
)
# Mirrors the c/cpp table in measure.LANGS: if/for/while/do/case/conditional, and
# binary operators holding && or ||. Switch itself is not an increment, and
# neither is default, because tree-sitter's case_statement already covers both
# spellings of a labeled branch.
DECISION_KINDS = frozenset(
    {"IfStmt", "ForStmt", "WhileStmt", "DoStmt", "CaseStmt", "DefaultStmt",
     "ConditionalOperator"}
)
BOOL_OPERATOR = re.compile(r"'(&&|\|\|)'\s*$")
ANONYMOUS_KINDS = frozenset({"LambdaExpr"})
# Kinds whose first child is a label rather than a body: `case 3:` and `default:`.
LABEL_KINDS = frozenset({"CaseStmt", "DefaultStmt"})
LITERAL_KINDS = frozenset(
    {"IntegerLiteral", "FloatingLiteral", "StringLiteral", "CharacterLiteral",
     "CXXBoolLiteralExpr", "GNUNullExpr", "ImplicitValueInitExpr"}
)
IDENTIFIER_KINDS = frozenset({"DeclRefExpr", "MemberExpr", "LabelRef"})

CONNECTOR = re.compile(r"^(?P<pad>[| `+\-]*)(?:\|-|`-)\s?(?P<body>.*)$")
INVALID_RANGE = re.compile(r"<{1,2}(?:invalid sloc|built-in|scratch space|command line)[^<>]*>{1,2}")
LOC_GROUP = re.compile(r"<([^<>]*)>")
QUOTED = re.compile(r"'[^']*'")
LOC_TOKEN = re.compile(r"\b(?:line|col):\d+(?::\d+)?|[\w.\-+/]+:\d+:\d+")
# A declaration's name is the identifier before the quoted type. The lookbehind
# keeps a hex address (`0x1 'int'`) from reporting `x1` as a name.
DECL_NAME = re.compile(r"(?<![\w:])([A-Za-z_~][\w:~]*)\s+'[^']*'")
NOT_A_NAME = frozenset(
    {"static", "inline", "extern", "used", "implicit", "virtual", "constexpr",
     "externally", "referenced", "unused", "weak", "dllimport", "dllexport"}
)


def find_clang() -> str | None:
    """The clang to run: the override, then clang, then the platform cc."""
    for candidate in (os.environ.get(CLANG_ENV), "clang", "cc"):
        if not candidate:
            continue
        found = shutil.which(candidate)
        if found:
            return found
    return None


def available(clang: str | None = None) -> bool:
    return (clang or find_clang()) is not None


def version(clang: str) -> str:
    """clang's own version line, so a report can name the front end it used."""
    try:
        proc = subprocess.run([clang, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.splitlines()[0].strip() if proc.stdout.strip() else ""


# --------------------------------------------------------------------------
# Compile flags
# --------------------------------------------------------------------------


def flags_for(path: Path, root: Path) -> list[str]:
    """Best-effort flags for one file: its own directory, the repo, and any build.

    C has no useful parse without a compilable context, so this is where the
    engine is fragile. A `compile_commands.json` is the good case and is picked up
    automatically. Otherwise the file's directory, the repo root, and the usual
    include directories are offered; then a `.sloptrack-cflags` file in the repo,
    which holds one argument per line so a macro like `-DCONFIG_VERSION="1.2"` needs
    no shell quoting; then `SLOPTRACK_CFLAGS`. A file that still fails is refused
    with clang's own error message.
    """
    flags: list[str] = []
    for directory in (path.parent, root):
        if directory.is_dir():
            flags.append(f"-I{directory}")
    for name in ("include", "src", "lib", "deps", "vendor"):
        directory = root / name
        if directory.is_dir():
            flags.append(f"-I{directory}")
    flags.extend(_compile_commands(path, root))
    flags.extend(_cflags_file(root))
    flags.extend(shlex.split(os.environ.get(CFLAGS_ENV, "")))
    return flags


def _cflags_file(root: Path) -> list[str]:
    """Arguments from `.sloptrack-cflags`, one per line, `#` for comments."""
    listing = root / ".sloptrack-cflags"
    if not listing.is_file():
        return []
    try:
        lines = listing.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _compile_commands(path: Path, root: Path) -> list[str]:
    """The build flags a compile database already knows for this file."""
    for candidate in (
        root / "compile_commands.json",
        root / "build" / "compile_commands.json",
        root / "out" / "compile_commands.json",
    ):
        if not candidate.is_file():
            continue
        try:
            entries = json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        for item in entries:
            target = str(item.get("file") or "")
            if not target or Path(target).name != path.name:
                continue
            words = item.get("arguments")
            if not isinstance(words, list):
                words = shlex.split(str(item.get("command") or ""))
            return _flag_words([str(word) for word in words])
    return []


def _flag_words(words: list[str]) -> list[str]:
    """Keep the include, define, and language flags; drop the rest.

    Compiling is how clang gets a context, so `-I`, `-D`, `-U`, `-std=`, and the
    include-path forms survive while `-o`, `-c`, and the source file do not.
    """
    kept: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        pair = word in ("-isystem", "-include", "-idirafter", "-include-pch")
        joined = word in ("-I", "-D", "-U")
        if (pair or joined) and index + 1 < len(words):
            value = words[index + 1]
            kept.append(f"{word}{value}" if joined else f"{word} {value}")
            index += 2
            continue
        if word.startswith(("-I", "-D", "-U", "-std=")):
            kept.append(word)
        index += 1
    return kept


# --------------------------------------------------------------------------
# The dump
# --------------------------------------------------------------------------


@dataclass
class Node:
    """One line of the AST dump, with the children indentation gives it."""

    kind: str
    text: str
    file: str
    line: int
    end_line: int
    children: list["Node"] = field(default_factory=list)
    # False for a node clang expanded from an included header. Such a node stays
    # in the tree so its measured-file descendants keep their place, but it
    # contributes no kind, no span, and no child count of its own: tree-sitter
    # would not have parsed it at all.
    same_file: bool = True
    # Set on `case`/`default` nodes by align_cases: how many children tree-sitter's
    # `case_statement` would have, which is the label plus its statements. Zero
    # means "work it out from the children", the rule every other kind uses.
    label_children: int = 0

    def walk(self):
        """Pre-order traversal of the measured file's own nodes.

        Nodes expanded from an included header are passed through rather than
        yielded: they are not this file's code, but their children may be, and
        dropping the subtree would lose them.
        """
        stack = [self]
        while stack:
            node = stack.pop()
            if node.same_file:
                yield node
            stack.extend(reversed(node.children))

    def own_children(self) -> list["Node"]:
        """Children that belong to the measured file, in source order."""
        return [child for child in self.children if child.same_file]

    def find(self, kinds: frozenset[str]) -> "Node | None":
        for node in self.walk():
            if node.kind in kinds:
                return node
        return None

    def children_count(self) -> int:
        """Analogue of tree-sitter's named children, which the clone rule counts.

        Three details carry the tree-sitter rule over to this tree, and the clone
        component of verbosity moves with them:

        - tree-sitter counts non-punctuation children, and clang's dump has no
          punctuation nodes, so every child counts.
        - The count looks through a body wrapper, which for C is `CompoundStmt`.
          Without that, an `if` holding one statement counts its braces instead of
          its contents and drops out as a candidate.
        - A `case` label is not a body. tree-sitter's `case_statement` holds the
          label, so on its own it counts one child and fails the "at least two"
          test. clang's `CaseStmt` holds the label and a substatement, so the label
          comes off here to keep the two engines on one definition. Otherwise a
          switch-heavy file looks far more duplicated under clang than under the
          rule the human and agent bands were built from.
        """
        children = self.own_children()
        if self.kind in LABEL_KINDS:
            return self.label_children
        for _ in range(4):
            inner = next((child for child in children if child.kind == "CompoundStmt"), None)
            if inner is None:
                break
            children = list(inner.children)
        return len(children)


def align_cases(root: Node) -> None:
    """Give `case` and `default` the span and child count tree-sitter's grammar gives.

    clang nests a fall-through chain inside its first label and leaves the
    statements after the chain in the switch body. tree-sitter's `case_statement`
    holds its own statements, and a label whose body is another label spans the one
    row it sits on. The clone rule reads both the child count and the row span, so
    without this adjustment a switch-heavy file reads as heavily duplicated under
    clang and as clean under the rule the human and agent bands were built from:
    on quickjs's cutils.c the raw clang shape produced 84 clone lines where the
    calibrated rule produces none.
    """
    for parent in root.walk():
        children = parent.own_children()
        for index, node in enumerate(children):
            if node.kind not in LABEL_KINDS:
                continue
            chain = [node]
            while (inner := chain[-1].own_children()) and inner[-1].kind in LABEL_KINDS:
                chain.append(inner[-1])
            for label in chain[:-1]:
                label.end_line = label.line
                label.label_children = 1
            tail = chain[-1]
            following = index + 1
            while following < len(children) and children[following].kind not in LABEL_KINDS:
                tail.end_line = max(tail.end_line, children[following].end_line)
                following += 1
            # What tree-sitter would call this node's children: the label, whatever
            # clang kept inside it, and the statements left in the switch body.
            equivalent = tail.own_children() + children[index + 1:following]
            for _ in range(4):
                inner = next((c for c in equivalent if c.kind == "CompoundStmt"), None)
                if inner is None:
                    break
                equivalent = list(inner.children)
            tail.label_children = len(equivalent)


def parse_dump(lines: list[str], wanted: str) -> Node:
    """Build the tree for one file, keeping header nodes only as scaffolding.

    clang dumps the whole translation unit, headers included, so attribution has
    to follow clang's own rule: a `file:N:C` sets the file, `line:N:C` keeps it,
    and a bare `col:N` keeps both. That state advances in token order, which is
    why it is replayed for every node, header nodes included.

    A node from a header is marked `same_file=False` rather than dropped. It
    takes no part in any metric, but it stays in the tree so that a descendant
    which does belong to this file keeps its place. Dropping the subtree instead
    lost real code: in tcc's boundtest.c a whole `printf` argument built from
    Apple's string macros never reached the tree, and nginx's radix-tree loops
    vanished the same way.

    A node's own source range is the first angle-bracket group on its line. Later
    location tokens sit outside it and give the name's position, not the end:
    `FunctionDecl ... <line:261:1, line:314:1> line:261:5 name 'int (...)'`.
    Reading those as the end would make every function one line long.
    """
    root = Node("TranslationUnit", "", wanted, 1, 1, [])
    stack: list[Node] = [root]
    current_file = wanted
    current_line = 1

    for raw in lines:
        match = CONNECTOR.match(raw)
        if not match:
            continue
        depth = len(match.group("pad")) // 2 + 1
        body = INVALID_RANGE.sub("", match.group("body"))
        kind = body.split(" ", 1)[0]

        # Quoted strings hold names and types, which are not locations and can
        # contain location-shaped text, so they come out before scanning.
        scrubbed = QUOTED.sub("''", body)
        groups = LOC_GROUP.findall(scrubbed)
        range_tokens = LOC_TOKEN.findall(groups[0]) if groups else []

        # This runs for every line, including lines inside a skipped header
        # subtree. clang omits the filename when a location sits in the same file
        # as the last location it printed, so the state is only correct if every
        # token advances it. Skipping the scan desynchronized it instead, and every
        # later node printed as `line:` or `col:` was then read as header code and
        # dropped: on quickjs's cutils.c that silently lost whole function bodies.
        start_file = ""
        start_line = 0
        end_line = 0
        end_file = ""
        for index, token in enumerate(LOC_TOKEN.findall(scrubbed)):
            if token.startswith("line:"):
                current_line = int(token.split(":")[1])
            elif token.startswith("col:"):
                pass
            else:
                name, number, _col = token.rsplit(":", 2)
                current_file, current_line = name, int(number)
            if index < len(range_tokens):
                if not start_line:
                    start_file, start_line = current_file, current_line
                end_file, end_line = current_file, current_line
        if not start_line:
            # No range of its own: the node sits where clang's cursor stands.
            start_file = end_file = current_file
            start_line = end_line = current_line
        # A range that starts here and ends in a header is a macro expansion:
        # clang reports where the expansion ends, which is in the header, while
        # tree-sitter reports the call site. The call site's own span is not in
        # the dump, so the node keeps one row. A single row can never pass the
        # clone rule's two-row floor, which is the conservative way to be wrong:
        # otherwise a two-row `if` around a macro claimed 212 rows of the file.
        if Path(end_file).name != Path(start_file).name:
            end_line = start_line

        node = Node(
            kind, body, start_file, start_line, max(start_line, end_line), [],
            same_file=Path(start_file).name == wanted,
        )
        while len(stack) > depth - 1 + 1:
            stack.pop()
        stack[-1].children.append(node)
        stack.append(node)
    align_cases(root)
    return root


def run_dump(clang: str, path: Path, flags: list[str],
             cwd: Path | None = None) -> tuple[list[str], list[str]]:
    """(dump lines, clang's error lines) for one file.

    clang runs from the repository root, because that is where a project's own
    flags make sense: nginx's build file says `-Isrc/core`, and every relative
    include path would miss if the process ran from the caller's directory.
    """
    command = [clang, "-fsyntax-only", "-ferror-limit=0", "-Xclang", "-ast-dump", *flags, str(path)]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=DUMP_TIMEOUT, errors="replace",
            cwd=str(cwd) if cwd else None,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], [f"could not run clang: {type(exc).__name__}"]
    # Both spellings matter. A missing header is a `fatal error`, and without it
    # in this list a file whose `#include` never resolved still produced an AST:
    # clang recovers, every type is unknown, and the metrics describe a guess.
    errors = [
        line.strip() for line in proc.stderr.splitlines()
        if ": error:" in line or ": fatal error:" in line
    ]
    return proc.stdout.splitlines(), errors


# --------------------------------------------------------------------------
# Reading the tree
# --------------------------------------------------------------------------


def decl_name(text: str) -> str:
    """The name of a declaration, from the identifier before its type.

    The dump puts flags on both sides: `used get_class_atom 'int (...)' static`.
    So the name is the identifier before the first quoted type, and the type is
    found by shape rather than by position.
    """
    match = DECL_NAME.search(LOC_GROUP.sub(" ", text))
    if not match:
        return ""
    name = match.group(1)
    return "" if name in NOT_A_NAME else name


def referenced_name(text: str) -> str:
    """The name a reference expression mentions."""
    quoted = re.findall(r"'([^']*)'", text)
    if len(quoted) >= 2:
        candidate = quoted[-2]
        if re.fullmatch(r"[A-Za-z_~][\w:~]*", candidate):
            return candidate
    tail = re.search(r"(?:[.>-]+|/)\s*([A-Za-z_]\w*)\s*$", text)
    if tail:
        return tail.group(1)
    return quoted[-1] if quoted and re.fullmatch(r"[A-Za-z_]\w*", quoted[-1]) else ""


def callee_name(node: Node) -> str:
    """The name a call goes through, looking through casts and parentheses."""
    current: Node | None = node
    for _ in range(6):
        if current is None:
            return ""
        children = current.own_children()
        if not children:
            return ""
        child = children[0]
        if child.kind in ("ImplicitCastExpr", "ParenExpr", "CXXBindTemporaryExpr",
                          "MaterializeTemporaryExpr", "ExprWithCleanups"):
            current = child
            continue
        if child.kind in IDENTIFIER_KINDS:
            return referenced_name(child.text)
        if child.kind == "CallExpr":
            current = child
            continue
        return referenced_name(child.text)
    return ""


def decisions_in(node: Node) -> int:
    """Cyclomatic increments below one node, mirroring the tree-sitter definition."""
    total = 0
    for current in node.walk():
        if current.kind in DECISION_KINDS:
            total += 1
        elif current.kind == "BinaryOperator" and BOOL_OPERATOR.search(current.text):
            total += 1
    return total


# Nodes clang inserts that tree-sitter's grammars have no counterpart for: an
# implicit conversion, a temporary's materialization, a full-expression wrapper.
# They must not change a shape, and they must not hide their own subtree either:
# dropping an ImplicitCastExpr whole is what made `x + y` hash as a bare
# BinaryOperator, which then matched every other operator with two operands.
INVISIBLE_KINDS = frozenset(
    {
        "ImplicitCastExpr", "ExprWithCleanups", "MaterializeTemporaryExpr",
        "CXXBindTemporaryExpr", "FullExpr", "ConstantExpr",
    }
)
# Kinds whose text carries the operator token, which tree-sitter reports as its
# own anonymous node. Without it `a + b` and `a == b` would hash alike.
OPERATOR_KINDS = frozenset(
    {"BinaryOperator", "UnaryOperator", "CompoundAssignOperator", "CXXOperatorCallExpr"}
)
# The last quoted group holds the operator: `'int' '+'`, `'int' '-' prefix`.
OPERATOR_TOKEN = re.compile(r"'(?P<op>[^\w\s']{1,3})'(?:\s+(?:prefix|postfix))?\s*$")


def structure_token(node: Node) -> str:
    """The node's kind plus the operator it applies, when it applies one."""
    match = OPERATOR_TOKEN.search(node.text)
    return f"{node.kind}:{match.group('op')}" if match else node.kind


def structural_hash(node: Node) -> str:
    """Hash a shape: kinds and operators in, names and literals out.

    This mirrors `measure._structural_hash` for the tree-sitter path, translated
    into clang's node vocabulary. Leaves keep their spelling unless they are an
    identifier or a literal, every child counts, and plumbing nodes are
    transparent rather than absent.
    """
    parts: list[str] = []

    def emit(current: Node) -> None:
        # A header node is scaffolding: it carries no shape of its own, but its
        # measured-file descendants do.
        if not current.same_file or current.kind in INVISIBLE_KINDS:
            for child in current.children:
                emit(child)
            return
        if current.kind in LITERAL_KINDS:
            parts.append("$LIT")
            return
        if current.kind in IDENTIFIER_KINDS:
            parts.append("$ID")
            return
        token = structure_token(current) if current.kind in OPERATOR_KINDS else current.kind
        parts.append(token)
        for child in current.children:
            emit(child)
        parts.append(")")

    emit(node)
    return hashlib.blake2b("\x00".join(parts).encode("utf-8"), digest_size=12).hexdigest()


def clone_candidates(root: Node) -> list[tuple[str, int, int]]:
    """(hash, start line, end line) for clone-sized subtrees, as measure expects."""
    kinds = FUNCTION_KINDS | DECISION_KINDS
    found: list[tuple[str, int, int]] = []
    for node in root.walk():
        if node.kind not in kinds:
            continue
        if node.children_count() < 2:
            continue
        if node.end_line - node.line + 1 < 2:
            continue
        found.append((structural_hash(node), node.line - 1, node.end_line - 1))
    return found


def call_references(root: Node, self_name: str) -> dict[str, int]:
    """Call sites by callee name, with the enclosing function's own name skipped."""
    counts: dict[str, int] = {}
    for node in root.walk():
        if node.kind != "CallExpr":
            continue
        name = callee_name(node)
        if not name or name == self_name:
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts


# --------------------------------------------------------------------------
# SLOC and comments
# --------------------------------------------------------------------------


def comment_spans(text: str) -> dict[int, list[tuple[int, int]]]:
    """Comment columns per row, in the shape `sloc_stats` expects.

    Written by hand because there is no tree to ask: a file clang accepts still
    needs its comments found, and the tree-sitter path gets that from its own
    parse. Strings, character literals, and escapes are tracked, so a `//` inside a
    URL in a string is not a comment, and a `'` after a digit is a C++ digit
    separator rather than a character literal.

    A block comment gets one span per row it covers, since a comment opening on
    row 40 and closing on row 45 covers six rows. Emitting only the closing row
    would count the opening rows as source and hide the closing one.
    """
    spans: dict[int, list[tuple[int, int]]] = {}
    lines = text.splitlines()
    lengths = [len(line) for line in lines] or [0]
    row = column = index = 0
    start_column = -1
    state = "code"
    previous = ""

    def emit() -> None:
        nonlocal start_column
        spans.setdefault(row, []).append((start_column, column))
        start_column = -1

    while index < len(text):
        character = text[index]
        if character == "\n":
            if state == "line":
                emit()
                state = "code"
            elif state == "block":
                spans.setdefault(row, []).append(
                    (start_column, lengths[row] if row < len(lengths) else column)
                )
                start_column = 0
            row += 1
            column = 0
            previous = ""
            index += 1
            continue
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if character == "/" and following == "*":
                start_column = column
                state = "block"
                index += 2
                column += 2
                continue
            if character == "/" and following == "/":
                start_column = column
                state = "line"
                index += 2
                column += 2
                continue
            if character == '"':
                state = "string"
            elif character == "'" and not previous.isdigit():
                state = "char"
        elif state == "string":
            if character == "\\":
                index += 2
                column += 2
                continue
            if character == '"':
                state = "code"
        elif state == "char":
            if character == "\\":
                index += 2
                column += 2
                continue
            if character == "'":
                state = "code"
        elif state == "block" and character == "*" and following == "/":
            column += 2
            index += 2
            emit()
            state = "code"
            continue
        previous = character
        column += 1
        index += 1
    if state in ("line", "block"):
        emit()
    return spans


# --------------------------------------------------------------------------
# The engine entry point
# --------------------------------------------------------------------------


def describe() -> dict:
    """What a report needs to say about this engine."""
    binary = find_clang()
    return {
        "available": binary is not None,
        "binary": binary or "",
        "version": version(binary) if binary else "",
        "languages": sorted(ENGINE_LANGS),
    }


def analyze_many(
    entries: list[FileEntry],
    root: Path,
    named_only: bool = True,
    clang: str | None = None,
    workers: int | None = None,
) -> list[FileAnalysis]:
    """Measure many files, in the order given, using a bounded process pool.

    Every file is its own clang process, so the work parallelizes without shared
    state and the answers still line up with the inputs. The pool is capped rather
    than sized to the machine: a C panel run is hundreds of compiles, and this is
    the tool's only heavy job.
    """
    if not entries:
        return []
    binary = clang or find_clang()
    if binary is None:
        return [
            unparsed(entry, "clang is not installed, and no other C parser is available")
            for entry in entries
        ]
    limit = workers or min(8, os.cpu_count() or 1)
    if len(entries) == 1 or limit <= 1:
        return [analyze(entry, root, named_only, binary) for entry in entries]
    with ThreadPoolExecutor(max_workers=limit) as pool:
        return list(pool.map(lambda entry: analyze(entry, root, named_only, binary), entries))


def analyze(
    entry: FileEntry, root: Path, named_only: bool = True,
    clang: str | None = None,
) -> FileAnalysis:
    """Measure one C or C++ file from clang's AST, or refuse it and say why."""
    binary = clang or find_clang()
    if binary is None:
        return unparsed(entry, "clang is not installed, and no other C parser is available")
    lines, errors = run_dump(binary, entry.path, flags_for(entry.path, root), root)
    if errors:
        return unparsed(entry, f"clang could not compile it: {errors[0][:180]}")
    tree = parse_dump(lines, entry.path.name)
    if not tree.children:
        return unparsed(entry, "clang produced no declarations for it")
    # clang prints `RecoveryExpr` where it carried on after a semantic error, and
    # it does not always print `: error:` with it. The tree around that node is a
    # guess, so the file is refused the way a tree-sitter ERROR node is: whole,
    # with a reason, and counted in the coverage line rather than in the metrics.
    recovered = next((node for node in tree.walk() if node.kind == "RecoveryExpr"), None)
    if recovered is not None:
        where = f"{entry.path.name}:{recovered.line}"
        return unparsed(
            entry,
            f"clang recovered from an error at {where} (RecoveryExpr), so this parse is partial",
        )

    source_lines = entry.text.splitlines()
    spans = comment_spans(entry.text)
    sloc, comment_rows = sloc_stats(source_lines, spans)

    raw_functions: list[tuple[Node, Node, str, bool]] = []
    anonymous = 0
    for node in tree.walk():
        if node.kind in ANONYMOUS_KINDS:
            anonymous += 1
            if not named_only:
                raw_functions.append((node, node, "", False))
        if node.kind not in FUNCTION_KINDS:
            continue
        body = node.find(frozenset({"CompoundStmt", "CXXTryStmt"}))
        if body is None:
            continue
        name = decl_name(node.text)
        raw_functions.append((node, body, name, bool(name)))

    functions: list[Func] = []
    references: dict[str, int] = {}
    for node, body, name, is_named in raw_functions:
        if not is_named and not named_only:
            name = "<anonymous>"
        # Same rule as the tree-sitter path: rows from the declaration's first line
        # through its last, counting only rows that hold real source.
        code_lines = {
            row for row in range(node.line - 1, node.end_line)
            if row < len(source_lines) and is_sloc(source_lines[row], spans.get(row, []))
        }
        functions.append(
            Func(
                name=name or "<anonymous>",
                file=entry.rel,
                lang=entry.lang.name,
                line=node.line,
                cc=1 + decisions_in(body),
                sloc=max(1, len(code_lines)),
            )
        )
        if is_named:
            for callee, count in call_references(body, name).items():
                references[callee] = references.get(callee, 0) + count

    return FileAnalysis(
        entry=entry, sloc=sloc, comment_rows=comment_rows, functions=functions,
        clone_candidates=clone_candidates(tree), parsed=True, anonymous=anonymous,
        references=references,
    )
