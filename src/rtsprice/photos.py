"""Поиск, обработка и публикация изображений позиций."""
from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path
from typing import Protocol

from PIL import Image

from .normalize import Item

MAX_SIDE = 1600
MAX_BYTES = 1_000_000
SUFFIX_PATTERN = re.compile(r"_(\d+)$")
UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(value: str) -> str:
    """Превратить строку в безопасный сегмент пути, не теряя различий.

    Кириллица и прочие небезопасные символы схлопываются в подчёркивание,
    поэтому к результату добавляется короткий хэш исходной строки. Без него
    «деталь-55» и «штука-55» дали бы одно и то же имя файла, и фотография
    одного товара затёрла бы фотографию другого.
    """
    text = str(value)
    cleaned = UNSAFE.sub("_", text).strip("_")
    if cleaned == text and cleaned:
        return cleaned
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned}_{digest}" if cleaned else digest


def find_photos(root: Path, source: str, article: str, limit: int = 5) -> list[Path]:
    """Файлы вида «<артикул>_<номер>.<расширение>», упорядоченные по номеру."""
    directory = Path(root) / source
    if not directory.is_dir():
        return []
    found: list[tuple[int, Path]] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        match = SUFFIX_PATTERN.match(path.stem[len(article):]) if path.stem.startswith(article) else None
        if match:
            found.append((int(match.group(1)), path))
    return [p for _, p in sorted(found)][:limit]


def normalize_image(data: bytes, max_side: int = MAX_SIDE, max_bytes: int = MAX_BYTES) -> bytes:
    image = Image.open(io.BytesIO(data))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if max(image.size) > max_side:
        ratio = max_side / max(image.size)
        image = image.resize((round(image.width * ratio), round(image.height * ratio)))
    for quality in (88, 78, 68, 58, 45):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        if buffer.tell() <= max_bytes:
            return buffer.getvalue()
    return buffer.getvalue()


def load_url_map(path: Path) -> dict[str, list[str]]:
    """Готовые ссылки на снимки: «источник/артикул» → список адресов.

    Файл пишут адаптеры поставщиков, которые публикуют изображения сами.
    Отсутствие файла — не ошибка: у большинства источников фотографии лежат
    локально, и карта им не нужна.
    """
    p = Path(path)
    if not p.exists():
        return {}
    body = json.loads(p.read_text(encoding="utf-8"))
    return {k: list(v) for k, v in body.items() if v}


def save_url_map(path: Path, url_map: dict[str, list[str]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(url_map, ensure_ascii=False, indent=1), encoding="utf-8")


class Publisher(Protocol):
    def publish(self, remote_name: str, data: bytes) -> str:
        """Опубликовать файл и вернуть его общедоступную ссылку."""


class LocalPublisher:
    """Публикация в локальную папку. Используется в тестах и при отладке."""

    def __init__(self, root: Path, base_url: str) -> None:
        self.root = Path(root)
        self.base_url = base_url.rstrip("/")
        self.uploads = 0

    def publish(self, remote_name: str, data: bytes) -> str:
        target = self.root / remote_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.uploads += 1
        return f"{self.base_url}/{remote_name}"


class SftpPublisher:
    """Публикация на сервер продавца по SFTP."""

    def __init__(self, host: str, user: str, key_path: str, remote_root: str,
                 base_url: str, port: int = 22, timeout: float = 120.0) -> None:
        self._where = dict(hostname=host, port=port, username=user,
                           key_filename=key_path, timeout=timeout)
        self.remote_root = remote_root.rstrip("/")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.uploads = 0
        self.reconnects = 0
        self._client = None
        self._sftp = None
        self._known_dirs: set[str] = set()
        self._connect()

    def _connect(self) -> None:
        import paramiko

        self.close()
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.connect(**self._where)
        self._client = client
        self._sftp = client.open_sftp()
        # Без таймаута оборванная сеть останавливает выгрузку навсегда и молча:
        # процесс живёт, файлы не идут, причина ниоткуда не видна.
        self._sftp.get_channel().settimeout(self.timeout)
        self._known_dirs = set()

    def _mkdirs(self, path: str) -> None:
        # Уже созданное запоминается: без этого каждый файл стоил бы трёх
        # обращений к серверу на проверку одних и тех же каталогов, а при
        # выгрузке в двенадцать тысяч снимков это часы чистого ожидания.
        if path in self._known_dirs:
            return
        parts, current = path.strip("/").split("/"), ""
        for part in parts:
            current = f"{current}/{part}"
            try:
                self._sftp.stat(current)
            except FileNotFoundError:
                self._sftp.mkdir(current)
        self._known_dirs.add(path)

    def _write(self, remote_name: str, data: bytes) -> None:
        target = f"{self.remote_root}/{remote_name}"
        self._mkdirs(target.rsplit("/", 1)[0])
        with self._sftp.open(target, "wb") as fh:
            fh.write(data)

    def publish(self, remote_name: str, data: bytes) -> str:
        try:
            self._write(remote_name, data)
        except Exception:
            # Сервер закрывает простаивающее соединение. Выгрузка Бринэкса
            # начинается с опроса API длиной в десяток минут, и к первой же
            # записи канал оказывался мёртвым: процесс работал, отчитывался о
            # прогрессе и не сохранял ничего. Одна попытка переподключиться.
            self.reconnects += 1
            self._connect()
            self._write(remote_name, data)
        self.uploads += 1
        return f"{self.base_url}/{remote_name}"

    def close(self) -> None:
        for handle in (self._sftp, self._client):
            try:
                if handle is not None:
                    handle.close()
            except Exception:
                pass
        self._sftp = self._client = None


class PhotoResolver:
    """Отдаёт ссылки на изображения позиции, публикуя только изменившиеся файлы."""

    def __init__(self, root: Path, publisher: Publisher | None, manifest_path: Path,
                 limit: int = 5, url_map: dict[str, list[str]] | None = None) -> None:
        self.root = Path(root)
        self.publisher = publisher
        self.manifest_path = Path(manifest_path)
        self.limit = limit
        self.url_map = url_map or {}
        self._manifest: dict[str, str] = {}
        if self.manifest_path.exists():
            self._manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def urls_for(self, item: Item) -> list[str]:
        # Готовые ссылки от адаптера поставщика. Снимок уже опубликован, и
        # хранить его копию на рабочей машине незачем: у Бринэкса это почти
        # три гигабайта файлов, которые больше никому не нужны.
        ready = self.url_map.get(f"{item.source}/{item.article}")
        if ready:
            return ready[: self.limit]
        if self.publisher is None:
            return []
        urls: list[str] = []
        for path in find_photos(self.root, item.source, item.article, self.limit):
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            known = self._manifest.get(digest)
            if known:
                urls.append(known)
                continue
            # Номер берётся из имени файла, а не из позиции в списке: если
            # удалить средний снимок, следующий занял бы чужой адрес и затёр
            # бы по нему другое содержимое, оставив ссылку в строке прежней.
            number = SUFFIX_PATTERN.search(path.stem).group(1)
            remote = f"{safe_name(item.source)}/{safe_name(item.article)}_{number}.jpg"
            url = self.publisher.publish(remote, normalize_image(raw))
            self._manifest[digest] = url
            urls.append(url)
        return urls

    def save(self) -> None:
        """Записать манифест один раз за сборку.

        Вызывается оркестровкой в конце, а не из urls_for: манифест пишется
        целиком, и сохранение на каждую новую фотографию превратило бы одну
        запись в десятки тысяч.
        """
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(self._manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )
