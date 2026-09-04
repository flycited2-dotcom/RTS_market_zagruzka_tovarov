"""Общая машинерия получения фотографий от поставщиков.

Здесь всё, что не зависит от конкретного поставщика: позиция, которой нужен
снимок, приёмники готовых изображений и сам прогон. Поставщик представлен
одним методом — отдать по коду товара ссылку на фотографию.

Подбора по названию здесь нет и не будет. Соединение только по коду:
ошибочная фотография в карточке хуже отсутствующей, покупатель получит не
то, что видел.
"""
from __future__ import annotations

import hashlib
import json
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Callable, Iterable, Protocol, Sequence

from .config import SourceConfig
from .photos import load_url_map, normalize_image, safe_name, save_url_map
from .readers import RawRow, text_value

TIMEOUT = 120


class PhotoBankError(RuntimeError):
    """Отказ, который не лечится повтором."""


class ImageIndex(Protocol):
    def images(self, codes: Iterable[str]) -> dict[str, str]:
        """Код товара → ссылка на фотографию. Коды без снимка опускаются."""


@dataclass(frozen=True)
class Target:
    """Позиция, которой нужна фотография.

    Отдельный тип, а не `normalize.Item`, намеренно: сборка позиции выдаёт ей
    номер РТС и дописывает его в карту идентификаторов. Скачивание картинок не
    должно раздавать номера — иначе неудачный запуск оставил бы в карте
    номера позиций, которых нет ни в одном выгруженном файле.
    """

    source: str
    article: str
    goods_id: str


def targets(cfg: SourceConfig, rows: Iterable[RawRow], key: str = "goods_id") -> list[Target]:
    """Позиции источника, у которых есть ключ к API, без повторов артикула."""
    seen: set[str] = set()
    out: list[Target] = []
    for row in rows:
        article = text_value(row.values.get("article"))
        code = text_value(row.values.get(key))
        if not article or not code or article in seen:
            continue
        seen.add(article)
        out.append(Target(cfg.code, article, code))
    return out


@dataclass
class FetchReport:
    asked: int = 0
    without_key: int = 0
    no_image: int = 0
    already_have: int = 0
    saved: int = 0
    distinct_urls: int = 0
    failed: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [
            f"позиций с кодом товара:      {self.asked}",
            f"без кода товара:             {self.without_key}",
            f"уникальных ссылок на фото:   {self.distinct_urls}",
            f"API не дал фотографии:       {self.no_image}",
            f"уже было на диске:           {self.already_have}",
            f"сохранено:                   {self.saved}",
            f"не скачалось:                {len(self.failed)}",
        ]


def download_image(url: str, timeout: int = 60, hops: int = 3) -> bytes:
    """Скачать файл с CDN поставщика.

    Через `urllib` нельзя: он добавляет к запросу «Connection: close», и CDN
    на этом заголовке перестаёт досылать хвост файла, отдаваемого по частям.
    Замерено: 221 184 байта из 222 509 и зависание до таймаута. Тот же адрес
    через `http.client` без этого заголовка отдаётся целиком за 0,3 секунды.
    """
    import http.client
    import ssl

    parsed = urllib.parse.urlsplit(url)
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    connection = http.client.HTTPSConnection(
        parsed.hostname, parsed.port or 443,
        timeout=timeout, context=ssl.create_default_context(),
    )
    try:
        connection.request("GET", path, headers={
            "Host": parsed.hostname, "User-Agent": "rtsprice/1.0", "Accept": "*/*",
        })
        response = connection.getresponse()
        if response.status in (301, 302, 303, 307, 308) and hops > 0:
            location = response.getheader("Location") or ""
            response.read()
            return _download(urllib.parse.urljoin(url, location), timeout, hops - 1)
        if response.status != 200:
            raise PhotoBankError(f"CDN ответил HTTP {response.status}")
        return response.read()
    finally:
        connection.close()


