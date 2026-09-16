"""The skill installer: copy, link, refusal, and the payload that ships.

The installed copy has to work on its own, because the documented commands in
SKILL.md run scripts from the skills directory rather than the `sloptrack`
command. The last test proves that by running the installed analyzer as a script.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from sloptrack import install_skill

SKILL_FILES = ["SKILL.md", "REFERENCE.md", "scripts/run.sh", "scripts/check_languages.sh"]
ANALYZER_FILES = ["scripts/slop_measure.py", "scripts/check_languages.py"]


def test_the_package_ships_the_skill_payload():
    source = install_skill.bundled_skill()

    assert source.is_dir()
    assert (source / "SKILL.md").is_file()
    assert (source / "REFERENCE.md").is_file()
    for script in ("run.sh", "check_languages.sh"):
        assert (source / "scripts" / script).is_file()


def test_skill_frontmatter_names_the_skill():
    text = (install_skill.bundled_skill() / "SKILL.md").read_text(encoding="utf-8")
    head = text.split("---")[1]

    assert f"name: {install_skill.SKILL_NAME}" in head
    assert "description:" in head


def test_copy_install_writes_a_standalone_tree(tmp_path):
    result = install_skill.install(tmp_path)

    assert result.mode == "copy"
    assert result.path == tmp_path / install_skill.SKILL_NAME
    for rel in SKILL_FILES + ANALYZER_FILES:
        assert (result.path / rel).is_file(), rel
    for script in (result.path / "scripts").glob("*.sh"):
        assert script.stat().st_mode & 0o111, script


def test_copy_install_can_be_repeated_with_force(tmp_path):
    install_skill.install(tmp_path)
    marker = tmp_path / install_skill.SKILL_NAME / "SKILL.md"
    marker.write_text("edited by the user", encoding="utf-8")

    with pytest.raises(FileExistsError):
        install_skill.install(tmp_path)
    assert marker.read_text(encoding="utf-8") == "edited by the user"

    install_skill.install(tmp_path, force=True)
    assert "measure-then-fix-slop" in marker.read_text(encoding="utf-8")


def test_force_replaces_a_symlink(tmp_path):
    stale = tmp_path / "elsewhere"
    stale.mkdir()
    target = tmp_path / install_skill.SKILL_NAME
    target.symlink_to(stale, target_is_directory=True)

    result = install_skill.install(tmp_path, force=True)

    assert not target.is_symlink()
    assert (result.path / "SKILL.md").is_file()


def test_a_dangling_symlink_is_replaced_without_force(tmp_path, capsys):
    """A moved checkout leaves a symlink to nothing, and the loader then reports a
    missing SKILL.md. That is not an install worth protecting."""
    target = tmp_path / install_skill.SKILL_NAME
    target.symlink_to(tmp_path / "moved-away", target_is_directory=True)
    assert not target.exists()

    result = install_skill.install(tmp_path)

    assert "broken symlink" in capsys.readouterr().err
    assert not result.path.is_symlink()
    assert (result.path / "SKILL.md").is_file()


def test_a_link_install_heals_a_dangling_link(tmp_path):
    target = tmp_path / install_skill.SKILL_NAME
    target.symlink_to(tmp_path / "moved-away", target_is_directory=True)

    result = install_skill.install(tmp_path, link=True)

    assert result.path.is_symlink()
    assert (result.path / "SKILL.md").is_file()


def test_the_installed_skill_exposes_skill_md_at_its_root(tmp_path):
    """The loader opens <skill>/SKILL.md, so the payload cannot nest a level deeper."""
    result = install_skill.install(tmp_path)
    entry = result.path / "SKILL.md"

    assert entry.is_file()
    assert entry.read_text(encoding="utf-8").startswith("---")


def test_link_install_points_at_the_checkout(tmp_path):
    result = install_skill.install(tmp_path, link=True)

    assert result.mode == "link"
    assert result.path.is_symlink()
    assert result.path.resolve() == install_skill.bundled_skill().resolve()


def test_link_is_refused_outside_a_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(install_skill, "package_dir", lambda: tmp_path / "site-packages" / "sloptrack")

    with pytest.raises(RuntimeError, match="source checkout"):
        install_skill.install(tmp_path, link=True)


def test_the_installed_analyzer_runs_as_a_script(tmp_path):
    """SKILL.md calls scripts/slop_measure.py directly, so it must stand alone."""
    result = install_skill.install(tmp_path)
    source = tmp_path / "target"
    source.mkdir()
    (source / "sample.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(result.path / "scripts" / "slop_measure.py"),
            str(source),
            "--no-git",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode in (0, 1), proc.stderr
    assert '"files_total": 1' in proc.stdout


def test_install_skill_main_reports_the_path(tmp_path, capsys):
    assert install_skill.main(["--dest", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert str(tmp_path / install_skill.SKILL_NAME) in out
    assert "run.sh" in out


def test_install_skill_main_fails_on_conflict(tmp_path, capsys):
    install_skill.main(["--dest", str(tmp_path)])
    capsys.readouterr()

    assert install_skill.main(["--dest", str(tmp_path)]) == 2
    assert "--force" in capsys.readouterr().err
