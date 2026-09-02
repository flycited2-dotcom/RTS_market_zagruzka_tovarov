"""Генерация описаний позиций и обрезка текста по границе слова."""
from __future__ import annotations

import re

NAME_LIMIT = 200
DESCRIPTION_LIMIT = 2000
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def truncate(text: object, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,.;:-")


def build_description(template: str, values: dict[str, object], fallback: object) -> str:
    """Подставить поля в шаблон, выбросив строки с незаполненными полями."""
    lines: list[str] = []
    for line in str(template).splitlines():
        names = _PLACEHOLDER.findall(line)
        if names and any(
            values.get(n) is None or not str(values.get(n)).strip() for n in names
        ):
            continue
        rendered = _PLACEHOLDER.sub(
            lambda m: str(values.get(m.group(1), "")).strip(), line
        ).strip()
        if rendered:
            lines.append(rendered)
    text = "\n".join(lines).strip()
    if not text:
        text = str(fallback or "").strip()
    if len(text) > DESCRIPTION_LIMIT:
        text = truncate(text, DESCRIPTION_LIMIT)
    return text
