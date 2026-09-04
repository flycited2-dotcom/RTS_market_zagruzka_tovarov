"""Модели конфигурации и их загрузка."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import yaml

VALID_STATES = ("on", "off", "frozen")
VALID_VAT_MODES = ("vat22", "none")
VALID_ROUNDING = ("ruble", "ten", "none")
VALID_ROW_RULES = ("price_not_empty", "barcode13", "article_not_empty")


@dataclass(frozen=True)
class SourceConfig:
    code: str
    title: str
    state: str
    prefix: int
    companies: tuple[str, ...]
    file_glob: str
    sheets: tuple[str, ...]
    header_rows: tuple[int, ...]
    data_starts_at: int
    row_is_product: str
    columns: dict[str, str]
    include_groups: tuple[str, ...] = ()
    exclude_groups: tuple[str, ...] = ()
    #: Начала наименований, которые не выгружаются. Фильтр по началу, а не
    #: по вхождению: «Лента» в начале строки — ритуальная лента, а внутри
    #: строки то же слово встречается у сантехники и стиральных машин.
    exclude_name_starts: tuple[str, ...] = ()
    #: Откуда брать фотографии: brinex, openfoodfacts или promet. Пусто —
    #: источник снимков не имеет, и команда photos его не трогает.
    photo_api: str = ""
    #: Колонка, по которой поставщик отдаёт снимок: код товара или штрихкод.
    photo_key: str = "goods_id"
    min_stock: float | None = None
    markup: float = 1.0
    markup_by_group: dict[str, float] = field(default_factory=dict)
    unit_default: str = "ШТ"
    unit_by_group: dict[str, str] = field(default_factory=dict)
    price_includes_vat: bool = True
    description_template: str = "{name}"
    okpd2_by_group: dict[str, str] = field(default_factory=dict)
    # Код для источника, весь ассортимент которого относится к одной позиции
    # классификатора. Применяется, когда в прайсе нет ни колонки ОКПД2, ни группы.
    okpd2_default: str | None = None
    country: str | None = None
    region: str | None = None


@dataclass(frozen=True)
class PhotoServerConfig:
    """Куда публиковать фотографии, чтобы площадка смогла их скачать.

    Живёт в отдельном файле `photos.yml`, который не попадает в git: там
    адрес сервера и путь к ключу. Пароли не поддерживаются намеренно —
    ключ не утекает в историю команд и не хранится в открытом виде.
    """

    host: str
    user: str
    key_path: str
    remote_root: str
    base_url: str
    port: int = 22


@dataclass(frozen=True)
class CompanyConfig:
    code: str
    title: str
    vat_mode: str
    markup: float = 1.0
    rounding: str = "ruble"
    delivery_by_carrier: str = "ДА"
    delivery_by_seller: str = "НЕТ"
    delivery_pickup: str = "НЕТ"
    warehouse_address: str = ""
    delivery_terms: str = ""
    validity_days: int = 21
    photo_base_url: str = ""


def _states(value: object) -> str:
    """YAML превращает `on` в True, а `off` в False — возвращаем обратно."""
    if value is True:
        return "on"
    if value is False:
        return "off"
    return str(value or "on")


def load_sources(path: Path) -> dict[str, SourceConfig]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    result: dict[str, SourceConfig] = {}
    by_prefix: dict[int, str] = {}
    for code, body in raw.items():
        body = body or {}
        state = _states(body.get("state"))
        if state not in VALID_STATES:
            raise ValueError(f"источник {code}: недопустимое состояние {state!r}")
        rule = str(body.get("row_is_product", "price_not_empty"))
        if rule not in VALID_ROW_RULES:
            raise ValueError(f"источник {code}: неизвестное правило строки {rule!r}")
        # Префикс задаёт границу области удаления. Два источника с одним
        # префиксом сливаются в одну область: пересборка любого из них снимает
        # с площадки позиции второго, даже если тот заморожен. Блоки в
        # sources.yml пишут копированием соседнего, так что повтор — обычная
        # опечатка, а цена ей — исчезнувший с витрины каталог.
        prefix = int(body["prefix"])
        if prefix in by_prefix:
            raise ValueError(
                f"префикс {prefix} занят дважды: источники {by_prefix[prefix]} и {code}; "
                f"общий префикс объединил бы их в одну область удаления"
            )
        by_prefix[prefix] = code
        result[code] = SourceConfig(
            code=code,
            title=str(body.get("title", code)),
            state=state,
            prefix=prefix,
            companies=tuple(body.get("companies") or ("ooo_tlt", "ip")),
            file_glob=str(body["file"]),
            sheets=tuple(body.get("sheets") or ()),
            header_rows=tuple(body.get("header_rows") or (1,)),
            data_starts_at=int(body.get("data_starts_at", 2)),
            row_is_product=rule,
            columns=dict(body.get("columns") or {}),
            include_groups=tuple(body.get("include_groups") or ()),
            exclude_groups=tuple(body.get("exclude_groups") or ()),
            exclude_name_starts=tuple(body.get("exclude_name_starts") or ()),
            photo_api=str(body.get("photo_api") or ""),
            photo_key=str(body.get("photo_key") or "goods_id"),
            min_stock=(None if body.get("min_stock") is None else float(body["min_stock"])),
            markup=float(body.get("markup", 1.0)),
            markup_by_group={str(k): float(v) for k, v in (body.get("markup_by_group") or {}).items()},
            unit_default=str(body.get("unit_default", "ШТ")),
            unit_by_group={str(k): str(v) for k, v in (body.get("unit_by_group") or {}).items()},
            price_includes_vat=bool(body.get("price_includes_vat", True)),
            description_template=str(body.get("description_template", "{name}")),
            okpd2_by_group={str(k): str(v) for k, v in (body.get("okpd2_by_group") or {}).items()},
            okpd2_default=(body.get("okpd2_default") or None),
            country=(body.get("country") or None),
            region=(body.get("region") or None),
        )
    return result


def load_companies(directory: Path) -> dict[str, CompanyConfig]:
    result: dict[str, CompanyConfig] = {}
    for p in sorted(Path(directory).glob("*.yml")):
        body = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        vat = str(body.get("vat_mode", "vat22"))
        if vat not in VALID_VAT_MODES:
            raise ValueError(f"{p.name}: недопустимый vat_mode {vat!r}")
        rounding = str(body.get("rounding", "ruble"))
        if rounding not in VALID_ROUNDING:
            raise ValueError(f"{p.name}: недопустимое округление {rounding!r}")
        delivery = body.get("delivery") or {}
        result[p.stem] = CompanyConfig(
            code=p.stem,
            title=str(body.get("title", p.stem)),
            vat_mode=vat,
            markup=float(body.get("markup", 1.0)),
            rounding=rounding,
            delivery_by_carrier=str(delivery.get("by_carrier", "ДА")),
            delivery_by_seller=str(delivery.get("by_seller", "НЕТ")),
            delivery_pickup=str(delivery.get("pickup", "НЕТ")),
            warehouse_address=str(body.get("warehouse_address", "")),
            delivery_terms=str(body.get("delivery_terms", "")),
            validity_days=int(body.get("validity_days", 21)),
            photo_base_url=str(body.get("photo_base_url", "")),
        )
    return result


def load_photo_server(path: Path) -> PhotoServerConfig | None:
    """Прочитать настройки публикации фотографий, если они заданы.

    Отсутствие файла — не ошибка: у продавца может не быть ни фотографий,
    ни сервера, и сборка тогда просто оставляет колонки изображений пустыми.
    """
    p = Path(path)
    if not p.exists():
        return None
    body = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    missing = [k for k in ("host", "user", "key_path", "remote_root", "base_url")
               if not body.get(k)]
    if missing:
        raise ValueError(f"{p.name}: не заданы обязательные поля: {', '.join(missing)}")
    return PhotoServerConfig(
        host=str(body["host"]),
        user=str(body["user"]),
        key_path=str(Path(str(body["key_path"])).expanduser()),
        remote_root=str(body["remote_root"]),
        base_url=str(body["base_url"]),
        port=int(body.get("port", 22)),
    )


def load_stoplist(path: Path) -> set[tuple[str, str]]:
    p = Path(path)
    if not p.exists():
        return set()
    with p.open(encoding="utf-8-sig", newline="") as fh:
        return {
            (row["source"].strip(), row["article"].strip())
            for row in csv.DictReader(fh)
            if row.get("source") and row.get("article")
        }
