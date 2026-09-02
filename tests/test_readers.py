from pathlib import Path

import openpyxl
import pytest

from rtsprice.config import SourceConfig
from rtsprice.readers import header_key, find_source_file, read_source, parse_number


def _cfg(**over) -> SourceConfig:
    base = dict(
        code="s", title="S", state="on", prefix=7, companies=("ooo_tlt",),
        file_glob="input/s/*.xlsx", sheets=("Лист1",), header_rows=(1,),
        data_starts_at=2, row_is_product="price_not_empty",
        columns={"article": "Артикул", "name": "Наименование", "price": "Цена"},
    )
    base.update(over)
    return SourceConfig(**base)


def _book(path: Path, rows: list[list]) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Лист1"
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def test_header_key_joins_non_empty_parts():
    assert header_key(["Закуп", "Цена"]) == "Закуп Цена"
    assert header_key(["Штрихкод", None]) == "Штрихкод"
    assert header_key([None, "Оптовая"]) == "Оптовая"
    assert header_key([" А ", "  ", "Б"]) == "А Б"


def test_read_source_maps_columns_and_skips_non_products(tmp_path: Path):
    p = _book(tmp_path / "p.xlsx", [
        ["Артикул", "Наименование", "Цена"],
        ["A-1", "Товар один", 100],
        ["РАЗДЕЛ", "Бытовая химия", None],
        ["A-2", "Товар два", 250.5],
    ])
    rows = read_source(_cfg(), p)
    assert [r.values["article"] for r in rows] == ["A-1", "A-2"]
    assert rows[0].values["name"] == "Товар один"
    assert rows[1].values["price"] == 250.5
    assert rows[0].row_number == 2


def test_read_source_two_row_header(tmp_path: Path):
    p = _book(tmp_path / "p.xlsx", [
        ["Штрихкод", "Номенклатура", None],
        [None, None, "Цена клиента"],
        ["4600000000017", "Сок", 99],
    ])
    cfg = _cfg(header_rows=(1, 2), data_starts_at=3,
               columns={"barcode": "Штрихкод", "name": "Номенклатура", "price": "Цена клиента"})
    rows = read_source(cfg, p)
    assert len(rows) == 1
    assert rows[0].values["barcode"] == "4600000000017"
    assert rows[0].values["price"] == 99


def test_read_source_barcode13_rule(tmp_path: Path):
    p = _book(tmp_path / "p.xlsx", [
        ["Штрихкод", "Номенклатура", "Цена клиента"],
        ["Бакалея", None, None],
        ["4600000000017", "Сок", 99],
    ])
    cfg = _cfg(header_rows=(1,), data_starts_at=2, row_is_product="barcode13",
               columns={"barcode": "Штрихкод", "name": "Номенклатура", "price": "Цена клиента"})
    rows = read_source(cfg, p)
    assert [r.values["name"] for r in rows] == ["Сок"]


def test_read_source_missing_column_raises(tmp_path: Path):
    p = _book(tmp_path / "p.xlsx", [["Артикул", "Наименование"], ["A-1", "Товар"]])
    with pytest.raises(KeyError, match="Цена"):
        read_source(_cfg(), p)


def test_find_source_file_returns_newest(tmp_path: Path):
    import os
    import time

    d = tmp_path / "input" / "s"
    d.mkdir(parents=True)
    old, new = d / "a.xlsx", d / "b.xlsx"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    os.utime(old, (time.time() - 500, time.time() - 500))
    assert find_source_file(str(d / "*.xlsx")) == new


def test_parse_number_returns_none_for_special_values():
    """parse_number returns None for None, booleans, and non-numeric strings."""
    assert parse_number(None) is None
    assert parse_number(True) is None
    assert parse_number(False) is None
    assert parse_number("по запросу") is None
    assert parse_number("1 200 - 1 500") is None


def test_parse_number_parses_numeric_values():
    """parse_number returns float for integers, floats, and numeric strings."""
    assert parse_number(1178) == 1178.0
    assert parse_number(1178.17) == 1178.17
    assert parse_number("1178.17") == 1178.17
    assert parse_number("17 050,00") == 17050.0
    assert parse_number("17 050,00") == 17050.0  # NBSP U+00A0
    assert parse_number("-5") == -5.0


def test_price_not_empty_rule_filters_zeros_and_empty(tmp_path: Path):
    """Zero prices (int, float, string) should be filtered out; non-numeric passes through."""
    p = _book(tmp_path / "p.xlsx", [
        ["Артикул", "Наименование", "Цена"],
        ["A-1", "Integer zero", 0],
        ["A-2", "Float zero", 0.0],
        ["A-3", "String 0.00", "0.00"],
        ["A-4", "String 0", "0"],
        ["A-5", "Empty string", ""],
        ["A-6", "None price", None],
        ["A-7", "Zero with NBSP", "0 000,00"],
        ["A-8", "Valid price", 100],
        ["A-9", "Space formatted", "17 050,00"],
        ["A-10", "NBSP formatted", "17 050,00"],
        ["A-11", "Non-numeric", "по запросу"],
    ])

    rows = read_source(_cfg(), p)
    # Non-products: A-1 to A-7 (zero or empty)
    # Products: A-8 to A-11 (valid price or non-numeric)
    assert [r.values["article"] for r in rows] == ["A-8", "A-9", "A-10", "A-11"]
