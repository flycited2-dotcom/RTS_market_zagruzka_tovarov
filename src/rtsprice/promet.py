"""Фотографии Промета из каталога safe.ru по разметке самого поставщика.

У Промета нет ни API изображений, как у Бринэкса, ни штрихкода, как у
Гуриненко. Артикул и внутренний числовой ID из прайса нигде наружу не
публикуются: поиск на сайте по артикулу не находит ничего, `ELEMENT_ID` в
адресе игнорируется, в разметке карточки артикула нет. Фотобанк
`safe.ru/library/foto-produktsii` проверен целиком — все 44 архива, 417
снимков, 350 моделей — и даёт около девяноста позиций: это витрина
флагманов, а не медиабанк каталога.

Ключ нашёлся в самой книге прайса. На витринных листах («Сейфы»,
«Оружейные», «Верстаки», «Медицина» и прочих) поставщик своей рукой
расставил **217 гиперссылок на разделы своего каталога**. Ссылка стоит на
строке-заголовке группы и действует до следующего заголовка, так что каждая
позиция получает раздел, назначенный ей продавцом. Это не наша догадка по
названию, а утверждение поставщика.

Отсюда сопоставление по трём независимым признакам:

1. **Раздел** сужает область поиска с 1 887 карточек сайта до десятка-другого
   внутри раздела. Ловушка, на которой сгорел фотобанк — «Замок Euro-Lock
   5888-0005 для NST 1991» подцепил снимок сейфа `NST-1991`, — здесь
   невозможна: замки лежат в разделе аксессуаров, сейфы в разделе сейфов.
2. **Код модели** различает позиции внутри раздела. Совпадение должно быть
   единственным: две карточки на один код — отказ, а не выбор наугад.
3. **Габариты и вес** подтверждают, что это тот же товар, а не соседняя
   модель линейки. На витринных листах есть колонки Высота / Ширина /
   Глубина / Вес, на карточке — «Размеры внешние, мм (ВхШхГ)» и «Вес, кг».
   Позиция без совпадения размеров снимок не получает.

Замерено на 04.09.2026: 1 704 позиции получили раздел, 379 сошлись
однозначно, и у всех проверенных совпали и габариты, и вес.
"""
from __future__ import annotations

import http.client
import os
import re
import ssl
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .photobank import TIMEOUT, PhotoBankError

BASE = "https://www.safe.ru"
PAUSE = 0.3

#: Во сколько раз вес прайса и карточки могут разойтись, оставаясь одним
#: товаром. Замерено: у сейфа AMH-125/2T прайс даёт 223 кг, карточка 270 —
#: это нетто и брутто, габариты при этом сходятся до сантиметра.
WEIGHT_RATIO = 1.5

#: Карта сайта с разделом каталога товаров: около 1 900 карточек после
#: снятия повторов. Нужна как запасной список, когда раздел из прайса исчез.
SITEMAP_URL = "https://www.safe.ru/sitemap-news-2.xml"

#: Сколько карточек-кандидатов имеет смысл проверять на одну позицию.
#: Больше — значит код модели слишком широкий, и это отказ, а не перебор.
MAX_CANDIDATES = 5

#: Разделы верхнего уровня. Если раздел из прайса отдаёт 404, поиск
#: поднимается на уровень выше — но не до этих: там тысяча карточек, и
#: сужение области, ради которого всё затевалось, пропало бы.
ROOT_SECTIONS = frozenset({
    "seyfy", "metallicheskaya-mebel", "metallicheskie-stellazhi",
    "proizvodstvennaya-mebel", "meditsinskaya-mebel-i-oborudovanie",
    "ofisnaya-mebel", "drugaya-produktsiya", "bankovskoe-oborudovanie",
})

#: Слова-категории: в прайсе «Сейф MDTB ES-30.Е», на сайте «Мебельный сейф
#: MDTB ES-30.Е». Само слово «сейф» не различает ничего и только мешает.
CATEGORY_WORDS = frozenset({
    "seif", "seyf", "shkaf", "shkafchik", "stellazh", "verstak", "kartoteka",
    "tumba", "krovat", "kushetka", "loker", "yaschik", "komplekt",
    "dlya", "i", "s", "na", "pod", "k",
})

#: Кириллические буквы, неотличимые на глаз от латинских. В кодах моделей
#: они попадаются постоянно: «FRS-36.СL», «ES-30.Е», «ES-63Т.Е» набраны в
#: прайсе вперемешку, и без этой замены совпадение теряется.
_TWINS = {"а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m",
          "н": "h", "о": "o", "р": "p", "с": "c", "т": "t", "у": "y",
          "х": "x", "і": "i", "ј": "j", "ѕ": "s"}

_RU = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
       "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
       "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
       "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
       "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"}

_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

_DIMENSIONS = re.compile(
    r"(?:Размеры внешние|Габариты издели\w+)[^:]{0,24}:\s*"
    r"(\d{2,4})\s*[xх×]\s*(\d{2,4})\s*[xх×]\s*(\d{2,4})")
_WEIGHT = re.compile(r"Вес,\s*кг\s*:\s*([\d.,]+)")
_CARD = re.compile(r'href="(/products/[a-z0-9\-]+/)"', re.I)
_DETAIL = re.compile(
    r'/upload/iblock/[^\s"\'<>)]*detail_picture\.(?:jpg|jpeg|png)', re.I)
_BIG = re.compile(r'/upload/iblock/[^\s"\'<>)]*_big\.(?:jpg|jpeg|png)', re.I)


class PrometError(PhotoBankError):
    """Отказ, который не лечится повтором."""


def normalize(text: object) -> str:
    """Строка к сравнимому виду: кириллица в латиницу, только буквы и цифры.

    Кириллица обрабатывается двояко, и это не прихоть, а два разных случая
    в одних и тех же наименованиях.

    **Транслитерация** — для русских слов: «Рубеж» и «РУБЕЖ» обязаны сойтись,
    а на сайте они записаны транслитом (`stellazh`, `seyf`). Отбрасывать
    незнакомые буквы нельзя: от «Рубеж» остаётся «ре», и сходится что угодно.

    **Замена двойника** — для кириллических букв, затесавшихся в латинский
    код модели. В прайсе «Сейф FRS-36.СL» набрано с кириллической «С», на
    сайте `frs-36-cl` с латинской. Глазом не отличить, для машины это разные
    строки; чистая транслитерация даёт `frs36sl`, и совпадение теряется.

    Различаются они по соседям: буква среди латиницы и цифр — двойник, буква
    среди кириллицы — русское слово. Поэтому «С» в «FRS-36.СL» станет `c`, а
    та же «С» в «Стол» — `s`.
    """
    source = str(text)
    out: list[str] = []
    for position, char in enumerate(source):
        lower = char.lower()
        twin = _TWINS.get(lower)
        out.append(twin if twin and _latin_context(source, position)
                   else _RU.get(lower, lower))
    return re.sub(r"[^a-z0-9]", "", "".join(out))


def _latin_context(source: str, position: int) -> bool:
    """Стоит ли символ среди латиницы и цифр, а не среди кириллицы.

    Ближайшие буквы и цифры по обе стороны, через точки, дефисы и пробелы:
    в «ES-30.Е» точка отделяет букву от цифры, но код остаётся латинским.
    """
    def neighbour(step: int) -> str | None:
        index = position + step
        while 0 <= index < len(source):
            char = source[index]
            if char.isalnum():
                return char
            if char not in ".-_/ ":
                return None
            index += step
        return None

    sides = [neighbour(-1), neighbour(1)]
    if any(c and ("а" <= c.lower() <= "я" or c.lower() == "ё")
           for c in sides):
        return False
    return any(c and (c.isdigit() or ("a" <= c.lower() <= "z")) for c in sides)


def model_code(name: object) -> str:
    """Код модели из наименования: всё, кроме слова-категории.

    «Сейф MDTB ES-30.Е» → `mdtbes30e`, и он же выделяется из заголовка
    карточки «Мебельный сейф MDTB ES-30.Е».
    """
    parts = re.split(r"[\s,]+", str(name))
    keep = [p for p in parts if normalize(p) not in CATEGORY_WORDS]
    return normalize(" ".join(keep))


def parent_section(url: str) -> str | None:
    """Раздел уровнем выше — если он не корневой.

    Прайс живёт дольше, чем структура сайта: на 04.09.2026 двенадцать
    разделов из девяноста шести отдавали 404, и оружейные сейфы целиком.
    Их родитель `/catalog/seyfy/oruzheynye-shkafy-i-seyfy/` жив и держит
    тридцать карточек.
    """
    path = urllib.parse.urlsplit(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 3 or parts[0] != "catalog":
        return None
    if parts[-2] in ROOT_SECTIONS and len(parts) == 3:
        return None
    return urllib.parse.urljoin(url, "/".join(parts[:-1]) + "/") \
        if url.startswith("/") else f"{BASE}/{'/'.join(parts[:-1])}/"


@dataclass(frozen=True)
class Position:
    """Позиция витринного листа: раздел каталога и размеры для сверки."""

    article: str
    name: str
    section: str
    height: float | None = None
    width: float | None = None
    depth: float | None = None
    weight: float | None = None

    @property
    def code(self) -> str:
        return model_code(self.name)


def _number(value: object) -> float | None:
    """Число из ячейки прайса, в том числе записанной с вариантом.

    Поставщик пишет высоту регулируемой мебели как «728/652,5», а сушильного
    шкафа — как «1950*/2000». Берём первое значение: с ним и сверяется
    карточка. Без этого позиция теряла размеры целиком и не могла пройти
    проверку, хотя габариты сходились.
    """
    if value is None:
        return None
    text = str(value).replace(",", ".").strip()
    try:
        return float(text)
    except ValueError:
        first = re.match(r"\s*(\d+(?:\.\d+)?)", text)
        return float(first.group(1)) if first else None


def read_catalog_map(workbook: Path | str) -> dict[str, Position]:
    """Карта «артикул → раздел каталога» из гиперссылок книги прайса.

    Гиперссылки хранятся отдельно от значений ячеек, поэтому книга читается
    дважды: связи — прямо из упаковки xlsx, значения — через openpyxl.

    Ссылка привязана к строке-заголовку и действует до следующей: всё, что
    лежит под «MDTB класса S2 ES», относится к этому разделу. Лист «база»
    пропускается — на нём ссылок нет, он плоский.
    """
    import openpyxl

    path = Path(workbook)
    with zipfile.ZipFile(path) as book:
        names = set(book.namelist())
        rels = {r.get("Id"): r.get("Target") for r in
                ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))}
        sheet_file = {
            sheet.get("name"): os.path.basename(rels[sheet.get(f"{_R}id")])
            for sheet in ET.fromstring(book.read("xl/workbook.xml")).find(f"{_M}sheets")
        }
        anchors: dict[str, dict[int, str]] = {}
        for title, fname in sheet_file.items():
            rel_name = f"xl/worksheets/_rels/{fname}.rels"
            if rel_name not in names:
                continue
            targets = {r.get("Id"): r.get("Target")
                       for r in ET.fromstring(book.read(rel_name))}
            found: dict[int, str] = {}
            sheet = ET.fromstring(book.read(f"xl/worksheets/{fname}"))
            for link in sheet.iter(f"{_M}hyperlink"):
                target = targets.get(link.get(f"{_R}id")) or ""
                if "/catalog/" not in target:
                    continue
                cell = (link.get("ref") or "").split(":")[0]
                digits = re.search(r"(\d+)", cell)
                if digits:
                    found[int(digits.group(1))] = target
            if found:
                anchors[title] = found

    out: dict[str, Position] = {}
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for title, links in anchors.items():
            header: dict[str, int] | None = None
            section: str | None = None
            for number, values in enumerate(wb[title].iter_rows(values_only=True), 1):
                if number in links:
                    section = links[number]
                low = [str(v).strip().lower() if v is not None else "" for v in values]
                if header is None:
                    if "артикул" in low and "наименование" in low:
                        header = {key: low.index(key) for key in
                                  ("артикул", "наименование", "высота", "ширина",
                                   "глубина", "вес") if key in low}
                    continue
                if section is None:
                    continue
                cell = lambda key: (values[header[key]]  # noqa: E731
                                    if header.get(key) is not None
                                    and header[key] < len(values) else None)
                article = cell("артикул")
                name = cell("наименование")
                if not article or not name or not str(article).strip():
                    continue
                article = str(article).strip()
                found = Position(
                    article=article, name=str(name), section=section,
                    height=_number(cell("высота")), width=_number(cell("ширина")),
                    depth=_number(cell("глубина")), weight=_number(cell("вес")),
                )
                # Лист «Главная» перечисляет весь ассортимент, но размеров на
                # нём нет — они на тематических листах. Запись с габаритами
                # вытесняет запись без них, иначе сверять будет нечем.
                previous = out.get(article)
                if previous is None or (previous.height is None
                                        and found.height is not None):
                    out[article] = found
    finally:
        wb.close()
    return out


