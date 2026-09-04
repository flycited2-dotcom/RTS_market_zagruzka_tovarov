"""Изображения товаров из API Бринэкса.

Соединение точное: `goods_id` из прайса — это `goods_id` в API. Нечёткого
сопоставления по названию здесь нет, а значит нет и риска подставить в
карточку чужой снимок. Ошибочная фотография хуже отсутствующей: покупатель
получит не то, что видел.

Модуль только складывает файлы в каталог фотографий. Публикацией и
дедупликацией занимается `photos.PhotoResolver` во время сборки — так
скачивание можно повторять и прерывать, не трогая ни один прайс-лист.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Callable, Iterable, Sequence

from .config import SourceConfig
from .photos import load_url_map, normalize_image, safe_name, save_url_map
from .readers import RawRow, text_value

# При 500 кодах в запросе сервер отвечает 414 Request-URI Too Large,
# при 200 — работает. Замерено, а не взято из документации.
BATCH = 200
PAUSE = 0.4
ATTEMPTS = 3
BACKOFF = 3.0
TIMEOUT = 120

#: Коды, при которых повторять запрос бессмысленно: дело не в сети.
#: 401 — токен неверен, 403 — запрос идёт не с разрешённого IP.
FATAL_STATUS = (401, 403)


class BrinexError(RuntimeError):
    """Отказ, который не лечится повтором."""


@dataclass(frozen=True)
class BrinexConfig:
    """Доступ к API. Живёт в `brinex.yml`, который не попадает в git.

    Токен выдаёт менеджер поставщика под конкретный статический IP-адрес,
    поэтому запрос с любой другой машины получает 403 независимо от того,
    насколько верен сам токен. `via_photo_server` направляет запросы через
    тот самый сервер, куда и так публикуются фотографии.
    """

    token: str
    base_url: str = "https://api.brinex.ru/v2"
    via_photo_server: bool = False


def load_brinex(path: Path) -> BrinexConfig | None:
    """Прочитать доступ к API, если он задан. Отсутствие файла — не ошибка."""
    import yaml

    p = Path(path)
    if not p.exists():
        return None
    body = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    token = str(body.get("token") or "").strip()
    if not token:
        raise ValueError(f"{p.name}: не задан token")
    return BrinexConfig(
        token=token,
        base_url=str(body.get("base_url") or "https://api.brinex.ru/v2").rstrip("/"),
        via_photo_server=bool(body.get("via_photo_server")),
    )


def _open(url: str, headers: dict[str, str], timeout: int) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


#: Запускается на сервере. Адрес и заголовки приходят на стандартный вход, а
#: не в аргументах командной строки: аргументы видны в списке процессов всем
#: пользователям машины, и токен утёк бы туда при каждом запросе.
_REMOTE = """
import json, sys, urllib.error, urllib.request
p = json.load(sys.stdin)
try:
    r = urllib.request.Request(p["url"], headers=p["headers"])
    sys.stdout.buffer.write(urllib.request.urlopen(r, timeout=p["timeout"]).read())
except urllib.error.HTTPError as e:
    sys.stderr.write("HTTPSTATUS %d" % e.code)
    sys.exit(2)
