from pathlib import Path

import openpyxl
import pytest

from rtsprice.config import SourceConfig
from rtsprice.readers import header_key, find_source_file, read_source


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
