from rtsprice.config import CompanyConfig, SourceConfig
from rtsprice.normalize import Item
from rtsprice.render import (
    C_BARCODE, C_DELETE, C_DESCRIPTION, C_FILE_FIRST, C_ID, C_IMAGE_FIRST, C_NAME,
    C_PRICE, C_REGION, C_UNIT, C_VALIDITY, C_VAT, COLUMN_COUNT, HEADER_ROW_1,
    HEADER_ROW_2, render_row,
)


def _src(**over) -> SourceConfig:
    base = dict(code="s", title="S", state="on", prefix=7, companies=("ooo_tlt",),
                file_glob="x", sheets=(), header_rows=(1,), data_starts_at=2,
                row_is_product="price_not_empty", columns={}, markup=1.0)
    base.update(over)
    return SourceConfig(**base)


def _co(**over) -> CompanyConfig:
    base = dict(code="ooo_tlt", title="ООО", vat_mode="vat22", rounding="ruble",
                validity_days=21, warehouse_address="г Москва")
    base.update(over)
    return CompanyConfig(**base)


def _item(**over) -> Item:
    base = dict(source="s", article="A-1", rts_id=70_000_001, name="Товар",
                description="Описание", price_in=122.0, unit="ШТ")
    base.update(over)
    return Item(**base)


def test_headers_have_expected_width_and_anchors():
    assert len(HEADER_ROW_1) == COLUMN_COUNT == 27
    assert len(HEADER_ROW_2) == COLUMN_COUNT
    assert HEADER_ROW_1[C_NAME].startswith("Наименование позиции")
    assert HEADER_ROW_1[C_PRICE].startswith("Цена за единицу (с НДС)")
    assert HEADER_ROW_1[C_DELETE] == "Удалить"
    assert HEADER_ROW_2[9] == "Доставка транспортной компанией"


def test_render_row_places_core_fields():
    row = render_row(_item(), _src(), _co(), [])
    assert len(row) == COLUMN_COUNT
    assert row[C_ID] == 70_000_001
    assert row[C_NAME] == "Товар"
    assert row[C_DESCRIPTION] == "Описание"
    assert row[C_UNIT] == "ШТ"
    assert row[C_PRICE] == 122.0
    assert row[C_VAT] == "Облагается НДС 22"
    assert row[C_VALIDITY] == 21
    assert row[C_DELETE] is None


def test_render_row_for_company_without_vat():
    row = render_row(_item(), _src(markup=1.0), _co(code="ip", vat_mode="none"))
    assert row[C_VAT] == "Не облагается НДС"
    assert row[C_PRICE] == 122.0


def test_render_row_places_photos_in_order():
    row = render_row(_item(), _src(), _co(), ["u1", "u2"])
    assert row[C_IMAGE_FIRST] == "u1"
    assert row[C_IMAGE_FIRST + 1] == "u2"
    assert row[C_IMAGE_FIRST + 2] is None


def test_render_row_limits_photos_to_five():
    row = render_row(_item(), _src(), _co(), [f"u{i}" for i in range(9)])
    assert row[C_IMAGE_FIRST + 4] == "u4"
    assert row[C_BARCODE] is None


def test_render_row_treats_zero_validity_as_no_expiry():
    row = render_row(_item(), _src(), _co(validity_days=0), [])
    assert row[C_VALIDITY] is None


def test_render_row_places_files_in_order_and_caps_at_three():
    row = render_row(_item(), _src(), _co(), [], ["f1", "f2", "f3", "f4"])
    assert row[C_FILE_FIRST] == "f1"
    assert row[C_FILE_FIRST + 1] == "f2"
    assert row[C_FILE_FIRST + 2] == "f3"
    assert row[C_BARCODE] is None


def test_render_row_drops_region_for_foreign_country():
    row = render_row(_item(country="Китай", region="Московская область"), _src(), _co(), [])
    assert row[C_REGION] is None


def test_render_row_keeps_region_for_russia():
    row = render_row(_item(country="Россия", region="Московская область"), _src(), _co(), [])
    assert row[C_REGION] == "Московская область"
