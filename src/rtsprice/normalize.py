# src/rtsprice/normalize.py
"""Приведение сырых строк прайса к позициям мастер-каталога."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import SourceConfig
from .describe import NAME_LIMIT, build_description, truncate
from .identity import IdMap
from .readers import RawRow, parse_number, text_value as _text
from .reference import resolve_unit

KNOWN_FIELDS = (
    "article", "name", "price", "unit", "okpd2", "barcode",
    "country", "region", "group", "stock",
)


@dataclass(frozen=True)
class Item:
    source: str
    article: str
    rts_id: int
    name: str
    description: str
    price_in: float
    unit: str
    okpd2: str | None = None
    barcode: str | None = None
    country: str | None = None
    region: str | None = None
    group: str | None = None
    stock: float | None = None
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Rejection:
    source: str
    article: str
    row_number: int
    field: str
    reason: str
    value: str = ""


STOCK_COMPARATORS = "><≥≤"


def parse_stock(value: object) -> float | None:
    """Разобрать остаток, допуская ведущее сравнение: «>40», «≥ 10».

    Поставщик, пишущий «>100», сообщает, что товара не меньше сотни, — это
    лучший остаток на складе, а не отсутствие данных. Без такого разбора
    фильтр min_stock отклонял именно самые обеспеченные позиции и снимал их
    с продажи при каждой сборке.

    Только для остатка. Цена со сравнением — не цена: «от 100 рублей» нельзя
    выставить на площадке, и такую строку правильно отклонить.
    """
    if isinstance(value, str):
        value = value.strip().lstrip(STOCK_COMPARATORS)
    return parse_number(value)


def normalize_source(
    cfg: SourceConfig,
    rows: list[RawRow],
    idmap: IdMap,
    stoplist: set[tuple[str, str]],
    units: set[str],
    aliases: dict[str, str],
) -> tuple[list[Item], list[Rejection]]:
    items: list[Item] = []
    rejected: list[Rejection] = []
    seen: set[str] = set()

    for row in rows:
        values = row.values
        article = _text(values.get("article")) or _text(values.get("barcode"))
        if not article:
            rejected.append(Rejection(cfg.code, "", row.row_number, "article",
                                      "нет ни артикула, ни штрих-кода"))
            continue
        if (cfg.code, article) in stoplist:
            rejected.append(Rejection(cfg.code, article, row.row_number, "article",
                                      "в стоп-листе"))
            continue
        if article in seen:
            rejected.append(Rejection(cfg.code, article, row.row_number, "article",
                                      "дубль артикула в прайсе"))
            continue

        group = _text(values.get("group"))
        if cfg.include_groups and (group or "") not in cfg.include_groups:
            rejected.append(Rejection(cfg.code, article, row.row_number, "group",
                                      "группа не входит в include_groups", group or ""))
            continue
        if group and group in cfg.exclude_groups:
            rejected.append(Rejection(cfg.code, article, row.row_number, "group",
                                      "группа в exclude_groups", group))
            continue

        stock = parse_stock(values.get("stock"))
        if cfg.min_stock is not None and (stock is None or stock < cfg.min_stock):
            reason = "нет данных об остатке" if stock is None else "остаток ниже минимального"
            rejected.append(Rejection(cfg.code, article, row.row_number, "stock",
                                      reason, _text(values.get("stock")) or ""))
            continue

        name = _text(values.get("name"))
        if not name:
            rejected.append(Rejection(cfg.code, article, row.row_number, "name",
                                      "пустое наименование"))
            continue

        price = parse_number(values.get("price"))
        if price is None or price <= 0:
            rejected.append(Rejection(cfg.code, article, row.row_number, "price",
                                      "цена не число или не положительна",
                                      _text(values.get("price")) or ""))
            continue

        unit = (
            resolve_unit(values.get("unit"), units, aliases)
            or cfg.unit_by_group.get(group or "")
            or cfg.unit_default
        )

        barcode = _text(values.get("barcode"))
        if barcode is not None and not (barcode.isdigit() and len(barcode) == 13):
            barcode = None

        # _text здесь по той же причине, что и для артикула: без него число из .xls
        # попало бы в описание как «15.0», а из .xlsx — как «15».
        attributes = {
            k: _text(v) for k, v in values.items()
            if k not in KNOWN_FIELDS and _text(v)
        }
        template_values: dict[str, object] = dict(attributes)
        template_values.update({
            "name": name, "article": article, "group": group,
            "unit": unit, "barcode": barcode,
        })

        items.append(Item(
            source=cfg.code,
            article=article,
            rts_id=idmap.get_or_create(cfg.code, cfg.prefix, article),
            name=truncate(name, NAME_LIMIT),
            description=build_description(cfg.description_template, template_values, name),
            price_in=price,
            unit=unit,
            okpd2=_text(values.get("okpd2")) or cfg.okpd2_by_group.get(group or ""),
            barcode=barcode,
            country=_text(values.get("country")) or cfg.country,
            region=_text(values.get("region")) or cfg.region,
            group=group,
            stock=stock,
            attributes=attributes,
        ))
        seen.add(article)

    return items, rejected