def fetch(url: str, timeout: int = TIMEOUT, hops: int = 3) -> str:
    """Забрать страницу safe.ru.

    Через `urllib` нельзя — та же беда, что у CDN Бринэкса: библиотека
    добавляет «Connection: close», и сервер перестаёт досылать ответ.
    Замерено на карточках товара: четыре запроса из шести повисли до
    таймаута, те же адреса через `http.client` пришли за доли секунды.
    """
    parsed = urllib.parse.urlsplit(url if "://" in url else BASE + url)
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    connection = http.client.HTTPSConnection(
        parsed.hostname, parsed.port or 443,
        timeout=timeout, context=ssl.create_default_context())
    try:
        connection.request("GET", path or "/", headers={
            "Host": parsed.hostname, "User-Agent": "rtsprice/1.0", "Accept": "*/*",
        })
        response = connection.getresponse()
        if response.status in (301, 302, 303, 307, 308) and hops > 0:
            location = response.getheader("Location") or ""
            response.read()
            return fetch(urllib.parse.urljoin(url, location), timeout, hops - 1)
        if response.status != 200:
            raise PrometError(f"{url}: HTTP {response.status}")
        return response.read().decode("utf-8", "replace")
    finally:
        connection.close()


def is_missing(html: str) -> bool:
    """Раздел или карточка исчезли с сайта. Сервер при этом отвечает 200."""
    return "Страница не найдена" in html


def cards_of(html: str) -> list[str]:
    """Адреса карточек товара на странице раздела, без повторов.

    Берётся адрес, а не текст ссылки: на части разделов название лежит не
    сразу за `href`, и текст приходит пустым, тогда как модель есть в самом
    адресе — `stellazh-ms-u-2000x1000x400-3`.
    """
    return list(dict.fromkeys(_CARD.findall(html)))


def photo_of(html: str) -> str | None:
    """Главный снимок карточки."""
    for pattern in (_DETAIL, _BIG):
        found = pattern.findall(html)
        if found:
            return urllib.parse.urljoin(BASE, found[0])
    return None


def _plain(html: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))


def measurements(html: str) -> tuple[tuple[int, int, int] | None, float | None]:
    """Габариты (ВхШхГ) и вес из характеристик карточки."""
    text = _plain(html)
    dimensions = _DIMENSIONS.search(text)
    weight = _WEIGHT.search(text)
    return (
        (int(dimensions.group(1)), int(dimensions.group(2)), int(dimensions.group(3)))
        if dimensions else None,
        _number(weight.group(1)) if weight else None,
    )


def agrees(position: Position, dimensions, weight, tolerance: float = 0.08) -> bool:
    """Сошлись ли размеры прайса с характеристиками карточки.

    Сравниваются наборы размеров, а не позиции в тройке: порядок ВхШхГ
    поставщик соблюдает не везде. «Экран WDS» в прайсе записан как 746x500,
    на карточке — как 500x746, и это один и тот же экран. Требование —
    чтобы каждый размер из прайса нашёлся среди размеров карточки; защита
    от подстановки чужого снимка от этого не страдает, потому что у соседней
    модели линейки размеры отличаются в разы, а не перестановкой.

    Допуск дан на округление, а не на «похоже». Позиция без размеров в
    прайсе проверку пройти не может — снимок ей не достанется, и это
    осознанно: неверная фотография хуже отсутствующей.
    """
    if dimensions is None or position.height is None or position.width is None:
        return False
    limit = lambda a: max(5.0, a * tolerance)  # noqa: E731
    available = list(dimensions)
    for size in (position.height, position.width, position.depth):
        if size is None:
            continue
        match = next((d for d in available if abs(size - d) <= limit(size)), None)
        if match is None:
            return False
        available.remove(match)
    # Вес отсеивает только грубое несоответствие. Поставщик указывает то
    # нетто, то брутто с упаковкой, и расхождение в четверть веса при трёх
    # сошедшихся размерах — про упаковку, а не про другой товар. А вот вес,
    # отличающийся вдвое, — это уже другая позиция.
    if position.weight and weight:
        heavier, lighter = max(position.weight, weight), min(position.weight, weight)
        if lighter <= 0 or heavier / lighter > WEIGHT_RATIO:
            return False
    return True