"""


class SshOpener:
    """Запрос к API с чужой машины: токен привязан к её IP-адресу.

    Через сервер идёт только поиск — короткий JSON. Сами изображения лежат
    на открытом CDN и качаются напрямую, поэтому гонять гигабайты картинок
    через чужой канал не приходится.
    """

    def __init__(self, host: str, user: str, key_path: str, port: int = 22,
                 python: str = "python3") -> None:
        import paramiko

        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.connect(hostname=host, port=port, username=user, key_filename=key_path)
        self._client = client
        self._python = python

    def __call__(self, url: str, headers: dict[str, str], timeout: int) -> bytes:
        stdin, stdout, stderr = self._client.exec_command(
            f"{self._python} -c {_shell_quote(_REMOTE)}", timeout=timeout
        )
        stdin.write(json.dumps({"url": url, "headers": headers, "timeout": timeout}))
        stdin.channel.shutdown_write()
        body = stdout.read()
        code = stdout.channel.recv_exit_status()
        if code == 0:
            return body
        message = stderr.read().decode("utf-8", "replace").strip()
        if message.startswith("HTTPSTATUS"):
            status = int(message.split()[1])
            raise urllib.error.HTTPError(url, status, message, {}, None)
        raise BrinexError(f"запрос через сервер не удался (код {code}): {message[:200]}")

    def close(self) -> None:
        self._client.close()


def _shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


class BrinexClient:
    """Опрос API пакетами с повтором временных сбоев."""

    def __init__(self, config: BrinexConfig, opener: Callable[..., bytes] = _open,
                 pause: float = PAUSE, attempts: int = ATTEMPTS,
                 backoff: float = BACKOFF, sleep: Callable[[float], None] = time.sleep,
                 batch: int = BATCH) -> None:
        self.config = config
        self._open = opener
        self.pause = pause
        self.attempts = attempts
        self.backoff = backoff
        self._sleep = sleep
        self.batch = batch
        self.requests = 0

    def _ask(self, goods_ids: Sequence[str]) -> list[dict]:
        query = urllib.parse.urlencode([("goods_id[]", str(g)) for g in goods_ids])
        url = f"{self.config.base_url}/search?{query}"
        headers = {"Authorization": f"Bearer {self.config.token}"}
        last: Exception | None = None
        for attempt in range(self.attempts):
            try:
                self.requests += 1
                body = self._open(url, headers, TIMEOUT)
                data = json.loads(body.decode("utf-8"))
                # На пустой или неверный запрос API отвечает не списком, а
                # объектом с полем message — и кодом 200. Молча принять это
                # за «фотографий нет» значит потерять весь пакет без следа.
                if not isinstance(data, list):
                    raise BrinexError(f"неожиданный ответ API: {str(data)[:200]}")
                return data
            except urllib.error.HTTPError as exc:
                if exc.code == 403:
                    raise BrinexError(
                        "API отказал: HTTP 403, запрос идёт не с разрешённого "
                        "IP-адреса. Поставщик привязывает токен к одному адресу. "
                        "Пропишите в brinex.yml `via_photo_server: true`, и запросы "
                        "пойдут через сервер публикации фотографий."
                    ) from exc
                if exc.code in FATAL_STATUS:
                    raise BrinexError(f"API отказал: HTTP {exc.code}, токен неверен.") from exc
                last = exc
            except BrinexError:
                raise
            except Exception as exc:  # сеть, таймаут, битый JSON
                last = exc
            if attempt < self.attempts - 1:
                self._sleep(self.backoff * (attempt + 1))
        raise BrinexError(f"пакет не получен после {self.attempts} попыток: {last}")

    def images(self, goods_ids: Iterable[str]) -> dict[str, str]:
        """`goods_id` → ссылка на фотографию.

        Коды, которых нет в ответе, просто не попадают в результат: товар мог
        быть снят с производства, и это не повод считать пакет неудавшимся.
        """
        unique = list(dict.fromkeys(str(g) for g in goods_ids if str(g).strip()))
        found: dict[str, str] = {}
        for start in range(0, len(unique), self.batch):
            for record in self._ask(unique[start:start + self.batch]):
                url = str(record.get("img_url") or "").strip()
                if url:
                    found[str(record.get("goods_id"))] = url
            if start + self.batch < len(unique):
                self._sleep(self.pause)
        return found


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


def _download(url: str, timeout: int = 60, hops: int = 3) -> bytes:
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
            raise BrinexError(f"CDN ответил HTTP {response.status}")
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
                 prefix: str = "brinex") -> None:
        self.publisher = publisher
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
        return len(group)

    def save(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        save_url_map(self.url_map_path, self.url_map)


def photo_path(root: Path, source: str, article: str) -> Path:
    """Куда лёг бы снимок позиции. Имя обязано совпадать с тем, что ищет
    `photos.find_photos`, иначе скачанное просто не попадёт в выгрузку."""
    return Path(root) / safe_name(source) / f"{safe_name(article)}_1.jpg"


def fetch_photos(
    wanted: Sequence[Target],
    client: BrinexClient,
    sink: DiskSink | PublishSink,
    download: Callable[[str], bytes] = _download,
    limit: int | None = None,
    workers: int = 6,
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
                    report.failed.append(failure)
                done += 1
                if done % 200 == 0:
                    log(f"обработано ссылок {done} из {report.distinct_urls}, "
                        f"получено {report.saved}…")
    finally:
        # Сохраняем и после обрыва: полученное не должно пропасть, иначе
        # прерванный запуск заставит скачивать и заливать всё заново.
        sink.save()
    return report
