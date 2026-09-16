"""The measurement pipeline, stage by stage.

Every stage here has one production caller, which is normal for a pipeline and
also means the metric the tool reports calls it a single-use callable. Calling
each stage directly on known input is what turns that into a second call site,
and it is worth doing on its own terms: a stage that is only ever exercised
through main() has its error paths unvisited.

Tests that need a parser are gated the same way test_grammars.py is, because the
grammars are installed only by `sloptrack measure` or the CI job that has them.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from sloptrack import install_skill, measure

SAMPLE = '''\
"""A module with two callables and a branch."""

import json


def load(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip() and not line.startswith("#"):
                rows.append(json.loads(line))
    return rows


def summarize(rows):
    total = 0
    for row in rows:
        total += row.get("amount", 0)
    return total
'''

LONG_LINE = "x = '" + "a" * 400 + "'\n"

try:
    GRAMMARS = measure.load_grammars({"python"})
except Exception:  # noqa: BLE001 - no tree-sitter at all is a skip, not a failure
    GRAMMARS = {}

needs_grammar = pytest.mark.skipif(not GRAMMARS, reason="the python grammar is not installed")


def python_config() -> measure.Lang:
    return {lang.name: lang for lang in measure.LANGS}["python"]


def lang_for(rel: str) -> measure.Lang:
    """The language the table assigns to this filename."""
    return measure.lang_for(Path(rel)) or python_config()


def entry(rel: str, text: str = SAMPLE, path: Path | None = None) -> measure.FileEntry:
    return measure.FileEntry(path=path or Path(rel), rel=rel, lang=lang_for(rel), text=text)


def func(name: str, cc: int = 1, sloc: int = 4, uses: int = 0, rel: str = "app.py") -> measure.Func:
    return measure.Func(name=name, file=rel, lang="python", line=1, cc=cc, sloc=sloc, uses=uses)


def analysis(
    rel: str = "app.py",
    functions: list[measure.Func] | None = None,
    references: dict[str, int] | None = None,
    clone_candidates: list[tuple[str, int, int]] | None = None,
    comment_rows: set[int] | None = None,
    anonymous: int = 0,
    parsed: bool = True,
    sloc: int = 20,
) -> measure.FileAnalysis:
    return measure.FileAnalysis(
        entry=entry(rel),
        sloc=sloc,
        comment_rows=comment_rows or set(),
        functions=functions or [],
        clone_candidates=clone_candidates or [],
        parsed=parsed,
        anonymous=anonymous,
        references=references or {},
    )


def clone_pair(span: int = 9) -> list[tuple[str, int, int]]:
    return [("digest", 0, span - 1), ("digest", 30, 30 + span - 1)]


def can_parse(name: str) -> bool:
    """Whether this interpreter can parse `name`.

    An installed grammar is an environment fact, not a behavior, so the tests that
    span the whole runner read it instead of assuming one answer.
    """
    return bool(measure.load_grammars({name}))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small git repository with one source file and one ignored directory."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "app.py").write_text(SAMPLE, encoding="utf-8")
    (root / "README.md").write_text("docs\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (root / "ignored").mkdir()
    (root / "ignored" / "skip.py").write_text(SAMPLE, encoding="utf-8")
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], cwd=root, check=True)
    subprocess.run([*git, "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=root, check=True)
    return root


def request(*argv: str) -> tuple[argparse.Namespace, measure.Target]:
    return measure.parse_request(list(argv))


def run_for(root: Path, *argv: str) -> measure.Run:
    args, target = measure.parse_request([str(root), *argv])
    return measure.run_measurement(target, args)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


def test_parse_request_resolves_a_directory(repo: Path):
    args, target = request(str(repo))

    assert target.scope == repo.resolve()
    assert target.root == repo.resolve()
    assert target.requested is None
    assert args.functions == "named"
    assert args.top == 10


def test_parse_request_resolves_a_single_file(repo: Path):
    args, target = request(str(repo / "pkg" / "app.py"))

    assert target.requested is not None
    assert target.requested.rel == "app.py"
    assert target.requested.text.startswith('"""A module')
    assert target.root == (repo / "pkg").resolve()


def test_resolve_target_reports_a_missing_path(tmp_path: Path):
    with pytest.raises(measure.InputError) as caught:
        measure.resolve_target(str(tmp_path / "nope"))

    assert "no such path" in str(caught.value)
    assert caught.value.hints == []


def test_resolve_target_reports_a_file_it_cannot_read(tmp_path: Path):
    notes = tmp_path / "notes.txt"
    notes.write_text("not source\n", encoding="utf-8")

    with pytest.raises(measure.InputError) as caught:
        measure.resolve_target(str(notes))

    assert str(caught.value) == "unsupported file type: notes.txt"


def test_input_error_lines_carry_the_hints():
    error = measure.InputError("bad thing", "try this", "not that")

    assert error.lines() == ["error: bad thing", "       try this", "       not that"]


