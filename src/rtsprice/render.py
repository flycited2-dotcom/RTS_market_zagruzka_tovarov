"""Преобразование позиции мастер-каталога в строку формата РТС."""
from __future__ import annotations

from .config import CompanyConfig, SourceConfig
from .normalize import Item
from .pricing import final_price, vat_label

COLUMN_COUNT = 27

C_ORDER = 0
C_ID = 1
C_ARTICLE = 2
C_NAME = 3
C_DESCRIPTION = 4
C_PRICE = 5
C_UNIT = 6
C_OKPD2 = 7
C_VAT = 8
C_DELIVERY_CARRIER = 9
C_DELIVERY_SELLER = 10
C_PICKUP = 11
C_WAREHOUSE = 12
C_TERMS = 13
C_IMAGE_FIRST = 14
C_FILE_FIRST = 19
C_BARCODE = 22
C_COUNTRY = 23
C_REGION = 24
C_VALIDITY = 25
C_DELETE = 26

MAX_IMAGES = 5
MAX_FILES = 3
RUSSIA = "россия"

HEADER_ROW_1: tuple[str | None, ...] = (
    "Порядковый номер позиции",
    "Внутренний идентификатор позиции из товароучетной системы(при наличии)",
    "Артикул товара или внутренний регистрационный номер товара (присваивается продавцом)",
    "Наименование позиции* (max-200 символов)",
    "Описание позиции* (max-2000 символов)",
    "Цена за единицу (с НДС), руб.*",
    "Единица измерения*",
    "Код классификатора ОКПД2",
    "НДС",
    "ДЕТАЛИ ДОСТАВКИ (Выберите опции доставки, доступные для этой позиции)",
    None,
    None,
    "Укажите адрес склада для самовывоза",
    "Дополнительная информация по поставке товара (выполнения работ, оказания услуг)",
    "Ссылка на картинку 1",
    "Ссылка на картинку 2",
    "Ссылка на картинку 3",
    "Ссылка на картинку 4",
    "Ссылка на картинку 5",
    "Ссылка на файл 1",
    "Ссылка на файл 2",
    "Ссылка на файл 3",
    "Штрих-код (13 цифр)",
    "Страна происхождения (заполняется для товара)",
    "Регион производства (Указывается, если Страна происхождения Россия)",
    "Актуальность (дней)",
    "Удалить",
)

HEADER_ROW_2: tuple[str | None, ...] = tuple(
    "Доставка транспортной компанией" if i == C_DELIVERY_CARRIER
    else "Доставка продавцом" if i == C_DELIVERY_SELLER
    else "Самовывоз со склада продавца" if i == C_PICKUP
    else None
    for i in range(COLUMN_COUNT)
)


def render_row(
    item: Item,
    source_cfg: SourceConfig,
    company_cfg: CompanyConfig,
    photo_urls: list[str] | None = None,
    file_urls: list[str] | None = None,
) -> list[object]:
    row: list[object] = [None] * COLUMN_COUNT
    row[C_ID] = item.rts_id
    row[C_ARTICLE] = item.article
    row[C_NAME] = item.name
    row[C_DESCRIPTION] = item.description
    row[C_PRICE] = final_price(item, source_cfg, company_cfg)
    row[C_UNIT] = item.unit
    row[C_OKPD2] = item.okpd2
    row[C_VAT] = vat_label(company_cfg)
    row[C_DELIVERY_CARRIER] = company_cfg.delivery_by_carrier
    row[C_DELIVERY_SELLER] = company_cfg.delivery_by_seller
    row[C_PICKUP] = company_cfg.delivery_pickup
    row[C_WAREHOUSE] = company_cfg.warehouse_address or None
    row[C_TERMS] = company_cfg.delivery_terms or None
    row[C_BARCODE] = item.barcode
    row[C_COUNTRY] = item.country
    if item.country and item.country.strip().casefold() == RUSSIA:
        row[C_REGION] = item.region
    # Ноль трактуется как «без срока»: пустая ячейка на площадке означает
    # именно это, а срок в ноль дней снял бы позицию с витрины в день загрузки.
    row[C_VALIDITY] = company_cfg.validity_days or None

    for offset, url in enumerate((photo_urls or [])[:MAX_IMAGES]):
        row[C_IMAGE_FIRST + offset] = url
    for offset, url in enumerate((file_urls or [])[:MAX_FILES]):
        row[C_FILE_FIRST + offset] = url
    return row
