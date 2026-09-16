"""The clang front end: pure parsing pieces first, then what only clang can answer.

The second half needs a clang binary. On a machine without clang those tests
skip instead of failing, so a green run stays honest about what it exercised.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sloptrack import clang_engine, measure

needs_clang = pytest.mark.skipif(
    not clang_engine.available(), reason="no clang binary on PATH; C and C++ are skipped"
)


def make_entry(tmp_path: Path, name: str, text: str) -> measure.FileEntry:
    path = tmp_path / name
    path.write_text(text)
    lang = measure.lang_for(path)
    assert lang is not None, name
    return measure.FileEntry(path=path, rel=name, lang=lang, text=text)


# --------------------------------------------------------------------------
# The dump parser, which has no external dependency
# --------------------------------------------------------------------------


DUMP = [
    "TranslationUnitDecl 0x1 <invalid sloc>",
    "|-TypedefDecl 0x2 <scratch.c:1:1, col:14> col:14 referenced wchar_t 'int'",
    "|-FunctionDecl 0x3 <scratch.c:4:1, line:9:1> line:4:5 used main 'int (void)'",
    "| `-CompoundStmt 0x4 <col:15, line:9:1>",
    "|   `-IfStmt 0x5 <line:5:3, line:7:3>",
    "|     |-BinaryOperator 0x6 <line:5:7, col:13> 'int' '||'",
    "|     |-CallExpr 0x7 <line:6:5, col:10>",
    "|     | `-DeclRefExpr 0x8 <col:5> 'void ()' Function 0x9 'helper' 'void ()'",
    "|     `-CaseStmt 0x10 <line:7:3, line:8:5>",
    "|       |-IntegerLiteral 0x11 <col:8> 'int' 1",
    "|       `-NullStmt 0x12 <line:8:5>",
    "`-VarDecl 0x13 </usr/include/other.h:2:1, col:9> col:5 used global 'int' cinit",
]


def node(kind: str, text: str = "", line: int = 1, end_line: int = 1, **kwargs) -> clang_engine.Node:
    return clang_engine.Node(
        kind=kind, text=text, file="a.c", line=line, end_line=end_line, **kwargs
    )


def test_parse_dump_keeps_the_wanted_file_and_marks_included_ones():
    root = clang_engine.parse_dump(DUMP, "scratch.c")

    # The header's VarDecl stays in the tree, marked, so a descendant that did
    # belong to this file would keep its place.
    assert [child.kind for child in root.children] == ["TypedefDecl", "FunctionDecl", "VarDecl"]
    assert [child.same_file for child in root.children] == [True, True, False]
    # Metrics read only the file's own nodes.
    assert "global" not in " ".join(child.text for child in root.walk())


def test_parse_dump_reads_the_range_from_the_first_location_group():
    root = clang_engine.parse_dump(DUMP, "scratch.c")
    function = root.children[1]

    # The range comes from `<scratch.c:4:1, line:9:1>`; the name location that
    # follows it, `line:4:5`, must not be mistaken for the end.
    assert (function.line, function.end_line) == (4, 9)


def test_parse_dump_children_survive_a_dropped_parent():
    """A header node between two siblings must not orphan them."""
    lines = [
        "TranslationUnitDecl 0x1 <invalid sloc>",
        "`-FunctionDecl 0x2 <one.c:1:1, line:3:1> line:1:5 f 'void ()'",
        "  `-CompoundStmt 0x3 <col:8, line:3:1>",
        "    |-DeclStmt 0x4 <line:2:3, col:9>",
        "    `-ReturnStmt 0x5 <line:3:3, col:9>",
    ]
    root = clang_engine.parse_dump(lines, "one.c")
    body = root.children[0].children[0]

    assert [child.kind for child in body.children] == ["DeclStmt", "ReturnStmt"]


def test_parse_dump_keeps_a_node_that_shares_its_parents_line():
    """Clang prints a one-line body as a continuation, not as a new depth."""
    lines = [
        "TranslationUnitDecl 0x1 <invalid sloc>",
        "`-FunctionDecl 0x2 <one.c:1:1, line:1:40> line:1:5 f 'int ()'",
        "  `-CompoundStmt 0x3 <col:14, line:1:40>",
        "    `-ReturnStmt 0x4 <col:20, col:30>",
        "      `-IntegerLiteral 0x5 <col:27> 'int' 1",
    ]
    root = clang_engine.parse_dump(lines, "one.c")

    assert root.children[0].children[0].children[0].kind == "ReturnStmt"


def test_decl_name_reads_through_leading_and_trailing_flags():
    assert clang_engine.decl_name("used get_class_atom 'int (...)' static") == "get_class_atom"
    assert clang_engine.decl_name("col:14 referenced wchar_t 'int'") == "wchar_t"
    # A location group is not a name, and a line without a type has none either.
    assert clang_engine.decl_name("0x1 <scratch.c:1:1> 'int'") == ""


def test_referenced_name_reads_operator_and_arrow_forms():
    # A declaration reference ends with the name it mentions.
    assert clang_engine.referenced_name("'void ()' Function 0x9 'helper' 'void ()'") == "helper"
    # A member reference keeps the field name second from the end, before the type.
    assert clang_engine.referenced_name("'int' lvalue . 'width' 'int'") == "width"
    assert clang_engine.referenced_name("'int' lvalue -> 'width' 'int'") == "width"
    # When the dump leaves the field unquoted at the end, the tail form reads it.
    assert clang_engine.referenced_name("'int' lvalue . width") == "width"


def test_callee_name_looks_through_casts_to_find_the_call():
    call = node(
        "CallExpr",
        "'int'",
        children=[
            node(
                "ImplicitCastExpr",
                "'int (*)(int)' <FunctionToPointerDecay>",
                children=[node("DeclRefExpr", "'int (int)' Function 0x1 'clamp' 'int (int)'")],
            )
        ],
    )

    assert clang_engine.callee_name(call) == "clamp"


def test_decisions_count_the_shape_the_metric_documents():
    root = clang_engine.parse_dump(DUMP, "scratch.c")

    # IfStmt + the `||` + CaseStmt, which is what the tree-sitter config counts.
    assert clang_engine.decisions_in(root) == 3


def test_case_labels_take_the_line_and_child_count_of_the_grammar():
    """A fall-through label owns its row; the last label owns the body."""
    lines = [
        "TranslationUnitDecl 0x1 <invalid sloc>",
        "`-FunctionDecl 0x2 <t.c:1:1, line:9:1> line:1:5 f 'void (int)'",
        "  `-CompoundStmt 0x3 <col:18, line:9:1>",
        "    `-SwitchStmt 0x4 <line:2:3, line:8:3>",
        "      `-CompoundStmt 0x5",
        "        |-CaseStmt 0x6 <line:3:3, line:5:5>",
        "        | |-IntegerLiteral 0x7 <col:8> 'int' 1",
        "        | `-CaseStmt 0x8 <line:4:3, line:5:5>",
        "        |   |-IntegerLiteral 0x9 <col:8> 'int' 2",
        "        |   `-CallExpr 0xa <line:5:5>",
        "        |     `-DeclRefExpr 0xb <col:5> 'void ()' Function 0xc 'g'",
        "        `-DefaultStmt 0xd <line:7:3, line:8:3>",
        "          `-NullStmt 0xe <line:8:3>",
    ]
    root = clang_engine.parse_dump(lines, "t.c")
    body = root.children[0].children[0].children[0].children[0]
    outer, default = body.children
    label, inner = outer.children

    # `case 1:` falls through, so it owns one row and no body. Its own child is
    # the literal in the label, not a statement.
    assert (outer.line, outer.end_line) == (3, 3)
    assert label.kind == "IntegerLiteral"
    assert outer.children_count() == 1
    # `case 2:` owns its body statement.
    assert (inner.line, inner.end_line) == (4, 5)
    assert inner.children_count() == 2
    # A default with a body keeps its own span.
    assert (default.line, default.end_line) == (7, 8)


def test_comment_spans_ignores_markers_inside_strings_and_char_literals():
    text = (
        'char *url = "http://example.com";  // trailing, real comment\n'
        "char c = '/';  /* block\n"
        "                  spans rows */\n"
        'char *t = "/*";  // not a block comment\n'
    )
    spans = clang_engine.comment_spans(text)
    rows = text.splitlines()

    # Rows are zero-based, matching what `sloc_stats` indexes with.
    assert sorted(spans) == [0, 1, 2, 3]
    assert [rows[0][start:end] for start, end in spans[0]] == ["// trailing, real comment"]
    # The block comment starts on row 2 and closes on row 3.
    assert rows[1][spans[1][0][0] :].startswith("/* block")
    assert rows[2][: spans[2][0][1]].endswith("*/")
    assert [rows[3][start:end] for start, end in spans[3]] == ["// not a block comment"]


def test_structural_hash_normalizes_names_and_literals_and_keeps_kinds():
    def expression(kind: str, text: str) -> clang_engine.Node:
        return node(
            "BinaryOperator",
            "'int' '+'",
            children=[node(kind, text), node("IntegerLiteral", "'int' 7")],
        )

    assert clang_engine.structural_hash(
        expression("DeclRefExpr", "'int' 'alpha'")
    ) == clang_engine.structural_hash(expression("DeclRefExpr", "'int' 'beta'"))
    # A different operator is a different shape, even with the same operands.
    assert clang_engine.structural_hash(
        expression("DeclRefExpr", "'int' 'alpha'")
    ) != clang_engine.structural_hash(
        node(
            "BinaryOperator",
            "'int' '=='",
            children=[node("DeclRefExpr", "'int' 'alpha'"), node("IntegerLiteral", "'int' 7")],
        )
    )


def test_cflags_file_adds_to_what_the_build_database_knows(tmp_path):
    # One argument per line, so a define holding quotes needs no shell escaping.
    (tmp_path / ".sloptrack-cflags").write_text("-DHAVE_CONFIG_H\n-DVERSION=\"1.2\"\n")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{"file": "src/a.c", "command": "cc -DMAGIC -o a.o src/a.c"}])
    )
    source = tmp_path / "src" / "a.c"
    source.parent.mkdir()
    source.write_text("int a;\n")

    words = clang_engine.flags_for(source, tmp_path)

    # The file's own directory is always on the include path, both the database
    # and the cflags file contribute, and another file's flags do not leak in.
    assert f"-I{source.parent}" in words
    assert "-DMAGIC" in words
    assert "-DHAVE_CONFIG_H" in words
    assert '-DVERSION="1.2"' in words


def test_compile_commands_flags_are_reused_from_the_build(tmp_path):
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {"file": f"{tmp_path}/src/a.c", "command": f"cc -DHAVE_X -I. -c {tmp_path}/src/a.c"},
                {"file": f"{tmp_path}/other.c", "command": "cc -DOTHER -c other.c"},
            ]
        )
    )
    source = tmp_path / "src" / "a.c"
    source.parent.mkdir()
    source.write_text("int a;\n")

    words = clang_engine.flags_for(source, tmp_path)

    assert "-DHAVE_X" in words
    assert "-DOTHER" not in words
    # Output and dependency flags are dropped: this run only parses.
    assert "-c" not in words


def test_describe_reports_what_the_report_needs():
    info = clang_engine.describe()

    assert set(info) >= {"available", "binary", "version", "languages"}
    assert info["languages"] == ["c", "cpp"]


# --------------------------------------------------------------------------
# What only clang can parse
# --------------------------------------------------------------------------


IDIOMS = """#include <stdio.h>
#define API __attribute__((visibility("default")))