def test_build_parser_keeps_the_documented_defaults():
    args = measure.build_parser().parse_args([])

    assert (args.path, args.since, args.top) == (".", "30 days ago", 10)
    assert args.functions == "named"
    assert not args.json and not args.scb and not args.no_git


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_compiled_excludes_matches_a_trailing_component():
    patterns = measure.compiled_excludes(["**/check_languages.py"])

    assert measure.excluded("pkg/check_languages.py", patterns)
    assert not measure.excluded("pkg/measure.py", patterns)


def test_excluded_is_false_with_no_patterns():
    assert not measure.excluded("anything.py", [])


def test_lang_for_reads_the_filename_before_the_extension():
    assert measure.lang_for(Path("Rakefile")).name == "ruby"
    assert measure.lang_for(Path("app.py")).name == "python"
    assert measure.lang_for(Path("notes.txt")) is None


def test_read_texts_loads_each_file_and_survives_a_missing_one(tmp_path: Path):
    present = tmp_path / "app.py"
    present.write_text("x = 1\n", encoding="utf-8")
    entries = [entry("app.py", "", present), entry("gone.py", "", tmp_path / "gone.py")]

    measure.read_texts(entries)

    assert entries[0].text == "x = 1\n"
    assert entries[1].text == ""


def test_git_listed_skips_ignored_files_and_keeps_the_scope_relative(repo: Path):
    entries = measure.git_listed(repo, repo, [])
    rels = sorted(e.rel for e in entries)

    assert rels == ["pkg/app.py"]
    assert not any("ignored" in rel for rel in rels)


def test_git_listed_is_scope_relative_inside_a_subtree(repo: Path):
    """A subtree run names files relative to the subtree, not to the repository."""
    scope = repo / "pkg"

    rels = sorted(e.rel for e in measure.git_listed(repo, scope, []))

    assert rels == ["app.py"]


def test_git_entry_drops_what_is_not_measurable(repo: Path):
    assert measure.git_entry(repo, repo, "README.md", []) is None
    assert measure.git_entry(repo, repo, "", []) is None
    assert measure.git_entry(repo, repo, "pkg/app.py", []) is not None
    assert measure.git_entry(repo, repo, "pkg/app.py", measure.compiled_excludes(["**/app.py"])) is None


def test_git_listed_returns_none_when_git_cannot_list(tmp_path: Path):
    assert measure.git_listed(tmp_path, tmp_path, []) is None


def test_tree_walked_finds_source_without_git(tmp_path: Path):
    (tmp_path / "app.py").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.py").write_text(SAMPLE, encoding="utf-8")

    rels = [e.rel for e in measure.tree_walked(tmp_path, [])]

    assert rels == ["app.py"]


def test_discover_uses_git_when_it_can_and_prefers_it_to_the_walk(repo: Path):
    rels = [e.rel for e in measure.discover(repo, [], use_git=True)]

    assert rels == ["pkg/app.py"]
    assert measure.discover(repo, [], use_git=True)[0].text.startswith('"""A module')


def test_discover_falls_back_to_the_walk_and_sorts(tmp_path: Path):
    (tmp_path / "b.py").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "a.py").write_text(SAMPLE, encoding="utf-8")

    assert [e.rel for e in measure.discover(tmp_path, [], use_git=True)] == ["a.py", "b.py"]
    assert [e.rel for e in measure.discover(tmp_path, [], use_git=False)] == ["a.py", "b.py"]


def test_long_line_share_counts_only_long_lines():
    assert measure.long_line_share(entry("app.py", "ok\n" + LONG_LINE)) == 0.5
    assert measure.long_line_share(entry("app.py", "\n\n")) == 0.0


def test_long_line_report_ranks_the_worst_first():
    files = [entry("a.py", "short\n"), entry("b.py", LONG_LINE * 2), entry("c.py", LONG_LINE)]

    report = measure.long_line_report(files)

    assert [rel for rel, _share in report] == ["b.py", "c.py"]


def test_is_bundled_needs_a_build_artifact_name():
    assert measure.is_bundled(entry("app.min.js"))
    assert not measure.is_bundled(entry("app.js"))
    assert not measure.is_bundled(entry("app.py", "x = '" + "a" * 400 + "'\n"))


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_only_languages_filters_and_passes_everything_through():
    files = [entry("a.py"), entry("b.rb")]

    assert [f.rel for f in measure.only_languages(files, [])] == ["a.py", "b.rb"]
    assert [f.rel for f in measure.only_languages(files, ["python"])] == ["a.py"]


def test_split_bundled_separates_the_build_artifacts():
    files = [entry("app.py"), entry("vendor.min.js")]

    kept, bundled = measure.split_bundled(files)

    assert [f.rel for f in kept] == ["app.py"]
    assert [f.rel for f in bundled] == ["vendor.min.js"]


def test_no_files_error_lists_what_is_recognized(tmp_path: Path):
    error = measure.no_files_error(tmp_path)

    assert str(error) == f"no recognized source files under {tmp_path}"
    assert "recognized: " in error.hints[0]
    assert any(".py" in hint for hint in error.hints)
    assert "scb-check" in error.hints[-1]


