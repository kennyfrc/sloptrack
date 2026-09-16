"""The numbers the docs promise, checked against the numbers the code uses.

A documented threshold that drifts from the constant is worse than no
documentation, because the report and the prose then disagree.
"""

from __future__ import annotations

from pathlib import Path

from sloptrack import measure

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SKILL = ROOT / "src" / "sloptrack" / "skill" / "SKILL.md"
REFERENCE = ROOT / "src" / "sloptrack" / "skill" / "REFERENCE.md"


def test_every_exit_code_is_documented():
    for name, path in (("README.md", README), ("SKILL.md", SKILL)):
        text = path.read_text(encoding="utf-8")
        for code in ("`0`", "`1`", "`2`", "`3`"):
            assert code in text, f"{name} does not document exit code {code}"


def test_the_coverage_gate_is_documented():
    percent = f"{measure.LOW_COVERAGE:.0%}"
    for path in (README, REFERENCE):
        assert percent in path.read_text(encoding="utf-8"), f"{path} omits {percent}"


def test_the_published_bands_are_documented():
    """Every band the report compares against has to appear in REFERENCE.md."""
    text = REFERENCE.read_text(encoding="utf-8")
    for signal, band in measure.BANDS.items():
        assert f"{band['human']:.2f}" in text, f"{signal} human band is missing"
        if band["agent"] is not None:
            assert f"{band['agent']:.2f}" in text, f"{signal} agent band is missing"


def test_the_high_complexity_cutoff_is_documented():
    assert str(measure.HIGH_CC) in REFERENCE.read_text(encoding="utf-8")