class PrometClient:
    """Ссылки на снимки Промета: раздел из прайса, модель, сверка размеров.

    Реализует протокол `photobank.ImageIndex`, поэтому скачиванием и
    публикацией занимается общий `fetch_photos`, как у Бринэкса.

    Ключ — артикул: он есть и в прайсе, и в карте разделов. Страницы
    разделов и карточек берутся по одному разу и держатся в памяти на время
    прогона: одна карточка обслуживает все свои артикулы.
    """

    def __init__(self, catalog: dict[str, Position],
                 opener: Callable[[str], str] = fetch,
                 pause: float = PAUSE,
                 sleep: Callable[[float], None] = time.sleep,
                 require_measurements: bool = True,
                 log: Callable[[str], None] = lambda _: None) -> None:
        self.catalog = catalog
        self._open = opener
        self.pause = pause
        self._sleep = sleep
        self.require_measurements = require_measurements
        self.log = log
        self._pages: dict[str, str | None] = {}
        self._all_cards: list[str] | None = None
        self.last_mismatch: tuple[int, int, int] | None = None
        self.requests = 0
        self.found_outside_section = 0
        self.skipped_no_measurements = 0
        self.skipped_no_section = 0
        self.skipped_dead_section = 0
        self.skipped_not_found = 0
        self.skipped_ambiguous = 0
        self.skipped_mismatch = 0

    def covers(self, article: str) -> bool:
        """Есть ли у артикула разметка поставщика.

        Спрашивается до прогона: из 13 255 позиций прайса размечены 1 722, и
        разбросаны они по всему файлу. Без этого отбора `--limit 20` берёт
        двадцать первых строк — а это комплектующие вроде «Заготовка ключа»,
        которых на витринных листах нет вовсе, и проба даёт ноль.
        """
        return str(article).strip() in self.catalog

    def _page(self, url: str) -> str | None:
        """Страница с диска памяти; None — если её нет или она не открылась."""
        if url in self._pages:
            return self._pages[url]
        try:
            self.requests += 1
            html = self._open(url)
        except Exception as exc:
            self.log(f"не открылась {url}: {type(exc).__name__} {exc}")
            html = None
        else:
            if is_missing(html):
                html = None
        self._pages[url] = html
        if self.pause:
            self._sleep(self.pause)
        return html

    def _section_cards(self, section: str) -> list[str]:
        """Карточки раздела, с подъёмом на уровень выше, если раздел исчез."""
        html = self._page(section)
        if html is None:
            up = parent_section(section)
            if up is None:
                return []
            html = self._page(up)
            if html is None:
                return []
        return cards_of(html)

    def catalog_cards(self) -> list[str]:
        """Все карточки сайта — запасной список, когда раздел из прайса исчез.

        Берётся из карты сайта: раздел каталога товаров лежит в
        `sitemap-news-2.xml`, около 1 900 адресов после снятия повторов.
        Список нужен редко, поэтому читается лениво и один раз за прогон.
        """
        if self._all_cards is None:
            xml = self._page(SITEMAP_URL) or ""
            self._all_cards = list(dict.fromkeys(
                re.findall(r"/products/[a-z0-9\-]+/", xml)))
            if self._all_cards:
                self.log(f"запасной список карточек сайта: {len(self._all_cards)}")
        return self._all_cards

    def _match(self, position: Position, cards: Sequence[str]) -> list[str]:
        """Карточки, чей адрес оканчивается кодом модели. Отбор, а не выбор.

        Именно оканчивается, а не содержит. Иначе «Шкаф SL-125/2T» с кодом
        `sl1252t` попадает и в свою карточку, и в соседнюю `sl-125-2t-el`,
        где тот же корпус с электронным замком. Размеры у них совпадают до
        миллиметра, спор ими не разрешить, и обе позиции теряются — хотя у
        каждой на сайте есть собственная карточка.

        Адрес карточки устроен как «категория-бренд-модель»
        (`bukhgalterskiy-shkaf-aiko-sl-125-2t`), поэтому код модели стоит
        в конце, и суффикс — правильная граница.
        """
        code = position.code
        if len(code) < 4:
            return []
        return [c for c in cards
                if normalize(c.rstrip("/").rsplit("/", 1)[-1]).endswith(code)]

    def _confirm(self, position: Position, cards: Sequence[str],
                 strict: bool) -> tuple[str, str] | None:
        """Единственная карточка, подтверждённая размерами.

        Кандидатов бывает несколько: у одной модели на сайте заводят
        карточки под разные исполнения. Раньше это был отказ, теперь спор
        разрешают габариты — но разрешают только вчистую: если размеры
        сошлись у двух карточек, снимок по-прежнему не берётся.

        `strict` включает обязательную сверку. Он поднят всегда, когда
        позиция ищется вне своего раздела: там код модели остаётся
        единственным признаком, и без размеров подтвердить нечем.
        """
        confirmed: list[tuple[str, str]] = []
        for card in cards:
            html = self._page(urllib.parse.urljoin(BASE, card))
            if html is None:
                continue
            if strict or self.require_measurements:
                dimensions, weight = measurements(html)
                if dimensions is None:
                    # На карточке нет блока размеров — так у сейфов Гранит,
                    # Гарант и серии TM. Сверять нечем, и это не повод
                    # отказывать: внутри своего раздела уже есть два
                    # признака — раздел назначил сам поставщик, код модели
                    # совпал. А вот вне раздела или при нескольких
                    # кандидатах различить нечем, и снимок не берётся.
                    if strict or len(cards) > 1:
                        self.skipped_no_measurements += 1
                        continue
                elif not agrees(position, dimensions, weight):
                    self.last_mismatch = dimensions
                    continue
            confirmed.append((card, html))
            if len(confirmed) > 1:
                break
        if len(confirmed) == 1:
            return confirmed[0]
        if len(confirmed) > 1:
            self.skipped_ambiguous += 1
        return None

    def images(self, articles: Iterable[str]) -> dict[str, str]:
        """Артикул → ссылка на фотографию.

        Артикулы без раздела, с исчезнувшим разделом, без единственной
        карточки или с разошедшимися размерами в ответ не попадают: пустое
        место в карточке товара честнее чужого снимка.
        """
        wanted = [str(a).strip() for a in articles if str(a).strip()]
        found: dict[str, str] = {}
        for article in dict.fromkeys(wanted):
            position = self.catalog.get(article)
            if position is None:
                self.skipped_no_section += 1
                continue

            strict = False
            cards = self._section_cards(position.section)
            if not cards:
                # Раздел исчез вместе с родителем — на 05.09.2026 так пропали
                # 351 позиция. Остаётся весь каталог сайта, но тогда размеры
                # обязаны сойтись: без раздела код модели — единственный
                # признак, и подтвердить его больше нечем.
                if position.height is None or position.width is None:
                    self.skipped_dead_section += 1
                    continue
                cards = self.catalog_cards()
                strict = True
                if not cards:
                    self.skipped_dead_section += 1
                    continue

            candidates = self._match(position, cards)
            if not candidates:
                self.skipped_not_found += 1
                continue
            # Слишком широкий код модели: перебирать десятки карточек ради
            # одной позиции незачем, а отбирать из них наугад нельзя.
            if len(candidates) > MAX_CANDIDATES:
                self.skipped_ambiguous += 1
                continue

            self.last_mismatch = None
            confirmed = self._confirm(position, candidates, strict)
            if confirmed is None:
                if self.last_mismatch is not None or (
                        (strict or self.require_measurements) and candidates):
                    self.skipped_mismatch += 1
                    self.log(f"размеры разошлись, снимок не берём: {position.name} "
                             f"(прайс {position.height}x{position.width}, "
                             f"сайт {self.last_mismatch})")
                else:
                    self.skipped_not_found += 1
                continue

            card, html = confirmed
            photo = photo_of(html)
            if photo:
                found[article] = photo
                if strict:
                    self.found_outside_section += 1
            else:
                self.skipped_not_found += 1
        return found

    def lines(self) -> list[str]:
        """Отчёт о том, почему позиции остались без снимка."""
        return [
            f"запросов к сайту:            {self.requests}",
            f"нет раздела в прайсе:        {self.skipped_no_section}",
            f"раздел исчез с сайта:        {self.skipped_dead_section}",
            f"карточка не найдена:         {self.skipped_not_found}",
            f"карточек несколько:          {self.skipped_ambiguous}",
            f"размеры не сошлись:          {self.skipped_mismatch}",
            f"на карточке нет размеров:    {self.skipped_no_measurements}",
            f"найдено вне своего раздела:  {self.found_outside_section}",
        ]