def test_select_inputs_reports_an_empty_scope(tmp_path: Path):
    target = measure.Target(scope=tmp_path, root=tmp_path)

    with pytest.raises(measure.InputError):
        measure.select_inputs(
            target, excludes=[], langs=[], use_git=False, include_bundled=False
        )


def test_select_inputs_reports_a_scope_of_nothing_but_artifacts(tmp_path: Path):
    (tmp_path / "app.min.js").write_text("var a=1\n", encoding="utf-8")
    target = measure.Target(scope=tmp_path, root=tmp_path)

    with pytest.raises(measure.InputError) as caught:
        measure.select_inputs(
            target, excludes=[], langs=[], use_git=False, include_bundled=False
        )

    assert "build artifact" in str(caught.value)
    assert "include-minified" in caught.value.hints[0]


def test_select_inputs_can_keep_the_artifacts(tmp_path: Path):
    (tmp_path / "app.min.js").write_text("var a=1\n", encoding="utf-8")
    target = measure.Target(scope=tmp_path, root=tmp_path)

    files, bundled = measure.select_inputs(
        target, excludes=[], langs=[], use_git=False, include_bundled=True
    )

    assert [f.rel for f in files] == ["app.min.js"]
    assert bundled == []


def test_select_inputs_takes_a_requested_file_as_given(repo: Path):
    target = measure.Target(
        scope=repo / "pkg" / "app.py",
        root=repo / "pkg",
        requested=entry("app.py", SAMPLE, repo / "pkg" / "app.py"),
    )

    files, bundled = measure.select_inputs(
        target, excludes=["**/*.py"], langs=[], use_git=True, include_bundled=False
    )

    assert [f.rel for f in files] == ["app.py"]
    assert bundled == []


def test_required_grammars_reads_the_request_without_importing_a_grammar(repo: Path):
    args, target = request(str(repo), "--no-git")

    assert measure.required_grammars(args, target) == ["tree-sitter-python"]


# ---------------------------------------------------------------------------
# Parsing stages
# ---------------------------------------------------------------------------


def test_load_grammar_returns_none_for_a_package_that_is_not_installed():
    cfg = measure.Lang(
        name="missing", exts=frozenset({".miss"}), filenames=frozenset(),
        grammar="tree_sitter_definitely_not_installed",
    )

    assert measure.load_grammar(cfg, object()) is None


def test_load_grammars_ignores_a_language_with_no_package():
    """A grammar package that is not published (or not installed) drops out."""
    cfg = measure.Lang(
        name="fiction", exts=frozenset({".fic"}), filenames=frozenset(),
        grammar="tree_sitter_fiction",
    )

    assert measure.load_grammars({cfg.name}) == {}


def test_load_parsers_needs_no_tree_sitter_for_an_empty_table():
    parsers, available = measure.load_parsers({})

    assert parsers == {}
    assert isinstance(available, bool)


def test_unparsed_counts_lines_without_a_parse():
    analysis_ = measure.unparsed(entry("app.py", "a = 1\n\nb = 2\n"))

    assert analysis_.sloc == 2
    assert analysis_.functions == []
    assert not analysis_.parsed


def test_analyze_files_falls_back_for_a_file_with_no_parser():
    results, failures = measure.analyze_files([entry("app.py")], {}, named_only=True)

    assert len(results) == 1
    assert not results[0].parsed
    assert failures == {}


def test_analyze_files_skips_a_blank_file():
    results, _failures = measure.analyze_files([entry("empty.py", "   \n")], {}, named_only=True)

    assert results == []


def test_unreliable_languages_only_flags_a_language_with_no_callable_at_all():
    parsers = {"python": object(), "ruby": object()}
    results = [analysis("a.py"), analysis("b.rb", anonymous=3)]

    assert measure.unreliable_languages(results, parsers) == {"python"}
    assert measure.unreliable_languages([analysis("a.py")], parsers) == {"python", "ruby"}


@needs_grammar
def test_comment_spans_maps_each_comment_row():
    import tree_sitter

    parser = tree_sitter.Parser(GRAMMARS["python"])
    cfg = python_config()
    text = 'x = 1  # trailing\ny = 2\n"""doc\nblock"""\n'
    root = parser.parse(text.encode()).root_node

    spans = measure.comment_spans(root, cfg, text.splitlines())

    # Only the trailing comment is a comment node; the string literal is not,
    # because it sits in an expression and not in a comment context.
    assert set(spans) == {0}


@needs_grammar
def test_sloc_stats_counts_code_and_remembers_comment_only_rows():
    import tree_sitter

    parser = tree_sitter.Parser(GRAMMARS["python"])
    lines = ["x = 1", "# just a comment", "", "y = 2"]
    root = parser.parse("\n".join(lines).encode()).root_node
    spans = measure.comment_spans(root, python_config(), lines)

    sloc, comment_rows = measure.sloc_stats(lines, spans)

    assert sloc == 2
    assert comment_rows == {1}


