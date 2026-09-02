from pathlib import Path

import pytest

from rtsprice.identity import IdMap, prefix_of


def test_assigns_sequential_ids_within_prefix(tmp_path: Path):
    m = IdMap(tmp_path / "id_map.csv")
    assert m.get_or_create("promet", 10, "A-1") == 100_000_001
    assert m.get_or_create("promet", 10, "A-2") == 100_000_002
    assert m.get_or_create("brinex", 11, "B-1") == 110_000_001


def test_same_article_keeps_same_id(tmp_path: Path):
    m = IdMap(tmp_path / "id_map.csv")
    first = m.get_or_create("promet", 10, "A-1")
    m.get_or_create("promet", 10, "A-2")
    assert m.get_or_create("promet", 10, "A-1") == first


def test_ids_survive_reload(tmp_path: Path):
    p = tmp_path / "id_map.csv"
    m = IdMap(p)
    kept = m.get_or_create("promet", 10, "A-1")
    m.get_or_create("promet", 10, "A-2")
    m.save()

    again = IdMap(p)
    assert again.get_or_create("promet", 10, "A-1") == kept
    assert again.get_or_create("promet", 10, "A-3") == 100_000_003


def test_prefix_of_extracts_source_prefix():
    assert prefix_of(100_000_001) == 10
    assert prefix_of(110_000_042) == 11


def test_rejects_prefix_out_of_range(tmp_path: Path):
    m = IdMap(tmp_path / "id_map.csv")
    with pytest.raises(ValueError):
        m.get_or_create("x", 0, "A")
    with pytest.raises(ValueError):
        m.get_or_create("x", 100, "A")
