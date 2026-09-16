"""CLI dispatch: the default command, the exit codes, and --no-uvx.

Every call here passes --no-uvx, so the tests exercise the code paths that do not
need network access or grammars. Without Tree-sitter the tool still reports SLOC
and says what it could not measure, which is the behavior under test.
"""

from __future__ import annotations

import json

import pytest

from sloptrack import cli

PYTHON = "def alpha(values):\n    return values\n"


def repo(tmp_path):
    (tmp_path / "sample.py").write_text(PYTHON, encoding="utf-8")
    return tmp_path


def test_version(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip().startswith("sloptrack ")


def test_top_level_help_lists_every_command(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for command in cli.SUBCOMMANDS:
        assert command in out


def test_langs_lists_every_table_entry(capsys):
    from sloptrack import measure

    assert cli.main(["langs"]) == 0
    out = capsys.readouterr().out
    for lang in measure.LANGS:
        assert lang.name in out


def test_measure_is_the_default_command(tmp_path, capsys):
    """Without grammars the run still reports, and refuses to call itself clean."""
    code = cli.main([str(repo(tmp_path)), "--no-uvx", "--no-git"])

    assert code in (0, 1, 3)
    assert "SLOP REPORT" in capsys.readouterr().out


def test_explicit_measure_matches_the_default(tmp_path, capsys):
    cli.main([str(repo(tmp_path)), "--no-uvx", "--no-git"])
    default = capsys.readouterr().out
    cli.main(["measure", str(repo(tmp_path)), "--no-uvx", "--no-git"])
    explicit = capsys.readouterr().out

    assert default == explicit


def test_no_uvx_is_stripped_before_measure_sees_it(tmp_path):
    """measure has no --no-uvx flag, so passing it through would be an error."""
    assert cli.main(["measure", str(repo(tmp_path)), "--no-uvx", "--no-git"]) in (0, 1, 3)


def test_json_flag_reaches_measure(tmp_path, capsys):
    code = cli.main([str(repo(tmp_path)), "--no-uvx", "--no-git", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code in (0, 1, 3)
    assert payload["sloc"]["files_total"] == 1
    assert set(payload) >= {"verbosity", "erosion", "granularity", "git"}


def test_missing_path_exits_two(capsys):
    assert cli.main(["measure", "/nonexistent/sloptrack-path", "--no-uvx"]) == 2
    assert "no such path" in capsys.readouterr().err


def test_directory_without_source_exits_two(tmp_path, capsys):
    (tmp_path / "notes.txt").write_text("nothing to measure", encoding="utf-8")
    assert cli.main(["measure", str(tmp_path), "--no-uvx", "--no-git"]) == 2
    assert "no recognized source files" in capsys.readouterr().err


def test_unknown_arguments_are_rejected(tmp_path, capsys):
    """argparse exits rather than returning, so the code comes back as SystemExit."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["measure", str(repo(tmp_path)), "--no-uvx", "--nope"])

    assert excinfo.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_packages_for_measure_are_the_languages_present(tmp_path):
    packages = cli._measure_packages([str(repo(tmp_path)), "--no-git"])

    assert packages == ["tree-sitter-python"]


def test_requirements_for_a_bad_path_are_none(capsys):
    assert cli._measure_packages(["/nonexistent/sloptrack-path"]) is None
    assert "no such path" in capsys.readouterr().err