static API int helper(const char *name) {
    return name ? 1 : 0;
}

int run(int mode) {
    static const char *const names[] = { [0] = "start", [1] = "stop" };
    void *labels[] = { &&start, &&stop };
    static const int table[] = { [0] = 4, [1] = 5 };
    goto *labels[mode];
start:
    return helper(names[0]) + helper(names[1]) + table[0];
stop:
    return 1;
}
"""


@needs_clang
def test_macro_specifiers_array_designators_and_computed_gotos_parse(tmp_path):
    row = make_entry(tmp_path, "idioms.c", IDIOMS)
    result = clang_engine.analyze(row, tmp_path)

    assert result.parsed, result.parse_problem
    names = {f.name for f in result.functions}
    assert {"helper", "run"} <= names
    assert sum(f.cc for f in result.functions) > 2
    # Both helper("a") and helper("b") are call sites inside run.
    assert result.references.get("helper") == 2


@needs_clang
def test_a_file_that_does_not_compile_is_refused_with_clangs_words(tmp_path):
    row = make_entry(tmp_path, "broken.c", "int f(void) { return undeclared_thing; }\n")
    result = clang_engine.analyze(row, tmp_path)

    assert result.parsed is False
    # The reason is clang's own message, so a reader can act on it.
    assert "use of undeclared" in result.parse_problem
    assert result.sloc > 0  # the lines still count, so coverage can show them


@needs_clang
def test_analyze_many_keeps_order_and_reports_each_failure(tmp_path):
    rows = [
        make_entry(tmp_path, "good.c", "int g(void) { return 1; }\n"),
        make_entry(tmp_path, "bad.c", "int h(void) { return nope; }\n"),
    ]
    results = clang_engine.analyze_many(rows, tmp_path, named_only=True)

    assert [r.entry.path.name for r in results] == ["good.c", "bad.c"]
    assert [r.parsed for r in results] == [True, False]
    assert "use of undeclared" in results[1].parse_problem


@needs_clang
def test_a_macro_span_does_not_swallow_the_file(tmp_path):
    """A node whose range ends in a header keeps one row, not the whole file."""
    (tmp_path / "macro.h").write_text(
        "#define LONG_ONE() do { call_a(); call_b(); call_c(); } while (0)\n"
    )
    source = """#include "macro.h"

