import io
from pathlib import Path

from PIL import Image

from rtsprice.normalize import Item
from rtsprice.photos import (
    LocalPublisher, PhotoResolver, find_photos, normalize_image, safe_name,
)


def _png(size: tuple[int, int] = (40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _item(article: str = "A-1") -> Item:
    return Item(source="promet", article=article, rts_id=1, name="N",
                description="D", price_in=1.0, unit="ШТ")


def test_find_photos_orders_by_suffix(tmp_path: Path):
    d = tmp_path / "promet"
    d.mkdir()
    for suffix in ("_3", "_1", "_2"):
        (d / f"A-1{suffix}.jpg").write_bytes(b"x")
    found = find_photos(tmp_path, "promet", "A-1", limit=5)
    assert [p.name for p in found] == ["A-1_1.jpg", "A-1_2.jpg", "A-1_3.jpg"]


def test_find_photos_respects_limit(tmp_path: Path):
    d = tmp_path / "promet"
    d.mkdir()
    for i in range(1, 8):
        (d / f"A-1_{i}.jpg").write_bytes(b"x")
    assert len(find_photos(tmp_path, "promet", "A-1", limit=5)) == 5


def test_find_photos_missing_returns_empty(tmp_path: Path):
    assert find_photos(tmp_path, "promet", "нет-такого", limit=5) == []


def test_normalize_image_converts_to_jpeg_and_resizes():
    data = normalize_image(_png((3000, 1500)), max_side=1600, max_bytes=1_000_000)
    image = Image.open(io.BytesIO(data))
    assert image.format == "JPEG"
    assert max(image.size) == 1600
    assert len(data) <= 1_000_000


def test_normalize_image_keeps_small_image_dimensions():
    image = Image.open(io.BytesIO(normalize_image(_png((40, 30)), 1600, 1_000_000)))
    assert image.size == (40, 30)


def test_resolver_publishes_and_returns_urls(tmp_path: Path):
    photos = tmp_path / "photos" / "promet"
    photos.mkdir(parents=True)
    (photos / "A-1_1.jpg").write_bytes(_png())
    (photos / "A-1_2.jpg").write_bytes(_png((50, 50)))

    publisher = LocalPublisher(tmp_path / "published", "https://example.ru/p")
    resolver = PhotoResolver(tmp_path / "photos", publisher, tmp_path / "photos.json")
    urls = resolver.urls_for(_item())

    assert urls == ["https://example.ru/p/promet/A-1_1.jpg",
                    "https://example.ru/p/promet/A-1_2.jpg"]
    assert (tmp_path / "published" / "promet" / "A-1_1.jpg").exists()


def test_resolver_skips_unchanged_files_on_second_run(tmp_path: Path):
    photos = tmp_path / "photos" / "promet"
    photos.mkdir(parents=True)
    (photos / "A-1_1.jpg").write_bytes(_png())

    publisher = LocalPublisher(tmp_path / "published", "https://example.ru/p")
    manifest = tmp_path / "photos.json"

    first = PhotoResolver(tmp_path / "photos", publisher, manifest)
    first.urls_for(_item())
    first.save()
    assert publisher.uploads == 1

    second = PhotoResolver(tmp_path / "photos", publisher, manifest)
    second.urls_for(_item())
    assert publisher.uploads == 1


def test_resolver_sanitizes_article_in_remote_name(tmp_path: Path):
    photos = tmp_path / "photos" / "promet"
    photos.mkdir(parents=True)
    (photos / "S5Z706_1.jpg").write_bytes(_png())
    publisher = LocalPublisher(tmp_path / "published", "https://example.ru/p")
    resolver = PhotoResolver(tmp_path / "photos", publisher, tmp_path / "photos.json")
    assert resolver.urls_for(_item("S5Z706")) == ["https://example.ru/p/promet/S5Z706_1.jpg"]


def test_safe_name_keeps_distinct_cyrillic_articles_distinct():
    assert safe_name("деталь-55") != safe_name("штука-55")
    assert safe_name("Ту-00000406") != safe_name("Гу-00000406")
    assert safe_name("S5Z706") == "S5Z706"


def test_remote_name_uses_file_number_not_position(tmp_path: Path):
    photos = tmp_path / "photos" / "promet"
    photos.mkdir(parents=True)
    (photos / "A-1_1.jpg").write_bytes(_png())
    (photos / "A-1_3.jpg").write_bytes(_png((60, 60)))

    publisher = LocalPublisher(tmp_path / "published", "https://example.ru/p")
    resolver = PhotoResolver(tmp_path / "photos", publisher, tmp_path / "photos.json")
    assert resolver.urls_for(_item()) == [
        "https://example.ru/p/promet/A-1_1.jpg",
        "https://example.ru/p/promet/A-1_3.jpg",
    ]


def test_resolver_returns_empty_without_photos(tmp_path: Path):
    publisher = LocalPublisher(tmp_path / "published", "https://example.ru/p")
    resolver = PhotoResolver(tmp_path / "photos", publisher, tmp_path / "photos.json")
    assert resolver.urls_for(_item()) == []


def test_resolver_prefers_ready_urls_over_local_files(tmp_path):
    """Ссылки от адаптера поставщика используются как есть, без публикации."""
    from rtsprice.normalize import Item
    from rtsprice.photos import PhotoResolver, load_url_map, save_url_map

    save_url_map(tmp_path / "urls.json",
                 {"brinex_tires/A-1": ["https://splithome.ru/rts-photos/brinex/x.jpg"]})
    resolver = PhotoResolver(
        tmp_path, publisher=None, manifest_path=tmp_path / "photos.json",
        url_map=load_url_map(tmp_path / "urls.json"),
    )
    item = Item(source="brinex_tires", article="A-1", rts_id=120000001,
                name="шина", description="", price_in=1.0, unit="ШТ")
    assert resolver.urls_for(item) == ["https://splithome.ru/rts-photos/brinex/x.jpg"]

    other = Item(source="brinex_tires", article="A-2", rts_id=120000002,
                 name="шина", description="", price_in=1.0, unit="ШТ")
    assert resolver.urls_for(other) == []


def test_url_map_absent_is_not_an_error(tmp_path):
    from rtsprice.photos import load_url_map

    assert load_url_map(tmp_path / "urls.json") == {}