class DiskSink:
    """Складывать снимки в каталог `photos/`, как это делают прочие источники."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def has(self, target: Target) -> bool:
        return photo_path(self.root, target.source, target.article).exists()

    def put(self, group: Sequence[Target], data: bytes) -> int:
        for target in group:
            path = photo_path(self.root, target.source, target.article)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return len(group)

    def save(self) -> None:
        return None


class PublishSink:
    """Публиковать снимки сразу и хранить только карту ссылок.

    У Бринэкса почти три гигабайта изображений. Держать их копию на рабочей
    машине незачем: файл нужен ровно один раз — чтобы уехать на сервер. Имя
    файла на сервере — хэш содержимого, поэтому одинаковые снимки разных
    позиций занимают там одно место и загружаются один раз.
    """

    def __init__(self, publisher, manifest_path: Path, url_map_path: Path,
                 prefix: str = "brinex", save_every: int = 200) -> None:
        self.publisher = publisher
        self.save_every = save_every
        self.manifest_path = Path(manifest_path)
        self.url_map_path = Path(url_map_path)
        self.prefix = prefix
        self.manifest: dict[str, str] = {}
        if self.manifest_path.exists():
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.url_map = load_url_map(self.url_map_path)
        self.uploaded = 0
        self._lock = Lock()

    def has(self, target: Target) -> bool:
        return bool(self.url_map.get(f"{target.source}/{target.article}"))

    def put(self, group: Sequence[Target], data: bytes) -> int:
        digest = hashlib.sha256(data).hexdigest()
        # Публикация идёт под замком: канал SFTP один, и параллельная запись
        # в него перемешала бы файлы. Медленная часть — скачивание, она
        # остаётся параллельной.
        with self._lock:
            url = self.manifest.get(digest)
            if url is None:
                url = self.publisher.publish(f"{self.prefix}/{digest}.jpg", data)
                self.manifest[digest] = url
                self.uploaded += 1
            for target in group:
                self.url_map[f"{target.source}/{target.article}"] = [url]
            # Карта пишется по ходу дела, а не только в конце: полная выгрузка
            # Бринэкса идёт часами, и обрыв на середине не должен стоить всего
            # уже загруженного — иначе повторный запуск зальёт то же самое.
            if self.save_every and self.uploaded % self.save_every == 0:
                self._save_unlocked()
        return len(group)

    def _save_unlocked(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        save_url_map(self.url_map_path, self.url_map)

    def save(self) -> None:
        with self._lock:
            self._save_unlocked()


def photo_path(root: Path, source: str, article: str) -> Path:
    """Куда лёг бы снимок позиции. Имя обязано совпадать с тем, что ищет
    `photos.find_photos`, иначе скачанное просто не попадёт в выгрузку."""
    return Path(root) / safe_name(source) / f"{safe_name(article)}_1.jpg"


def fetch_photos(
    wanted: Sequence[Target],
    client: ImageIndex,
    sink: DiskSink | PublishSink,
    download: Callable[[str], bytes] = download_image,
    limit: int | None = None,
    workers: int = 6,
    give_up_after: int = 50,
    log: Callable[[str], None] = lambda _: None,
) -> FetchReport:
    """Забрать фотографии позиций у поставщика и отдать их приёмнику.

    Повторный запуск не трогает то, что уже получено: адаптер можно прерывать
    и продолжать. Одна ссылка скачивается ровно один раз, даже если её делят
    сотни позиций — у шин одной модели поставщик отдаёт общий снимок.
    """
    report = FetchReport()

    todo: list[Target] = []
    for target in wanted:
        if not target.goods_id:
            report.without_key += 1
            continue
        if sink.has(target):
            report.already_have += 1
            continue
        todo.append(target)
        if limit is not None and len(todo) >= limit:
            break

    report.asked = len(todo)
    if not todo:
        return report

    log(f"запрашиваю API по {len(todo)} позициям…")
    urls = client.images(t.goods_id for t in todo)

    # Ссылка скачивается один раз, даже если её делят десятки позиций: у шин
    # одной модели снимок протектора общий на всю линейку размеров.
    by_url: dict[str, list[Target]] = {}
    for target in todo:
        url = urls.get(target.goods_id)
        if url:
            by_url.setdefault(url, []).append(target)
        else:
            report.no_image += 1
    report.distinct_urls = len(by_url)
    log(f"API вернул {len(urls)} ссылок, уникальных {report.distinct_urls}")

    def one(url: str) -> tuple[int, str | None]:
        """Забрать один снимок и отдать его всем своим позициям."""
        group = by_url[url]
        try:
            data = normalize_image(download(url))
        except Exception as exc:
            return 0, f"{group[0].source}/{group[0].article}: {type(exc).__name__} {exc}"
        try:
            return sink.put(group, data), None
        except Exception as exc:
            return 0, f"{group[0].source}/{group[0].article}: {type(exc).__name__} {exc}"

    done = 0
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for saved, failure in pool.map(one, list(by_url)):
                report.saved += saved
                if failure:
                    # Первый сбой виден сразу, а не в отчёте через два часа:
                    # прогон, где не получается ничего, обязан объясниться
                    # немедленно.
                    if len(report.failed) == 0:
                        log(f"сбой: {failure}")
                    report.failed.append(failure)
                done += 1
                if done % 200 == 0:
                    log(f"обработано ссылок {done} из {report.distinct_urls}, "
                        f"получено {report.saved}…")
                if done >= give_up_after and report.saved == 0:
                    raise PhotoBankError(
                        f"из первых {done} ссылок не получено ни одной. "
                        f"Прогон остановлен, чтобы не тратить часы впустую. "
                        f"Первый сбой: {report.failed[0] if report.failed else '—'}"
                    )
    finally:
        # Сохраняем и после обрыва: полученное не должно пропасть, иначе
        # прерванный запуск заставит скачивать и заливать всё заново.
        sink.save()
    return report
