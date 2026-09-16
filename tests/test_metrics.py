"""Metric math, checked without a parser.

compute_signals takes functions and file analyses, so the three metrics can be
pinned on hand-built inputs. The fixture corpus in check_languages.py covers the
vocabulary; these cover the arithmetic.
"""

from __future__ import annotations

from pathlib import Path

from sloptrack import measure


def func(name: str, cc: int, sloc: int) -> measure.Func:
    return measure.Func(name=name, file="a.py", lang="python", line=1, cc=cc, sloc=sloc)


def analysis(rel: str = "a.py", **kwargs) -> measure.FileAnalysis:
    entry = measure.FileEntry(path=Path(rel), rel=rel, lang=measure.LANGS[0])
    return measure.FileAnalysis(
        entry=entry,
        sloc=kwargs.pop("sloc", 10),
        comment_rows=kwargs.pop("comment_rows", set()),
        **kwargs,
    )


def test_erosion_is_mass_weighted_not_a_head_count():
    # Two functions: one eroded at CC 12, one simple at CC 1 but three times the
    # length. Mass is CC * sqrt(SLOC), so the long simple one still outweighs it.
    small = func("tangled", cc=12, sloc=4)  # 12 * 2 = 24
    big = func("long", cc=1, sloc=144)  # 1 * 12 = 12
    signals = measure.compute_signals([small, big], [analysis(functions=[small, big])])

    assert signals.total_mass == 36
    assert signals.high_mass == 24
    assert signals.erosion == 24 / 36
    assert [f.name for f in signals.high_cc] == ["tangled"]


def test_erosion_threshold_is_strictly_above_ten():
    ten = func("ten", cc=10, sloc=4)
    eleven = func("eleven", cc=11, sloc=4)
    signals = measure.compute_signals([ten, eleven], [analysis(functions=[ten, eleven])])

    assert [f.name for f in signals.high_cc] == ["eleven"]
    assert signals.erosion == 11 / 21


def test_erosion_is_none_without_functions():
    assert measure.compute_signals([], []).erosion is None


def test_granularity_counts_only_callables_with_a_call_site():
    once = func("once", cc=1, sloc=4)
    twice = func("twice", cc=1, sloc=4)
    entry_point = func("main", cc=1, sloc=4)
    signals = measure.compute_signals(
        [once, twice, entry_point],
        [analysis(functions=[once, twice, entry_point], references={"once": 1, "twice": 2})],
    )

    assert (once.uses, twice.uses, entry_point.uses) == (1, 2, 0)
    assert [f.name for f in signals.single_use_functions] == ["once"]
    assert [f.name for f in signals.reused_functions] == ["twice"]
    assert [f.name for f in signals.unused_functions] == ["main"]
    assert signals.granularity == 0.5


def test_granularity_is_none_when_nothing_is_called():
    """A callable with no call site is not counted, so the ratio can be empty."""
    lonely = func("lonely", cc=1, sloc=4)
    signals = measure.compute_signals([lonely], [analysis(functions=[lonely])])

    assert signals.granularity is None
    assert signals.unused_functions == [lonely]


def test_references_are_summed_across_files_by_name():
    once = func("shared", cc=1, sloc=4)
    other = func("shared", cc=1, sloc=4)
    signals = measure.compute_signals(
        [once, other],
        [
            analysis("a.py", functions=[once], references={"shared": 1}),
            analysis("b.py", functions=[other], references={"shared": 1}),
        ],
    )

    assert once.uses == 2 and other.uses == 2
    assert signals.granularity == 0.0


def test_clone_lines_need_two_members_and_a_minimum_span():
    too_short = [("digest-b", 0, 1), ("digest-b", 20, 21)]
    alone = [("digest-c", 0, 40)]
    first = analysis("a.py", clone_candidates=[("digest-a", 0, 5)] + too_short + alone)
    second = analysis("b.py", clone_candidates=[("digest-a", 0, 5)])

    signals = measure.compute_signals([], [first, second])

    # Only digest-a qualifies: digest-b is under MIN_CLONE_LINES, digest-c is alone.
    assert signals.groups["digest-a"] == [("a.py", 0, 5), ("b.py", 0, 5)]
    assert signals.clone_lines == 12


def test_clone_lines_exclude_comment_rows():
    pair = [("digest-a", 0, 5), ("digest-a", 10, 15)]
    first = analysis("a.py", clone_candidates=pair, comment_rows={0, 1})
    second = analysis("b.py", clone_candidates=pair, comment_rows={10})

    signals = measure.compute_signals([], [first, second])

    # 24 duplicate rows across both files, minus three rows that are comments.
    assert signals.clone_lines == 24 - 3
