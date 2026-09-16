"""The known-answer corpus, run in-process.

These are the tests that catch grammar drift: a node renamed upstream, a
function-name field that moved. They only run where the grammar packages are
importable, so CI installs them through uvx and the plain `pytest` run skips.
"""

from __future__ import annotations

import pytest

from sloptrack import check_languages, measure

LANGUAGES = [lang.name for lang in measure.LANGS]


def _grammars() -> dict:
    try:
        return measure.load_grammars(set(LANGUAGES))
    except Exception:  # noqa: BLE001 - no tree-sitter at all is a skip, not a failure
        return {}


GRAMMARS = _grammars()
AVAILABLE = set(GRAMMARS)
needs_every_grammar = pytest.mark.skipif(
    AVAILABLE != set(LANGUAGES),
    reason="grammar packages are not installed; run `sloptrack check-languages`",
)


def test_every_language_has_a_grammar_and_a_fixture():
    assert set(LANGUAGES) == set(check_languages.SAMPLES)


def test_print_requirements_lists_grammar_packages(capsys):
    """The wrapper asks this before anything is installed, so it must not import grammars."""
    assert check_languages.main(["--print-requirements"]) == 0
    packages = capsys.readouterr().out.split()

    assert packages == measure.grammar_packages({lang.name for lang in measure.LANGS})
    assert "tree-sitter-python" in packages


def test_print_requirements_narrows_to_the_requested_languages(capsys):
    assert check_languages.main(["--print-requirements", "--lang", "python", "--lang", "bash"]) == 0

    assert capsys.readouterr().out.split() == ["tree-sitter-bash", "tree-sitter-python"]


def test_print_requirements_ignores_an_unknown_language(capsys):
    """An unknown name is reported by the check itself, not by the package list."""
    assert check_languages.main(["--print-requirements", "--lang", "nope"]) == 0

    assert capsys.readouterr().out.strip() == ""


@needs_every_grammar
def test_the_whole_corpus_passes(capsys):
    assert check_languages.main([]) == 0

    out = capsys.readouterr().out
    assert f"{len(LANGUAGES)} passed, 0 failed" in out


@needs_every_grammar
@pytest.mark.parametrize("name", LANGUAGES)
def test_each_language_passes_on_its_own(name):
    """A per-language failure names the culprit instead of one aggregate line."""
    cfg = {lang.name: lang for lang in measure.LANGS}[name]
    passed, detail, analysis = check_languages.check_language(name, cfg, GRAMMARS)

    assert passed, detail
    assert analysis is not None and analysis.functions


@needs_every_grammar
def test_fixture_call_counts_are_measured():
    """Granularity depends on counting call sites, so assert both halves of it.

    The base fixture must not call its own functions, and the checker's appended
    two-call fixture must count one call for alpha and two or more for beta.
    """
    cfg = {lang.name: lang for lang in measure.LANGS}["python"]

    passed, detail, analysis = check_languages.check_language("python", cfg, GRAMMARS)

    assert passed
    assert analysis.references == {}, "the base fixture must not call its own functions"
    assert "uses alpha 1/beta 2" in detail
