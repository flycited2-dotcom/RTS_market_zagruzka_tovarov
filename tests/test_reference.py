from pathlib import Path
from rtsprice.reference import (
    load_units,
    load_unit_aliases,
    resolve_unit,
    load_okpd2,
    extract_reference_tables,
    UNITS_VALIDATION_LAST_ROW,
)


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


def test_resolve_unit_case_sensitive_match_first(tmp_path: Path):
    """Test that exact case match takes precedence over case-insensitive match.
    Prevents non-deterministic resolution of similar units like ед vs ЕД."""
    units = {"ед", "ЕД"}
    assert resolve_unit("ед", units, {}) == "ед"
    assert resolve_unit("ЕД", units, {}) == "ЕД"
    # Case-insensitive fallback should still work when exact match fails
    assert resolve_unit("Ед", units, {}) in ("ед", "ЕД")  # one of them, deterministically


def test_resolve_unit_rejects_nonexistent_alias_target(tmp_path: Path):
    """Test that resolve_unit returns None if alias points to non-existent symbol."""
    units = {"ШТ", "КГ"}
    # Alias points to УПАК which doesn't exist in units
    aliases = {"упак": "УПАК"}
    assert resolve_unit("упак", units, aliases) is None


def test_extract_reference_tables_respects_validation_range(tmp_path: Path):
    """Test that extract_reference_tables does not extract rows beyond UNITS_VALIDATION_LAST_ROW."""
    import openpyxl

    # Create a workbook with units beyond the validation range
    wb = openpyxl.Workbook()
    units_sheet = wb.active
    units_sheet.title = "Справочник единиц измерения"
    okpd2_sheet = wb.create_sheet("Справочник ОКПД2")

    # Add units: header + some valid rows + rows beyond the cap
    units_sheet.append(["Обозначение", "Наименование", "Код"])  # Header (row 1)
    for i in range(2, UNITS_VALIDATION_LAST_ROW + 1):
        units_sheet.append([f"SYMBOL{i-1}", f"Name{i-1}", 100 + i])  # Valid rows
    # Add rows beyond the cap
    units_sheet.append(["BEYOND_CAP_1", "Beyond Cap 1", 800])  # Row 536
    units_sheet.append(["BEYOND_CAP_2", "Beyond Cap 2", 801])  # Row 537
    units_sheet.append(["-", "Placeholder", 802])  # Row 538, should also be skipped

    # Add OKPD2 header
    okpd2_sheet.append(["Код", "Наименование"])
    okpd2_sheet.append(["01.11.11.111", "Product 1"])

    template_path = tmp_path / "template.xlsx"
    wb.save(template_path)

    # Extract and check
    output_dir = tmp_path / "output"
    extract_reference_tables(template_path, output_dir)

    # Load and verify
    units = load_units(output_dir / "okei.csv")

    # Should contain valid rows but not rows beyond UNITS_VALIDATION_LAST_ROW
    assert len(units) == UNITS_VALIDATION_LAST_ROW - 1  # Rows 2 to 535
    assert any("SYMBOL" in u for u in units), "Should contain valid rows"
    assert "BEYOND_CAP_1" not in units, "Should not contain rows beyond validation range"
    assert "BEYOND_CAP_2" not in units, "Should not contain rows beyond validation range"
    assert "-" not in units, "Should not contain placeholder rows"
