"""Фотографии продовольственных товаров по штрихкоду из Open Food Facts.

Открытая база, ключ — штрихкод EAN-13, тот самый, что уже есть в прайсах
Гуриненко. Соединение точное, как и у Бринэкса: подбора по названию нет.

База общественная и живёт на пожертвованиях, поэтому запросы идут по одному
с паузой. Без пауз она режет частоту, и замер покрытия выходит вчетверо
занижённым — так и случилось при первой прикидке: 4% вместо 14%.
"""
from __future__ import annotations

import http.client
import json
import ssl
import time
from typing import Callable, Iterable

from .photobank import PhotoBankError

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