@needs_grammar
def test_scan_functions_reads_names_complexity_and_call_sites():
    import tree_sitter

    parser = tree_sitter.Parser(GRAMMARS["python"])
    parser_entry = entry("app.py", SAMPLE)
    root = parser.parse(SAMPLE.encode()).root_node
    lines = SAMPLE.splitlines()
    spans = measure.comment_spans(root, python_config(), lines)

    scan = measure.scan_functions(parser_entry, python_config(), root, lines, spans, True)

    assert [f.name for f in scan.functions] == ["load", "summarize"]
    assert scan.anonymous == 0
    assert scan.functions[0].cc >= 3
    assert scan.functions[0].file == "app.py"


@needs_grammar
def test_analyze_file_reports_sloc_functions_and_references():
    import tree_sitter

    parser = tree_sitter.Parser(GRAMMARS["python"])
    text = "def alpha(x):\n    return x\n\n\ndef beta(y):\n    return alpha(y)\n"

    result = measure.analyze_file(entry("app.py", text), parser)

    assert result.parsed
    assert [f.name for f in result.functions] == ["alpha", "beta"]
    assert result.references == {"alpha": 1}
    assert result.sloc == 4


@needs_grammar
def test_analyze_file_counts_anonymous_callables_when_asked():
    """Python names its callables, so this is checked on JavaScript.

    A callback has no name to count, so the default reading skips it. The `all`
    reading includes it and records how many it added, which is the flag the
    report warns about.
    """
    import tree_sitter

    grammar = measure.load_grammars({"javascript"})
    if not grammar:
        pytest.skip("the javascript grammar is not installed")
    parser = tree_sitter.Parser(grammar["javascript"])
    text = "function outer(rows) {\n  return rows.map((r) => r + 1);\n}\n"

    named = measure.analyze_file(entry("app.js", text), parser, named_only=True)
    everything = measure.analyze_file(entry("app.js", text), parser, named_only=False)

    assert [f.name for f in named.functions] == ["outer"]
    assert [f.name for f in everything.functions] == ["outer", "(r) => r + 1"]
    # The counter reports what the scan saw either way; named_only decides
    # whether the nameless callable reaches the metrics.
    assert named.anonymous == everything.anonymous == 1


@needs_grammar
def test_is_callable_rejects_a_bodyless_declaration():
    import tree_sitter

    parser = tree_sitter.Parser(GRAMMARS["python"])
    nodes = [n for n in measure.named_walk(parser.parse(b"def f(): ...\n").root_node)
             if n.type == "function_definition"]
    cfg = python_config()

    assert nodes and all(measure.is_callable(node, cfg) for node in nodes)


@needs_grammar
def test_counts_as_decision_ignores_anonymous_tokens():
    import tree_sitter

    parser = tree_sitter.Parser(GRAMMARS["python"])
    root = parser.parse(b"if a and b:\n    pass\n").root_node
    cfg = python_config()
    nodes = list(measure.named_walk(root))

    assert any(measure.counts_as_decision(n, cfg) for n in nodes)
    assert not any(measure.counts_as_decision(n, cfg) for n in nodes if not n.is_named)


@needs_grammar
def test_decision_count_folds_or_stops_at_a_nested_callable():
    import tree_sitter

    parser = tree_sitter.Parser(GRAMMARS["python"])
    text = "def outer(xs):\n    def inner(y):\n        if y:\n            return 1\n        return 0\n    return [inner(x) for x in xs if x]\n"
    root = parser.parse(text.encode()).root_node
    cfg = python_config()
    body = next(n for n in measure.named_walk(root) if n.type == "function_definition") \
        .child_by_field_name("body")

    folded = measure._decision_count(body, cfg, fold_nested=True)
    separate = measure._decision_count(body, cfg, fold_nested=False)

    assert folded > 0
    assert folded == separate + 1


# ---------------------------------------------------------------------------
# Metric stages
# ---------------------------------------------------------------------------


def test_use_counts_sums_references_across_files():
    files = [analysis("a.py", references={"alpha": 2}), analysis("b.py", references={"alpha": 1})]

    assert measure.use_counts(files) == {"alpha": 3}


def test_split_by_use_partitions_on_the_use_count():
    functions = [func("none", uses=0), func("once", uses=1), func("twice", uses=2)]

    split = measure.split_by_use(functions)

    assert [f.name for f in split.unused] == ["none"]
    assert [f.name for f in split.single_use] == ["once"]
    assert [f.name for f in split.reused] == ["twice"]
    assert split.granularity == 0.5


def test_split_by_use_has_no_granularity_without_used_callables():
    assert measure.split_by_use([func("none", uses=0)]).granularity is None


def test_measured_sloc_sums_only_the_trusted_files():
    assert measure.measured_sloc([analysis(sloc=10), analysis(sloc=5)]) == 15


def test_verbosity_value_is_none_without_a_denominator():
    assert measure.verbosity_value(3, 10) == 0.3
    assert measure.verbosity_value(3, 0) is None


def test_clone_groups_by_digest_keeps_every_occurrence():
    files = [analysis("a.py", clone_candidates=[("d", 0, 9)]),
             analysis("b.py", clone_candidates=[("d", 0, 9)])]

    groups = measure.clone_groups_by_digest(files)

    assert len(groups["d"]) == 2


