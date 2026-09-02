"""Проверка отрендеренной строки: отклонения и автоисправления."""
from __future__ import annotations

from dataclasses import dataclass

from .describe import DESCRIPTION_LIMIT, NAME_LIMIT, truncate
from .render import (
    C_BARCODE, C_COUNTRY, C_DESCRIPTION, C_ID, C_NAME, C_OKPD2, C_PRICE,
    C_REGION, C_UNIT, RUSSIA,
)


@dataclass(frozen=True)
class Issue:
    level: str
    field: str
    reason: str
    value: str = ""


def validate_and_fix(
    row: list[object],
    units: set[str],
    okpd2_codes: set[str],
    seen_ids: set[int],
) -> tuple[list[object] | None, list[Issue]]:
    issues: list[Issue] = []
    out = list(row)

    try:
        rts_id = int(out[C_ID])
    except (TypeError, ValueError):
        return None, [Issue("reject", "Внутренний идентификатор",
                            "не является целым числом", str(out[C_ID]))]
    if rts_id in seen_ids:
        return None, [Issue("reject", "Внутренний идентификатор",
                            "повторяется в выгрузке", str(rts_id))]

    if not str(out[C_NAME] or "").strip():
        return None, [Issue("reject", "Наименование", "пустое обязательное поле")]
    if not str(out[C_DESCRIPTION] or "").strip():
        return None, [Issue("reject", "Описание", "пустое обязательное поле")]

    try:
        price = float(out[C_PRICE])
    except (TypeError, ValueError):
        return None, [Issue("reject", "Цена", "не является числом", str(out[C_PRICE]))]
    if price <= 0:
        return None, [Issue("reject", "Цена", "не положительна", str(price))]
    out[C_PRICE] = price

    unit = str(out[C_UNIT] or "").strip()
    if unit not in units:
        return None, [Issue("reject", "Единица измерения",
                            "значение отсутствует в справочнике ОКЕИ", unit)]

    if len(str(out[C_NAME])) > NAME_LIMIT:
        out[C_NAME] = truncate(out[C_NAME], NAME_LIMIT)
        issues.append(Issue("fix", "Наименование", "обрезано до 200 символов"))
    if len(str(out[C_DESCRIPTION])) > DESCRIPTION_LIMIT:
        out[C_DESCRIPTION] = truncate(out[C_DESCRIPTION], DESCRIPTION_LIMIT)
        issues.append(Issue("fix", "Описание", "обрезано до 2000 символов"))

    barcode = str(out[C_BARCODE] or "").strip()
    if barcode and not (barcode.isdigit() and len(barcode) == 13):
        out[C_BARCODE] = None
        issues.append(Issue("fix", "Штрих-код", "не 13 цифр, поле очищено", barcode))

    country = str(out[C_COUNTRY] or "").strip()
    if out[C_REGION] and country.casefold() != RUSSIA:
        issues.append(Issue("fix", "Регион производства",
                            "страна не Россия, поле очищено", str(out[C_REGION])))
        out[C_REGION] = None

    okpd2 = str(out[C_OKPD2] or "").strip()
    if okpd2 and okpd2 not in okpd2_codes:
        out[C_OKPD2] = None
        issues.append(Issue("fix", "ОКПД2", "код отсутствует в справочнике", okpd2))

    seen_ids.add(rts_id)
    return out, issues
