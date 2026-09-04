"""Ссылки на снимки из Open Food Facts."""
import gzip
import json
from pathlib import Path

import pytest

from rtsprice.openfoodfacts import DumpIndex, OpenFoodFactsClient, _key
from rtsprice.photobank import PhotoBankError

TAB = chr(9)
NL = chr(10)


def dump(tmp_path: Path, rows, header=("code", "product_name", "image_url")) -> Path:
    path = tmp_path / "off.csv.gz"
    lines = [TAB.join(header)] + [TAB.join(r) for r in rows]
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write(NL.join(lines) + NL)
    return path


def test_key_aligns_leading_zeros():
    """EAN-8 в базе и он же с ведущими нулями в прайсе — один товар."""
    assert _key("00004601234") == _key("4601234")


def test_dump_finds_requested_codes(tmp_path: Path):
    path = dump(tmp_path, [
        ("4600699502494", "Сок", "https://images.off/a.jpg"),
        ("4650259900185", "Крупа", "https://images.off/b.jpg"),
        ("9999999999999", "Чужое", "https://images.off/c.jpg"),
    ])
    got = DumpIndex(path).images(["4600699502494", "4650259900185", "1111111111111"])
    assert got == {
        "4600699502494": "https://images.off/a.jpg",
        "4650259900185": "https://images.off/b.jpg",
    }


def test_dump_returns_code_as_the_caller_wrote_it(tmp_path: Path):
    """Ключ ответа — штрихкод из прайса, иначе позиция не найдёт свой снимок."""
    path = dump(tmp_path, [("4601234", "Товар", "https://images.off/a.jpg")])
    assert DumpIndex(path).images(["0004601234"]) == {
        "0004601234": "https://images.off/a.jpg"
    }


def test_dump_skips_rows_without_image(tmp_path: Path):
    path = dump(tmp_path, [
        ("4600000000001", "Без снимка", ""),
        ("4600000000002", "Мусор вместо ссылки", "нет"),
    ])
    assert DumpIndex(path).images(["4600000000001", "4600000000002"]) == {}


def test_dump_skips_rows_with_shifted_columns(tmp_path: Path):
    """Сдвинутая строка отдала бы ссылку не того товара — пропускаем."""
    path = tmp_path / "off.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write(TAB.join(("code", "product_name", "image_url")) + NL)
        fh.write("4600000000001" + TAB + "Лишняя" + TAB + "колонка" + TAB + "https://x/a.jpg" + NL)
        fh.write(TAB.join(("4600000000002", "Целая", "https://x/b.jpg")) + NL)
    got = DumpIndex(path).images(["4600000000001", "4600000000002"])
    assert got == {"4600000000002": "https://x/b.jpg"}


def test_dump_reports_missing_column(tmp_path: Path):
    path = dump(tmp_path, [("1", "Товар", "https://x/a.jpg")],
                header=("code", "product_name", "picture"))
    with pytest.raises(PhotoBankError, match="image_url"):
        DumpIndex(path).images(["1"])


def test_dump_reads_the_file_once_regardless_of_how_many_codes(tmp_path: Path):
    path = dump(tmp_path, [(str(4600000000000 + i), "Т", f"https://x/{i}.jpg")
                           for i in range(50)])
    index = DumpIndex(path)
    index.images([str(4600000000000 + i) for i in range(50)])
    assert index.scanned == 50


def test_api_client_reads_front_image_first():
    """Снимок «спереди» — упаковка целиком; общий бывает фрагментом состава."""
    def get(path):
        return json.dumps({"status": 1, "product": {
            "image_url": "https://x/состав.jpg",
            "image_front_url": "https://x/упаковка.jpg",
        }}).encode()

    client = OpenFoodFactsClient(get=get, sleep=lambda _: None)
    assert client.images(["4600699502494"]) == {
        "4600699502494": "https://x/упаковка.jpg"
    }


def test_api_client_counts_products_absent_from_base():
    client = OpenFoodFactsClient(get=lambda p: b'{"status":0}', sleep=lambda _: None)
    assert client.images(["1", "2"]) == {}
    assert client.not_in_base == 2


def test_api_client_does_not_swallow_rate_limiting():
    """429 нельзя принять за «нет в базе»: так сгорели обе первые прикидки."""
    def get(path):
        raise PhotoBankError("Open Food Facts ответил HTTP 429")

    with pytest.raises(PhotoBankError, match="429"):
        OpenFoodFactsClient(get=get, sleep=lambda _: None).images(["1"])
