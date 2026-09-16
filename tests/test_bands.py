"""The published bands, and the strings the report prints for them.

These numbers are the claim the report makes. A silent edit here would change
every reading, so pin the values and the boundary behavior.
"""

from __future__ import annotations

from sloptrack import measure


def test_bands_are_the_published_values():
    python = measure.bands_for("python")
    assert python["verbosity"] == {
        "human": 0.15, "human_sd": 0.06, "agent": 0.33, "agent_sd": 0.10,
    }
    assert python["erosion"] == {
        "human": 0.31, "human_sd": 0.17, "agent": 0.68, "agent_sd": 0.20,
    }
    assert python["granularity"] == {
        "human": 0.27, "human_sd": 0.13, "agent": None, "agent_sd": None,
    }


def test_c_bands_are_the_measured_panel_values():
    """Pinned so an edit to the C panel numbers is a deliberate one.

    Measured on 11 maintained C repositories at or above 60% coverage, whole
    tree, taken from the panel recorded in REFERENCE.md. The `agent` figure is
    the worst maintained repository in that panel, not an agent sample: there
    are no C agent runs to measure.
    """
    c = measure.bands_for("c")
    assert c["verbosity"] == {
        "human": 0.13, "human_sd": 0.11, "agent": 0.43, "agent_sd": None,
    }
    assert c["erosion"] == {
        "human": 0.69, "human_sd": 0.22, "agent": 0.97, "agent_sd": None,
    }
    assert c["granularity"] == {
        "human": 0.40, "human_sd": 0.09, "agent": None, "agent_sd": None,
    }


def test_the_c_family_only_applies_to_c_shaped_repos():
    """A vendored C file next to a Python tree must not switch the bands."""
    assert measure.FAMILY_LANGS["c"] == frozenset({"c", "cpp"})
    assert measure.REFERENCE_FAMILY == "python"
    assert measure.bands_for("klingon") is measure.bands_for("python")


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


def test_granularity_has_no_agent_band_in_either_family():
    assert measure.bands_for("python")["granularity"]["agent"] is None
    assert measure.bands_for("c")["granularity"]["agent"] is None


def test_ratio_reports_the_multiple_of_the_reference_value():
    assert measure.ratio(0.30, "verbosity") == "2.00x the reference"
    assert measure.ratio(0.0, "verbosity") == "0.00x the reference"
    # The same reading is a different multiple under the C panel.
    assert measure.ratio(0.31, "erosion", "c") == "0.45x the reference"


def test_c_bands_read_the_c_panel_boundaries():
    assert measure.band(0.13, "verbosity", "c") == "at-or-below human baseline"
    assert measure.band(0.14, "verbosity", "c") == "between human and agent bands"
    assert measure.band(0.43, "verbosity", "c") == "in or above agent band"
    # A reading can sit below the C baseline and above the Python one, which is
    # the mismatch the C family exists to avoid: 0.66 erosion is agent-grade for
    # a Python panel and ordinary for the C one.
    assert measure.band(0.66, "erosion", "python") == "between human and agent bands"
    assert measure.band(0.66, "erosion", "c") == "at-or-below human baseline"
    assert measure.band(0.97, "erosion", "c") == "in or above agent band"
    assert measure.granularity_band(0.30, "c") == "below human band"
    assert measure.granularity_band(0.40, "c") == "within human band"
    assert measure.granularity_band(0.50, "c") == "above human band"
    assert measure.granularity_band(0.30, "python") == "within human band"
