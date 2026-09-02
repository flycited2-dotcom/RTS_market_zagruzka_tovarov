"""Снимки прошлых выгрузок и расчёт строк на удаление."""
from __future__ import annotations

import csv
from pathlib import Path

from .identity import prefix_of
from .render import C_DELETE, C_ID, C_PRICE, C_VALIDITY, COLUMN_COUNT

# Числами становятся только те колонки, которые ими и являются.
NUMERIC_COLUMNS = (C_ID, C_PRICE, C_VALIDITY, C_DELETE)


def _decode(value: str, column: int) -> object:
    """Восстановить значение ячейки снимка.

    Разбор по колонке, а не по виду значения: артикул «00000001688» и
    штрих-код с ведущим нулём при числовом разборе потеряли бы нули, и
    строка снятия с продажи ушла бы на площадку с испорченными полями.
    """
    if value == "":
        return None
    if column not in NUMERIC_COLUMNS:
        return value
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def load_snapshot(path: Path) -> dict[int, list[object]]:
    p = Path(path)
    if not p.exists():
        return {}
    result: dict[int, list[object]] = {}
    with p.open(encoding="utf-8-sig", newline="") as fh:
        for raw in csv.reader(fh):
            if len(raw) < COLUMN_COUNT:
                raw = raw + [""] * (COLUMN_COUNT - len(raw))
            row = [_decode(v, i) for i, v in enumerate(raw[:COLUMN_COUNT])]
            if row[C_ID] is None:
                continue
            try:
                rts_id = int(row[C_ID])
            except (TypeError, ValueError):
                # Испорченный идентификатор пропускаем, а не роняем сборку:
                # снимок — служебный файл, и одна битая строка не повод
                # оставить весь каталог без обновления.
                continue
            row[C_ID] = rts_id
            result[rts_id] = row
    return result


def save_snapshot(path: Path, rows: list[list[object]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        for row in rows:
            writer.writerow(["" if v is None else v for v in row])
    tmp.replace(p)


def deletion_rows(
    previous: dict[int, list[object]],
    current_ids: set[int],
    keep_prefixes: set[int],
) -> list[list[object]]:
    """Строки для позиций, исчезнувших из выгрузки, кроме замороженных источников."""
    result: list[list[object]] = []
    for rts_id, row in sorted(previous.items()):
        if rts_id in current_ids or prefix_of(rts_id) in keep_prefixes:
            continue
        copy = list(row)
        copy[C_DELETE] = 1
        result.append(copy)
    return result
