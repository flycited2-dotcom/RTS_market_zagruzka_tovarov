"""Чтение книг поставщиков: xlsx через openpyxl, xls через xlrd."""
from __future__ import annotations

import glob
import re
from dataclasses import dataclass
from pathlib import Path

from .config import SourceConfig


@dataclass(frozen=True)
class RawRow:
    source: str
    sheet: str
    row_number: int
    values: dict[str, object]


def header_key(parts: list[object]) -> str:
    """Склеить вертикальные части шапки одной колонки в один заголовок."""
    chunks = [
        re.sub(r"\s+", " ", str(p)).strip()
        for p in parts
        if p is not None and str(p).strip()
    ]
    return " ".join(chunks)


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def find_source_file(pattern: str) -> Path:
    """Самый свежий файл, подходящий под шаблон пути."""
    matches = [Path(p) for p in glob.glob(pattern)]
    if not matches:
        raise FileNotFoundError(f"не найден файл по шаблону {pattern}")
    return max(matches, key=lambda p: p.stat().st_mtime)


def _grid(path: Path, sheet_names: tuple[str, ...]) -> list[tuple[str, list[list[object]]]]:
    """Прочитать нужные листы книги как список строк-списков."""
    if path.suffix.lower() == ".xls":
        import xlrd

        wb = xlrd.open_workbook(path)
        sheets = [s for s in wb.sheets() if not sheet_names or s.name in sheet_names]
        return [
            (
                s.name,
                [
                    [s.cell_value(r, c) if s.cell_value(r, c) != "" else None
                     for c in range(s.ncols)]
                    for r in range(s.nrows)
                ],
            )
            for s in sheets
        ]

    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    result = []
    for ws in wb.worksheets:
        if sheet_names and ws.title not in sheet_names:
            continue
        result.append((ws.title, [list(row) for row in ws.iter_rows(values_only=True)]))
    wb.close()
    return result


def _is_product(rule: str, values: dict[str, object]) -> bool:
    if rule == "price_not_empty":
        price = values.get("price")
        return price is not None and str(price).strip() not in ("", "0")
    if rule == "barcode13":
        barcode = str(values.get("barcode") or "").strip()
        return barcode.isdigit() and len(barcode) == 13
    return bool(str(values.get("article") or "").strip())


def read_source(cfg: SourceConfig, path: Path) -> list[RawRow]:
    rows: list[RawRow] = []
    for sheet_name, grid in _grid(Path(path), cfg.sheets):
        if not grid:
            continue
        width = max(len(r) for r in grid)
        headers = [
            header_key(
                [
                    grid[i - 1][c] if i - 1 < len(grid) and c < len(grid[i - 1]) else None
                    for i in cfg.header_rows
                ]
            )
            for c in range(width)
        ]
        index = {_norm(h): c for c, h in enumerate(headers) if h}
        mapping: dict[str, int] = {}
        for logical, title in cfg.columns.items():
            col = index.get(_norm(title))
            if col is None:
                raise KeyError(
                    f"источник {cfg.code}, лист {sheet_name!r}: не найдена колонка {title!r}; "
                    f"доступны: {[h for h in headers if h][:20]}"
                )
            mapping[logical] = col

        for number, row in enumerate(grid, start=1):
            if number < cfg.data_starts_at:
                continue
            values: dict[str, object] = {}
            for logical, col in mapping.items():
                value = row[col] if col < len(row) else None
                if isinstance(value, str) and not value.strip():
                    value = None
                values[logical] = value
            if _is_product(cfg.row_is_product, values):
                rows.append(RawRow(cfg.code, sheet_name, number, values))
    return rows
