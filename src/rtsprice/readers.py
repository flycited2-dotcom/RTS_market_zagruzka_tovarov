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


def parse_number(value: object) -> float | None:
    r"""Разобрать число в форматах «17 050,00», «1 178.17», 1178.17.

    Русские выгрузки разделяют разряды обычным, неразрывным или узким
    неразрывным пробелом. Класс ``\s`` в Python покрывает их все, поэтому
    перечислять символы поимённо не нужно — и невозможно потерять один из
    них при копировании кода.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"\s", "", str(value)).replace(",", ".")
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        return None
    return float(text)


def text_value(value: object) -> str | None:
    """Привести значение ячейки к тексту.

    Целое число, пришедшее как float, канонизируется: xlrd отдаёт все числа
    как float, openpyxl — как int, и без этого один и тот же артикул получил
    бы из .xls и .xlsx два разных ключа, а значит два идентификатора РТС и
    дубль позиции на витрине.

    Живёт в читалке, а не в нормализации, потому что признак товарной строки
    разбирает те же самые ячейки. Разойдись эти два правила — строка с
    штрих-кодом 4607087560123.0 исчезла бы ещё до всякого учёта.
    """
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    out = re.sub(r"\s+", " ", str(value)).strip()
    return out or None


def _is_product(rule: str, values: dict[str, object]) -> bool:
    if rule == "price_not_empty":
        price = values.get("price")
        if price is None or not str(price).strip():
            return False
        number = parse_number(price)
        # Неразбираемое значение («по запросу») остаётся товарной строкой:
        # её отклонит слой нормализации с внятной причиной.
        return number is None or number != 0
    if rule == "barcode13":
        barcode = text_value(values.get("barcode")) or ""
        return barcode.isdigit() and len(barcode) == 13
    return bool(text_value(values.get("article")))


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
        # Один заголовок может стоять над несколькими колонками: у одного
        # поставщика «Цена клиента» повторяется для двух складов, и позиция
        # заполнена ровно в одной из них. Поэтому заголовок отображается на
        # список колонок, а значением берётся первое непустое.
        index: dict[str, list[int]] = {}
        for c, h in enumerate(headers):
            if h:
                index.setdefault(_norm(h), []).append(c)
        mapping: dict[str, list[int]] = {}
        for logical, title in cfg.columns.items():
            cols = index.get(_norm(title))
            if not cols:
                raise KeyError(
                    f"источник {cfg.code}, лист {sheet_name!r}: не найдена колонка {title!r}; "
                    f"доступны: {[h for h in headers if h][:20]}"
                )
            mapping[logical] = cols

        for number, row in enumerate(grid, start=1):
            if number < cfg.data_starts_at:
                continue
            values: dict[str, object] = {}
            for logical, cols in mapping.items():
                value = None
                for col in cols:
                    candidate = row[col] if col < len(row) else None
                    if isinstance(candidate, str) and not candidate.strip():
                        candidate = None
                    if candidate is not None:
                        value = candidate
                        break
                values[logical] = value
            if _is_product(cfg.row_is_product, values):
                rows.append(RawRow(cfg.code, sheet_name, number, values))
    return rows
