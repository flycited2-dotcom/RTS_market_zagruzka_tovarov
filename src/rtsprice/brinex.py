"""Ссылки на изображения товаров из API Бринэкса.

Соединение точное: `goods_id` из прайса — это `goods_id` в API, так что
подбирать по названию не приходится. Скачиванием и публикацией занимается
`photobank`; здесь только разговор с поставщиком.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .photobank import TIMEOUT, PhotoBankError

# При 500 кодах в запросе сервер отвечает 414 Request-URI Too Large,
# при 200 — работает. Замерено, а не взято из документации.
BATCH = 200
PAUSE = 0.4
ATTEMPTS = 3
BACKOFF = 3.0

#: Коды, при которых повторять запрос бессмысленно: дело не в сети.
#: 401 — токен неверен, 403 — запрос идёт не с разрешённого IP.
FATAL_STATUS = (401, 403)


class BrinexError(PhotoBankError):
    """Отказ API Бринэкса, который не лечится повтором."""


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