def test_is_clone_group_needs_two_members_of_clone_length():
    assert measure.is_clone_group([("a.py", 0, 9), ("b.py", 0, 9)])
    assert not measure.is_clone_group([("a.py", 0, 9)])
    assert not measure.is_clone_group([("a.py", 0, 1), ("b.py", 0, 1)])


def test_duplicate_lines_excludes_comment_rows():
    files = [analysis("a.py", clone_candidates=clone_pair(), comment_rows={0, 1})]

    lines = measure.duplicate_lines(files, measure.clone_groups_by_digest(files))

    assert lines == 18 - 2


def test_mass_totals_weights_complexity_by_length():
    totals = measure.mass_totals([func("small", cc=2, sloc=4), func("wide", cc=12, sloc=9)])

    assert totals.total == pytest.approx(2 * 2 + 12 * 3)
    assert totals.high == pytest.approx(36)
    assert [f.name for f in totals.high_cc] == ["wide"]
    assert totals.erosion == pytest.approx(36 / 40)


def test_mass_totals_has_no_erosion_without_mass():
    assert measure.mass_totals([]).erosion is None


def test_compute_signals_fills_every_part_from_the_analyses():
    functions = [func("alpha"), func("beta")]
    files = [
        analysis("a.py", references={"alpha": 1}, clone_candidates=clone_pair()),
        analysis("b.py", references={"beta": 2}),
    ]

    signals = measure.compute_signals(functions, files)

    assert signals.granularity == 0.5
    assert signals.verbosity == pytest.approx(18 / 40)
    assert signals.erosion == 0.0
    assert [f.name for f in signals.single_use_functions] == ["alpha"]
    assert [f.name for f in signals.reused_functions] == ["beta"]
    assert signals.measured_sloc == 40


def test_compute_signals_reads_a_verbosity_of_none_when_nothing_was_parsed():
    signals = measure.compute_signals([], [])

    assert signals.verbosity is None
    assert signals.granularity is None
    assert signals.erosion is None


def test_band_fields_pair_a_value_with_its_band():
    assert measure.band_fields(0.10, "verbosity") == {
        "band": "at-or-below human baseline", "vs_human": "0.67x the human baseline",
    }
    assert measure.band_fields(None, "erosion") == {"band": None, "vs_human": None}


# ---------------------------------------------------------------------------
# Run and payload
# ---------------------------------------------------------------------------


def test_run_measurement_records_the_scan(repo: Path):
    run = run_for(repo)

    assert [f.rel for f in run.files] == ["pkg/app.py"]
    assert run.present == {"python"}
    assert run.total_sloc > 0
    # Whether the grammars are importable is an environment fact, so assert the
    # flag agrees with the interpreter instead of assuming one answer.
    assert run.tree_sitter is can_parse("python")
    if not run.tree_sitter:
        assert run.signals.functions == []
    assert run.anonymous_skipped == 0
    assert run.git.available


def test_run_measurement_without_git_skips_growth_but_walks_the_tree(repo: Path):
    """--no-git turns off the history, and the walk then cannot honor .gitignore.

    That is a known difference between the two discovery paths: git lists what it
    tracks, and the walker only knows the SKIP_DIRS list.
    """
    run = run_for(repo, "--no-git")

    assert run.git.skip_reason == "--no-git"
    assert [f.rel for f in run.files] == ["ignored/skip.py", "pkg/app.py"]


def test_run_measurement_reports_a_bad_path_as_an_input_error():
    with pytest.raises(measure.InputError):
        measure.parse_request(["/nonexistent/sloptrack-path"])


def synthetic_run(tmp_path: Path, **overrides) -> measure.Run:
    """A Run with known numbers, so the payload sections can be checked exactly."""
    args = measure.build_parser().parse_args([str(tmp_path), "--no-git", "--top", "2"])
    functions = overrides.pop("functions", [func("alpha"), func("wide", cc=12, sloc=16)])
    files = overrides.pop("analyzed", [analysis(
        "app.py", clone_candidates=clone_pair(), references={"alpha": 1, "wide": 3},
    )])
    values = dict(
        args=args,
        target=measure.Target(scope=tmp_path, root=tmp_path),
        scan_root=tmp_path,
        files=[entry("app.py")],
        bundled=[],
        long_lines=[("app.py", 0.25)],
        present={"python"},
        grammars={},
        parsers={},
        tree_sitter=False,
        results=list(files),
        analyzed=list(files),
        unreliable=set(),
        failures={},
        signals=measure.compute_signals(functions, files),
        scb=None,
        scb_error=None,
        git=measure.GitStats(skip_reason="--no-git"),
    )
    values.update(overrides)
    return measure.Run(**values)


def test_language_sloc_groups_files_by_language(tmp_path: Path):
    counts = measure.language_sloc([analysis("a.py", sloc=10), analysis("b.py", sloc=5)])

    assert counts == {"python": {"files": 2, "sloc": 15}}


