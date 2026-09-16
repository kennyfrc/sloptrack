"""Install the measure-then-fix-slop agent skill.

The skill is a directory an agent reads: SKILL.md for the workflow, REFERENCE.md
for the method, and a scripts directory it can run. Copying is the default, and
the copy is self-contained: the analyzer and the language checker are written
next to the shell wrappers, so the documented paths keep working without the
`sloptrack` command being installed.

`--link` points the skills directory at a source checkout instead, which keeps
edits live. It needs a checkout, so it cannot work from a wheel.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

SKILL_NAME = "measure-then-fix-slop"
# The installed names are the documented ones: run.sh calls slop_measure.py.
PACKAGE_FILES = {
    "measure.py": "slop_measure.py",
    "check_languages.py": "check_languages.py",
}
DEFAULT_SKILLS_DIR = "~/.agents/skills"
SKILLS_DIR_ENV = "SLOPTRACK_SKILLS_DIR"


@dataclass
class Install:
    """What was written, so the caller can report and check it."""

    path: Path
    mode: str  # "copy" or "link"
    source: Path

    @property
    def verify(self) -> str:
        return f"{self.path}/scripts/run.sh ."


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def bundled_skill() -> Path:
    """The skill payload shipped inside the package."""
    return package_dir() / "skill"


def checkout_skill_dir() -> Path:
    """The skill directory of a source checkout, for --link."""
    root = package_dir().parent.parent
    if not (root / "pyproject.toml").exists():
        raise RuntimeError(
            f"--link needs a source checkout, and {root} is not one. "
            "Run it from a clone of the repository, or drop --link to install a copy."
        )
    return package_dir() / "skill"


def install(dest_root: str | Path, *, link: bool = False, force: bool = False) -> Install:
    """Install the skill under `dest_root`, replacing an existing one only with force."""
    source = checkout_skill_dir() if link else bundled_skill()
    if not source.is_dir():
        raise RuntimeError(f"skill payload is missing: {source}")

    target = Path(dest_root).expanduser() / SKILL_NAME
    if target.is_symlink() or target.exists():
        if not force:
            raise FileExistsError(
                f"{target} already exists. Re-run with --force to replace it."
            )
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    if link:
        target.symlink_to(source, target_is_directory=True)
        return Install(path=target, mode="link", source=source)

    shutil.copytree(source, target)
    for packaged, installed in PACKAGE_FILES.items():
        shutil.copy2(package_dir() / packaged, target / "scripts" / installed)
    for script in (target / "scripts").glob("*.sh"):
        script.chmod(script.stat().st_mode | 0o111)
    return Install(path=target, mode="copy", source=source)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sloptrack install-skill",
        description="Install the measure-then-fix-slop skill so an agent can read it.",
    )
    ap.add_argument(
        "--dest",
        default=os.environ.get(SKILLS_DIR_ENV) or DEFAULT_SKILLS_DIR,
        help=f"skills directory to install into (default: {DEFAULT_SKILLS_DIR})",
    )
    ap.add_argument(
        "--link",
        action="store_true",
        help="symlink a source checkout instead of copying, so edits are live",
    )
    ap.add_argument("--force", action="store_true", help="replace an existing install")
    args = ap.parse_args(argv)

    try:
        result = install(args.dest, link=args.link, force=args.force)
    except (FileExistsError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"installed the {SKILL_NAME} skill ({result.mode}) at {result.path}")
    print(f"verify with: {result.verify}")
    return 0
