"""Scope: which files a run measures.

A subtree argument has to mean the subtree. Dropping it silently measures the
whole repository, which is how a report ends up describing files the caller
never asked about. These tests build a real git repository so the git-tracked
path through discover() is the one under test, not the directory-walk fallback.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from sloptrack import cli, measure

SOURCE = "def alpha(values):\n    return values\n"
OTHER = "def beta(values):\n    return values\n"
TEST_SOURCE = "def test_alpha():\n    assert True\n"

# The scope tests that look at parsed output need a grammar, like test_grammars.
needs_grammar = pytest.mark.skipif(
    not measure.load_grammars({"python"}),
    reason="the python grammar is not installed; run `sloptrack measure` or CI",
)


def git(*args: str, cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A committed git repository with src/ and tests/ subtrees."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    git("init", "--quiet", "-b", "main", cwd=tmp_path)
    git("config", "user.email", "test@example.com", cwd=tmp_path)
    git("config", "user.name", "Test", cwd=tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text(TEST_SOURCE, encoding="utf-8")
    (tmp_path / "other.rb").write_text(OTHER, encoding="utf-8")
    git("add", "-A", cwd=tmp_path)
    git("commit", "--quiet", "-m", "fixture", cwd=tmp_path)
    return tmp_path


def measured(path, *extra: str) -> dict:
    """Run the analyzer in-process and return the JSON payload."""
    import io
    from contextlib import redirect_stdout

    from sloptrack import measure as measure_module

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = measure_module.main([str(path), "--json", "--no-git", *extra])
    assert code in (0, 1), code
    return json.loads(buf.getvalue())


def test_repository_scope_measures_everything(repo):
    payload = measured(repo)

    assert payload["root"] == str(repo)
    assert payload["sloc"]["files_total"] == 3


def test_subtree_scope_measures_only_that_subtree(repo):
    payload = measured(repo / "src")

    assert [lang for lang in payload["sloc"]["by_language"]] == ["python"]
    assert payload["sloc"]["files_total"] == 1


def test_subtree_scope_is_reported_as_the_scope(repo):
    """The header has to name what was measured, or the report misleads."""
    payload = measured(repo / "src")

    assert payload["root"] == str(repo / "src")


@needs_grammar
def test_test_scope_measures_only_tests(repo):
    payload = measured(repo / "tests")

    assert payload["sloc"]["files_total"] == 1
    assert payload["erosion"]["functions"] == 1


@needs_grammar
def test_paths_in_the_report_are_relative_to_the_scope(repo):
    """Globs match these paths, so they stay scope-relative in both discovery paths."""
    payload = measured(repo / "src", "--top", "5")

    assert {f["file"] for f in payload["hotspots"]} == {"app.py"}


def test_exclude_matches_scope_relative_paths(repo, capsys):
    """The same glob excludes the file with git listing and with the walk fallback."""
    assert cli.main(["measure", str(repo / "src"), "--no-uvx", "--no-git", "--exclude", "app.py"]) == 2

    assert "no recognized source files" in capsys.readouterr().err


def test_exclude_drops_a_subtree_from_a_repository_scope(repo):
    payload = measured(repo, "--exclude", "tests/*")

    assert payload["sloc"]["files_total"] == 2


def test_a_subtree_with_no_sources_names_the_scope(repo, capsys):
    empty = repo / "empty"
    empty.mkdir()

    assert cli.main(["measure", str(empty), "--no-uvx", "--no-git"]) == 2
    err = capsys.readouterr().err
    assert "no recognized source files" in err
    assert str(empty) in err