def test_engine_payload_records_what_the_engine_saw(tmp_path: Path):
    engine = measure.engine_payload(synthetic_run(tmp_path))

    assert engine["tree_sitter"] is False
    assert engine["languages_present"] == ["python"]
    assert engine["languages_without_grammar"] == ["python"]
    assert engine["functions_mode"] == "named"
    assert engine["long_line_files"] == [{"file": "app.py", "share": 0.25}]
    assert engine["parse_failures"] == {}


def test_sloc_payload_splits_measured_from_total(tmp_path: Path):
    run = synthetic_run(tmp_path)
    run.results.append(measure.unparsed(entry("other.py", "a = 1\nb = 2\n")))

    sloc = measure.sloc_payload(run)

    assert sloc["total"] == 22
    assert sloc["measured"] == 20
    assert sloc["unmeasured"] == 2
    assert sloc["files_total"] == 2
    assert sloc["files_measured"] == 1


def test_verbosity_payload_reports_the_clone_component(tmp_path: Path):
    verbosity = measure.verbosity_payload(synthetic_run(tmp_path))

    assert verbosity["clone_lines"] == 18
    assert verbosity["band"] == "in or above agent band"
    assert verbosity["ast_grep_lines"] is None


def test_erosion_payload_names_the_eroded_function(tmp_path: Path):
    erosion = measure.erosion_payload(synthetic_run(tmp_path))

    assert erosion["functions"] == 2
    assert erosion["high_cc_functions"] == 1
    assert erosion["high_cc_mass"] == pytest.approx(48)
    assert erosion["band"] == "in or above agent band"


def test_granularity_payload_ranks_the_single_use_callables(tmp_path: Path):
    granularity = measure.granularity_payload(synthetic_run(tmp_path))

    assert granularity["used_functions"] == 2
    assert granularity["single_use_functions"] == 1
    assert granularity["band"] == "above human band"
    assert granularity["single_use_top"][0]["name"] == "alpha"
    assert granularity["single_use_top"][0]["uses"] == 1


def test_func_entry_omits_uses_unless_asked():
    assert "uses" not in measure.func_entry(func("alpha", uses=2))
    assert measure.func_entry(func("alpha", uses=2), with_uses=True)["uses"] == 2


def test_rank_by_returns_the_heaviest_first_and_respects_top():
    ranked = measure.rank_by([func("light", sloc=1), func("heavy", cc=9, sloc=100)], 1)

    assert [f.name for f in ranked] == ["heavy"]


def test_duplicate_payload_reports_the_recoverable_lines(tmp_path: Path):
    blocks = measure.duplicate_payload(synthetic_run(tmp_path))

    assert blocks == [
        {"lines": 9, "occurrences": 2, "recoverable_lines": 9, "locations": ["app.py:1", "app.py:31"]}
    ]


def test_git_payload_carries_the_skip_reason(tmp_path: Path):
    git = measure.git_payload(measure.GitStats(skip_reason="--no-git"))

    assert git["available"] is False
    assert git["skip_reason"] == "--no-git"


def test_scb_coverage_needs_a_report(tmp_path: Path):
    assert measure.scb_coverage(synthetic_run(tmp_path)) is None

    run = synthetic_run(tmp_path, scb={"total_loc": 10})
    assert measure.scb_coverage(run) == pytest.approx(0.5)


def test_build_payload_has_every_section(tmp_path: Path):
    payload = measure.build_payload(synthetic_run(tmp_path))

    assert set(payload) == {
        "root", "engine", "sloc", "verbosity", "erosion", "granularity",
        "git", "hotspots", "duplicate_blocks", "scb_check",
    }
    assert payload["root"] == str(tmp_path)
    assert [f["name"] for f in payload["hotspots"]] == ["wide", "alpha"]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def test_print_metric_row_formats_the_value_and_its_band(capsys):
    measure.print_metric_row("EROSION", 0.312, "between human and agent bands", "1.01x the human baseline")

    assert capsys.readouterr().out == "  EROSION     0.312   between human and agent bands   (1.01x the human baseline)\n"


def test_print_metric_row_explains_a_missing_value(capsys):
    measure.print_metric_row("EROSION", None, unavailable=" (no functions parsed)")

    assert capsys.readouterr().out == "  EROSION     n/a (no functions parsed)\n"


def test_print_metric_detail_indents_under_its_metric(capsys):
    measure.print_metric_detail("clone lines 1 of 2 SLOC")

    assert capsys.readouterr().out == "              clone lines 1 of 2 SLOC\n"


def test_report_sections_print_the_payload(tmp_path: Path, capsys):
    payload = measure.build_payload(synthetic_run(tmp_path))

    measure.print_inputs(payload)
    measure.print_engine_notes(payload["engine"])

    out = capsys.readouterr().out
    assert "languages      python 1f/20sloc" in out
    assert "total SLOC     20" in out
    assert "Tree-sitter is unavailable" in out
    assert "no grammar for python" in out


def test_report_metrics_read_each_band(tmp_path: Path, capsys):
    payload = measure.build_payload(synthetic_run(tmp_path))

    measure.print_metrics(payload)

    out = capsys.readouterr().out
    assert "VERBOSITY   0.900" in out
    assert "EROSION" in out and "functions have CC > 10" in out
    assert "GRANULARITY 0.500" in out and "invoked once" in out