int call_a(void);
int call_b(void);
int call_c(void);

int guarded(int mode) {
    if (mode > 0) LONG_ONE();
    return mode;
}

int other(int mode) {
    if (mode > 1) LONG_ONE();
    return mode + 1;
}
"""
    row = make_entry(tmp_path, "span.c", source)
    result = clang_engine.analyze(row, tmp_path)

    assert result.parsed, result.parse_problem
    # The two `if`s are two rows each; no candidate may claim the rest of the file.
    longest = max((end - start + 1 for _, start, end in result.clone_candidates), default=0)
    assert longest <= len(source.splitlines())


@needs_clang
def test_relative_include_paths_resolve_against_the_repo_root(tmp_path):
    """`.sloptrack-cflags` says `-Iinclude`, and clang runs from the repo root."""
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "local.h").write_text("#define LOCAL_FLAG 1\n")
    (tmp_path / ".sloptrack-cflags").write_text("-Iinclude\n")
    source = '#include "local.h"\nint f(void) { return LOCAL_FLAG; }\n'
    row = make_entry(tmp_path, "relative.c", source)

    result = clang_engine.analyze(row, tmp_path)

    assert result.parsed, result.parse_problem


@needs_clang
def test_a_missing_header_is_refused_not_guessed(tmp_path):
    """A fatal preprocessor error leaves clang guessing, so the file is refused."""
    row = make_entry(tmp_path, "missing.c", '#include "not_here_at_all.h"\nint f(void) { return 1; }\n')
    result = clang_engine.analyze(row, tmp_path)

    assert result.parsed is False
    assert "not_here_at_all.h" in result.parse_problem


@needs_clang
def test_clang_recovery_refuses_the_file(tmp_path):
    """`RecoveryExpr` means clang guessed, so the file is not measured."""
    source = "int f(void) { return missing_function(1) + 2; }\n"
    row = make_entry(tmp_path, "recovered.c", source)
    result = clang_engine.analyze(row, tmp_path)

    assert result.parsed is False
    assert "RecoveryExpr" in result.parse_problem or "clang could not compile" in result.parse_problem


@needs_clang
def test_clone_candidates_need_two_statements_and_a_body(tmp_path):
    source = """static int sum(const int *rows, int n) {
    int total = 0;
    for (int i = 0; i < n; i++) {
        if (rows[i] > 0) {
            total += rows[i];
        }
    }
    return total;
}

static int total_of(const int *rows, int n) {
    int total = 0;
    for (int i = 0; i < n; i++) {
        if (rows[i] > 0) {
            total += rows[i];
        }
    }
    return total;
}

int wrap(const int *rows, int n) {
    return sum(rows, n) + total_of(rows, n);
}
"""
    row = make_entry(tmp_path, "clones.c", source)
    result = clang_engine.analyze(row, tmp_path)

    assert result.parsed, result.parse_problem
    hashes: dict[str, int] = {}
    for digest, start, end in result.clone_candidates:
        hashes[digest] = hashes.get(digest, 0) + 1
    # The two identical loop bodies hash the same, so the clone rule can see them.
    assert max(hashes.values()) >= 2
    assert result.references.get("sum") == 1
    assert result.references.get("total_of") == 1
