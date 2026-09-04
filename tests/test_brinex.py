"""Адаптер изображений Бринэкса."""
import io
import json
import textwrap
import urllib.error
from pathlib import Path

import pytest
from PIL import Image

from rtsprice.brinex import (
    BrinexClient, BrinexConfig, BrinexError, DiskSink, PublishSink, Target,
    fetch_photos, load_brinex, photo_path, targets,
)
from rtsprice.config import load_sources
from rtsprice.readers import RawRow


CONFIG = BrinexConfig(token="секрет", base_url="https://api.example/v2")


def picture(color=(10, 120, 200), size=(40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def responder(pages):
    """Опросчик, отдающий заготовленные ответы и запоминающий запросы."""
    calls = []

    def opener(url, headers, timeout):
        calls.append((url, headers))
        return json.dumps(pages.pop(0), ensure_ascii=False).encode("utf-8")

    opener.calls = calls
    return opener


def test_load_brinex_absent_is_not_an_error(tmp_path: Path):
    assert load_brinex(tmp_path / "brinex.yml") is None


def test_load_brinex_requires_token(tmp_path: Path):
    p = tmp_path / "brinex.yml"
    p.write_text("base_url: https://api.example/v2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="token"):
        load_brinex(p)


def test_client_sends_bearer_and_reads_img_url():
    opener = responder([[{"goods_id": 95005, "img_url": "https://cdn/a.jpg"}]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    assert client.images(["95005"]) == {"95005": "https://cdn/a.jpg"}
    url, headers = opener.calls[0]
    assert headers["Authorization"] == "Bearer секрет"
    assert "goods_id%5B%5D=95005" in url


def test_client_skips_records_without_image():
    opener = responder([[
        {"goods_id": 1, "img_url": "https://cdn/a.jpg"},
        {"goods_id": 2, "img_url": ""},
        {"goods_id": 3},
    ]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    assert client.images(["1", "2", "3"]) == {"1": "https://cdn/a.jpg"}


def test_client_splits_into_batches():
    """При 500 кодах в одном запросе сервер отвечает 414, поэтому дробим."""
    opener = responder([[], []])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None, batch=2)
    client.images(["1", "2", "3"])
    assert len(opener.calls) == 2
    assert "goods_id%5B%5D=3" in opener.calls[1][0]


def test_client_deduplicates_codes():
    opener = responder([[]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    client.images(["7", "7", "7"])
    assert opener.calls[0][0].count("goods_id") == 1


def test_client_does_not_retry_forbidden():
    """403 — запрос не с разрешённого IP. Повтор не поможет и только жжёт квоту."""
    def opener(url, headers, timeout):
        opener.tries += 1
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    opener.tries = 0
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    with pytest.raises(BrinexError, match="IP"):
        client.images(["1"])
    assert opener.tries == 1


def test_client_retries_temporary_failure():
    calls = {"n": 0}

    def opener(url, headers, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("нет ответа")
        return json.dumps([{"goods_id": 1, "img_url": "https://cdn/a.jpg"}]).encode()

    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    assert client.images(["1"]) == {"1": "https://cdn/a.jpg"}
    assert calls["n"] == 3


def test_client_rejects_non_list_answer():
    """На пустой поиск API отвечает объектом с message и кодом 200.

    Принять это за «фотографий нет» значило бы молча потерять весь пакет.
    """
    opener = responder([{"status": "error", "message": "Не передан параметр"}])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    with pytest.raises(BrinexError, match="неожиданный ответ"):
        client.images(["1"])


def test_targets_skips_rows_without_key_and_duplicates(tmp_path: Path):
    p = tmp_path / "sources.yml"
    p.write_text(textwrap.dedent("""
        brinex_tires:
          prefix: 12
          file: "input/brinex/*.xlsx"
          columns:
            article: "Артикул"
            goods_id: "Код товара (goods_id)"
    """), encoding="utf-8")
    cfg = load_sources(p)["brinex_tires"]
    rows = [
        RawRow("brinex_tires", "л", 1, {"article": "A-1", "goods_id": "100"}),
        RawRow("brinex_tires", "л", 2, {"article": "A-2", "goods_id": None}),
        RawRow("brinex_tires", "л", 3, {"article": "A-1", "goods_id": "100"}),
        RawRow("brinex_tires", "л", 4, {"article": None, "goods_id": "101"}),
    ]
    assert targets(cfg, rows) == [Target("brinex_tires", "A-1", "100")]


def test_fetch_downloads_each_url_once(tmp_path: Path):
    """У шин одной модели рисунок протектора общий: ссылка одна на всю линейку."""
    opener = responder([[
        {"goods_id": 1, "img_url": "https://cdn/tread.jpg"},
        {"goods_id": 2, "img_url": "https://cdn/tread.jpg"},
        {"goods_id": 3, "img_url": "https://cdn/other.jpg"},
    ]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    downloads = []

    def download(url):
        downloads.append(url)
        return picture()

    report = fetch_photos(
        [Target("brinex_tires", f"A-{i}", str(i)) for i in (1, 2, 3)],
        client, DiskSink(tmp_path), download=download,
    )
    assert report.saved == 3
    assert report.distinct_urls == 2
    assert len(downloads) == 2
    assert (tmp_path / "brinex_tires" / "A-1_1.jpg").exists()
    assert (tmp_path / "brinex_tires" / "A-2_1.jpg").exists()


def test_fetch_is_idempotent(tmp_path: Path):
    """Второй запуск ничего не качает: адаптер можно прерывать и продолжать."""
    opener = responder([[{"goods_id": 1, "img_url": "https://cdn/a.jpg"}], []])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    wanted = [Target("brinex_wheels", "A-1", "1")]

    first = fetch_photos(wanted, client, DiskSink(tmp_path), download=lambda u: picture())
    assert first.saved == 1

    def refuse(url):
        raise AssertionError("повторное скачивание уже имеющегося файла")

    second = fetch_photos(wanted, client, DiskSink(tmp_path), download=refuse)
    assert second.saved == 0 and second.already_have == 1
    assert len(opener.calls) == 1


def test_fetch_counts_positions_without_photo(tmp_path: Path):
    opener = responder([[{"goods_id": 1, "img_url": "https://cdn/a.jpg"}]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    report = fetch_photos(
        [Target("brinex_wheels", "A-1", "1"), Target("brinex_wheels", "A-2", "2")],
        client, DiskSink(tmp_path), download=lambda u: picture(),
    )
    assert report.saved == 1 and report.no_image == 1


def test_fetch_survives_broken_download(tmp_path: Path):
    """Один битый файл не должен обрывать скачивание остальных."""
    opener = responder([[
        {"goods_id": 1, "img_url": "https://cdn/bad.jpg"},
        {"goods_id": 2, "img_url": "https://cdn/ok.jpg"},
    ]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)

    def download(url):
        if "bad" in url:
            return "это не картинка".encode("utf-8")
        return picture()

    report = fetch_photos(
        [Target("brinex_wheels", "A-1", "1"), Target("brinex_wheels", "A-2", "2")],
        client, DiskSink(tmp_path), download=download,
    )
    assert report.saved == 1
    assert len(report.failed) == 1 and "A-1" in report.failed[0]


def test_fetch_limit_bounds_the_probe(tmp_path: Path):
    opener = responder([[{"goods_id": 1, "img_url": "https://cdn/a.jpg"}]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    report = fetch_photos(
        [Target("brinex_wheels", f"A-{i}", str(i)) for i in range(1, 6)],
        client, DiskSink(tmp_path), download=lambda u: picture(), limit=1,
    )
    assert report.asked == 1
    assert opener.calls[0][0].count("goods_id") == 1


def test_saved_name_matches_what_the_build_looks_for(tmp_path: Path):
    """Имя файла обязано совпадать с шаблоном photos.find_photos.

    Разойдись они — скачанное просто не попало бы в выгрузку, и молча.
    """
    from rtsprice.photos import find_photos

    opener = responder([[{"goods_id": 1, "img_url": "https://cdn/a.jpg"}]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    fetch_photos([Target("brinex_wheels", "r14253", "1")], client,
                 DiskSink(tmp_path), download=lambda u: picture())
    assert find_photos(tmp_path, "brinex_wheels", "r14253") == [
        photo_path(tmp_path, "brinex_wheels", "r14253")
    ]


def test_downloaded_image_is_normalized(tmp_path: Path):
    """Площадка ограничивает размер: адаптер обязан ужимать, а не класть как есть."""
    opener = responder([[{"goods_id": 1, "img_url": "https://cdn/a.jpg"}]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    fetch_photos([Target("brinex_wheels", "A-1", "1")], client,
                 DiskSink(tmp_path), download=lambda u: picture(size=(4000, 3000)))
    saved = Image.open(photo_path(tmp_path, "brinex_wheels", "A-1"))
    assert max(saved.size) <= 1600


class Recorder:
    """Издатель, запоминающий загрузки. Заменяет SFTP в тестах."""

    def __init__(self):
        self.files = {}

    def publish(self, remote_name, data):
        self.files[remote_name] = data
        return f"https://example.ru/p/{remote_name}"


def test_publish_sink_uploads_identical_image_once(tmp_path: Path):
    """Одинаковые снимки разных позиций занимают на сервере одно место."""
    opener = responder([[
        {"goods_id": 1, "img_url": "https://cdn/a.jpg"},
        {"goods_id": 2, "img_url": "https://cdn/b.jpg"},
    ]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    recorder = Recorder()
    sink = PublishSink(recorder, tmp_path / "photos.json", tmp_path / "urls.json")

    report = fetch_photos(
        [Target("brinex_tires", "A-1", "1"), Target("brinex_tires", "A-2", "2")],
        client, sink, download=lambda u: picture(),
    )
    assert report.saved == 2
    assert len(recorder.files) == 1          # два адреса, одно содержимое
    assert sink.uploaded == 1
    urls = json.loads((tmp_path / "urls.json").read_text(encoding="utf-8"))
    assert urls["brinex_tires/A-1"] == urls["brinex_tires/A-2"]


def test_publish_sink_keeps_nothing_on_disk(tmp_path: Path):
    """Ради этого всё и затевалось: три гигабайта не оседают на рабочей машине."""
    opener = responder([[{"goods_id": 1, "img_url": "https://cdn/a.jpg"}]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    sink = PublishSink(Recorder(), tmp_path / "photos.json", tmp_path / "urls.json")
    fetch_photos([Target("brinex_wheels", "A-1", "1")], client, sink,
                 download=lambda u: picture())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["photos.json", "urls.json"]


def test_publish_sink_resumes_after_interruption(tmp_path: Path):
    opener = responder([[{"goods_id": 1, "img_url": "https://cdn/a.jpg"}], []])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    wanted = [Target("brinex_wheels", "A-1", "1")]
    first = PublishSink(Recorder(), tmp_path / "photos.json", tmp_path / "urls.json")
    fetch_photos(wanted, client, first, download=lambda u: picture())

    second = PublishSink(Recorder(), tmp_path / "photos.json", tmp_path / "urls.json")
    report = fetch_photos(wanted, client, second, download=lambda u: picture())
    assert report.already_have == 1 and report.saved == 0


def test_map_saved_even_when_download_breaks_midway(tmp_path: Path):
    """Оборвалось на середине — уже полученное обязано уцелеть."""
    opener = responder([[
        {"goods_id": 1, "img_url": "https://cdn/ok.jpg"},
        {"goods_id": 2, "img_url": "https://cdn/bad.jpg"},
    ]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)
    sink = PublishSink(Recorder(), tmp_path / "photos.json", tmp_path / "urls.json")

    def download(url):
        if "bad" in url:
            raise TimeoutError("оборвалось")
        return picture()

    fetch_photos([Target("brinex_wheels", "A-1", "1"), Target("brinex_wheels", "A-2", "2")],
                 client, sink, download=download, workers=1)
    urls = json.loads((tmp_path / "urls.json").read_text(encoding="utf-8"))
    assert "brinex_wheels/A-1" in urls and "brinex_wheels/A-2" not in urls


def test_fetch_stops_when_nothing_at_all_comes_through(tmp_path: Path):
    """Прогон, где не получается ничего, обязан остановиться сразу.

    Именно так и вышло на первой полной выгрузке: соединение SFTP простояло
    десять минут, пока шёл опрос API, сервер его закрыл, и процесс два часа
    честно отчитывался о прогрессе, не сохранив ни одного файла.
    """
    opener = responder([[
        {"goods_id": i, "img_url": f"https://cdn/{i}.jpg"} for i in range(1, 21)
    ]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)

    def download(url):
        raise OSError("канал закрыт сервером")

    with pytest.raises(BrinexError, match="не получено ни одной"):
        fetch_photos([Target("brinex_wheels", f"A-{i}", str(i)) for i in range(1, 21)],
                     client, DiskSink(tmp_path), download=download,
                     workers=1, give_up_after=10)


def test_fetch_does_not_stop_while_something_works(tmp_path: Path):
    opener = responder([[
        {"goods_id": i, "img_url": f"https://cdn/{i}.jpg"} for i in range(1, 21)
    ]])
    client = BrinexClient(CONFIG, opener=opener, sleep=lambda _: None)

    def download(url):
        if url.endswith("/1.jpg"):
            return picture()
        raise OSError("сбой")

    report = fetch_photos(
        [Target("brinex_wheels", f"A-{i}", str(i)) for i in range(1, 21)],
        client, DiskSink(tmp_path), download=download, workers=1, give_up_after=10,
    )
    assert report.saved == 1 and len(report.failed) == 19
