"""Климатическая техника из хаба SplitHome: Бриз, Daichi, Русклимат, JAC.

Единственный источник, который приходит не файлом от поставщика, а живым
срезом: остатки и оптовые цены четырёх поставщиков собирает сервис
`splithub-tender-api` на сервере, а сюда попадает уже готовый каталог.

**Токен не покидает сервер.** Сервис слушает только `127.0.0.1:8780`, наружу
не публикуется, а его ключ лежит в `/opt/splithub_api_telegram/.env`. Запрос
выполняется по SSH прямо на сервере: мы отправляем туда команду, а получаем
готовый JSON. Тот же приём, что с API Бринэкса, и по той же причине — чужой
ключ незачем держать на рабочей машине.

Каталог сохраняется книгой в `input/climate/`, чтобы дальше его читал общий
конвейер: наценка, коды ОКПД2, номера РТС и снятие с продажи должны работать
ровно так же, как для прайсов, присланных файлом.

Замерено 05.09.2026: 542 позиции, все в наличии в Симферополе, все с оптовой
ценой, у 464 есть фотография — 86% источника, лучший показатель из всех.
"""
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .photobank import PhotoBankError

#: Путь к сервису на сервере: оттуда читается токен и туда идёт запрос.
REMOTE_ROOT = "/opt/splithub_api_telegram"
ENDPOINT = "http://127.0.0.1:8780/api/internal/tender-climate-products/search"

#: Сколько позиций забирать за раз. Каталог небольшой и целиком помещается в
#: один ответ; предел стоит на случай, если хаб разрастётся.
CATALOG_LIMIT = 5000

#: Колонки книги, которую читает общий конвейер. Порядок важен: он же описан
#: в `sources.yml`, и расхождение оставит источник без обязательного поля.
COLUMNS = ("Артикул", "Наименование", "Цена", "Остаток", "Бренд",
           "Поставщик", "Серия", "Категория", "Фото")


class ClimateError(PhotoBankError):
    """Отказ хаба, который не лечится повтором."""


@dataclass(frozen=True)
class Product:
    """Позиция климатического каталога."""

    sku: str
    name: str
    price: float
    quantity: int
    brand: str
    supplier: str
    series: str
    category: str
    image_url: str

    @classmethod
    def from_api(cls, row: dict) -> "Product":
        specs = row.get("specifications") if isinstance(row.get("specifications"), dict) else {}
        return cls(
            sku=str(row.get("sku") or "").strip(),
            name=str(row.get("name") or "").strip(),
            price=float(row.get("purchasePriceGross") or 0),
            quantity=int(row.get("stockQuantity") or 0),
            brand=str(row.get("vendor") or "").strip(),
            supplier=str(row.get("supplierName") or "").strip(),
            series=str(specs.get("series") or "").strip(),
            category=str(row.get("category") or "").strip(),
            image_url=str(row.get("imageUrl") or "").strip(),
        )


#: Выполняется на сервере. Токен читается там же и в аргументы не попадает:
#: аргументы видны в списке процессов всем пользователям машины.
_REMOTE = r"""
cd {root} || exit 3
TOKEN=$(grep '^TENDER_CLIMATE_API_TOKEN=' .env | cut -d= -f2-)
if [ -z "$TOKEN" ]; then echo 'NOTOKEN' >&2; exit 4; fi
curl -sS -m {timeout} -X POST {endpoint} \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{{"all": true, "limit": {limit}}}'
"""


def fetch_catalog(server, *, timeout: int = 180,
                  runner: Callable[[str, int], bytes] | None = None) -> list[Product]:
    """Забрать весь каталог хаба. `server` — доступ из `photos.yml`.

    Запрос идёт на сервере, а не отсюда: сервис слушает только localhost, и
    это осознанно — наружу его публиковать незачем.
    """
    command = _REMOTE.format(root=REMOTE_ROOT, endpoint=ENDPOINT,
                             limit=CATALOG_LIMIT, timeout=timeout)
    body = (runner or _ssh_runner(server))(command, timeout)
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except ValueError as exc:
        raise ClimateError(f"хаб ответил не JSON: {body[:200]!r}") from exc
    if not payload.get("ok"):
        raise ClimateError(f"хаб ответил отказом: {str(payload)[:200]}")
    products = [Product.from_api(row) for row in payload.get("products") or []]
    # Позиция без артикула или без цены не станет строкой прайса: у неё нет
    # ключа для номера РТС и нечего показывать покупателю.
    return [p for p in products if p.sku and p.price > 0]


def _ssh_runner(server) -> Callable[[str, int], bytes]:
    def run(command: str, timeout: int) -> bytes:
        import paramiko

        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=server.host, port=server.port,
                       username=server.user, key_filename=server.key_path)
        try:
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            body = stdout.read()
            code = stdout.channel.recv_exit_status()
            if code != 0:
                message = stderr.read().decode("utf-8", "replace").strip()
                if "NOTOKEN" in message:
                    raise ClimateError(
                        "на сервере нет TENDER_CLIMATE_API_TOKEN — "
                        f"проверьте {REMOTE_ROOT}/.env"
                    )
                raise ClimateError(f"запрос к хабу не удался (код {code}): {message[:200]}")
            return body
        finally:
            client.close()

    return run


def write_price(path: Path, products: Iterable[Product]) -> int:
    """Сохранить каталог книгой, какую читает общий конвейер.

    Книга, а не JSON, потому что весь конвейер построен на чтении прайсов
    поставщиков: так климат проходит теми же правилами, что и остальные
    источники, и не заводит себе отдельной дороги.
    """
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Каталог"
    sheet.append(list(COLUMNS))
    count = 0
    for item in products:
        sheet.append([item.sku, item.name, item.price, item.quantity, item.brand,
                      item.supplier, item.series, item.category, item.image_url])
        count += 1
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    return count


def save_photo_map(path: Path, products: Iterable[Product]) -> int:
    """Отложить карту «артикул → адрес снимка» рядом с прайсом.

    Снимки лежат на серверах поставщиков (`images.breez.ru` и подобных).
    Ссылаться на них прямо из карточки товара нельзя: чужой адрес может
    смениться в любой день, и позиция останется без картинки. Поэтому карта
    нужна отдельно — по ней `photos` скачает снимки и опубликует их на нашем
    сервере, как это делается для остальных источников.
    """
    urls = {p.sku: p.image_url for p in products if p.sku and p.image_url}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(urls, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(urls)


class ClimateImages:
    """Ссылки на снимки климата: артикул → адрес у поставщика.

    Реализует протокол `photobank.ImageIndex`, поэтому скачиванием,
    сжатием и публикацией занимается общий `fetch_photos` — тот же, что у
    Бринэкса и Гуриненко.
    """

    def __init__(self, photo_map: dict[str, str]) -> None:
        self.photo_map = {str(k): str(v) for k, v in photo_map.items() if v}

    @classmethod
    def from_file(cls, path: Path) -> "ClimateImages":
        path = Path(path)
        if not path.exists():
            raise ClimateError(
                f"не найдена карта снимков {path.name}. "
                f"Сначала обновите каталог: python -m rtsprice climate"
            )
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def images(self, articles: Iterable[str]) -> dict[str, str]:
        wanted = [str(a).strip() for a in articles if str(a).strip()]
        return {a: self.photo_map[a] for a in dict.fromkeys(wanted) if a in self.photo_map}
