from pathlib import Path
import textwrap
from rtsprice.config import (
    load_companies, load_photo_server, load_sources, load_stoplist,
)


def test_load_sources_applies_defaults(tmp_path: Path):
    p = tmp_path / "sources.yml"
    p.write_text(textwrap.dedent("""
        promet:
          title: "Промет"
          state: on
          prefix: 10
          file: "input/promet/*.xlsx"
          sheets: ["база"]
          header_rows: [1]
          data_starts_at: 2
          row_is_product: price_not_empty
          columns:
            article: "Артикул"
            name: "Наименование"
            price: "Розничная цена"
    """), encoding="utf-8")
    src = load_sources(p)["promet"]
    assert src.code == "promet"
    assert src.state == "on"
    assert src.prefix == 10
    assert src.companies == ("ooo_tlt", "ip")
    assert src.markup == 1.0
    assert src.unit_default == "ШТ"
    assert src.price_includes_vat is True
    assert src.header_rows == (1,)


def test_load_sources_rejects_unknown_state(tmp_path: Path):
    p = tmp_path / "sources.yml"
    p.write_text('x:\n  state: paused\n  prefix: 1\n  file: "a"\n', encoding="utf-8")
    try:
        load_sources(p)
    except ValueError as exc:
        assert "paused" in str(exc)
    else:
        raise AssertionError("ожидалась ValueError")


def test_load_companies(tmp_path: Path):
    d = tmp_path / "companies"
    d.mkdir()
    (d / "ip.yml").write_text(textwrap.dedent("""
        title: "ИП"
        vat_mode: none
        markup: 1.3
        rounding: ruble
        validity_days: 21
        photo_base_url: "https://example.ru/p"
    """), encoding="utf-8")
    c = load_companies(d)["ip"]
    assert c.vat_mode == "none"
    assert c.markup == 1.3
    assert c.delivery_by_carrier == "ДА"


def test_load_stoplist(tmp_path: Path):
    p = tmp_path / "stoplist.csv"
    p.write_text("source,article\npromet,A-1\npromet,A-2\n", encoding="utf-8-sig")
    assert load_stoplist(p) == {("promet", "A-1"), ("promet", "A-2")}


def test_load_sources_rejects_duplicate_prefix(tmp_path: Path):
    """Два источника с одним префиксом сливаются в одну область удаления."""
    p = tmp_path / "sources.yml"
    p.write_text(textwrap.dedent("""
        brinex_wheels:
          prefix: 11
          file: "input/brinex/*.xlsx"
        brinex_tires:
          prefix: 11
          file: "input/brinex/*.xlsx"
    """), encoding="utf-8")
    try:
        load_sources(p)
    except ValueError as exc:
        assert "11" in str(exc)
        assert "brinex_wheels" in str(exc)
        assert "brinex_tires" in str(exc)
    else:
        raise AssertionError("ожидалась ValueError о повторе префикса")


def test_photo_server_absent_is_not_an_error(tmp_path: Path):
    assert load_photo_server(tmp_path / "photos.yml") is None


def test_photo_server_loads_and_expands_home(tmp_path: Path):
    p = tmp_path / "photos.yml"
    p.write_text(textwrap.dedent("""
        host: 213.109.202.45
        user: root
        key_path: "~/.ssh/id_photos"
        remote_root: /var/www/photos
        base_url: "https://example.ru/photos"
    """), encoding="utf-8")
    s = load_photo_server(p)
    assert s.host == "213.109.202.45"
    assert s.port == 22
    assert "~" not in s.key_path
    assert s.key_path.endswith("id_photos")


def test_photo_server_reports_missing_fields(tmp_path: Path):
    p = tmp_path / "photos.yml"
    p.write_text("host: 1.2.3.4\nuser: root\n", encoding="utf-8")
    try:
        load_photo_server(p)
    except ValueError as exc:
        assert "key_path" in str(exc) and "base_url" in str(exc)
    else:
        raise AssertionError("ожидалась ValueError")
