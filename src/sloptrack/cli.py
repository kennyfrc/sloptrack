"""Command line entry point.

Four commands, and a default: anything that is not a known command is a path or
a flag for `measure`, so `sloptrack .` and `sloptrack --json .` both work.

`measure` and `check-languages` need Tree-sitter grammars for the languages they
parse. When `uvx` is on PATH they re-run themselves under a throwaway uvx
environment carrying exactly those grammars, so nothing is installed into the
caller's project. `--no-uvx` skips that and uses whatever the current interpreter
can already import.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import __version__, install_skill, measure

TREE_SITTER_VERSION = "0.25.2"
SUBCOMMANDS = ("measure", "check-languages", "langs", "install-skill")
# Set on the child process so the wrapped copy does not wrap itself again.
UVX_FLAG_ENV = "SLOPTRACK_IN_UVX"

USAGE = """\
sloptrack: measure the slop in a codebase

  sloptrack measure [PATH] [flags]     the three metrics (this is the default)
  sloptrack check-languages [flags]    verify each grammar against a fixture
  sloptrack langs                      list supported languages and grammars
  sloptrack install-skill [flags]      install the agent skill into ~/.agents/skills
  sloptrack --version

  sloptrack [PATH] [flags]             the same as `sloptrack measure`

Every command passes --no-uvx through to run in the current environment.
Run `sloptrack measure --help` for the measurement flags.
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv[:1] in (["-V"], ["--version"]):
        print(f"sloptrack {__version__}")
        return 0
    if argv[:1] in (["-h"], ["--help"]):
        print(USAGE, end="")
        return 0

    if argv[:1] and argv[0] in SUBCOMMANDS:
        command, rest = argv[0], argv[1:]
    else:
        command, rest = "measure", argv

    if command == "measure":
        return _wrapped(measure, rest, _measure_packages)
    if command == "check-languages":
        return _wrapped(check_languages(), rest, lambda _argv: list(measure.grammar_packages(
            {lang.name for lang in measure.LANGS}
        )))
    if command == "langs":
        return _langs()
    return install_skill.main(rest)


def check_languages():
    """Import the checker lazily: it is only needed by one command."""
    from . import check_languages as module

    return module


# ---------------------------------------------------------------------------
# uvx wrapping
# ---------------------------------------------------------------------------


def _wrapped(module, argv: list[str], packages_for: Callable[[list[str]], list[str] | None]) -> int:
    """Run a module's main() in an environment that has its grammars.

    Returns the module's own exit code, or uvx's when the run is delegated.
    """
    no_uvx = "--no-uvx" in argv
    argv = [arg for arg in argv if arg != "--no-uvx"]

    if no_uvx or not shutil.which("uvx") or os.environ.get(UVX_FLAG_ENV) == "1":
        return module.main(argv)

    packages = packages_for(argv)
    if packages is None:
        return 2  # the module already explained the problem on stderr
    return _under_uvx(Path(module.__file__), packages, argv)


def _measure_packages(argv: list[str]) -> list[str] | None:
    """The grammar packages this measurement needs, or None if the path is bad."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = measure.main(["--print-requirements", *argv])
    if code != 0:
        return None
    return buf.getvalue().split()


def _under_uvx(script: Path, packages: list[str], args: list[str]) -> int:
    command = ["uvx", "--quiet", "--with", f"tree-sitter=={TREE_SITTER_VERSION}"]
    for package in packages:
        command += ["--with", package]
    command += ["python3", str(script), *args]

    env = dict(os.environ, **{UVX_FLAG_ENV: "1"})
    try:
        return subprocess.call(command, env=env)
    except FileNotFoundError:
        print("error: uvx disappeared between the check and the run", file=sys.stderr)
        return 2


# ---------------------------------------------------------------------------
# langs
# ---------------------------------------------------------------------------


def _langs() -> int:
    rows = [
        (lang.name, " ".join(sorted(lang.exts | lang.filenames)), lang.grammar or "-")
        for lang in measure.LANGS
    ]
    width = max(len(row[0]) for row in rows)
    print(f"{'language':<{width}}  {'extensions':<28}  grammar package")
    for name, extensions, grammar in rows:
        print(f"{name:<{width}}  {extensions:<28}  {grammar}")
    print(f"\n{len(rows)} languages. Add one by appending a LANGS entry and a fixture.")
    return 0
