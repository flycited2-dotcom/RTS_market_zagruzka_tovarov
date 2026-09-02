# tests/test_normalize.py
from pathlib import Path

from rtsprice.config import SourceConfig
from rtsprice.identity import IdMap
from rtsprice.normalize import normalize_source
from rtsprice.readers import RawRow


def _cfg(**over) -> SourceConfig:
    base = dict(
        code="s", title="S", state="on", prefix=7, companies=("ooo_tlt",),
        file_glob="x", sheets=(), header_rows=(1,), data_starts_at=2,
        row_is_product="price_not_empty",
        columns={"article": "Артикул", "name": "Наименование", "price": "Цена"},
        unit_default="ШТ", description_template="{name}",
    )
    base.update(over)
    return SourceConfig(**base)


def _row(n: int = 2, **values) -> RawRow:
    base = {"article": "A-1", "name": "Товар", "price": 100}
    base.update(values)
    return RawRow("s", "Лист1", n, base)


UNITS = {"ШТ", "КГ"}


def test_builds_item_with_id_and_description(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    items, rejected = normalize_source(_cfg(), [_row()], idmap, set(), UNITS, {})
    assert not rejected
    item = items[0]
    assert item.rts_id == 70_000_001
    assert item.article == "A-1"
    assert item.name == "Товар"
    assert item.description == "Товар"
    assert item.price_in == 100.0
    assert item.unit == "ШТ"


def test_rejects_row_without_name(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    items, rejected = normalize_source(_cfg(), [_row(name=None)], idmap, set(), UNITS, {})
    assert items == []
    assert rejected[0].field == "name"


def test_rejects_unparsable_price(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    items, rejected = normalize_source(_cfg(), [_row(price="по запросу")], idmap, set(), UNITS, {})
    assert items == []
    assert rejected[0].field == "price"


def test_parses_price_with_spaces_and_comma(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    items, _ = normalize_source(_cfg(), [_row(price="17 050,00")], idmap, set(), UNITS, {})
    assert items[0].price_in == 17050.0


def test_stoplist_excludes_article(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    items, rejected = normalize_source(_cfg(), [_row()], idmap, {("s", "A-1")}, UNITS, {})
    assert items == []
    assert rejected[0].reason == "в стоп-листе"


def test_exclude_groups_filters_rows(tmp_path: Path):
    cfg = _cfg(columns={"article": "A", "name": "N", "price": "P", "group": "G"},
               exclude_groups=("Химия",))
    idmap = IdMap(tmp_path / "m.csv")
    rows = [_row(group="Химия"), _row(2, article="A-2", group="Посуда")]
    items, _ = normalize_source(cfg, rows, idmap, set(), UNITS, {})
    assert [i.article for i in items] == ["A-2"]


def test_min_stock_filters_out_of_stock(tmp_path: Path):
    cfg = _cfg(columns={"article": "A", "name": "N", "price": "P", "stock": "S"}, min_stock=1)
    idmap = IdMap(tmp_path / "m.csv")
    rows = [_row(stock=0), _row(2, article="A-2", stock=5)]
    items, _ = normalize_source(cfg, rows, idmap, set(), UNITS, {})
    assert [i.article for i in items] == ["A-2"]


def test_name_longer_than_limit_is_truncated(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    long_name = "слово " * 60
    items, _ = normalize_source(_cfg(), [_row(name=long_name)], idmap, set(), UNITS, {})
    assert len(items[0].name) <= 200
    assert len(items[0].description) > 200


def test_barcode_kept_only_when_13_digits(tmp_path: Path):
    cfg = _cfg(columns={"article": "A", "name": "N", "price": "P", "barcode": "B"})
    idmap = IdMap(tmp_path / "m.csv")
    rows = [_row(barcode="4600000000017"), _row(2, article="A-2", barcode="12345")]
    items, _ = normalize_source(cfg, rows, idmap, set(), UNITS, {})
    assert items[0].barcode == "4600000000017"
    assert items[1].barcode is None


def test_unknown_unit_falls_back_to_default(tmp_path: Path):
    cfg = _cfg(columns={"article": "A", "name": "N", "price": "P", "unit": "U"})
    idmap = IdMap(tmp_path / "m.csv")
    items, _ = normalize_source(cfg, [_row(unit="бушель")], idmap, set(), UNITS, {})
    assert items[0].unit == "ШТ"


def test_integer_article_read_as_float_gets_one_identity(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    rows = [_row(article=76862.0), _row(3, article=76862)]
    items, rejected = normalize_source(_cfg(), rows, idmap, set(), UNITS, {})
    assert items[0].article == "76862"
    assert rejected[0].reason == "дубль артикула в прайсе"


def test_min_stock_reports_missing_stock_separately(tmp_path: Path):
    cfg = _cfg(columns={"article": "A", "name": "N", "price": "P", "stock": "S"}, min_stock=1)
    idmap = IdMap(tmp_path / "m.csv")
    items, rejected = normalize_source(cfg, [_row(stock=None)], idmap, set(), UNITS, {})
    assert items == []
    assert rejected[0].reason == "нет данных об остатке"
    assert rejected[0].value == ""


def test_numeric_attribute_is_canonicalised_for_description(tmp_path: Path):
    cfg = _cfg(columns={"article": "A", "name": "N", "price": "P", "diameter": "D"},
               description_template="{name}\nДиаметр: {diameter}")
    idmap = IdMap(tmp_path / "m.csv")
    items, _ = normalize_source(cfg, [_row(diameter=15.0)], idmap, set(), UNITS, {})
    assert items[0].attributes["diameter"] == "15"
    assert items[0].description == "Товар\nДиаметр: 15"


def test_duplicate_article_within_source_rejected(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    items, rejected = normalize_source(_cfg(), [_row(), _row(3)], idmap, set(), UNITS, {})
    assert len(items) == 1
    assert rejected[0].reason == "дубль артикула в прайсе"
