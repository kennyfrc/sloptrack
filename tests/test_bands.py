"""The published bands, and the strings the report prints for them.

These numbers are the claim the report makes. A silent edit here would change
every reading, so pin the values and the boundary behavior.
"""

from __future__ import annotations

from sloptrack import measure


def test_bands_are_the_published_values():
    assert measure.BANDS["verbosity"] == {
        "human": 0.15, "human_sd": 0.06, "agent": 0.33, "agent_sd": 0.10,
    }
    assert measure.BANDS["erosion"] == {
        "human": 0.31, "human_sd": 0.17, "agent": 0.68, "agent_sd": 0.20,
    }
    assert measure.BANDS["granularity"] == {
        "human": 0.27, "human_sd": 0.13, "agent": None, "agent_sd": None,
    }


def test_verbosity_band_boundaries():
    assert measure.band(0.14, "verbosity") == "at-or-below human baseline"
    assert measure.band(0.15, "verbosity") == "at-or-below human baseline"
    assert measure.band(0.16, "verbosity") == "between human and agent bands"
    assert measure.band(0.32, "verbosity") == "between human and agent bands"
    assert measure.band(0.33, "verbosity") == "in or above agent band"


def test_erosion_band_boundaries():
    assert measure.band(0.31, "erosion") == "at-or-below human baseline"
    assert measure.band(0.5, "erosion") == "between human and agent bands"
    assert measure.band(0.68, "erosion") == "in or above agent band"


def test_granularity_is_a_band_not_a_floor():
    """Below the band is a failure too: too few named steps, not a better score."""
    assert measure.granularity_band(0.13) == "below human band"
    assert measure.granularity_band(0.14) == "within human band"
    assert measure.granularity_band(0.27) == "within human band"
    assert measure.granularity_band(0.40) == "within human band"
    assert measure.granularity_band(0.41) == "above human band"


def test_granularity_has_no_agent_band():
    assert measure.BANDS["granularity"]["agent"] is None


def test_ratio_reports_the_multiple_of_the_human_baseline():
    assert measure.ratio(0.30, "verbosity") == "2.00x the human baseline"
    assert measure.ratio(0.0, "verbosity") == "0.00x the human baseline"
