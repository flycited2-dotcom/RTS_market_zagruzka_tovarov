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
    return UNSAFE.sub("_", str(value)).strip("_") or "item"


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
                 base_url: str, port: int = 22) -> None:
        import paramiko

        self.remote_root = remote_root.rstrip("/")
        self.base_url = base_url.rstrip("/")
        self.uploads = 0
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.connect(hostname=host, port=port, username=user, key_filename=key_path)
        self._client = client
        self._sftp = client.open_sftp()

    def _mkdirs(self, path: str) -> None:
        parts, current = path.strip("/").split("/"), ""
        for part in parts:
            current = f"{current}/{part}"
            try:
                self._sftp.stat(current)
            except FileNotFoundError:
                self._sftp.mkdir(current)

    def publish(self, remote_name: str, data: bytes) -> str:
        target = f"{self.remote_root}/{remote_name}"
        self._mkdirs(target.rsplit("/", 1)[0])
        with self._sftp.open(target, "wb") as fh:
            fh.write(data)
        self.uploads += 1
        return f"{self.base_url}/{remote_name}"

    def close(self) -> None:
        self._sftp.close()
        self._client.close()


class PhotoResolver:
    """Отдаёт ссылки на изображения позиции, публикуя только изменившиеся файлы."""

    def __init__(self, root: Path, publisher: Publisher, manifest_path: Path,
                 limit: int = 5) -> None:
        self.root = Path(root)
        self.publisher = publisher
        self.manifest_path = Path(manifest_path)
        self.limit = limit
        self._manifest: dict[str, str] = {}
        if self.manifest_path.exists():
            self._manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def urls_for(self, item: Item) -> list[str]:
        urls: list[str] = []
        for index, path in enumerate(
            find_photos(self.root, item.source, item.article, self.limit), start=1
        ):
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            known = self._manifest.get(digest)
            if known:
                urls.append(known)
                continue
            remote = f"{safe_name(item.source)}/{safe_name(item.article)}_{index}.jpg"
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
