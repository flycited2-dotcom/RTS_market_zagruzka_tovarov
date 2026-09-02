from pathlib import Path
from rtsprice.reference import load_units, load_unit_aliases, resolve_unit, load_okpd2


def _units(tmp_path: Path) -> Path:
    p = tmp_path / "okei.csv"
    p.write_text("code,symbol,name\n796,ШТ,Штука\n166,КГ,Килограмм\n112,Л; ДМ3,Литр\n",
                 encoding="utf-8-sig")
    return p


def test_load_units_returns_symbols(tmp_path: Path):
    assert load_units(_units(tmp_path)) == {"ШТ", "КГ", "Л; ДМ3"}


def test_resolve_unit_direct_match(tmp_path: Path):
    units = load_units(_units(tmp_path))
    assert resolve_unit("шт", units, {}) == "ШТ"
    assert resolve_unit("  КГ ", units, {}) == "КГ"


def test_resolve_unit_via_alias(tmp_path: Path):
    units = load_units(_units(tmp_path))
    aliases = {"штука": "ШТ", "pcs": "ШТ", "уп": "ШТ"}
    assert resolve_unit("Штука", units, aliases) == "ШТ"
    assert resolve_unit("pcs", units, aliases) == "ШТ"


def test_resolve_unit_unknown_returns_none(tmp_path: Path):
    units = load_units(_units(tmp_path))
    assert resolve_unit("бушель", units, {}) is None
    assert resolve_unit(None, units, {}) is None


def test_load_okpd2(tmp_path: Path):
    p = tmp_path / "okpd2.csv"
    p.write_text("code,name\n26.40.20.122,Телевизоры\n", encoding="utf-8-sig")
    assert "26.40.20.122" in load_okpd2(p)