def test_report_metrics_explain_an_unmeasured_run(capsys):
    run = measure.compute_signals([], [])

    measure.print_verbosity({"verbosity": {"value": None, "band": None, "vs_human": None}, "sloc": {"measured": 0}})
    measure.print_erosion({"erosion": {"value": None, "band": None, "vs_human": None}})
    measure.print_granularity({"granularity": {
        "value": None, "band": None, "human": 0.27, "human_sd": 0.13,
    }})

    out = capsys.readouterr().out
    assert "VERBOSITY   n/a   nothing was parsed" in out
    assert "EROSION     n/a (no functions parsed)" in out
    assert "GRANULARITY n/a (no callable has a counted reference)" in out
    assert run.verbosity is None


def test_report_growth_prints_a_skip_reason(capsys):
    measure.print_growth({"git": {"available": False, "skip_reason": "--no-git"}})

    assert capsys.readouterr().out == "\n  GROWTH      skipped (--no-git)\n"


def test_report_growth_prints_the_window(capsys):
    measure.print_growth({"git": {
        "available": True, "window": "30 days ago", "delta_loc": 12, "insertions": 20,
        "deletions": 8, "commits": 3, "agent_commits": 1, "agent_insertions": 5,
    }})

    out = capsys.readouterr().out
    assert "+12 LOC over '30 days ago'" in out
    assert "+20 / -8 in 3 commits" in out
    assert "agent trailer" in out


def test_report_scb_check_hints_when_it_can_run(capsys):
    measure.print_scb_check({
        "engine": {"languages_present": ["python"], "scb_check_error": None},
        "scb_check": None,
    })

    assert "Re-run with --scb" in capsys.readouterr().out


def test_report_scb_check_prints_numbers_and_warns_about_coverage(capsys):
    measure.print_scb_check({
        "engine": {"languages_present": ["python"], "scb_check_coverage": 0.4},
        "scb_check": {"total_loc": 100, "verbosity": 0.2, "erosion": 0.3, "cog_erosion": 0.1},
    })

    out = capsys.readouterr().out
    assert "verbosity 0.200" in out and "scanned 100 SLOC" in out
    assert "WARNING: scb-check saw under 60%" in out


def test_report_scb_check_reports_a_failure(capsys):
    measure.print_scb_check({
        "engine": {"languages_present": [], "scb_check_error": "not installed"},
        "scb_check": None,
    })

    assert "scb-check   FAILED: not installed" in capsys.readouterr().out


def test_report_hotspots_flags_an_eroded_function(capsys):
    measure.print_hotspots({"hotspots": [{"file": "a.py", "line": 3, "name": "wide", "cc": 12, "sloc": 40}]})

    assert capsys.readouterr().out == (
        "\n  WORST FUNCTIONS BY MASS (CC > 10 = eroded)\n"
        "    CC   12     40 sloc  a.py:3  wide  <-- eroded\n"
    )


def test_report_hotspots_is_silent_without_hotspots(capsys):
    measure.print_hotspots({"hotspots": []})

    assert capsys.readouterr().out == ""


def test_report_duplicates_prints_the_saving(capsys):
    measure.print_duplicates({"duplicate_blocks": [
        {"lines": 9, "occurrences": 3, "recoverable_lines": 18, "locations": ["a.py:1", "b.py:1"]},
    ]})

    out = capsys.readouterr().out
    assert "9 lines x3, saves   18" in out
    assert "(+1 more)" in out


def test_report_single_use_lists_the_candidates(capsys):
    measure.print_single_use({"granularity": {"single_use_top": [
        {"file": "a.py", "line": 4, "name": "alpha", "cc": 2, "sloc": 6, "uses": 1},
    ]}})

    out = capsys.readouterr().out
    assert "SINGLE-USE CALLABLES" in out
    assert "6 sloc  CC 2    a.py:4  alpha  (uses 1)" in out


