"""Фотографии продовольственных товаров по штрихкоду из Open Food Facts.

Открытая база, ключ — штрихкод EAN-13, тот самый, что уже есть в прайсах
Гуриненко. Соединение точное, как и у Бринэкса: подбора по названию нет.

**Опрашивать API нельзя.** База отвечает `429 Too Many Requests` уже при
одном запросе в секунду: из сорока запросов с паузой 0,9 с отказано
пятнадцати. Опрос пяти тысяч штрихкодов растянулся бы на часы и всё равно
дал бы недостоверный ответ, потому что отказ неотличим от «нет в базе» —
на этом сгорели обе первые прикидки покрытия, 14% и 3,3%.

Поэтому основной путь — `DumpIndex`: полная выгрузка базы читается потоком
один раз. Клиент по API оставлен для точечной проверки отдельных кодов.
"""
from __future__ import annotations

import gzip
import http.client
import json
import ssl
import time
from pathlib import Path
from typing import Callable, Iterable

from .photobank import PhotoBankError

#: Выгрузка называется .csv, но разделена табуляцией, и колонок в ней 211.
DUMP_URL = ("https://static.openfoodfacts.org/data/"
            "en.openfoodfacts.org.products.csv.gz")


def _key(barcode: object) -> str:
    """Ключ сравнения штрихкодов.

    В выгрузке коды хранятся как есть: EAN-8 лежит восьмизначным, а тот же
    товар в прайсе поставщика записан тринадцатизначным с ведущими нулями.
    Без выравнивания нулей совпадений почти не было бы.
    """
    return str(barcode).strip().lstrip("0")


class DumpIndex:
    """Ссылки на снимки из полной выгрузки базы.

    Файл в полтора гигабайта читается потоком и на диск не разворачивается.
    Проход один: сначала собираются нужные коды, потом выгрузка проходится
    насквозь ровно один раз, сколько бы штрихкодов мы ни искали.
    """

    def __init__(self, dump_path: Path, log: Callable[[str], None] = lambda _: None,
                 code_column: str = "code", image_column: str = "image_url") -> None:
        self.dump_path = Path(dump_path)
        self._log = log
        self.code_column = code_column
        self.image_column = image_column
        self.scanned = 0

    def images(self, codes: Iterable[str]) -> dict[str, str]:
        wanted: dict[str, str] = {}
        for code in codes:
            text = str(code).strip()
            if text:
                wanted.setdefault(_key(text), text)
        if not wanted:
            return {}

        found: dict[str, str] = {}
        with gzip.open(self.dump_path, "rt", encoding="utf-8", errors="replace",
                       newline="") as fh:
            header = fh.readline().rstrip("\n").split("\t")
            try:
                at_code = header.index(self.code_column)
                at_image = header.index(self.image_column)
            except ValueError as exc:
                raise PhotoBankError(
                    f"{self.dump_path.name}: в выгрузке нет колонки "
                    f"{self.code_column!r} или {self.image_column!r}"
                ) from exc
            width = len(header)

            for line in fh:
                self.scanned += 1
                if self.scanned % 500_000 == 0:
                    self._log(f"просмотрено записей {self.scanned:,}, "
                              f"найдено {len(found)}…".replace(",", " "))
                parts = line.rstrip("\n").split("\t")
                # Строка со сдвигом колонок отдала бы ссылку не того товара.
                # Дешевле пропустить её, чем поставить в карточку чужой снимок.
                if len(parts) != width:
                    continue
                original = wanted.get(_key(parts[at_code]))
                if original is None:
                    continue
                url = parts[at_image].strip()
                if url.startswith("http"):
                    found[original] = url
        return found

HOST = "world.openfoodfacts.org"
PAUSE = 1.1
TIMEOUT = 25
ATTEMPTS = 3

#: Просят называться, чтобы отличать клиентов друг от друга. Просьба разумная,
#: и выполнить её ничего не стоит.
USER_AGENT = "rtsprice/1.0 (выгрузка товаров на РТС-маркет)"

#: Только нужные поля: полная карточка продукта весит десятки килобайт.
FIELDS = "code,product_name,image_url,image_front_url"


def _get(path: str, timeout: int = TIMEOUT) -> bytes:
    connection = http.client.HTTPSConnection(
        HOST, timeout=timeout, context=ssl.create_default_context()
    )
    try:
        connection.request("GET", path, headers={
            "Host": HOST, "User-Agent": USER_AGENT, "Accept": "*/*",
        })
        response = connection.getresponse()
        body = response.read()
        if response.status == 404:
            return b'{"status":0}'          # товара нет в базе — не отказ
        if response.status != 200:
            raise PhotoBankError(f"Open Food Facts ответил HTTP {response.status}")
        return body
    finally:
        connection.close()


class OpenFoodFactsClient:
    """Опрос базы по одному штрихкоду с паузой между запросами.

    Пакетного поиска у неё нет, поэтому запросов ровно столько, сколько
    штрихкодов. На пять тысяч позиций это полтора часа — но повторный запуск
    спрашивает только про новые, так что платится это один раз.
    """

    def __init__(self, get: Callable[[str], bytes] = _get, pause: float = PAUSE,
                 attempts: int = ATTEMPTS, sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] = lambda _: None) -> None:
        self._get = get
        self.pause = pause
        self.attempts = attempts
        self._sleep = sleep
        self._log = log
        self.requests = 0
        self.not_in_base = 0
        self.failed = 0

    def _one(self, barcode: str) -> str | None:
        path = f"/api/v2/product/{barcode}.json?fields={FIELDS}"
        for attempt in range(self.attempts):
            try:
                self.requests += 1
                body = json.loads(self._get(path).decode("utf-8"))
                if body.get("status") != 1:
                    self.not_in_base += 1
                    return None
                product = body.get("product") or {}
                # Снимок «спереди» — это упаковка целиком, ровно то, что нужно
                # в карточке. Общий image_url бывает фрагментом состава.
                url = product.get("image_front_url") or product.get("image_url")
                return str(url).strip() or None if url else None
            except PhotoBankError:
                raise
            except Exception:
                if attempt == self.attempts - 1:
                    self.failed += 1
                    return None
                self._sleep(self.pause * (attempt + 2))
        return None

    def images(self, codes: Iterable[str]) -> dict[str, str]:
        unique = list(dict.fromkeys(str(c).strip() for c in codes if str(c).strip()))
        found: dict[str, str] = {}
        for number, barcode in enumerate(unique, 1):
            url = self._one(barcode)
            if url:
                found[barcode] = url
            if number % 200 == 0:
                self._log(f"опрошено штрихкодов {number} из {len(unique)}, "
                          f"найдено {len(found)}…")
            if number < len(unique):
                self._sleep(self.pause)
        return found
