"""Расчёт цены для конкретного юридического лица."""
from __future__ import annotations

import math

from .config import CompanyConfig, SourceConfig
from .normalize import Item

VAT_RATE = 0.22
VAT_LABEL_WITH = "Облагается НДС 22"
VAT_LABEL_WITHOUT = "Не облагается НДС"


def to_net(price: float, includes_vat: bool) -> float:
    """Цена поставщика без НДС. База для компании, которая НДС возмещает."""
    return float(price) / (1.0 + VAT_RATE) if includes_vat else float(price)


def to_gross(price: float, includes_vat: bool) -> float:
    """Цена поставщика с НДС, то есть фактически уплачиваемая сумма.

    База для компании на упрощённой системе: входящий НДС ей не возмещается
    и является частью себестоимости, а не налогом.
    """
    return float(price) if includes_vat else float(price) * (1.0 + VAT_RATE)


def markup_for(source_cfg: SourceConfig, group: str | None, company_cfg: CompanyConfig) -> float:
    """Наценка от частного к общему: группа, затем источник, затем компания.

    Правило по группе выбирается по наличию ключа, а не по истинности значения:
    наценка 0 — конфигурация бессмысленная, но она должна дать цену 0 и громкий
    отказ на слое проверки, а не тихо провалиться на уровень выше.
    """
    key = group or ""
    if key in source_cfg.markup_by_group:
        return float(source_cfg.markup_by_group[key])
    if source_cfg.markup != 1.0:
        return float(source_cfg.markup)
    return float(company_cfg.markup)


def round_price(value: float, rule: str) -> float:
    """Округление всегда вверх, включая режим none.

    Округлённая вниз цена — подарок покупателю на каждой проданной единице.
    Поправка 1e-9 гасит двоичный мусор, чтобы математически точное значение
    не уехало на целый рубль вверх.
    """
    if rule == "ruble":
        return float(math.ceil(value - 1e-9))
    if rule == "ten":
        return float(math.ceil((value - 1e-9) / 10.0) * 10)
    return math.ceil(value * 100 - 1e-9) / 100


def vat_label(company_cfg: CompanyConfig) -> str:
    return VAT_LABEL_WITH if company_cfg.vat_mode == "vat22" else VAT_LABEL_WITHOUT


def final_price(item: Item, source_cfg: SourceConfig, company_cfg: CompanyConfig) -> float:
    """Итоговая цена для колонки F.

    Компания с НДС наценивает сумму без НДС и затем начисляет налог сверху.
    Компания без НДС наценивает фактически уплаченную сумму, поскольку входящий
    НДС для неё невозместим. При одинаковой наценке обе получают одну и ту же
    итоговую цену — различается только её налоговый состав.
    """
    markup = markup_for(source_cfg, item.group, company_cfg)
    if company_cfg.vat_mode == "vat22":
        base = to_net(item.price_in, source_cfg.price_includes_vat)
        value = base * markup * (1.0 + VAT_RATE)
    else:
        base = to_gross(item.price_in, source_cfg.price_includes_vat)
        value = base * markup
    return round_price(value, company_cfg.rounding)
