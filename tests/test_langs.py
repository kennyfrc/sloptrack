"""The language table, and the invariants a new entry has to keep.

The fixture corpus is what proves a vocabulary still matches its grammar. These
tests prove the table itself is coherent, which is the part a copy-paste entry
breaks: an extension claimed twice, or a fixture whose filename no longer maps
back to the language it is meant to check.
"""

from __future__ import annotations

from collections import Counter

from sloptrack import check_languages, measure


def test_language_names_are_unique():
    names = [lang.name for lang in measure.LANGS]
    assert len(names) == len(set(names))


def test_extensions_are_claimed_once():
    """Overlapping extensions would make BY_EXT silently pick the last entry."""
    claims: Counter[str] = Counter()
    for lang in measure.LANGS:
        claims.update(lang.exts)
        claims.update(lang.filenames)

    assert [name for name, count in claims.items() if count > 1] == []


def test_every_language_maps_back_from_its_own_extensions():
    for lang in measure.LANGS:
        for ext in lang.exts:
            assert measure.BY_EXT[ext] is lang, ext
        for filename in lang.filenames:
            assert measure.BY_FILENAME[filename] is lang, filename


def test_every_language_has_a_grammar_and_a_fixture():
    assert {lang.name for lang in measure.LANGS} == set(check_languages.SAMPLES)


def test_every_fixture_filename_maps_to_its_language():
    for name, (filename, _source, _funcs, _cc) in check_languages.SAMPLES.items():
        by_name = measure.BY_FILENAME.get(filename)
        by_ext = measure.BY_EXT.get("." + filename.rsplit(".", 1)[-1].lower())
        assert (by_name or by_ext) is not None, f"{name}: {filename} is not discoverable"
        assert (by_name or by_ext).name == name, f"{name}: {filename} maps elsewhere"


def test_fixture_expectations_are_positive():
    for name, (_filename, source, min_funcs, min_cc) in check_languages.SAMPLES.items():
        assert min_funcs >= 1, name
        assert min_cc >= 1, name
        assert source.strip(), name


def test_grammar_packages_are_pypi_names():
    packages = measure.grammar_packages({lang.name for lang in measure.LANGS})
    assert len(packages) == len(measure.LANGS)
    assert all("_" not in package for package in packages)
    assert "tree-sitter-python" in packages
