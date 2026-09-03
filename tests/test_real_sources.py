# tests/test_real_sources.py
from pathlib import Path

import pytest

from rtsprice.config import load_sources
from rtsprice.identity import IdMap
from rtsprice.normalize import normalize_source
from rtsprice.readers import find_source_file, read_source
from rtsprice.reference import load_unit_aliases, load_units

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MINIMUM = {
    "promet": 12000,
    "brinex_wheels": 7000,
    "brinex_tires": 4000,
    "gurinenko_bakaleya": 1255,
    "gurinenko_grushevo": 3960,
    "opt": 400,
    "priceopt": 1600,
}


@pytest.mark.parametrize("code", sorted(EXPECTED_MINIMUM))
def test_source_reads_and_normalizes(code: str, tmp_path: Path):
    sources = load_sources(ROOT / "sources.yml")
    cfg = sources[code]
    path = find_source_file(cfg.file_glob)

    rows, _ = read_source(cfg, path)
    assert len(rows) >= EXPECTED_MINIMUM[code], f"{code}: прочитано слишком мало строк"

    units = load_units(ROOT / "reference" / "okei.csv")
    aliases = load_unit_aliases(ROOT / "reference" / "unit_aliases.yml")
    items, rejected = normalize_source(cfg, rows, IdMap(tmp_path / f"{code}.csv"),
                                       set(), units, aliases)
    assert len(items) >= 0.8 * len(rows), (
        f"{code}: отсеяно больше 20% строк, причины: "
        f"{ {r.reason for r in rejected} }"
    )
    for item in items[:50]:
        assert item.name and len(item.name) <= 200
        assert item.description
        assert item.price_in > 0
        assert item.unit in units