def test_print_report_writes_the_whole_report(tmp_path: Path, capsys):
    measure.print_report(measure.build_payload(synthetic_run(tmp_path)))

    out = capsys.readouterr().out
    assert out.startswith(f"SLOP REPORT  {tmp_path}\n")
    for section in ("languages", "VERBOSITY", "EROSION", "GRANULARITY", "GROWTH",
                    "WORST FUNCTIONS", "DUPLICATE BLOCKS", "SINGLE-USE CALLABLES"):
        assert section in out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def test_main_prints_json_and_writes_no_report(repo: Path, capsys):
    assert measure.main([str(repo), "--json", "--no-git"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["root"] == str(repo.resolve())
    assert payload["engine"]["tree_sitter"] is can_parse("python")


def test_main_prints_the_text_report(repo: Path, capsys):
    assert measure.main([str(repo), "--no-git"]) == 0

    assert capsys.readouterr().out.startswith("SLOP REPORT")


def test_main_prints_requirements_and_stops(repo: Path, capsys):
    assert measure.main([str(repo), "--print-requirements", "--no-git"]) == 0

    assert capsys.readouterr().out.strip() == "tree-sitter-python"


def test_main_explains_a_bad_path(tmp_path: Path, capsys):
    assert measure.main([str(tmp_path / "missing")]) == 2

    assert capsys.readouterr().err.startswith("error: no such path:")


def test_main_exits_one_when_erosion_is_over_the_agent_band(tmp_path: Path, monkeypatch, capsys):
    """A reading over the band is a finding, not a crash, so it exits 1."""
    run = synthetic_run(tmp_path)
    monkeypatch.setattr(measure, "run_measurement", lambda target, args: run)
    run.signals.erosion = 0.9

    assert measure.main([str(tmp_path), "--no-git"]) == 1
    assert "EROSION" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# scb-check
# ---------------------------------------------------------------------------


def test_scb_command_is_none_without_either_tool(monkeypatch):
    monkeypatch.setattr(measure.shutil, "which", lambda _name: None)

    assert measure.scb_command() is None


def test_scb_command_prefers_an_installed_check(monkeypatch):
    monkeypatch.setattr(measure.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert measure.scb_command() == ["scb-check"]


def test_json_object_takes_the_last_object_and_ignores_broken_lines():
    assert measure.json_object('noise\n{"a": 1}\n{not json}\n') == {"a": 1}
    assert measure.json_object("no json here\n") is None


def test_run_scb_check_reports_a_missing_tool(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(measure.shutil, "which", lambda _name: None)

    assert measure.run_scb_check(tmp_path) == (None, "neither scb-check nor uvx is on PATH")


def test_run_scb_check_returns_the_report(monkeypatch, tmp_path: Path):
    class Done:
        returncode = 0
        stdout = '{"total_loc": 42}'
        stderr = ""

    monkeypatch.setattr(measure.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(measure.subprocess, "run", lambda *a, **k: Done())

    assert measure.run_scb_check(tmp_path) == ({"total_loc": 42}, None)


def test_run_scb_check_explains_a_silent_failure(monkeypatch, tmp_path: Path):
    class Done:
        returncode = 1
        stdout = ""
        stderr = "boom\n"

    monkeypatch.setattr(measure.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(measure.subprocess, "run", lambda *a, **k: Done())

    report, error = measure.run_scb_check(tmp_path)

    assert report is None
    assert error == "scb-check exited 1 without a JSON report (boom)"


def test_run_scb_check_reports_a_timeout(monkeypatch, tmp_path: Path):
    def boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("scb-check", 5)

    monkeypatch.setattr(measure.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(measure.subprocess, "run", boom)

    _report, error = measure.run_scb_check(tmp_path, timeout=5)

    assert error == "scb-check did not finish within 5s"


# ---------------------------------------------------------------------------
# Git stages
# ---------------------------------------------------------------------------


def test_git_root_finds_the_repository(repo: Path):
    assert measure.git_root(repo / "pkg") == repo.resolve()
    assert measure.git_root(repo) == repo.resolve()


def test_git_root_is_none_outside_a_repository(tmp_path: Path):
    assert measure.git_root(tmp_path) is None


def test_git_stats_reports_the_window(repo: Path):
    stats = measure.git_stats(repo, "30 days ago")

    assert stats.available
    assert stats.total_commits == 1
    assert stats.insertions > 0


def test_git_stats_is_unavailable_outside_a_repository(tmp_path: Path):
    stats = measure.git_stats(tmp_path, "30 days ago")

    assert not stats.available
    assert stats.total_commits == 0


# ---------------------------------------------------------------------------
# Installer stages
# ---------------------------------------------------------------------------


def test_package_dir_is_the_installed_package():
    assert (measure.__file__ is not None)
    assert (install_skill.package_dir() / "measure.py").is_file()


def test_bundled_skill_holds_the_documented_files():
    skill = install_skill.bundled_skill()

    assert (skill / "SKILL.md").is_file()
    assert (skill / "scripts" / "run.sh").is_file()


def test_clear_existing_leaves_a_missing_target_alone(tmp_path: Path):
    install_skill.clear_existing(tmp_path / "nothing", force=False)


def test_clear_existing_refuses_a_live_install(tmp_path: Path):
    target = tmp_path / install_skill.SKILL_NAME
    target.mkdir()

    with pytest.raises(FileExistsError) as caught:
        install_skill.clear_existing(target, force=False)

    assert "--force" in str(caught.value)
    assert target.is_dir()


def test_already_installed_names_the_remedy(tmp_path: Path):
    error = install_skill.already_installed(tmp_path / "skill")

    assert "--force" in str(error)


def test_clear_existing_replaces_with_force(tmp_path: Path):
    target = tmp_path / install_skill.SKILL_NAME
    target.mkdir()
    (target / "stale.txt").write_text("old\n", encoding="utf-8")

    install_skill.clear_existing(target, force=True)

    assert not target.exists()


def test_verify_points_at_the_installed_wrapper(tmp_path: Path):
    result = install_skill.install(tmp_path)

    assert result.verify == f"{result.path}/scripts/run.sh ."