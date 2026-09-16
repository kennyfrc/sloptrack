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

    scripts/check_languages.sh [--lang NAME] [--verbose]

Exit codes: 0 all pass, 1 at least one language failed, 2 at least one grammar
is not importable (the wrapper could not install it).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from textwrap import dedent

sys.path.insert(0, str(Path(__file__).resolve().parent))

import slop_measure as m  # noqa: E402

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


def clone_group_count(analysis: m.FileAnalysis) -> int:
    """Count clone groups the same way the report does."""
    groups: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for digest, start, end in analysis.clone_candidates:
        groups[digest].append((start, end))
    return sum(
        1
        for members in groups.values()
        if len(members) >= 2 and members[0][1] - members[0][0] + 1 >= m.MIN_CLONE_LINES
    )


def largest_clone_span(analysis: m.FileAnalysis) -> int:
    """Longest span among clone groups with at least two members."""
    groups: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for digest, start, end in analysis.clone_candidates:
        groups[digest].append((start, end))
    spans = [
        members[0][1] - members[0][0] + 1
        for members in groups.values()
        if len(members) >= 2 and members[0][1] - members[0][0] + 1 >= m.MIN_CLONE_LINES
    ]
    return max(spans, default=0)


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


def check_language(
    name: str, cfg: m.Lang, grammars: dict[str, object]
) -> tuple[bool, str, m.FileAnalysis | None]:
    """Run one fixture. Returns (passed, detail, analysis)."""
    filename, source, min_functions, min_cc = SAMPLES[name]
    ts_lang = grammars.get(name)
    if ts_lang is None:
        pkg = (cfg.grammar or "?").replace("_", "-")
        return False, f"MISSING GRAMMAR {cfg.grammar} (pip package: {pkg})", None
    if cfg.functions == frozenset():
        return False, "no function node types declared in the LANGS table", None

    try:
        import tree_sitter

        parser = tree_sitter.Parser(ts_lang)
    except Exception as exc:  # noqa: BLE001
        return False, f"parser could not start: {exc}", None

    tree = parser.parse(source.encode("utf-8"))
    if tree.root_node.has_error:
        return False, "fixture does not parse: grammar reported an ERROR node", None

    entry = m.FileEntry(path=Path(filename), rel=filename, lang=cfg, text=source)
    try:
        analysis = m.analyze_file(entry, parser, named_only=True)
    except Exception as exc:  # noqa: BLE001
        return False, f"analyze_file raised {type(exc).__name__}: {exc}", None

    functions = analysis.functions
    worst = max((f.cc for f in functions), default=0)
    clones = clone_group_count(analysis)
    largest = largest_clone_span(analysis)
    # The base fixture calls none of its functions, so no declared name may have
    # a counted call. A nonzero value means a declaration or a shadow was read as
    # a use.
    declared_names = {f.name for f in functions}
    miscounted = {
        call_name: analysis.references.get(call_name, 0)
        for call_name in declared_names
        if analysis.references.get(call_name, 0)
    }

    # A second pass with the first copy's numbers changed. The two bodies now
    # differ only in literal values, so a matching function-level group proves
    # the `literals` vocabulary normalizes them.
    mutated = mutate_first_copy(source)
    mutated_span = 0
    mutated_error = ""
    if mutated != source:
        mutated_entry = m.FileEntry(path=Path(filename), rel=filename, lang=cfg, text=mutated)
        try:
            mutated_analysis = m.analyze_file(mutated_entry, parser, named_only=True)
            mutated_span = largest_clone_span(mutated_analysis)
        except Exception as exc:  # noqa: BLE001
            mutated_span = -1
            mutated_error = f"re-analyze after literal change raised {type(exc).__name__}: {exc}"

    reasons: list[str] = []
    if mutated_error:
        reasons.append(mutated_error)
    if len(functions) < min_functions:
        reasons.append(
            f"found {len(functions)} named function(s), expected >= {min_functions}"
            f" ({analysis.anonymous} anonymous)"
        )
    if worst < min_cc:
        reasons.append(f"max CC {worst}, expected >= {min_cc} (decision vocabulary missed a branch)")
    if clones < 1:
        reasons.append("no clone group fired on the duplicated block")
    if largest < MIN_FUNCTION_CLONE_SPAN:
        reasons.append(
            f"largest clone span {largest}, expected >= {MIN_FUNCTION_CLONE_SPAN}"
            " (function-level clones did not fire)"
        )
    if mutated == source:
        reasons.append("no numeric literal in the first copy; the literal check could not run")
    elif mutated_span >= 0 and mutated_span < MIN_FUNCTION_CLONE_SPAN:
        reasons.append(
            f"after changing one copy's literals, largest clone span {mutated_span}"
            f" (expected >= {MIN_FUNCTION_CLONE_SPAN}); literals are not normalized"
        )
    bad_name = next(
        (f for f in functions if "{" in f.name or "\n" in f.name or len(f.name) > 40),
        None,
    )
    if bad_name is not None:
        reasons.append(f"function name looks like a text blob: {bad_name.name[:40]!r}")
    if miscounted:
        reasons.append(f"declaration name(s) counted as calls: {miscounted}")

    use_note = ""
    calls = USE_CALLS.get(name)
    if calls:
        alpha_name, beta_name = USE_CALL_NAMES.get(name, ("alpha", "beta"))
        use_entry = m.FileEntry(path=Path(filename), rel=filename, lang=cfg, text=source + calls)
        try:
            use_analysis = m.analyze_file(use_entry, parser, named_only=True)
            refs = use_analysis.references
            alpha_uses = refs.get(alpha_name, 0)
            beta_uses = refs.get(beta_name, 0)
            use_note = f"  uses {alpha_name} {alpha_uses}/{beta_name} {beta_uses}"
            if alpha_uses != 1:
                reasons.append(f"call counting: expected {alpha_name} called once, got {alpha_uses}")
            if beta_uses < 2:
                reasons.append(f"call counting: expected {beta_name} called twice or more, got {beta_uses}")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"use fixture raised {type(exc).__name__}: {exc}")

    detail = (
        f"functions {len(functions)}  max CC {worst}  clones {clones}"
        f"  span {largest}  lit-span {mutated_span}  sloc {analysis.sloc}{use_note}"
    )
    if reasons:
        return False, detail + "  |  " + "; ".join(reasons), analysis
    return True, detail, analysis


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify language vocabularies on known-answer fixtures.")
    ap.add_argument("--lang", action="append", default=[], help="check only this language (repeatable)")
    ap.add_argument("--verbose", action="store_true", help="list every function found per language")
    args = ap.parse_args()

    wanted = args.lang or list(SAMPLES)
    unknown = [name for name in wanted if name not in SAMPLES]
    if unknown:
        print(f"error: no fixture for {', '.join(unknown)}", file=sys.stderr)
        print(f"       add one to SAMPLES in {Path(__file__).name}", file=sys.stderr)
        return 2
    missing_from_table = [name for name in wanted if name not in {l.name for l in m.LANGS}]
    if missing_from_table:
        print(f"error: {', '.join(missing_from_table)} not in the LANGS table", file=sys.stderr)
        return 2

    by_name = {l.name: l for l in m.LANGS}
    grammars = m.load_grammars(set(wanted))

    failures = 0
    missing = 0
    print("LANGUAGE CHECK  fixtures with known answers")
    for name in wanted:
        cfg = by_name[name]
        passed, detail, analysis = check_language(name, cfg, grammars)
        if detail.startswith("MISSING"):
            missing += 1
            mark = "MISS "
        elif passed:
            mark = "PASS "
        else:
            failures += 1
            mark = "FAIL "
        print(f"  {mark} {name:<11} {detail}")
        if args.verbose and analysis is not None:
            for f in analysis.functions:
                print(f"         {f.name:<24} CC {f.cc:<4} SLOC {f.sloc}")

    total = len(wanted)
    print(
        f"\n  {total - failures - missing} passed, {failures} failed, {missing} missing grammar(s)"
    )
    if not args.lang:
        unfixtured = [l.name for l in m.LANGS if l.name not in SAMPLES]
        if unfixtured:
            failures += len(unfixtured)
            for name in unfixtured:
                print(f"  FAIL  {name:<11} in LANGS but has no fixture in SAMPLES")
            print(f"  {len(unfixtured)} table entr(y/ies) are unverified; add a sample before trusting them")
    if missing:
        print("  run through scripts/check_languages.sh so uvx installs the grammars")
        return 2
    if failures:
        return 1
    print("  every table entry yields functions, complexity, clones, and call counts on its fixture")
    return 0


if __name__ == "__main__":
    sys.exit(main())