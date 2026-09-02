import pytest

from rtsprice.config import CompanyConfig, SourceConfig
from rtsprice.normalize import Item
from rtsprice.pricing import (
    final_price, markup_for, round_price, to_gross, to_net, vat_label,
)


def _src(**over) -> SourceConfig:
    base = dict(code="s", title="S", state="on", prefix=7, companies=("ooo_tlt",),
                file_glob="x", sheets=(), header_rows=(1,), data_starts_at=2,
                row_is_product="price_not_empty", columns={})
    base.update(over)
    return SourceConfig(**base)


def _co(**over) -> CompanyConfig:
    base = dict(code="ooo_tlt", title="ООО", vat_mode="vat22")
    base.update(over)
    return CompanyConfig(**base)


def _item(price: float, group: str | None = None) -> Item:
    return Item(source="s", article="A", rts_id=1, name="N", description="D",
                price_in=price, unit="ШТ", group=group)


def test_to_net_strips_vat_when_included():
    assert to_net(122.0, includes_vat=True) == pytest.approx(100.0)


def test_to_net_keeps_price_when_vat_excluded():
    assert to_net(100.0, includes_vat=False) == 100.0


def test_to_gross_keeps_price_that_already_includes_vat():
    assert to_gross(122.0, includes_vat=True) == 122.0


def test_to_gross_adds_vat_when_supplier_price_is_net():
    assert to_gross(100.0, includes_vat=False) == pytest.approx(122.0)


def test_round_price_rules():
    assert round_price(100.2, "ruble") == 101.0
    assert round_price(100.0, "ruble") == 100.0
    assert round_price(101.0, "ten") == 110.0
    assert round_price(100.234, "none") == pytest.approx(100.24)


def test_markup_prefers_group_then_source_then_company():
    src = _src(markup=1.2, markup_by_group={"Сейфы": 1.5})
    company = _co(markup=1.1)
    assert markup_for(src, "Сейфы", company) == 1.5
    assert markup_for(src, "Прочее", company) == 1.2
    assert markup_for(_src(), "Прочее", company) == 1.1


def test_markup_zero_in_group_is_honoured_not_ignored():
    src = _src(markup=1.2, markup_by_group={"Промо": 0.0})
    assert markup_for(src, "Промо", _co(markup=1.1)) == 0.0


def test_round_price_none_rounds_up_not_to_even():
    assert round_price(100.234, "none") == pytest.approx(100.24)
    assert round_price(100.23, "none") == pytest.approx(100.23)
    assert round_price(0.125, "none") == pytest.approx(0.13)


def test_final_price_for_company_with_vat():
    # закупка 122 с НДС -> 100 без НДС -> наценка 1.2 -> 120 -> плюс НДС 22% -> 146.4 -> 147
    price = final_price(_item(122.0), _src(markup=1.2), _co(rounding="ruble"))
    assert price == 147.0


def test_final_price_for_company_without_vat_uses_gross_base():
    # ИП не возмещает НДС: закупка 122 с НДС -> наценка 1.3 -> 158.6 -> 159
    price = final_price(_item(122.0), _src(markup=1.3), _co(vat_mode="none", rounding="ruble"))
    assert price == 159.0


def test_markup_100_percent_doubles_supplier_price_for_both_companies():
    item = _item(122.0)
    assert final_price(item, _src(), _co(markup=2.0, rounding="none")) == pytest.approx(244.0)
    assert final_price(
        item, _src(), _co(vat_mode="none", markup=2.0, rounding="none")
    ) == pytest.approx(244.0)


def test_final_price_when_supplier_price_excludes_vat():
    src = _src(markup=1.0, price_includes_vat=False)
    assert final_price(_item(100.0), src, _co(rounding="none")) == pytest.approx(122.0)


def test_vat_label():
    assert vat_label(_co()) == "Облагается НДС 22"
    assert vat_label(_co(vat_mode="none")) == "Не облагается НДС"
