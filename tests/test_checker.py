"""The checker's stages, unit tested on their own.

`check_languages` is the file that decides whether a language's vocabulary still
matches its grammar, so its stages are worth testing past the end-to-end run in
test_grammars.py. The pure ones run anywhere; the ones that need a parser are
gated with the rest of the grammar tests.
"""

from __future__ import annotations

from sloptrack import check_languages, measure

PYTHON = "python"


def cfg_for(name: str) -> measure.Lang:
    return {lang.name: lang for lang in measure.LANGS}[name]


def analysis(
    functions: list[measure.Func],
    references: dict[str, int] | None = None,
    clone_candidates: list[tuple[str, int, int]] | None = None,
):
    """A FileAnalysis carrying only what the findings look at."""
    entry = measure.FileEntry(path=__file__, rel="fixture.py", lang=cfg_for(PYTHON), text="")
    return measure.FileAnalysis(
        entry=entry,
        sloc=1,
        comment_rows=set(),
        functions=functions,
        references=references or {},
        clone_candidates=clone_candidates or [],
        parsed=True,
    )


def clone_pair(span: int = 6) -> list[tuple[str, int, int]]:
    """Two members of one clone group, long enough for the checker to accept it."""
    return [("digest", 0, span - 1), ("digest", 20, 20 + span - 1)]


def func(name: str, cc: int = 1) -> measure.Func:
    return measure.Func(name=name, file="fixture.py", lang=PYTHON, line=1, cc=cc, sloc=1)


def fixture(**overrides) -> check_languages.Fixture:
    """A passing Python fixture, with any field replaced for one test."""
    base = dict(
        name=PYTHON,
        cfg=cfg_for(PYTHON),
        filename="sample.py",
        base=analysis([func("alpha"), func("beta")], clone_candidates=clone_pair()),
        min_functions=2,
        min_cc=1,
        mutated_span=check_languages.MIN_FUNCTION_CLONE_SPAN,
        literal_status=check_languages.LITERAL_OK,
    )
    base.update(overrides)
    return check_languages.Fixture(**base)


def test_shortfall_reports_only_a_short_measurement():
    assert check_languages.shortfall("max CC", 1, 3, "why") == "max CC 1, expected >= 3 (why)"
    assert check_languages.shortfall("max CC", 3, 3, "why") is None
    assert check_languages.shortfall("max CC", 9, 3, "why") is None


def test_unexpected_reads_as_a_guard():
    assert check_languages.unexpected(True, "broke") == "broke"
    assert check_languages.unexpected(False, "broke") is None


def test_a_shortfall_becomes_a_viable_finding():
    """shortfall returns None on success so a findings list can filter it out."""
    findings = [f for f in (check_languages.shortfall("max CC", 4, 3, "why"),) if f is not None]
    assert findings == []


def test_declaration_calls_ignores_zero_counts():
    block = analysis([func("alpha"), func("beta")], references={"alpha": 2, "beta": 0})
    assert check_languages.declaration_calls(block) == {"alpha": 2}


def test_blob_name_flags_a_name_that_is_really_text():
    assert check_languages.blob_name([func("alpha")]) == ""
    assert check_languages.blob_name([func("def f() {\n  x")]).startswith("def f()")


def test_blob_name_truncates_a_long_name():
    assert len(check_languages.blob_name([func("x" * 100)])) == 40


def test_findings_are_empty_for_a_fixture_that_measured_everything():
    assert check_languages.fixture_findings(fixture()) == []


def test_findings_name_every_shortfall_at_once():
    findings = check_languages.fixture_findings(
        fixture(min_functions=5, min_cc=9, mutated_span=1)
    )
    assert len(findings) == 3
    assert any("named function(s)" in f for f in findings)
    assert any("max CC" in f for f in findings)
    assert any("literals are not normalized" in f for f in findings)


def test_findings_explain_a_literal_check_that_could_not_run():
    findings = check_languages.fixture_findings(fixture(literal_status=check_languages.LITERAL_NONE))
    assert any("no numeric literal" in f for f in findings)


def test_findings_prefer_the_error_when_re_analysis_raised():
    findings = check_languages.fixture_findings(
        fixture(literal_status=check_languages.LITERAL_ERROR, literal_detail="raised ValueError")
    )
    assert findings[0] == "raised ValueError"


def test_findings_include_a_call_counting_problem():
    findings = check_languages.fixture_findings(
        fixture(call_findings=("call counting: expected alpha called once, got 3",))
    )
    assert findings == ["call counting: expected alpha called once, got 3"]


def test_select_languages_maps_names_to_configs():
    chosen = check_languages.select_languages([PYTHON, "ruby"])
    assert set(chosen) == {PYTHON, "ruby"}
    assert chosen[PYTHON].name == PYTHON


def test_select_languages_drops_a_name_it_cannot_map():
    assert check_languages.select_languages(["nope"]) == {}


def test_selection_problems_report_an_unknown_name_and_point_at_the_fix():
    problems = check_languages.selection_problems(["elm"])
    assert len(problems) == 1
    assert "no fixture for elm" in problems[0]
    assert "SAMPLES in check_languages.py" in problems[0]


def test_selection_problems_names_a_language_missing_from_the_table():
    """A fixture with no LANGS entry cannot be checked, and says which side is wrong."""
    problems = check_languages.selection_problems([PYTHON])
    assert problems == []


def test_selection_problems_are_empty_for_a_known_language():
    assert check_languages.selection_problems(sorted(check_languages.SAMPLES)) == []


def test_unfixtured_languages_finds_table_entries_with_no_fixture(monkeypatch):
    extra = measure.Lang(
        name="nofix", exts=frozenset({".nf"}), filenames=frozenset(), grammar="tree-sitter-nofix",
    )
    monkeypatch.setattr(measure, "LANGS", (*measure.LANGS, extra))
    assert check_languages.unfixtured_languages() == ["nofix"]


def test_report_unverified_prints_one_line_per_entry(capsys):
    assert check_languages.report_unverified(["nofix", "other"]) == 2
    out = capsys.readouterr().out
    assert "nofix" in out and "other" in out
    assert "2 table entr" in out


def test_report_unverified_is_silent_with_nothing_to_report(capsys):
    assert check_languages.report_unverified([]) == 0
    assert capsys.readouterr().out == ""


def test_print_requirements_needs_no_grammar(capsys):
    assert check_languages.print_requirements([PYTHON]) == 0
    assert capsys.readouterr().out.split() == ["tree-sitter-python"]


def test_mutate_first_copy_changes_only_the_first_half():
    source = "value = 1\nother = 2\nvalue = 1\nother = 2\n"
    mutated = check_languages.mutate_first_copy(source)
    first, second = mutated.splitlines()[:2], mutated.splitlines()[2:]
    assert first == ["value = 8", "other = 9"]
    assert second == ["value = 1", "other = 2"]


def test_mutate_first_copy_leaves_an_unmutated_source_alone():
    assert check_languages.mutate_first_copy("no numbers here\n") == "no numbers here\n"