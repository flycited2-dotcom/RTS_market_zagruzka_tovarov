# План реализации: конвейер сборки прайс-листов для РТС-маркет

> **Для агентов:** ОБЯЗАТЕЛЬНЫЙ ПОДНАВЫК — используйте superpowers:subagent-driven-development
> (рекомендуется) или superpowers:executing-plans для выполнения задача за задачей.
> Шаги размечены чекбоксами `- [ ]`.

**Цель:** превратить разнородные прайсы поставщиков в готовые к импорту файлы РТС-маркет отдельно
для ООО «TLT» и для ИП, с управлением составом выгрузки и проверкой данных до загрузки.

**Архитектура:** конвейер из четырёх стадий — чтение по конфигурации источника, нормализация в
единый мастер-каталог, рендер под конкретное юридическое лицо, проверка и запись. Правила площадки
живут отдельно от правил поставщика, поэтому новый поставщик добавляется одним конфигом без
изменения кода.

**Стек:** Python 3.13, openpyxl (xlsx), xlrd (xls), PyYAML, Pillow, paramiko, requests, pytest.
Все библиотеки уже установлены в системном интерпретаторе.

**Спецификация:** `docs/superpowers/specs/2026-09-02-rts-market-price-pipeline-design.md`

## Глобальные ограничения

- Целевой лист выходного файла называется `Данные для импорта`, содержит 27 значащих колонок,
  шапка занимает строки 1–2, данные начинаются со строки 3.
- Единица измерения (колонка G) — **обозначение** ОКЕИ (`ШТ`, `КГ`), не наименование.
- НДС для ООО — строка `Облагается НДС 22`; для ИП — строка `Не облагается НДС`.
- Значения колонок доставки J, K, L — только `ДА` или `НЕТ`.
- Колонка B принимает только цифры. Колонка C принимает текст.
- Наименование — не более 200 символов, описание — не более 2000.
- Штрих-код — ровно 13 цифр либо пусто.
- Не более 50 000 строк данных в одном файле.
- Значение `1` в колонке AA удаляет позицию.
- Рабочая наценка обеих компаний — `2.0`, то есть плюс 100% к закупочной цене. Значение `1.0`
  в конфигурации источника означает «не задано» и передаёт управление уровню компании.
- Все пути в конфигурации задаются относительно корня проекта.
- Кодировка всех CSV — UTF-8 с BOM (`utf-8-sig`), чтобы файлы открывались в Excel без искажений.

## Структура файлов

| Файл | Ответственность |
|---|---|
| `src/rtsprice/config.py` | модели и загрузка `sources.yml`, `companies/*.yml`, `stoplist.csv` |
| `src/rtsprice/reference.py` | справочники ОКЕИ и ОКПД2, разрешение единиц измерения |
| `src/rtsprice/readers.py` | чтение xlsx и xls, склейка шапки, отбор товарных строк |
| `src/rtsprice/identity.py` | стабильный числовой идентификатор позиции |
| `src/rtsprice/describe.py` | генерация описаний, обрезка по границе слова |
| `src/rtsprice/normalize.py` | сырые строки в позиции мастер-каталога |
| `src/rtsprice/pricing.py` | НДС, наценка, округление |
| `src/rtsprice/photos.py` | поиск, обработка и публикация изображений |
| `src/rtsprice/render.py` | позиция в строку из 27 колонок |
| `src/rtsprice/validate.py` | отклонения и автоисправления |
| `src/rtsprice/state.py` | снимки выгрузок, строки удаления |
| `src/rtsprice/writer.py` | запись .xlsx, нарезка на части |
| `src/rtsprice/report.py` | `errors.xlsx` и `report.md` |
| `src/rtsprice/pipeline.py` | оркестровка сборки |
| `src/rtsprice/cli.py` | команды `status`, `build`, `check`, `on/off/freeze`, `feedback` |

---

### Задача 1: Каркас проекта и конфигурация

**Файлы:**
- Создать: `pyproject.toml`, `src/rtsprice/__init__.py`, `src/rtsprice/config.py`
- Создать: `sources.yml`, `companies/ooo_tlt.yml`, `companies/ip.yml`, `stoplist.csv`
- Тест: `tests/test_config.py`

**Интерфейсы:**
- Производит: `SourceConfig`, `CompanyConfig`, `load_sources(path) -> dict[str, SourceConfig]`,
  `load_companies(path) -> dict[str, CompanyConfig]`, `load_stoplist(path) -> set[tuple[str, str]]`

- [ ] **Шаг 1: Инициализировать репозиторий и структуру**

```bash
cd "C:/Users/TLT-1/Documents/GitHub/RTS_tender"
git init
mkdir -p src/rtsprice tests companies input photos reference state output
printf 'output/\nstate/\ninput/\nphotos/\n__pycache__/\n*.pyc\n.pytest_cache/\n' > .gitignore
```

- [ ] **Шаг 2: Создать `pyproject.toml`**

```toml
[project]
name = "rtsprice"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["openpyxl", "xlrd", "PyYAML", "Pillow", "paramiko", "requests"]

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Шаг 3: Написать падающий тест**

```python
# tests/test_config.py
from pathlib import Path
import textwrap
from rtsprice.config import load_sources, load_companies, load_stoplist


def test_load_sources_applies_defaults(tmp_path: Path):
    p = tmp_path / "sources.yml"
    p.write_text(textwrap.dedent("""
        promet:
          title: "Промет"
          state: on
          prefix: 10
          file: "input/promet/*.xlsx"
          sheets: ["база"]
          header_rows: [1]
          data_starts_at: 2
          row_is_product: price_not_empty
          columns:
            article: "Артикул"
            name: "Наименование"
            price: "Розничная цена"
    """), encoding="utf-8")
    src = load_sources(p)["promet"]
    assert src.code == "promet"
    assert src.state == "on"
    assert src.prefix == 10
    assert src.companies == ("ooo_tlt", "ip")
    assert src.markup == 1.0
    assert src.unit_default == "ШТ"
    assert src.price_includes_vat is True
    assert src.header_rows == (1,)


def test_load_sources_rejects_unknown_state(tmp_path: Path):
    p = tmp_path / "sources.yml"
    p.write_text('x:\n  state: paused\n  prefix: 1\n  file: "a"\n', encoding="utf-8")
    try:
        load_sources(p)
    except ValueError as exc:
        assert "paused" in str(exc)
    else:
        raise AssertionError("ожидалась ValueError")


def test_load_companies(tmp_path: Path):
    d = tmp_path / "companies"
    d.mkdir()
    (d / "ip.yml").write_text(textwrap.dedent("""
        title: "ИП"
        vat_mode: none
        markup: 1.3
        rounding: ruble
        validity_days: 21
        photo_base_url: "https://example.ru/p"
    """), encoding="utf-8")
    c = load_companies(d)["ip"]
    assert c.vat_mode == "none"
    assert c.markup == 1.3
    assert c.delivery_by_carrier == "ДА"


def test_load_stoplist(tmp_path: Path):
    p = tmp_path / "stoplist.csv"
    p.write_text("source,article\npromet,A-1\npromet,A-2\n", encoding="utf-8-sig")
    assert load_stoplist(p) == {("promet", "A-1"), ("promet", "A-2")}
```

- [ ] **Шаг 4: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_config.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.config'`

- [ ] **Шаг 5: Реализовать `config.py`**

```python
# src/rtsprice/config.py
"""Модели конфигурации и их загрузка."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import yaml

VALID_STATES = ("on", "off", "frozen")
VALID_VAT_MODES = ("vat22", "none")
VALID_ROUNDING = ("ruble", "ten", "none")
VALID_ROW_RULES = ("price_not_empty", "barcode13", "article_not_empty")


@dataclass(frozen=True)
class SourceConfig:
    code: str
    title: str
    state: str
    prefix: int
    companies: tuple[str, ...]
    file_glob: str
    sheets: tuple[str, ...]
    header_rows: tuple[int, ...]
    data_starts_at: int
    row_is_product: str
    columns: dict[str, str]
    include_groups: tuple[str, ...] = ()
    exclude_groups: tuple[str, ...] = ()
    min_stock: float | None = None
    markup: float = 1.0
    markup_by_group: dict[str, float] = field(default_factory=dict)
    unit_default: str = "ШТ"
    unit_by_group: dict[str, str] = field(default_factory=dict)
    price_includes_vat: bool = True
    description_template: str = "{name}"
    okpd2_by_group: dict[str, str] = field(default_factory=dict)
    country: str | None = None
    region: str | None = None


@dataclass(frozen=True)
class CompanyConfig:
    code: str
    title: str
    vat_mode: str
    markup: float = 1.0
    rounding: str = "ruble"
    delivery_by_carrier: str = "ДА"
    delivery_by_seller: str = "НЕТ"
    delivery_pickup: str = "НЕТ"
    warehouse_address: str = ""
    delivery_terms: str = ""
    validity_days: int = 21
    photo_base_url: str = ""


def _states(value: object) -> str:
    """YAML превращает `on` в True, а `off` в False — возвращаем обратно."""
    if value is True:
        return "on"
    if value is False:
        return "off"
    return str(value or "on")


def load_sources(path: Path) -> dict[str, SourceConfig]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    result: dict[str, SourceConfig] = {}
    for code, body in raw.items():
        body = body or {}
        state = _states(body.get("state"))
        if state not in VALID_STATES:
            raise ValueError(f"источник {code}: недопустимое состояние {state!r}")
        rule = str(body.get("row_is_product", "price_not_empty"))
        if rule not in VALID_ROW_RULES:
            raise ValueError(f"источник {code}: неизвестное правило строки {rule!r}")
        result[code] = SourceConfig(
            code=code,
            title=str(body.get("title", code)),
            state=state,
            prefix=int(body["prefix"]),
            companies=tuple(body.get("companies") or ("ooo_tlt", "ip")),
            file_glob=str(body["file"]),
            sheets=tuple(body.get("sheets") or ()),
            header_rows=tuple(body.get("header_rows") or (1,)),
            data_starts_at=int(body.get("data_starts_at", 2)),
            row_is_product=rule,
            columns=dict(body.get("columns") or {}),
            include_groups=tuple(body.get("include_groups") or ()),
            exclude_groups=tuple(body.get("exclude_groups") or ()),
            min_stock=(None if body.get("min_stock") is None else float(body["min_stock"])),
            markup=float(body.get("markup", 1.0)),
            markup_by_group={str(k): float(v) for k, v in (body.get("markup_by_group") or {}).items()},
            unit_default=str(body.get("unit_default", "ШТ")),
            unit_by_group={str(k): str(v) for k, v in (body.get("unit_by_group") or {}).items()},
            price_includes_vat=bool(body.get("price_includes_vat", True)),
            description_template=str(body.get("description_template", "{name}")),
            okpd2_by_group={str(k): str(v) for k, v in (body.get("okpd2_by_group") or {}).items()},
            country=(body.get("country") or None),
            region=(body.get("region") or None),
        )
    return result


def load_companies(directory: Path) -> dict[str, CompanyConfig]:
    result: dict[str, CompanyConfig] = {}
    for p in sorted(Path(directory).glob("*.yml")):
        body = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        vat = str(body.get("vat_mode", "vat22"))
        if vat not in VALID_VAT_MODES:
            raise ValueError(f"{p.name}: недопустимый vat_mode {vat!r}")
        rounding = str(body.get("rounding", "ruble"))
        if rounding not in VALID_ROUNDING:
            raise ValueError(f"{p.name}: недопустимое округление {rounding!r}")
        delivery = body.get("delivery") or {}
        result[p.stem] = CompanyConfig(
            code=p.stem,
            title=str(body.get("title", p.stem)),
            vat_mode=vat,
            markup=float(body.get("markup", 1.0)),
            rounding=rounding,
            delivery_by_carrier=str(delivery.get("by_carrier", "ДА")),
            delivery_by_seller=str(delivery.get("by_seller", "НЕТ")),
            delivery_pickup=str(delivery.get("pickup", "НЕТ")),
            warehouse_address=str(body.get("warehouse_address", "")),
            delivery_terms=str(body.get("delivery_terms", "")),
            validity_days=int(body.get("validity_days", 21)),
            photo_base_url=str(body.get("photo_base_url", "")),
        )
    return result


def load_stoplist(path: Path) -> set[tuple[str, str]]:
    p = Path(path)
    if not p.exists():
        return set()
    with p.open(encoding="utf-8-sig", newline="") as fh:
        return {
            (row["source"].strip(), row["article"].strip())
            for row in csv.DictReader(fh)
            if row.get("source") and row.get("article")
        }
```

- [ ] **Шаг 6: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_config.py -v`
Ожидается: PASS, 4 теста

- [ ] **Шаг 7: Создать заготовки конфигурации**

```bash
cat > companies/ooo_tlt.yml <<'YML'
title: "ООО ТЛТ"
vat_mode: vat22
markup: 2.0
rounding: ruble
validity_days: 21
photo_base_url: ""
delivery:
  by_carrier: "ДА"
  by_seller: "НЕТ"
  pickup: "НЕТ"
warehouse_address: ""
delivery_terms: ""
YML
sed 's/vat_mode: vat22/vat_mode: none/; s/title: "ООО ТЛТ"/title: "ИП"/' companies/ooo_tlt.yml > companies/ip.yml
printf 'source,article\n' > stoplist.csv
printf '# Реестр источников. Заполняется в задаче 16.\n' > sources.yml
```

- [ ] **Шаг 8: Зафиксировать**

```bash
git add -A
git commit -m "feat: каркас проекта и модели конфигурации"
```

---

### Задача 2: Справочники ОКЕИ и ОКПД2

**Файлы:**
- Создать: `src/rtsprice/reference.py`, `reference/unit_aliases.yml`
- Тест: `tests/test_reference.py`

**Интерфейсы:**
- Потребляет: ничего из предыдущих задач
- Производит: `extract_reference_tables(template, out_dir)`, `load_units(path) -> set[str]`,
  `load_unit_aliases(path) -> dict[str, str]`,
  `resolve_unit(raw, units, aliases) -> str | None`, `load_okpd2(path) -> set[str]`

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_reference.py
from pathlib import Path
from rtsprice.reference import load_units, load_unit_aliases, resolve_unit, load_okpd2


def _units(tmp_path: Path) -> Path:
    p = tmp_path / "okei.csv"
    p.write_text("code,symbol,name\n796,ШТ,Штука\n166,КГ,Килограмм\n112,Л; ДМ3,Литр\n",
                 encoding="utf-8-sig")
    return p


def test_load_units_returns_symbols(tmp_path: Path):
    assert load_units(_units(tmp_path)) == {"ШТ", "КГ", "Л; ДМ3"}


def test_resolve_unit_direct_match(tmp_path: Path):
    units = load_units(_units(tmp_path))
    assert resolve_unit("шт", units, {}) == "ШТ"
    assert resolve_unit("  КГ ", units, {}) == "КГ"


def test_resolve_unit_via_alias(tmp_path: Path):
    units = load_units(_units(tmp_path))
    aliases = {"штука": "ШТ", "pcs": "ШТ", "уп": "ШТ"}
    assert resolve_unit("Штука", units, aliases) == "ШТ"
    assert resolve_unit("pcs", units, aliases) == "ШТ"


def test_resolve_unit_unknown_returns_none(tmp_path: Path):
    units = load_units(_units(tmp_path))
    assert resolve_unit("бушель", units, {}) is None
    assert resolve_unit(None, units, {}) is None


def test_load_okpd2(tmp_path: Path):
    p = tmp_path / "okpd2.csv"
    p.write_text("code,name\n26.40.20.122,Телевизоры\n", encoding="utf-8-sig")
    assert "26.40.20.122" in load_okpd2(p)
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_reference.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.reference'`

- [ ] **Шаг 3: Реализовать `reference.py`**

```python
# src/rtsprice/reference.py
"""Справочники площадки: единицы измерения ОКЕИ и коды ОКПД2."""
from __future__ import annotations

import csv
import re
from pathlib import Path

import yaml

UNITS_SHEET = "Справочник единиц измерения"
OKPD2_SHEET = "Справочник ОКПД2"
OKPD2_PATTERN = re.compile(r"^\d{2}(\.\d{1,2}){0,2}(\.\d{1,3})?$")


def _key(value: str) -> str:
    """Ключ сравнения: без регистра, без лишних пробелов и точек по краям."""
    return re.sub(r"\s+", " ", str(value)).strip().strip(".").casefold()


def extract_reference_tables(template: Path, out_dir: Path) -> None:
    """Единожды вытащить справочники из шаблона РТС в CSV."""
    import openpyxl

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.load_workbook(template, data_only=True, read_only=True)

    with (out_dir / "okei.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "symbol", "name"])
        for symbol, name, code in wb[UNITS_SHEET].iter_rows(min_row=2, max_col=3, values_only=True):
            if symbol and str(symbol).strip() and str(symbol).strip() != "-":
                w.writerow([code, str(symbol).strip(), str(name or "").strip()])

    with (out_dir / "okpd2.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "name"])
        for code, name in wb[OKPD2_SHEET].iter_rows(min_row=2, max_col=2, values_only=True):
            if code:
                w.writerow([str(code).strip(), str(name or "").strip()])
    wb.close()


def load_units(path: Path) -> set[str]:
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        return {row["symbol"].strip() for row in csv.DictReader(fh) if row.get("symbol")}


def load_unit_aliases(path: Path) -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {_key(k): str(v) for k, v in raw.items()}


def resolve_unit(raw: object, units: set[str], aliases: dict[str, str]) -> str | None:
    """Привести произвольное обозначение к обозначению ОКЕИ или вернуть None."""
    if raw is None or not str(raw).strip():
        return None
    index = {_key(u): u for u in units}
    k = _key(raw)
    if k in index:
        return index[k]
    if k in aliases:
        return aliases[k]
    return None


def load_okpd2(path: Path) -> set[str]:
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        return {row["code"].strip() for row in csv.DictReader(fh) if row.get("code")}
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_reference.py -v`
Ожидается: PASS, 5 тестов

- [ ] **Шаг 5: Извлечь настоящие справочники из шаблона**

Положить шаблон в `reference/template.xlsx`, затем выполнить:

```bash
cp "C:/Users/TLT-1/Downloads/Шаблон для импорта ППЛ.xlsx" reference/template.xlsx
python -c "from pathlib import Path; import sys; sys.path.insert(0,'src'); from rtsprice.reference import extract_reference_tables; extract_reference_tables(Path('reference/template.xlsx'), Path('reference'))"
python -c "import sys; sys.path.insert(0,'src'); from pathlib import Path; from rtsprice.reference import load_units, load_okpd2; print('единиц:', len(load_units(Path('reference/okei.csv'))), 'кодов ОКПД2:', len(load_okpd2(Path('reference/okpd2.csv'))))"
```

Ожидается: около 500 единиц и около 19 800 кодов ОКПД2.

- [ ] **Шаг 6: Создать словарь алиасов**

```bash
cat > reference/unit_aliases.yml <<'YML'
# Соответствие «как пишет поставщик» -> «обозначение ОКЕИ»
шт: ШТ
штука: ШТ
штуки: ШТ
pcs: ШТ
ед: ШТ
компл: ШТ
комплект: ШТ
набор: ШТ
упак: УПАК
уп: УПАК
кг: КГ
килограмм: КГ
г: Г
литр: "Л; ДМ3"
л: "Л; ДМ3"
м: М
метр: М
м2: М2
м3: М3
пара: ПАР
YML
```

- [ ] **Шаг 7: Зафиксировать**

```bash
git add -A
git commit -m "feat: справочники ОКЕИ и ОКПД2, разрешение единиц измерения"
```

---

### Задача 3: Чтение прайсов поставщиков

**Файлы:**
- Создать: `src/rtsprice/readers.py`
- Тест: `tests/test_readers.py`

**Интерфейсы:**
- Потребляет: `SourceConfig` из задачи 1
- Производит: `RawRow(source, sheet, row_number, values: dict[str, object])`,
  `header_key(parts) -> str`, `parse_number(value) -> float | None`,
  `find_source_file(pattern) -> Path`, `read_source(cfg, path) -> list[RawRow]`

Шапка склеивается из строк `header_rows`: значения непустых ячеек одной колонки соединяются
пробелом сверху вниз. Это разрешает три реальных случая: шапка в одной строке (Промет), шапка,
разнесённая по разным колонкам двух строк (Гуриненко), и двухуровневая шапка, где верх задаёт
блок, а низ — показатель. Для прайса ОПТ пара «Закуп» и «Цена» даёт заголовок `Закуп Цена`, что
отличает её от `от 100 т.руб/мес Цена`.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_readers.py
from pathlib import Path

import openpyxl
import pytest

from rtsprice.config import SourceConfig
from rtsprice.readers import header_key, find_source_file, read_source


def _cfg(**over) -> SourceConfig:
    base = dict(
        code="s", title="S", state="on", prefix=7, companies=("ooo_tlt",),
        file_glob="input/s/*.xlsx", sheets=("Лист1",), header_rows=(1,),
        data_starts_at=2, row_is_product="price_not_empty",
        columns={"article": "Артикул", "name": "Наименование", "price": "Цена"},
    )
    base.update(over)
    return SourceConfig(**base)


def _book(path: Path, rows: list[list]) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Лист1"
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def test_header_key_joins_non_empty_parts():
    assert header_key(["Закуп", "Цена"]) == "Закуп Цена"
    assert header_key(["Штрихкод", None]) == "Штрихкод"
    assert header_key([None, "Оптовая"]) == "Оптовая"
    assert header_key([" А ", "  ", "Б"]) == "А Б"


def test_read_source_maps_columns_and_skips_non_products(tmp_path: Path):
    p = _book(tmp_path / "p.xlsx", [
        ["Артикул", "Наименование", "Цена"],
        ["A-1", "Товар один", 100],
        ["РАЗДЕЛ", "Бытовая химия", None],
        ["A-2", "Товар два", 250.5],
    ])
    rows = read_source(_cfg(), p)
    assert [r.values["article"] for r in rows] == ["A-1", "A-2"]
    assert rows[0].values["name"] == "Товар один"
    assert rows[1].values["price"] == 250.5
    assert rows[0].row_number == 2


def test_read_source_two_row_header(tmp_path: Path):
    p = _book(tmp_path / "p.xlsx", [
        ["Штрихкод", "Номенклатура", None],
        [None, None, "Цена клиента"],
        ["4600000000017", "Сок", 99],
    ])
    cfg = _cfg(header_rows=(1, 2), data_starts_at=3,
               columns={"barcode": "Штрихкод", "name": "Номенклатура", "price": "Цена клиента"})
    rows = read_source(cfg, p)
    assert len(rows) == 1
    assert rows[0].values["barcode"] == "4600000000017"
    assert rows[0].values["price"] == 99


def test_read_source_barcode13_rule(tmp_path: Path):
    p = _book(tmp_path / "p.xlsx", [
        ["Штрихкод", "Номенклатура", "Цена клиента"],
        ["Бакалея", None, None],
        ["4600000000017", "Сок", 99],
    ])
    cfg = _cfg(header_rows=(1,), data_starts_at=2, row_is_product="barcode13",
               columns={"barcode": "Штрихкод", "name": "Номенклатура", "price": "Цена клиента"})
    rows = read_source(cfg, p)
    assert [r.values["name"] for r in rows] == ["Сок"]


def test_read_source_missing_column_raises(tmp_path: Path):
    p = _book(tmp_path / "p.xlsx", [["Артикул", "Наименование"], ["A-1", "Товар"]])
    with pytest.raises(KeyError, match="Цена"):
        read_source(_cfg(), p)


def test_find_source_file_returns_newest(tmp_path: Path):
    import os
    import time

    d = tmp_path / "input" / "s"
    d.mkdir(parents=True)
    old, new = d / "a.xlsx", d / "b.xlsx"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    os.utime(old, (time.time() - 500, time.time() - 500))
    assert find_source_file(str(d / "*.xlsx")) == new
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_readers.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.readers'`

- [ ] **Шаг 3: Реализовать `readers.py`**

```python
# src/rtsprice/readers.py
"""Чтение книг поставщиков: xlsx через openpyxl, xls через xlrd."""
from __future__ import annotations

import glob
import re
from dataclasses import dataclass
from pathlib import Path

from .config import SourceConfig


@dataclass(frozen=True)
class RawRow:
    source: str
    sheet: str
    row_number: int
    values: dict[str, object]


def header_key(parts: list[object]) -> str:
    """Склеить вертикальные части шапки одной колонки в один заголовок."""
    chunks = [
        re.sub(r"\s+", " ", str(p)).strip()
        for p in parts
        if p is not None and str(p).strip()
    ]
    return " ".join(chunks)


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def find_source_file(pattern: str) -> Path:
    """Самый свежий файл, подходящий под шаблон пути."""
    matches = [Path(p) for p in glob.glob(pattern)]
    if not matches:
        raise FileNotFoundError(f"не найден файл по шаблону {pattern}")
    return max(matches, key=lambda p: p.stat().st_mtime)


def _grid(path: Path, sheet_names: tuple[str, ...]) -> list[tuple[str, list[list[object]]]]:
    """Прочитать нужные листы книги как список строк-списков."""
    if path.suffix.lower() == ".xls":
        import xlrd

        wb = xlrd.open_workbook(path)
        sheets = [s for s in wb.sheets() if not sheet_names or s.name in sheet_names]
        return [
            (
                s.name,
                [
                    [s.cell_value(r, c) if s.cell_value(r, c) != "" else None
                     for c in range(s.ncols)]
                    for r in range(s.nrows)
                ],
            )
            for s in sheets
        ]

    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    result = []
    for ws in wb.worksheets:
        if sheet_names and ws.title not in sheet_names:
            continue
        result.append((ws.title, [list(row) for row in ws.iter_rows(values_only=True)]))
    wb.close()
    return result


def parse_number(value: object) -> float | None:
    """Разобрать число в форматах «17 050,00», «1 178.17», 1178.17.

    Русские выгрузки разделяют разряды обычным, неразрывным или узким
    неразрывным пробелом. Класс ``\s`` в Python покрывает их все, поэтому
    перечислять символы поимённо не нужно — и невозможно потерять один из
    них при копировании кода.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"\s", "", str(value)).replace(",", ".")
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        return None
    return float(text)


def _is_product(rule: str, values: dict[str, object]) -> bool:
    if rule == "price_not_empty":
        price = values.get("price")
        if price is None or not str(price).strip():
            return False
        number = parse_number(price)
        # Неразбираемое значение («по запросу») остаётся товарной строкой:
        # её отклонит слой нормализации с внятной причиной.
        return number is None or number != 0
    if rule == "barcode13":
        barcode = str(values.get("barcode") or "").strip()
        return barcode.isdigit() and len(barcode) == 13
    return bool(str(values.get("article") or "").strip())


def read_source(cfg: SourceConfig, path: Path) -> list[RawRow]:
    rows: list[RawRow] = []
    for sheet_name, grid in _grid(Path(path), cfg.sheets):
        if not grid:
            continue
        width = max(len(r) for r in grid)
        headers = [
            header_key(
                [
                    grid[i - 1][c] if i - 1 < len(grid) and c < len(grid[i - 1]) else None
                    for i in cfg.header_rows
                ]
            )
            for c in range(width)
        ]
        index = {_norm(h): c for c, h in enumerate(headers) if h}
        mapping: dict[str, int] = {}
        for logical, title in cfg.columns.items():
            col = index.get(_norm(title))
            if col is None:
                raise KeyError(
                    f"источник {cfg.code}, лист {sheet_name!r}: не найдена колонка {title!r}; "
                    f"доступны: {[h for h in headers if h][:20]}"
                )
            mapping[logical] = col

        for number, row in enumerate(grid, start=1):
            if number < cfg.data_starts_at:
                continue
            values: dict[str, object] = {}
            for logical, col in mapping.items():
                value = row[col] if col < len(row) else None
                if isinstance(value, str) and not value.strip():
                    value = None
                values[logical] = value
            if _is_product(cfg.row_is_product, values):
                rows.append(RawRow(cfg.code, sheet_name, number, values))
    return rows
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_readers.py -v`
Ожидается: PASS, 6 тестов

- [ ] **Шаг 5: Зафиксировать**

```bash
git add -A
git commit -m "feat: чтение прайсов xlsx и xls со склейкой многострочной шапки"
```

---

### Задача 4: Стабильные числовые идентификаторы

**Файлы:**
- Создать: `src/rtsprice/identity.py`
- Тест: `tests/test_identity.py`

**Интерфейсы:**
- Производит: `IdMap(path)` с методами `get_or_create(source, prefix, article) -> int` и
  `save()`; функцию `prefix_of(rts_id) -> int`

Идентификатор строится как `prefix * 10_000_000 + порядковый номер`. Соответствие только
пополняется: артикул, встреченный ранее, всегда получает прежний номер. Это условие корректного
обновления позиций на площадке, поэтому файл соответствий никогда не перезаписывается с нуля.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_identity.py
from pathlib import Path

import pytest

from rtsprice.identity import IdMap, prefix_of


def test_assigns_sequential_ids_within_prefix(tmp_path: Path):
    m = IdMap(tmp_path / "id_map.csv")
    assert m.get_or_create("promet", 10, "A-1") == 100_000_001
    assert m.get_or_create("promet", 10, "A-2") == 100_000_002
    assert m.get_or_create("brinex", 11, "B-1") == 110_000_001


def test_same_article_keeps_same_id(tmp_path: Path):
    m = IdMap(tmp_path / "id_map.csv")
    first = m.get_or_create("promet", 10, "A-1")
    m.get_or_create("promet", 10, "A-2")
    assert m.get_or_create("promet", 10, "A-1") == first


def test_ids_survive_reload(tmp_path: Path):
    p = tmp_path / "id_map.csv"
    m = IdMap(p)
    kept = m.get_or_create("promet", 10, "A-1")
    m.get_or_create("promet", 10, "A-2")
    m.save()

    again = IdMap(p)
    assert again.get_or_create("promet", 10, "A-1") == kept
    assert again.get_or_create("promet", 10, "A-3") == 100_000_003


def test_prefix_of_extracts_source_prefix():
    assert prefix_of(100_000_001) == 10
    assert prefix_of(110_000_042) == 11


def test_rejects_prefix_out_of_range(tmp_path: Path):
    m = IdMap(tmp_path / "id_map.csv")
    with pytest.raises(ValueError):
        m.get_or_create("x", 0, "A")
    with pytest.raises(ValueError):
        m.get_or_create("x", 100, "A")
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_identity.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.identity'`

- [ ] **Шаг 3: Реализовать `identity.py`**

```python
# src/rtsprice/identity.py
"""Стабильное соответствие «источник плюс артикул» и числового идентификатора РТС."""
from __future__ import annotations

import csv
from pathlib import Path

BLOCK = 10_000_000


def prefix_of(rts_id: int) -> int:
    return int(rts_id) // BLOCK


class IdMap:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._by_key: dict[tuple[str, str], int] = {}
        self._max_in_prefix: dict[int, int] = {}
        self._dirty = False
        if self.path.exists():
            with self.path.open(encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    rts_id = int(row["rts_id"])
                    self._by_key[(row["source"], row["article"])] = rts_id
                    p = prefix_of(rts_id)
                    self._max_in_prefix[p] = max(self._max_in_prefix.get(p, 0), rts_id % BLOCK)

    def get_or_create(self, source: str, prefix: int, article: str) -> int:
        if not 1 <= int(prefix) <= 99:
            raise ValueError(f"префикс источника должен быть от 1 до 99, получено {prefix}")
        key = (source, str(article))
        if key in self._by_key:
            return self._by_key[key]
        nxt = self._max_in_prefix.get(int(prefix), 0) + 1
        if nxt >= BLOCK:
            raise ValueError(f"исчерпан диапазон идентификаторов для префикса {prefix}")
        rts_id = int(prefix) * BLOCK + nxt
        self._by_key[key] = rts_id
        self._max_in_prefix[int(prefix)] = nxt
        self._dirty = True
        return rts_id

    def save(self) -> None:
        if not self._dirty and self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["source", "article", "rts_id"])
            for (source, article), rts_id in sorted(self._by_key.items(), key=lambda kv: kv[1]):
                writer.writerow([source, article, rts_id])
        tmp.replace(self.path)
        self._dirty = False
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_identity.py -v`
Ожидается: PASS, 5 тестов

- [ ] **Шаг 5: Зафиксировать**

```bash
git add -A
git commit -m "feat: стабильные числовые идентификаторы позиций"
```

---

### Задача 5: Описания и обрезка текста

**Файлы:**
- Создать: `src/rtsprice/describe.py`
- Тест: `tests/test_describe.py`

**Интерфейсы:**
- Производит: `truncate(text, limit) -> str`, `build_description(template, values, fallback) -> str`,
  константы `NAME_LIMIT = 200` и `DESCRIPTION_LIMIT = 2000`

Строка шаблона, в которой хотя бы одно поле оказалось пустым, выбрасывается целиком — так
«Диаметр: » не попадает на витрину. Если после подстановки не осталось ничего, используется
резервное значение, чтобы обязательное поле не оказалось пустым.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_describe.py
from rtsprice.describe import build_description, truncate


def test_truncate_keeps_short_text():
    assert truncate("короткий", 200) == "короткий"


def test_truncate_cuts_on_word_boundary():
    out = truncate("Сейф взломостойкий модели ES тридцать литров", 25)
    assert len(out) <= 25
    assert out == "Сейф взломостойкий"


def test_truncate_handles_single_long_word():
    assert truncate("ААААААААААА", 5) == "ААААА"


def test_build_description_drops_lines_with_empty_fields():
    tpl = "{name}\nПроизводитель: {brand}\nДиаметр: {diameter}"
    out = build_description(tpl, {"name": "Диск X", "brand": "СКАД", "diameter": ""}, "Диск X")
    assert out == "Диск X\nПроизводитель: СКАД"


def test_build_description_falls_back_when_empty():
    assert build_description("{brand}", {"brand": None}, "Товар") == "Товар"


def test_build_description_ignores_unknown_placeholders():
    assert build_description("{name}\n{missing}", {"name": "Товар"}, "Товар") == "Товар"


def test_build_description_respects_limit():
    out = build_description("{name}", {"name": "х" * 3000}, "х")
    assert len(out) <= 2000
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_describe.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.describe'`

- [ ] **Шаг 3: Реализовать `describe.py`**

```python
# src/rtsprice/describe.py
"""Генерация описаний позиций и обрезка текста по границе слова."""
from __future__ import annotations

import re

NAME_LIMIT = 200
DESCRIPTION_LIMIT = 2000
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def truncate(text: object, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,.;:-")


def build_description(template: str, values: dict[str, object], fallback: object) -> str:
    """Подставить поля в шаблон, выбросив строки с незаполненными полями."""
    lines: list[str] = []
    for line in str(template).splitlines():
        names = _PLACEHOLDER.findall(line)
        if names and any(
            values.get(n) is None or not str(values.get(n)).strip() for n in names
        ):
            continue
        rendered = _PLACEHOLDER.sub(
            lambda m: str(values.get(m.group(1), "")).strip(), line
        ).strip()
        if rendered:
            lines.append(rendered)
    text = "\n".join(lines).strip()
    if not text:
        text = str(fallback or "").strip()
    if len(text) > DESCRIPTION_LIMIT:
        text = truncate(text, DESCRIPTION_LIMIT)
    return text
```

Обрезка описания схлопывает переводы строк, поскольку `truncate` нормализует пробельные символы.
Это допустимо: срабатывает только на описаниях длиннее 2000 символов, которых в рассмотренных
прайсах нет, а сохранность обязательного поля важнее вёрстки.

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_describe.py -v`
Ожидается: PASS, 7 тестов

- [ ] **Шаг 5: Зафиксировать**

```bash
git add -A
git commit -m "feat: генерация описаний и обрезка по границе слова"
```

---

### Задача 6: Нормализация в мастер-каталог

**Файлы:**
- Создать: `src/rtsprice/normalize.py`
- Тест: `tests/test_normalize.py`

**Интерфейсы:**
- Потребляет: `SourceConfig` (задача 1), `resolve_unit` (задача 2), `RawRow` и `parse_number` (задача 3),
  `IdMap` (задача 4), `build_description`, `truncate`, `NAME_LIMIT` (задача 5)
- Производит: `Item`, `Rejection`, `normalize_source(cfg, rows, idmap, stoplist, units, aliases) ->
  tuple[list[Item], list[Rejection]]`

Здесь применяются все фильтры состава: стоп-лист, группы, минимальный остаток, наличие цены.
Отвергнутая позиция не молчит — она возвращается объектом `Rejection` и попадает в `errors.xlsx`.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_normalize.py
from pathlib import Path

from rtsprice.config import SourceConfig
from rtsprice.identity import IdMap
from rtsprice.normalize import normalize_source
from rtsprice.readers import RawRow


def _cfg(**over) -> SourceConfig:
    base = dict(
        code="s", title="S", state="on", prefix=7, companies=("ooo_tlt",),
        file_glob="x", sheets=(), header_rows=(1,), data_starts_at=2,
        row_is_product="price_not_empty",
        columns={"article": "Артикул", "name": "Наименование", "price": "Цена"},
        unit_default="ШТ", description_template="{name}",
    )
    base.update(over)
    return SourceConfig(**base)


def _row(n: int = 2, **values) -> RawRow:
    base = {"article": "A-1", "name": "Товар", "price": 100}
    base.update(values)
    return RawRow("s", "Лист1", n, base)


UNITS = {"ШТ", "КГ"}


def test_builds_item_with_id_and_description(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    items, rejected = normalize_source(_cfg(), [_row()], idmap, set(), UNITS, {})
    assert not rejected
    item = items[0]
    assert item.rts_id == 70_000_001
    assert item.article == "A-1"
    assert item.name == "Товар"
    assert item.description == "Товар"
    assert item.price_in == 100.0
    assert item.unit == "ШТ"


def test_rejects_row_without_name(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    items, rejected = normalize_source(_cfg(), [_row(name=None)], idmap, set(), UNITS, {})
    assert items == []
    assert rejected[0].field == "name"


def test_rejects_unparsable_price(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    items, rejected = normalize_source(_cfg(), [_row(price="по запросу")], idmap, set(), UNITS, {})
    assert items == []
    assert rejected[0].field == "price"


def test_parses_price_with_spaces_and_comma(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    items, _ = normalize_source(_cfg(), [_row(price="17 050,00")], idmap, set(), UNITS, {})
    assert items[0].price_in == 17050.0


def test_stoplist_excludes_article(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    items, rejected = normalize_source(_cfg(), [_row()], idmap, {("s", "A-1")}, UNITS, {})
    assert items == []
    assert rejected[0].reason == "в стоп-листе"


def test_exclude_groups_filters_rows(tmp_path: Path):
    cfg = _cfg(columns={"article": "A", "name": "N", "price": "P", "group": "G"},
               exclude_groups=("Химия",))
    idmap = IdMap(tmp_path / "m.csv")
    rows = [_row(group="Химия"), _row(2, article="A-2", group="Посуда")]
    items, _ = normalize_source(cfg, rows, idmap, set(), UNITS, {})
    assert [i.article for i in items] == ["A-2"]


def test_min_stock_filters_out_of_stock(tmp_path: Path):
    cfg = _cfg(columns={"article": "A", "name": "N", "price": "P", "stock": "S"}, min_stock=1)
    idmap = IdMap(tmp_path / "m.csv")
    rows = [_row(stock=0), _row(2, article="A-2", stock=5)]
    items, _ = normalize_source(cfg, rows, idmap, set(), UNITS, {})
    assert [i.article for i in items] == ["A-2"]


def test_name_longer_than_limit_is_truncated(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    long_name = "слово " * 60
    items, _ = normalize_source(_cfg(), [_row(name=long_name)], idmap, set(), UNITS, {})
    assert len(items[0].name) <= 200
    assert len(items[0].description) > 200


def test_barcode_kept_only_when_13_digits(tmp_path: Path):
    cfg = _cfg(columns={"article": "A", "name": "N", "price": "P", "barcode": "B"})
    idmap = IdMap(tmp_path / "m.csv")
    rows = [_row(barcode="4600000000017"), _row(2, article="A-2", barcode="12345")]
    items, _ = normalize_source(cfg, rows, idmap, set(), UNITS, {})
    assert items[0].barcode == "4600000000017"
    assert items[1].barcode is None


def test_unknown_unit_falls_back_to_default(tmp_path: Path):
    cfg = _cfg(columns={"article": "A", "name": "N", "price": "P", "unit": "U"})
    idmap = IdMap(tmp_path / "m.csv")
    items, _ = normalize_source(cfg, [_row(unit="бушель")], idmap, set(), UNITS, {})
    assert items[0].unit == "ШТ"


def test_integer_article_read_as_float_gets_one_identity(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    rows = [_row(article=76862.0), _row(3, article=76862)]
    items, rejected = normalize_source(_cfg(), rows, idmap, set(), UNITS, {})
    assert items[0].article == "76862"
    assert rejected[0].reason == "дубль артикула в прайсе"


def test_min_stock_reports_missing_stock_separately(tmp_path: Path):
    cfg = _cfg(columns={"article": "A", "name": "N", "price": "P", "stock": "S"}, min_stock=1)
    idmap = IdMap(tmp_path / "m.csv")
    items, rejected = normalize_source(cfg, [_row(stock=None)], idmap, set(), UNITS, {})
    assert items == []
    assert rejected[0].reason == "нет данных об остатке"
    assert rejected[0].value == ""


def test_numeric_attribute_is_canonicalised_for_description(tmp_path: Path):
    cfg = _cfg(columns={"article": "A", "name": "N", "price": "P", "diameter": "D"},
               description_template="{name}
Диаметр: {diameter}")
    idmap = IdMap(tmp_path / "m.csv")
    items, _ = normalize_source(cfg, [_row(diameter=15.0)], idmap, set(), UNITS, {})
    assert items[0].attributes["diameter"] == "15"
    assert items[0].description == "Товар
Диаметр: 15"


def test_duplicate_article_within_source_rejected(tmp_path: Path):
    idmap = IdMap(tmp_path / "m.csv")
    items, rejected = normalize_source(_cfg(), [_row(), _row(3)], idmap, set(), UNITS, {})
    assert len(items) == 1
    assert rejected[0].reason == "дубль артикула в прайсе"
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_normalize.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.normalize'`

- [ ] **Шаг 3: Реализовать `normalize.py`**

```python
# src/rtsprice/normalize.py
"""Приведение сырых строк прайса к позициям мастер-каталога."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import SourceConfig
from .describe import NAME_LIMIT, build_description, truncate
from .identity import IdMap
from .readers import RawRow, parse_number
from .reference import resolve_unit

KNOWN_FIELDS = (
    "article", "name", "price", "unit", "okpd2", "barcode",
    "country", "region", "group", "stock",
)


@dataclass(frozen=True)
class Item:
    source: str
    article: str
    rts_id: int
    name: str
    description: str
    price_in: float
    unit: str
    okpd2: str | None = None
    barcode: str | None = None
    country: str | None = None
    region: str | None = None
    group: str | None = None
    stock: float | None = None
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Rejection:
    source: str
    article: str
    row_number: int
    field: str
    reason: str
    value: str = ""


def _text(value: object) -> str | None:
    """Привести значение ячейки к тексту.

    Целое число, пришедшее как float, канонизируется: xlrd отдаёт все числа
    как float, openpyxl — как int, и без этого один и тот же артикул получил
    бы из .xls и .xlsx два разных ключа, а значит два идентификатора РТС и
    дубль позиции на витрине.
    """
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    out = re.sub(r"\s+", " ", str(value)).strip()
    return out or None


def normalize_source(
    cfg: SourceConfig,
    rows: list[RawRow],
    idmap: IdMap,
    stoplist: set[tuple[str, str]],
    units: set[str],
    aliases: dict[str, str],
) -> tuple[list[Item], list[Rejection]]:
    items: list[Item] = []
    rejected: list[Rejection] = []
    seen: set[str] = set()

    for row in rows:
        values = row.values
        article = _text(values.get("article")) or _text(values.get("barcode"))
        if not article:
            rejected.append(Rejection(cfg.code, "", row.row_number, "article",
                                      "нет ни артикула, ни штрих-кода"))
            continue
        if (cfg.code, article) in stoplist:
            rejected.append(Rejection(cfg.code, article, row.row_number, "article",
                                      "в стоп-листе"))
            continue
        if article in seen:
            rejected.append(Rejection(cfg.code, article, row.row_number, "article",
                                      "дубль артикула в прайсе"))
            continue

        group = _text(values.get("group"))
        if cfg.include_groups and (group or "") not in cfg.include_groups:
            rejected.append(Rejection(cfg.code, article, row.row_number, "group",
                                      "группа не входит в include_groups", group or ""))
            continue
        if group and group in cfg.exclude_groups:
            rejected.append(Rejection(cfg.code, article, row.row_number, "group",
                                      "группа в exclude_groups", group))
            continue

        stock = parse_number(values.get("stock"))
        if cfg.min_stock is not None and (stock is None or stock < cfg.min_stock):
            reason = "нет данных об остатке" if stock is None else "остаток ниже минимального"
            rejected.append(Rejection(cfg.code, article, row.row_number, "stock",
                                      reason, _text(values.get("stock")) or ""))
            continue

        name = _text(values.get("name"))
        if not name:
            rejected.append(Rejection(cfg.code, article, row.row_number, "name",
                                      "пустое наименование"))
            continue

        price = parse_number(values.get("price"))
        if price is None or price <= 0:
            rejected.append(Rejection(cfg.code, article, row.row_number, "price",
                                      "цена не число или не положительна",
                                      _text(values.get("price")) or ""))
            continue

        unit = (
            resolve_unit(values.get("unit"), units, aliases)
            or cfg.unit_by_group.get(group or "")
            or cfg.unit_default
        )

        barcode = _text(values.get("barcode"))
        if barcode is not None and not (barcode.isdigit() and len(barcode) == 13):
            barcode = None

        # _text здесь по той же причине, что и для артикула: без него число из .xls
        # попало бы в описание как «15.0», а из .xlsx — как «15».
        attributes = {
            k: _text(v) for k, v in values.items()
            if k not in KNOWN_FIELDS and _text(v)
        }
        template_values: dict[str, object] = dict(attributes)
        template_values.update({
            "name": name, "article": article, "group": group,
            "unit": unit, "barcode": barcode,
        })

        items.append(Item(
            source=cfg.code,
            article=article,
            rts_id=idmap.get_or_create(cfg.code, cfg.prefix, article),
            name=truncate(name, NAME_LIMIT),
            description=build_description(cfg.description_template, template_values, name),
            price_in=price,
            unit=unit,
            okpd2=_text(values.get("okpd2")) or cfg.okpd2_by_group.get(group or ""),
            barcode=barcode,
            country=_text(values.get("country")) or cfg.country,
            region=_text(values.get("region")) or cfg.region,
            group=group,
            stock=stock,
            attributes=attributes,
        ))
        seen.add(article)

    return items, rejected
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_normalize.py -v`
Ожидается: PASS, 14 тестов

- [ ] **Шаг 5: Зафиксировать**

```bash
git add -A
git commit -m "feat: нормализация прайсов в мастер-каталог с фильтрами состава"
```

---

### Задача 7: Цена, НДС и округление

**Файлы:**
- Создать: `src/rtsprice/pricing.py`
- Тест: `tests/test_pricing.py`

**Интерфейсы:**
- Потребляет: `SourceConfig`, `CompanyConfig` (задача 1), `Item` (задача 6)
- Производит: `VAT_RATE`, `to_net(price, includes_vat) -> float`,
  `to_gross(price, includes_vat) -> float`,
  `markup_for(source_cfg, group, company_cfg) -> float`,
  `round_price(value, rule) -> float`, `final_price(item, source_cfg, company_cfg) -> float`,
  `vat_label(company_cfg) -> str`

Наценка ищется от частного к общему: сначала по группе внутри источника, затем по источнику,
затем по компании. Значение `1.0` в конфигурации источника считается «не задано» и передаёт
управление уровню компании — иначе умолчание источника всегда перекрывало бы настройку компании.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_pricing.py
import pytest

from rtsprice.config import CompanyConfig, SourceConfig
from rtsprice.normalize import Item
from rtsprice.pricing import (
    final_price, markup_for, round_price, to_gross, to_net, vat_label,
)


def _src(**over) -> SourceConfig:
    base = dict(code="s", title="S", state="on", prefix=7, companies=("ooo_tlt",),
                file_glob="x", sheets=(), header_rows=(1,), data_starts_at=2,
                row_is_product="price_not_empty", columns={})
    base.update(over)
    return SourceConfig(**base)


def _co(**over) -> CompanyConfig:
    base = dict(code="ooo_tlt", title="ООО", vat_mode="vat22")
    base.update(over)
    return CompanyConfig(**base)


def _item(price: float, group: str | None = None) -> Item:
    return Item(source="s", article="A", rts_id=1, name="N", description="D",
                price_in=price, unit="ШТ", group=group)


def test_to_net_strips_vat_when_included():
    assert to_net(122.0, includes_vat=True) == pytest.approx(100.0)


def test_to_net_keeps_price_when_vat_excluded():
    assert to_net(100.0, includes_vat=False) == 100.0


def test_to_gross_keeps_price_that_already_includes_vat():
    assert to_gross(122.0, includes_vat=True) == 122.0


def test_to_gross_adds_vat_when_supplier_price_is_net():
    assert to_gross(100.0, includes_vat=False) == pytest.approx(122.0)


def test_round_price_rules():
    assert round_price(100.2, "ruble") == 101.0
    assert round_price(100.0, "ruble") == 100.0
    assert round_price(101.0, "ten") == 110.0
    assert round_price(100.234, "none") == pytest.approx(100.24)


def test_markup_prefers_group_then_source_then_company():
    src = _src(markup=1.2, markup_by_group={"Сейфы": 1.5})
    company = _co(markup=1.1)
    assert markup_for(src, "Сейфы", company) == 1.5
    assert markup_for(src, "Прочее", company) == 1.2
    assert markup_for(_src(), "Прочее", company) == 1.1


def test_markup_zero_in_group_is_honoured_not_ignored():
    src = _src(markup=1.2, markup_by_group={"Промо": 0.0})
    assert markup_for(src, "Промо", _co(markup=1.1)) == 0.0


def test_round_price_none_rounds_up_not_to_even():
    assert round_price(100.234, "none") == pytest.approx(100.24)
    assert round_price(100.23, "none") == pytest.approx(100.23)
    assert round_price(0.125, "none") == pytest.approx(0.13)


def test_final_price_for_company_with_vat():
    # закупка 122 с НДС -> 100 без НДС -> наценка 1.2 -> 120 -> плюс НДС 22% -> 146.4 -> 147
    price = final_price(_item(122.0), _src(markup=1.2), _co(rounding="ruble"))
    assert price == 147.0


def test_final_price_for_company_without_vat_uses_gross_base():
    # ИП не возмещает НДС: закупка 122 с НДС -> наценка 1.3 -> 158.6 -> 159
    price = final_price(_item(122.0), _src(markup=1.3), _co(vat_mode="none", rounding="ruble"))
    assert price == 159.0


def test_markup_100_percent_doubles_supplier_price_for_both_companies():
    item = _item(122.0)
    assert final_price(item, _src(), _co(markup=2.0, rounding="none")) == pytest.approx(244.0)
    assert final_price(
        item, _src(), _co(vat_mode="none", markup=2.0, rounding="none")
    ) == pytest.approx(244.0)


def test_final_price_when_supplier_price_excludes_vat():
    src = _src(markup=1.0, price_includes_vat=False)
    assert final_price(_item(100.0), src, _co(rounding="none")) == pytest.approx(122.0)


def test_vat_label():
    assert vat_label(_co()) == "Облагается НДС 22"
    assert vat_label(_co(vat_mode="none")) == "Не облагается НДС"
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_pricing.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.pricing'`

- [ ] **Шаг 3: Реализовать `pricing.py`**

```python
# src/rtsprice/pricing.py
"""Расчёт цены для конкретного юридического лица."""
from __future__ import annotations

import math

from .config import CompanyConfig, SourceConfig
from .normalize import Item

VAT_RATE = 0.22
VAT_LABEL_WITH = "Облагается НДС 22"
VAT_LABEL_WITHOUT = "Не облагается НДС"


def to_net(price: float, includes_vat: bool) -> float:
    """Цена поставщика без НДС. База для компании, которая НДС возмещает."""
    return float(price) / (1.0 + VAT_RATE) if includes_vat else float(price)


def to_gross(price: float, includes_vat: bool) -> float:
    """Цена поставщика с НДС, то есть фактически уплачиваемая сумма.

    База для компании на упрощённой системе: входящий НДС ей не возмещается
    и является частью себестоимости, а не налогом.
    """
    return float(price) if includes_vat else float(price) * (1.0 + VAT_RATE)


def markup_for(source_cfg: SourceConfig, group: str | None, company_cfg: CompanyConfig) -> float:
    """Наценка от частного к общему: группа, затем источник, затем компания.

    Правило по группе выбирается по наличию ключа, а не по истинности значения:
    наценка 0 — конфигурация бессмысленная, но она должна дать цену 0 и громкий
    отказ на слое проверки, а не тихо провалиться на уровень выше.
    """
    key = group or ""
    if key in source_cfg.markup_by_group:
        return float(source_cfg.markup_by_group[key])
    if source_cfg.markup != 1.0:
        return float(source_cfg.markup)
    return float(company_cfg.markup)


def round_price(value: float, rule: str) -> float:
    """Округление всегда вверх, включая режим none.

    Округлённая вниз цена — подарок покупателю на каждой проданной единице.
    Поправка 1e-9 гасит двоичный мусор, чтобы математически точное значение
    не уехало на целый рубль вверх.
    """
    if rule == "ruble":
        return float(math.ceil(value - 1e-9))
    if rule == "ten":
        return float(math.ceil((value - 1e-9) / 10.0) * 10)
    return math.ceil(value * 100 - 1e-9) / 100


def vat_label(company_cfg: CompanyConfig) -> str:
    return VAT_LABEL_WITH if company_cfg.vat_mode == "vat22" else VAT_LABEL_WITHOUT


def final_price(item: Item, source_cfg: SourceConfig, company_cfg: CompanyConfig) -> float:
    """Итоговая цена для колонки F.

    Компания с НДС наценивает сумму без НДС и затем начисляет налог сверху.
    Компания без НДС наценивает фактически уплаченную сумму, поскольку входящий
    НДС для неё невозместим. При одинаковой наценке обе получают одну и ту же
    итоговую цену — различается только её налоговый состав.
    """
    markup = markup_for(source_cfg, item.group, company_cfg)
    if company_cfg.vat_mode == "vat22":
        base = to_net(item.price_in, source_cfg.price_includes_vat)
        value = base * markup * (1.0 + VAT_RATE)
    else:
        base = to_gross(item.price_in, source_cfg.price_includes_vat)
        value = base * markup
    return round_price(value, company_cfg.rounding)
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_pricing.py -v`
Ожидается: PASS, 13 тестов

- [ ] **Шаг 5: Зафиксировать**

```bash
git add -A
git commit -m "feat: расчёт цены, НДС и округления по юридическому лицу"
```

---

### Задача 8: Рендер строки в формат РТС

**Файлы:**
- Создать: `src/rtsprice/render.py`
- Тест: `tests/test_render.py`

**Интерфейсы:**
- Потребляет: `Item` (задача 6), `final_price`, `vat_label` (задача 7)
- Производит: `HEADER_ROW_1`, `HEADER_ROW_2` (кортежи по 27 значений), `COLUMN_COUNT = 27`,
  индексы `C_ID`, `C_ARTICLE`, `C_NAME`, `C_DESCRIPTION`, `C_PRICE`, `C_UNIT`, `C_OKPD2`, `C_VAT`,
  `C_IMAGE_FIRST`, `C_FILE_FIRST`, `C_BARCODE`, `C_COUNTRY`, `C_REGION`, `C_VALIDITY`, `C_DELETE`;
  функцию `render_row(item, source_cfg, company_cfg, photo_urls) -> list[object]`

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_render.py
from rtsprice.config import CompanyConfig, SourceConfig
from rtsprice.normalize import Item
from rtsprice.render import (
    C_BARCODE, C_DELETE, C_DESCRIPTION, C_FILE_FIRST, C_ID, C_IMAGE_FIRST, C_NAME,
    C_PRICE, C_REGION, C_UNIT, C_VALIDITY, C_VAT, COLUMN_COUNT, HEADER_ROW_1,
    HEADER_ROW_2, render_row,
)


def _src(**over) -> SourceConfig:
    base = dict(code="s", title="S", state="on", prefix=7, companies=("ooo_tlt",),
                file_glob="x", sheets=(), header_rows=(1,), data_starts_at=2,
                row_is_product="price_not_empty", columns={}, markup=1.0)
    base.update(over)
    return SourceConfig(**base)


def _co(**over) -> CompanyConfig:
    base = dict(code="ooo_tlt", title="ООО", vat_mode="vat22", rounding="ruble",
                validity_days=21, warehouse_address="г Москва")
    base.update(over)
    return CompanyConfig(**base)


def _item(**over) -> Item:
    base = dict(source="s", article="A-1", rts_id=70_000_001, name="Товар",
                description="Описание", price_in=122.0, unit="ШТ")
    base.update(over)
    return Item(**base)


def test_headers_have_expected_width_and_anchors():
    assert len(HEADER_ROW_1) == COLUMN_COUNT == 27
    assert len(HEADER_ROW_2) == COLUMN_COUNT
    assert HEADER_ROW_1[C_NAME].startswith("Наименование позиции")
    assert HEADER_ROW_1[C_PRICE].startswith("Цена за единицу (с НДС)")
    assert HEADER_ROW_1[C_DELETE] == "Удалить"
    assert HEADER_ROW_2[9] == "Доставка транспортной компанией"


def test_render_row_places_core_fields():
    row = render_row(_item(), _src(), _co(), [])
    assert len(row) == COLUMN_COUNT
    assert row[C_ID] == 70_000_001
    assert row[C_NAME] == "Товар"
    assert row[C_DESCRIPTION] == "Описание"
    assert row[C_UNIT] == "ШТ"
    assert row[C_PRICE] == 122.0
    assert row[C_VAT] == "Облагается НДС 22"
    assert row[C_VALIDITY] == 21
    assert row[C_DELETE] is None


def test_render_row_for_company_without_vat():
    row = render_row(_item(), _src(markup=1.0), _co(code="ip", vat_mode="none"))
    assert row[C_VAT] == "Не облагается НДС"
    assert row[C_PRICE] == 122.0


def test_render_row_places_photos_in_order():
    row = render_row(_item(), _src(), _co(), ["u1", "u2"])
    assert row[C_IMAGE_FIRST] == "u1"
    assert row[C_IMAGE_FIRST + 1] == "u2"
    assert row[C_IMAGE_FIRST + 2] is None


def test_render_row_limits_photos_to_five():
    row = render_row(_item(), _src(), _co(), [f"u{i}" for i in range(9)])
    assert row[C_IMAGE_FIRST + 4] == "u4"
    assert row[C_BARCODE] is None


def test_render_row_treats_zero_validity_as_no_expiry():
    row = render_row(_item(), _src(), _co(validity_days=0), [])
    assert row[C_VALIDITY] is None


def test_render_row_places_files_in_order_and_caps_at_three():
    row = render_row(_item(), _src(), _co(), [], ["f1", "f2", "f3", "f4"])
    assert row[C_FILE_FIRST] == "f1"
    assert row[C_FILE_FIRST + 1] == "f2"
    assert row[C_FILE_FIRST + 2] == "f3"
    assert row[C_BARCODE] is None


def test_render_row_drops_region_for_foreign_country():
    row = render_row(_item(country="Китай", region="Московская область"), _src(), _co(), [])
    assert row[C_REGION] is None


def test_render_row_keeps_region_for_russia():
    row = render_row(_item(country="Россия", region="Московская область"), _src(), _co(), [])
    assert row[C_REGION] == "Московская область"
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_render.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.render'`

- [ ] **Шаг 3: Реализовать `render.py`**

```python
# src/rtsprice/render.py
"""Преобразование позиции мастер-каталога в строку формата РТС."""
from __future__ import annotations

from .config import CompanyConfig, SourceConfig
from .normalize import Item
from .pricing import final_price, vat_label

COLUMN_COUNT = 27

C_ORDER = 0
C_ID = 1
C_ARTICLE = 2
C_NAME = 3
C_DESCRIPTION = 4
C_PRICE = 5
C_UNIT = 6
C_OKPD2 = 7
C_VAT = 8
C_DELIVERY_CARRIER = 9
C_DELIVERY_SELLER = 10
C_PICKUP = 11
C_WAREHOUSE = 12
C_TERMS = 13
C_IMAGE_FIRST = 14
C_FILE_FIRST = 19
C_BARCODE = 22
C_COUNTRY = 23
C_REGION = 24
C_VALIDITY = 25
C_DELETE = 26

MAX_IMAGES = 5
MAX_FILES = 3
RUSSIA = "россия"

HEADER_ROW_1: tuple[str | None, ...] = (
    "Порядковый номер позиции",
    "Внутренний идентификатор позиции из товароучетной системы(при наличии)",
    "Артикул товара или внутренний регистрационный номер товара (присваивается продавцом)",
    "Наименование позиции* (max-200 символов)",
    "Описание позиции* (max-2000 символов)",
    "Цена за единицу (с НДС), руб.*",
    "Единица измерения*",
    "Код классификатора ОКПД2",
    "НДС",
    "ДЕТАЛИ ДОСТАВКИ (Выберите опции доставки, доступные для этой позиции)",
    None,
    None,
    "Укажите адрес склада для самовывоза",
    "Дополнительная информация по поставке товара (выполнения работ, оказания услуг)",
    "Ссылка на картинку 1",
    "Ссылка на картинку 2",
    "Ссылка на картинку 3",
    "Ссылка на картинку 4",
    "Ссылка на картинку 5",
    "Ссылка на файл 1",
    "Ссылка на файл 2",
    "Ссылка на файл 3",
    "Штрих-код (13 цифр)",
    "Страна происхождения (заполняется для товара)",
    "Регион производства (Указывается, если Страна происхождения Россия)",
    "Актуальность (дней)",
    "Удалить",
)

HEADER_ROW_2: tuple[str | None, ...] = tuple(
    "Доставка транспортной компанией" if i == C_DELIVERY_CARRIER
    else "Доставка продавцом" if i == C_DELIVERY_SELLER
    else "Самовывоз со склада продавца" if i == C_PICKUP
    else None
    for i in range(COLUMN_COUNT)
)


def render_row(
    item: Item,
    source_cfg: SourceConfig,
    company_cfg: CompanyConfig,
    photo_urls: list[str] | None = None,
    file_urls: list[str] | None = None,
) -> list[object]:
    row: list[object] = [None] * COLUMN_COUNT
    row[C_ID] = item.rts_id
    row[C_ARTICLE] = item.article
    row[C_NAME] = item.name
    row[C_DESCRIPTION] = item.description
    row[C_PRICE] = final_price(item, source_cfg, company_cfg)
    row[C_UNIT] = item.unit
    row[C_OKPD2] = item.okpd2
    row[C_VAT] = vat_label(company_cfg)
    row[C_DELIVERY_CARRIER] = company_cfg.delivery_by_carrier
    row[C_DELIVERY_SELLER] = company_cfg.delivery_by_seller
    row[C_PICKUP] = company_cfg.delivery_pickup
    row[C_WAREHOUSE] = company_cfg.warehouse_address or None
    row[C_TERMS] = company_cfg.delivery_terms or None
    row[C_BARCODE] = item.barcode
    row[C_COUNTRY] = item.country
    if item.country and item.country.strip().casefold() == RUSSIA:
        row[C_REGION] = item.region
    # Ноль трактуется как «без срока»: пустая ячейка на площадке означает
    # именно это, а срок в ноль дней снял бы позицию с витрины в день загрузки.
    row[C_VALIDITY] = company_cfg.validity_days or None

    for offset, url in enumerate((photo_urls or [])[:MAX_IMAGES]):
        row[C_IMAGE_FIRST + offset] = url
    for offset, url in enumerate((file_urls or [])[:MAX_FILES]):
        row[C_FILE_FIRST + offset] = url
    return row
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_render.py -v`
Ожидается: PASS, 9 тестов

- [ ] **Шаг 5: Зафиксировать**

```bash
git add -A
git commit -m "feat: рендер позиции в 27 колонок формата РТС"
```

---

### Задача 9: Проверка и автоисправление строк

**Файлы:**
- Создать: `src/rtsprice/validate.py`
- Тест: `tests/test_validate.py`

**Интерфейсы:**
- Потребляет: индексы колонок из `render` (задача 8)
- Производит: `Issue(level, field, reason)`,
  `validate_and_fix(row, units, okpd2_codes, seen_ids) -> tuple[list | None, list[Issue]]`

Уровень `reject` отбрасывает строку целиком, уровень `fix` чинит значение и записывает
предупреждение. Проверка выполняется над уже отрендеренной строкой, поэтому ловит и ошибки
конфигурации компании, а не только данные поставщика.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_validate.py
from rtsprice.render import (
    C_BARCODE, C_COUNTRY, C_DESCRIPTION, C_ID, C_NAME, C_OKPD2, C_PRICE,
    C_REGION, C_UNIT, COLUMN_COUNT,
)
from rtsprice.validate import validate_and_fix

UNITS = {"ШТ", "КГ"}
OKPD2 = {"26.40.20.122"}


def _row(**over) -> list[object]:
    row: list[object] = [None] * COLUMN_COUNT
    row[C_ID] = 70_000_001
    row[C_NAME] = "Товар"
    row[C_DESCRIPTION] = "Описание"
    row[C_PRICE] = 100.0
    row[C_UNIT] = "ШТ"
    for index, value in over.items():
        row[int(index)] = value
    return row


def test_valid_row_passes_without_issues():
    fixed, issues = validate_and_fix(_row(), UNITS, OKPD2, set())
    assert fixed is not None
    assert issues == []


def test_rejects_empty_name():
    fixed, issues = validate_and_fix(_row(**{str(C_NAME): None}), UNITS, OKPD2, set())
    assert fixed is None
    assert issues[0].level == "reject"
    assert issues[0].field == "Наименование"


def test_rejects_zero_price():
    fixed, _ = validate_and_fix(_row(**{str(C_PRICE): 0}), UNITS, OKPD2, set())
    assert fixed is None


def test_rejects_unknown_unit():
    fixed, issues = validate_and_fix(_row(**{str(C_UNIT): "бушель"}), UNITS, OKPD2, set())
    assert fixed is None
    assert "справочник" in issues[0].reason


def test_rejects_duplicate_id():
    fixed, issues = validate_and_fix(_row(), UNITS, OKPD2, {70_000_001})
    assert fixed is None
    assert issues[0].field == "Внутренний идентификатор"


def test_rejects_non_numeric_id():
    fixed, _ = validate_and_fix(_row(**{str(C_ID): "A-1"}), UNITS, OKPD2, set())
    assert fixed is None


def test_fixes_long_name_and_description():
    row = _row(**{str(C_NAME): "слово " * 60, str(C_DESCRIPTION): "х" * 2500})
    fixed, issues = validate_and_fix(row, UNITS, OKPD2, set())
    assert fixed is not None
    assert len(fixed[C_NAME]) <= 200
    assert len(fixed[C_DESCRIPTION]) <= 2000
    assert {i.level for i in issues} == {"fix"}


def test_rejects_name_that_truncation_emptied():
    fixed, issues = validate_and_fix(_row(**{str(C_NAME): "," * 250}), UNITS, OKPD2, set())
    assert fixed is None
    assert issues[0].level == "reject"
    assert "опустело" in issues[0].reason


def test_clears_bad_barcode():
    fixed, issues = validate_and_fix(_row(**{str(C_BARCODE): "12345"}), UNITS, OKPD2, set())
    assert fixed[C_BARCODE] is None
    assert issues[0].field == "Штрих-код"


def test_clears_region_for_foreign_country():
    row = _row(**{str(C_COUNTRY): "Китай", str(C_REGION): "Московская область"})
    fixed, issues = validate_and_fix(row, UNITS, OKPD2, set())
    assert fixed[C_REGION] is None
    assert issues[0].level == "fix"


def test_clears_unknown_okpd2():
    fixed, issues = validate_and_fix(_row(**{str(C_OKPD2): "99.99"}), UNITS, OKPD2, set())
    assert fixed[C_OKPD2] is None
    assert issues[0].field == "ОКПД2"


def test_keeps_known_okpd2():
    fixed, issues = validate_and_fix(_row(**{str(C_OKPD2): "26.40.20.122"}), UNITS, OKPD2, set())
    assert fixed[C_OKPD2] == "26.40.20.122"
    assert issues == []
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_validate.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.validate'`

- [ ] **Шаг 3: Реализовать `validate.py`**

```python
# src/rtsprice/validate.py
"""Проверка отрендеренной строки: отклонения и автоисправления."""
from __future__ import annotations

from dataclasses import dataclass

from .describe import DESCRIPTION_LIMIT, NAME_LIMIT, truncate
from .render import (
    C_BARCODE, C_COUNTRY, C_DESCRIPTION, C_ID, C_NAME, C_OKPD2, C_PRICE,
    C_REGION, C_UNIT, RUSSIA,
)


@dataclass(frozen=True)
class Issue:
    level: str
    field: str
    reason: str
    value: str = ""


def validate_and_fix(
    row: list[object],
    units: set[str],
    okpd2_codes: set[str],
    seen_ids: set[int],
) -> tuple[list[object] | None, list[Issue]]:
    issues: list[Issue] = []
    out = list(row)

    try:
        rts_id = int(out[C_ID])
    except (TypeError, ValueError):
        return None, [Issue("reject", "Внутренний идентификатор",
                            "не является целым числом", str(out[C_ID]))]
    if rts_id in seen_ids:
        return None, [Issue("reject", "Внутренний идентификатор",
                            "повторяется в выгрузке", str(rts_id))]

    if not str(out[C_NAME] or "").strip():
        return None, [Issue("reject", "Наименование", "пустое обязательное поле")]
    if not str(out[C_DESCRIPTION] or "").strip():
        return None, [Issue("reject", "Описание", "пустое обязательное поле")]

    try:
        price = float(out[C_PRICE])
    except (TypeError, ValueError):
        return None, [Issue("reject", "Цена", "не является числом", str(out[C_PRICE]))]
    if price <= 0:
        return None, [Issue("reject", "Цена", "не положительна", str(price))]
    out[C_PRICE] = price

    unit = str(out[C_UNIT] or "").strip()
    if unit not in units:
        return None, [Issue("reject", "Единица измерения",
                            "значение отсутствует в справочнике ОКЕИ", unit)]

    # Обрезка отрезает хвост после последнего пробела и снимает знаки препинания,
    # поэтому строка из одних запятых схлопывается в пустую. Поле обязательное,
    # так что после обрезки его проверяют повторно.
    if len(str(out[C_NAME])) > NAME_LIMIT:
        out[C_NAME] = truncate(out[C_NAME], NAME_LIMIT)
        issues.append(Issue("fix", "Наименование", "обрезано до 200 символов"))
    if not str(out[C_NAME]).strip():
        return None, [Issue("reject", "Наименование", "после обрезки поле опустело")]
    if len(str(out[C_DESCRIPTION])) > DESCRIPTION_LIMIT:
        out[C_DESCRIPTION] = truncate(out[C_DESCRIPTION], DESCRIPTION_LIMIT)
        issues.append(Issue("fix", "Описание", "обрезано до 2000 символов"))
    if not str(out[C_DESCRIPTION]).strip():
        return None, [Issue("reject", "Описание", "после обрезки поле опустело")]

    barcode = str(out[C_BARCODE] or "").strip()
    if barcode and not (barcode.isdigit() and len(barcode) == 13):
        out[C_BARCODE] = None
        issues.append(Issue("fix", "Штрих-код", "не 13 цифр, поле очищено", barcode))

    country = str(out[C_COUNTRY] or "").strip()
    if out[C_REGION] and country.casefold() != RUSSIA:
        issues.append(Issue("fix", "Регион производства",
                            "страна не Россия, поле очищено", str(out[C_REGION])))
        out[C_REGION] = None

    okpd2 = str(out[C_OKPD2] or "").strip()
    if okpd2 and okpd2 not in okpd2_codes:
        out[C_OKPD2] = None
        issues.append(Issue("fix", "ОКПД2", "код отсутствует в справочнике", okpd2))

    seen_ids.add(rts_id)
    return out, issues
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_validate.py -v`
Ожидается: PASS, 12 тестов

- [ ] **Шаг 5: Зафиксировать**

```bash
git add -A
git commit -m "feat: проверка строк с отклонениями и автоисправлениями"
```

---

### Задача 10: Снимки выгрузок и строки удаления

**Файлы:**
- Создать: `src/rtsprice/state.py`
- Тест: `tests/test_state.py`

**Интерфейсы:**
- Потребляет: `C_ID`, `C_DELETE`, `COLUMN_COUNT` (задача 8), `prefix_of` (задача 4)
- Производит: `load_snapshot(path) -> dict[int, list]`, `save_snapshot(path, rows)`,
  `deletion_rows(previous, current_ids, keep_prefixes) -> list[list]`

Строка удаления копируется из снимка целиком, а не собирается заново: требования площадки к
составу полей в такой строке не документированы, и полная строка заведомо безопасна. Источники
в состоянии `frozen` защищены через `keep_prefixes` — их позиции отсутствуют в новой выгрузке,
но удалять их нельзя.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_state.py
from pathlib import Path

from rtsprice.render import (
    COLUMN_COUNT, C_ARTICLE, C_BARCODE, C_DELETE, C_ID, C_NAME, C_OKPD2, C_PRICE,
)
from rtsprice.state import deletion_rows, load_snapshot, save_snapshot


def _row(rts_id: int, name: str = "Товар") -> list[object]:
    row: list[object] = [None] * COLUMN_COUNT
    row[C_ID] = rts_id
    row[C_NAME] = name
    row[C_PRICE] = 100.0
    return row


def test_snapshot_round_trip(tmp_path: Path):
    p = tmp_path / "last.csv"
    save_snapshot(p, [_row(100_000_001), _row(100_000_002, "Второй")])
    loaded = load_snapshot(p)
    assert set(loaded) == {100_000_001, 100_000_002}
    assert loaded[100_000_002][C_NAME] == "Второй"
    assert loaded[100_000_001][C_PRICE] == 100.0


def test_snapshot_preserves_leading_zeros_in_text_columns(tmp_path: Path):
    p = tmp_path / "last.csv"
    row = _row(100_000_001)
    row[C_ARTICLE] = "00000001688"
    row[C_BARCODE] = "0004670003040"
    row[C_OKPD2] = "26.40"
    save_snapshot(p, [row])
    back = load_snapshot(p)[100_000_001]
    assert back[C_ARTICLE] == "00000001688"
    assert back[C_BARCODE] == "0004670003040"
    assert back[C_OKPD2] == "26.40"


def test_load_snapshot_skips_row_with_unparsable_id(tmp_path: Path):
    p = tmp_path / "last.csv"
    save_snapshot(p, [_row(100_000_001), _row(100_000_002)])
    lines = p.read_text(encoding="utf-8-sig").splitlines()
    lines[1] = lines[1].replace("100000002", "мусор", 1)
    p.write_text("
".join(lines) + "
", encoding="utf-8-sig")
    assert set(load_snapshot(p)) == {100_000_001}


def test_load_snapshot_missing_file_is_empty(tmp_path: Path):
    assert load_snapshot(tmp_path / "нет.csv") == {}


def test_deletion_rows_marks_disappeared_positions():
    previous = {100_000_001: _row(100_000_001), 100_000_002: _row(100_000_002)}
    rows = deletion_rows(previous, current_ids={100_000_001}, keep_prefixes=set())
    assert len(rows) == 1
    assert rows[0][C_ID] == 100_000_002
    assert rows[0][C_DELETE] == 1


def test_deletion_rows_skips_frozen_prefixes():
    previous = {100_000_001: _row(100_000_001), 110_000_001: _row(110_000_001)}
    rows = deletion_rows(previous, current_ids=set(), keep_prefixes={11})
    assert [r[C_ID] for r in rows] == [100_000_001]


def test_deletion_rows_empty_when_nothing_disappeared():
    previous = {100_000_001: _row(100_000_001)}
    assert deletion_rows(previous, {100_000_001}, set()) == []
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_state.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.state'`

- [ ] **Шаг 3: Реализовать `state.py`**

```python
# src/rtsprice/state.py
"""Снимки прошлых выгрузок и расчёт строк на удаление."""
from __future__ import annotations

import csv
from pathlib import Path

from .identity import prefix_of
from .render import C_DELETE, C_ID, C_PRICE, C_VALIDITY, COLUMN_COUNT

# Числами становятся только те колонки, которые ими и являются.
NUMERIC_COLUMNS = (C_ID, C_PRICE, C_VALIDITY, C_DELETE)


def _decode(value: str, column: int) -> object:
    """Восстановить значение ячейки снимка.

    Разбор по колонке, а не по виду значения: артикул «00000001688» и
    штрих-код с ведущим нулём при числовом разборе потеряли бы нули, и
    строка снятия с продажи ушла бы на площадку с испорченными полями.
    """
    if value == "":
        return None
    if column not in NUMERIC_COLUMNS:
        return value
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def load_snapshot(path: Path) -> dict[int, list[object]]:
    p = Path(path)
    if not p.exists():
        return {}
    result: dict[int, list[object]] = {}
    with p.open(encoding="utf-8-sig", newline="") as fh:
        for raw in csv.reader(fh):
            if len(raw) < COLUMN_COUNT:
                raw = raw + [""] * (COLUMN_COUNT - len(raw))
            row = [_decode(v, i) for i, v in enumerate(raw[:COLUMN_COUNT])]
            if row[C_ID] is None:
                continue
            try:
                rts_id = int(row[C_ID])
            except (TypeError, ValueError):
                # Испорченный идентификатор пропускаем, а не роняем сборку:
                # снимок — служебный файл, и одна битая строка не повод
                # оставить весь каталог без обновления.
                continue
            row[C_ID] = rts_id
            result[rts_id] = row
    return result


def save_snapshot(path: Path, rows: list[list[object]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        for row in rows:
            writer.writerow(["" if v is None else v for v in row])
    tmp.replace(p)


def deletion_rows(
    previous: dict[int, list[object]],
    current_ids: set[int],
    keep_prefixes: set[int],
) -> list[list[object]]:
    """Строки для позиций, исчезнувших из выгрузки, кроме замороженных источников."""
    result: list[list[object]] = []
    for rts_id, row in sorted(previous.items()):
        if rts_id in current_ids or prefix_of(rts_id) in keep_prefixes:
            continue
        copy = list(row)
        copy[C_DELETE] = 1
        result.append(copy)
    return result
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_state.py -v`
Ожидается: PASS, 7 тестов

- [ ] **Шаг 5: Зафиксировать**

```bash
git add -A
git commit -m "feat: снимки выгрузок и строки снятия с продажи"
```

---

### Задача 11: Запись выходного файла

**Файлы:**
- Создать: `src/rtsprice/writer.py`
- Тест: `tests/test_writer.py`

**Интерфейсы:**
- Потребляет: `HEADER_ROW_1`, `HEADER_ROW_2`, `COLUMN_COUNT`, `C_ORDER` (задача 8)
- Производит: `MAX_ROWS_PER_FILE = 50_000`, `SHEET_NAME = "Данные для импорта"`,
  `write_price_file(rows, out_path) -> list[Path]`

Файл собирается с нуля, а не копированием шаблона: шаблон весит 2,7 МБ за счёт справочника ОКПД2
на 19 802 строки, который в выгрузке не нужен и только замедляет работу. Структура шапки
воспроизводится точно, включая объединение J1:L1 и вертикальные объединения остальных колонок.
Приемлемость такого файла подтверждается проверочным импортом в задаче 16.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_writer.py
from pathlib import Path

import openpyxl

from rtsprice.render import COLUMN_COUNT, C_ID, C_NAME
from rtsprice.writer import MAX_ROWS_PER_FILE, SHEET_NAME, write_price_file


def _row(rts_id: int) -> list[object]:
    row: list[object] = [None] * COLUMN_COUNT
    row[C_ID] = rts_id
    row[C_NAME] = f"Товар {rts_id}"
    return row


def test_writes_header_and_data(tmp_path: Path):
    out = tmp_path / "ooo.xlsx"
    written = write_price_file([_row(1), _row(2)], out)
    assert written == [out]

    ws = openpyxl.load_workbook(out).active
    assert ws.title == SHEET_NAME
    assert ws.cell(1, 4).value.startswith("Наименование позиции")
    assert ws.cell(2, 10).value == "Доставка транспортной компанией"
    assert ws.cell(3, 2).value == 1
    assert ws.cell(4, 2).value == 2
    assert ws.max_row == 4


def test_fills_sequential_order_numbers(tmp_path: Path):
    out = tmp_path / "ooo.xlsx"
    write_price_file([_row(10), _row(20)], out)
    ws = openpyxl.load_workbook(out).active
    assert ws.cell(3, 1).value == 1
    assert ws.cell(4, 1).value == 2


def test_merges_delivery_header(tmp_path: Path):
    out = tmp_path / "ooo.xlsx"
    write_price_file([_row(1)], out)
    ranges = {str(r) for r in openpyxl.load_workbook(out).active.merged_cells.ranges}
    assert "J1:L1" in ranges
    assert "A1:A2" in ranges


def test_splits_into_parts_over_limit(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rtsprice.writer.MAX_ROWS_PER_FILE", 2)
    out = tmp_path / "ooo.xlsx"
    written = write_price_file([_row(i) for i in range(5)], out)
    assert [p.name for p in written] == ["ooo_part1.xlsx", "ooo_part2.xlsx", "ooo_part3.xlsx"]
    assert openpyxl.load_workbook(written[0]).active.max_row == 4
    assert openpyxl.load_workbook(written[2]).active.max_row == 3


def test_order_numbers_run_continuously_across_parts(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rtsprice.writer.MAX_ROWS_PER_FILE", 2)
    written = write_price_file([_row(i) for i in range(5)], tmp_path / "ooo.xlsx")
    numbers: list[object] = []
    for path in written:
        ws = openpyxl.load_workbook(path).active
        numbers += [ws.cell(r, 1).value for r in range(3, ws.max_row + 1)]
    assert numbers == [1, 2, 3, 4, 5]


def test_exact_limit_stays_one_unsplit_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rtsprice.writer.MAX_ROWS_PER_FILE", 3)
    out = tmp_path / "ooo.xlsx"
    written = write_price_file([_row(i) for i in range(3)], out)
    assert written == [out]
    assert openpyxl.load_workbook(out).active.max_row == 5


def test_empty_rows_still_writes_header(tmp_path: Path):
    out = tmp_path / "ooo.xlsx"
    written = write_price_file([], out)
    assert openpyxl.load_workbook(written[0]).active.max_row == 2
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_writer.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.writer'`

- [ ] **Шаг 3: Реализовать `writer.py`**

```python
# src/rtsprice/writer.py
"""Запись выходного файла в формате шаблона РТС."""
from __future__ import annotations

from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

from .render import (
    COLUMN_COUNT, C_DELIVERY_CARRIER, C_ORDER, C_PICKUP, HEADER_ROW_1, HEADER_ROW_2,
)

MAX_ROWS_PER_FILE = 50_000
SHEET_NAME = "Данные для импорта"


def _new_book() -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    for column, value in enumerate(HEADER_ROW_1, start=1):
        ws.cell(1, column, value)
    for column, value in enumerate(HEADER_ROW_2, start=1):
        ws.cell(2, column, value)

    first = get_column_letter(C_DELIVERY_CARRIER + 1)
    last = get_column_letter(C_PICKUP + 1)
    ws.merge_cells(f"{first}1:{last}1")
    for index in range(COLUMN_COUNT):
        if C_DELIVERY_CARRIER <= index <= C_PICKUP:
            continue
        letter = get_column_letter(index + 1)
        ws.merge_cells(f"{letter}1:{letter}2")
    return wb


def _write_chunk(rows: list[list[object]], path: Path, start_order: int) -> Path:
    wb = _new_book()
    ws = wb.active
    for offset, row in enumerate(rows):
        values = list(row)
        values[C_ORDER] = start_order + offset
        ws.append(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def write_price_file(rows: list[list[object]], out_path: Path) -> list[Path]:
    """Записать строки, разбив на части, если их больше допустимого предела."""
    out_path = Path(out_path)
    if len(rows) <= MAX_ROWS_PER_FILE:
        return [_write_chunk(rows, out_path, 1)]

    written: list[Path] = []
    for part, start in enumerate(range(0, len(rows), MAX_ROWS_PER_FILE), start=1):
        chunk = rows[start:start + MAX_ROWS_PER_FILE]
        path = out_path.with_name(f"{out_path.stem}_part{part}{out_path.suffix}")
        written.append(_write_chunk(chunk, path, start + 1))
    return written
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_writer.py -v`
Ожидается: PASS, 7 тестов

- [ ] **Шаг 5: Зафиксировать**

```bash
git add -A
git commit -m "feat: запись выходного файла с нарезкой на части"
```

---

### Задача 12: Фотографии

**Файлы:**
- Создать: `src/rtsprice/photos.py`
- Тест: `tests/test_photos.py`

**Интерфейсы:**
- Потребляет: `Item` (задача 6)
- Производит: `find_photos(root, source, article, limit) -> list[Path]`,
  `normalize_image(data, max_side, max_bytes) -> bytes`, протокол `Publisher` с методом
  `publish(remote_name, data) -> str`, классы `LocalPublisher`, `SftpPublisher`,
  `PhotoResolver(root, publisher, manifest_path)` с методом `urls_for(item) -> list[str]`

Файлы именуются артикулом с суффиксом: `photos/promet/S10399630407_1.jpg`. Артикул виден прямо
в прайсе поставщика, поэтому раскладывать снимки можно без обращения к служебным таблицам.
Небезопасные для имени файла символы артикула заменяются подчёркиванием. Манифест хранит
соответствие хэша содержимого и опубликованной ссылки, поэтому повторная сборка не перезаливает
неизменившиеся снимки.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_photos.py
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
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_photos.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.photos'`

- [ ] **Шаг 3: Реализовать `photos.py`**

```python
# src/rtsprice/photos.py
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
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_photos.py -v`
Ожидается: PASS, 11 тестов

- [ ] **Шаг 5: Зафиксировать**

```bash
git add -A
git commit -m "feat: публикация фотографий с кэшем по хэшу содержимого"
```

---

### Задача 13: Отчёты

**Файлы:**
- Создать: `src/rtsprice/report.py`
- Тест: `tests/test_report.py`

**Интерфейсы:**
- Потребляет: `Rejection` (задача 6), `Issue` (задача 9)
- Производит: `SourceStats`, `DiffStats`, `write_errors_xlsx(path, rejections, issues)`,
  `write_report_md(path, stats, diffs) -> str`

Отчёт читается перед загрузкой и служит последней возможностью заметить, что поставщик прислал
испорченный прайс. Поэтому он показывает не только итоги, но и причины отсева.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_report.py
from pathlib import Path

import openpyxl

from rtsprice.normalize import Rejection
from rtsprice.report import DiffStats, SourceStats, write_errors_xlsx, write_report_md
from rtsprice.validate import Issue


def test_errors_file_lists_rejections_and_fixes(tmp_path: Path):
    out = tmp_path / "errors.xlsx"
    write_errors_xlsx(
        out,
        [Rejection("promet", "A-1", 5, "price", "цена не число", "по запросу")],
        [("promet", "A-2", Issue("fix", "Штрих-код", "не 13 цифр, поле очищено", "123"))],
    )
    ws = openpyxl.load_workbook(out).active
    assert [c.value for c in ws[1]] == [
        "Источник", "Артикул", "Строка", "Уровень", "Поле", "Причина", "Значение",
    ]
    assert ws.cell(2, 4).value == "отклонено"
    assert ws.cell(2, 6).value == "цена не число"
    assert ws.cell(3, 4).value == "исправлено"


def test_report_contains_per_source_numbers(tmp_path: Path):
    out = tmp_path / "report.md"
    text = write_report_md(
        out,
        [SourceStats("promet", "Промет", "on", "прайс.xlsx", 13756, 500, 13256,
                     {"цена не число или не положительна": 500})],
        {"ooo_tlt": DiffStats(new=10, changed_price=25, unchanged=13221, deleted=3)},
    )
    assert "Промет" in text
    assert "13 256" in text or "13256" in text
    assert "цена не число" in text
    assert "новых: 10" in text
    assert "снимается: 3" in text
    assert out.read_text(encoding="utf-8") == text


def test_report_flags_source_that_read_nothing(tmp_path: Path):
    text = write_report_md(
        tmp_path / "report.md",
        [SourceStats("opt", "ОПТ", "on", "", 0, 0, 0, {"не найден файл": 1})],
        {},
    )
    assert "ВНИМАНИЕ" in text
    assert "ни одной строки" in text


def test_report_stays_quiet_about_frozen_source(tmp_path: Path):
    text = write_report_md(
        tmp_path / "report.md",
        [SourceStats("brinex", "Бринэкс", "frozen", "", 0, 0, 0)],
        {},
    )
    assert "ВНИМАНИЕ" not in text


def test_report_flags_source_that_lost_most_positions(tmp_path: Path):
    text = write_report_md(
        tmp_path / "report.md",
        [SourceStats("gur", "Гуриненко", "on", "p.xlsx", 100, 95, 5, {"нет цены": 95})],
        {},
    )
    assert "ВНИМАНИЕ" in text
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_report.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.report'`

- [ ] **Шаг 3: Реализовать `report.py`**

```python
# src/rtsprice/report.py
"""Отчёт о сборке и файл с ошибками."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import openpyxl

from .normalize import Rejection
from .validate import Issue

SUSPICIOUS_LOSS_RATIO = 0.5


@dataclass
class SourceStats:
    code: str
    title: str
    state: str
    file_name: str
    read: int
    rejected: int
    accepted: int
    reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class DiffStats:
    new: int = 0
    changed_price: int = 0
    unchanged: int = 0
    deleted: int = 0


def write_errors_xlsx(
    path: Path,
    rejections: list[Rejection],
    issues: list[tuple[str, str, Issue]],
) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ошибки"
    ws.append(["Источник", "Артикул", "Строка", "Уровень", "Поле", "Причина", "Значение"])
    for r in rejections:
        ws.append([r.source, r.article, r.row_number, "отклонено", r.field, r.reason, r.value])
    for source, article, issue in issues:
        ws.append([source, article, "", "исправлено", issue.field, issue.reason, issue.value])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def write_report_md(
    path: Path,
    stats: list[SourceStats],
    diffs: dict[str, DiffStats],
) -> str:
    lines = [f"# Отчёт о сборке прайс-листов, {date.today():%d.%m.%Y}", "", "## Источники", ""]
    lines.append("| Источник | Состояние | Файл | Прочитано | Отсеяно | Принято |")
    lines.append("|---|---|---|---|---|---|")
    for s in stats:
        name = s.file_name or "—"
        lines.append(
            f"| {s.title} | {s.state} | {name} | {s.read} | {s.rejected} | {s.accepted} |"
        )

    # Источник, из которого не прочитано ни строки, — самый опасный случай:
    # поставщик переименовал колонку или файл не положили, и в выгрузку не
    # попадёт ничего. Без отдельной проверки он выглядел бы как замороженный.
    # Замороженные и выключенные источники молчат намеренно.
    warnings = [
        s for s in stats
        if s.state == "on"
        and (s.read == 0 or s.rejected / s.read > SUSPICIOUS_LOSS_RATIO)
    ]
    if warnings:
        lines += ["", "## ВНИМАНИЕ", ""]
        for s in warnings:
            if s.read == 0:
                lines.append(
                    f"- {s.title}: не прочитано ни одной строки. "
                    f"Файл не найден или структура прайса изменилась."
                )
                continue
            share = round(100 * s.rejected / s.read)
            lines.append(f"- {s.title}: отсеяно {share}% строк. Проверьте прайс и конфигурацию.")

    detailed = [s for s in stats if s.reasons]
    if detailed:
        lines += ["", "## Причины отсева", ""]
        for s in detailed:
            lines.append(f"### {s.title}")
            for reason, count in sorted(s.reasons.items(), key=lambda kv: -kv[1]):
                lines.append(f"- {reason}: {count}")
            lines.append("")

    if diffs:
        lines += ["## Изменения относительно прошлой выгрузки", ""]
        for company, d in diffs.items():
            lines.append(
                f"- {company}: новых: {d.new}, изменилась цена: {d.changed_price}, "
                f"без изменений: {d.unchanged}, снимается: {d.deleted}"
            )

    text = "\n".join(lines) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_report.py -v`
Ожидается: PASS, 5 тестов

- [ ] **Шаг 5: Зафиксировать**

```bash
git add -A
git commit -m "feat: отчёт о сборке и файл с ошибками"
```

---

### Задача 14: Оркестровка сборки

**Файлы:**
- Создать: `src/rtsprice/pipeline.py`
- Тест: `tests/test_pipeline.py`

**Интерфейсы:**
- Потребляет: всё из задач 1–13
- Производит: `Paths` (набор путей проекта), `BuildResult`,
  `build(paths, sources, companies, only=None, skip=None, company_codes=None, dry_run=False)
  -> BuildResult`,
  `collect_items(paths, sources, idmap, stoplist)
  -> tuple[dict[str, list[Item]], list[SourceStats], list[Rejection]]`

Порядок операций внутри одной компании: собрать позиции включённых источников, отрендерить,
проверить, посчитать удаления по снимку, записать файл, сохранить снимок. При `dry_run` файлы и
снимки не пишутся — это режим команды `check`.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_pipeline.py
from pathlib import Path

import openpyxl
import pytest

import dataclasses

from rtsprice.config import CompanyConfig, SourceConfig
from rtsprice.pipeline import Paths, build
from rtsprice.render import C_DELETE, C_ID, C_NAME, C_PRICE
from rtsprice.state import load_snapshot


def _paths(tmp_path: Path) -> Paths:
    for name in ("input", "photos", "reference", "state", "output"):
        (tmp_path / name).mkdir(exist_ok=True)
    okei = tmp_path / "reference" / "okei.csv"
    okei.write_text("code,symbol,name\n796,ШТ,Штука\n", encoding="utf-8-sig")
    (tmp_path / "reference" / "okpd2.csv").write_text("code,name\n", encoding="utf-8-sig")
    return Paths(root=tmp_path)


def _source(tmp_path: Path, rows: list[list], code: str = "s", prefix: int = 7,
            state: str = "on") -> SourceConfig:
    directory = tmp_path / "input" / code
    directory.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Лист1"
    ws.append(["Артикул", "Наименование", "Цена"])
    for row in rows:
        ws.append(row)
    wb.save(directory / "price.xlsx")
    return SourceConfig(
        code=code, title=code.upper(), state=state, prefix=prefix, companies=("ooo_tlt",),
        file_glob=str(directory / "*.xlsx"), sheets=("Лист1",), header_rows=(1,),
        data_starts_at=2, row_is_product="price_not_empty",
        columns={"article": "Артикул", "name": "Наименование", "price": "Цена"},
    )


def _company() -> dict[str, CompanyConfig]:
    return {"ooo_tlt": CompanyConfig(code="ooo_tlt", title="ООО", vat_mode="vat22",
                                     rounding="ruble", validity_days=21)}


def test_build_writes_file_and_snapshot(tmp_path: Path):
    paths = _paths(tmp_path)
    sources = {"s": _source(tmp_path, [["A-1", "Товар", 122], ["A-2", "Второй", 244]])}
    result = build(paths, sources, _company())

    written = result.files["ooo_tlt"]
    ws = openpyxl.load_workbook(written[0]).active
    assert ws.max_row == 4
    assert ws.cell(3, C_NAME + 1).value == "Товар"
    assert (paths.state / "last_ooo_tlt.csv").exists()
    assert result.stats[0].accepted == 2


def test_second_build_marks_disappeared_position_for_deletion(tmp_path: Path):
    paths = _paths(tmp_path)
    build(paths, {"s": _source(tmp_path, [["A-1", "Товар", 122], ["A-2", "Второй", 244]])},
          _company())
    result = build(paths, {"s": _source(tmp_path, [["A-1", "Товар", 122]])}, _company())

    ws = openpyxl.load_workbook(result.files["ooo_tlt"][0]).active
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=3)]
    deleted = [r for r in rows if r[C_DELETE] == 1]
    assert len(deleted) == 1
    assert deleted[0][C_NAME] == "Второй"
    assert result.diffs["ooo_tlt"].deleted == 1


def test_frozen_source_is_not_deleted(tmp_path: Path):
    paths = _paths(tmp_path)
    build(paths, {"s": _source(tmp_path, [["A-1", "Товар", 122]])}, _company())
    frozen = {"s": _source(tmp_path, [["A-1", "Товар", 122]], state="frozen")}
    result = build(paths, frozen, _company())

    ws = openpyxl.load_workbook(result.files["ooo_tlt"][0]).active
    assert ws.max_row == 2
    assert result.diffs["ooo_tlt"].deleted == 0


def test_frozen_source_can_still_be_deleted_after_being_switched_off(tmp_path: Path):
    paths = _paths(tmp_path)
    build(paths, {"s": _source(tmp_path, [["A-1", "Товар", 122]])}, _company())
    build(paths, {"s": _source(tmp_path, [["A-1", "Товар", 122]], state="frozen")},
          _company())
    result = build(paths, {"s": _source(tmp_path, [["A-1", "Товар", 122]], state="off")},
                   _company())
    assert result.diffs["ooo_tlt"].deleted == 1


def test_off_source_positions_are_deleted(tmp_path: Path):
    paths = _paths(tmp_path)
    build(paths, {"s": _source(tmp_path, [["A-1", "Товар", 122]])}, _company())
    result = build(paths, {"s": _source(tmp_path, [["A-1", "Товар", 122]], state="off")},
                   _company())
    assert result.diffs["ooo_tlt"].deleted == 1


def test_only_filter_limits_sources(tmp_path: Path):
    paths = _paths(tmp_path)
    sources = {
        "s": _source(tmp_path, [["A-1", "Товар", 122]], code="s", prefix=7),
        "t": _source(tmp_path, [["B-1", "Другой", 244]], code="t", prefix=8),
    }
    result = build(paths, sources, _company(), only=["s"])
    assert [s.code for s in result.stats] == ["s"]


def test_only_filter_does_not_delete_other_sources(tmp_path: Path):
    paths = _paths(tmp_path)
    sources = {
        "s": _source(tmp_path, [["A-1", "Товар", 122]], code="s", prefix=7),
        "t": _source(tmp_path, [["B-1", "Другой", 244]], code="t", prefix=8),
    }
    build(paths, sources, _company())
    result = build(paths, sources, _company(), only=["s"])

    assert result.diffs["ooo_tlt"].deleted == 0
    assert set(load_snapshot(paths.state / "last_ooo_tlt.csv")) == {70_000_001, 80_000_001}


def test_broken_columns_do_not_abort_build_or_delete(tmp_path: Path):
    paths = _paths(tmp_path)
    build(paths, {"s": _source(tmp_path, [["A-1", "Товар", 122]])}, _company())

    broken = dataclasses.replace(
        _source(tmp_path, [["A-1", "Товар", 122]]),
        columns={"article": "Артикул", "name": "Наименование", "price": "Нет такой колонки"},
    )
    result = build(paths, {"s": broken}, _company())

    assert result.stats[0].read == 0
    assert "Нет такой колонки" in " ".join(result.stats[0].reasons)
    assert result.diffs["ooo_tlt"].deleted == 0


def test_dry_run_writes_nothing(tmp_path: Path):
    paths = _paths(tmp_path)
    sources = {"s": _source(tmp_path, [["A-1", "Товар", 122]])}
    result = build(paths, sources, _company(), dry_run=True)
    assert result.files == {}
    assert not (paths.state / "last_ooo_tlt.csv").exists()
    assert result.stats[0].accepted == 1


def test_price_change_counted_in_diff(tmp_path: Path):
    paths = _paths(tmp_path)
    build(paths, {"s": _source(tmp_path, [["A-1", "Товар", 122]])}, _company())
    result = build(paths, {"s": _source(tmp_path, [["A-1", "Товар", 244]])}, _company())
    assert result.diffs["ooo_tlt"].changed_price == 1
    assert result.diffs["ooo_tlt"].new == 0


def test_missing_source_file_is_reported_not_fatal(tmp_path: Path):
    paths = _paths(tmp_path)
    broken = SourceConfig(
        code="x", title="X", state="on", prefix=9, companies=("ooo_tlt",),
        file_glob=str(tmp_path / "input" / "x" / "*.xlsx"), sheets=(), header_rows=(1,),
        data_starts_at=2, row_is_product="price_not_empty", columns={},
    )
    result = build(paths, {"x": broken}, _company())
    assert result.stats[0].read == 0
    assert "не найден" in " ".join(result.stats[0].reasons)
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_pipeline.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.pipeline'`

- [ ] **Шаг 3: Реализовать `pipeline.py`**

```python
# src/rtsprice/pipeline.py
"""Оркестровка полной сборки прайс-листов."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import CompanyConfig, SourceConfig
from .identity import IdMap, prefix_of
from .normalize import Item, Rejection, normalize_source
from .photos import PhotoResolver
from .readers import find_source_file, read_source
from .reference import load_okpd2, load_unit_aliases, load_units
from .render import C_ID, C_PRICE, render_row
from .report import DiffStats, SourceStats, write_errors_xlsx, write_report_md
from .state import deletion_rows, load_snapshot, save_snapshot
from .validate import Issue, validate_and_fix
from .writer import write_price_file


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def reference(self) -> Path:
        return self.root / "reference"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def photos(self) -> Path:
        return self.root / "photos"

    @property
    def output(self) -> Path:
        return self.root / "output" / f"{date.today():%Y-%m-%d}"


@dataclass
class BuildResult:
    stats: list[SourceStats] = field(default_factory=list)
    diffs: dict[str, DiffStats] = field(default_factory=dict)
    files: dict[str, list[Path]] = field(default_factory=dict)
    rejections: list[Rejection] = field(default_factory=list)
    issues: list[tuple[str, str, Issue]] = field(default_factory=list)


def _active(sources: dict[str, SourceConfig], only, skip) -> list[SourceConfig]:
    chosen = [
        cfg for code, cfg in sources.items()
        if (not only or code in only) and (not skip or code not in skip)
    ]
    return sorted(chosen, key=lambda c: c.code)


def collect_items(
    paths: Paths,
    sources: list[SourceConfig],
    idmap: IdMap,
    stoplist: set[tuple[str, str]],
) -> tuple[dict[str, list[Item]], list[SourceStats], list[Rejection]]:
    units = load_units(paths.reference / "okei.csv")
    aliases = load_unit_aliases(paths.reference / "unit_aliases.yml")

    by_source: dict[str, list[Item]] = {}
    stats: list[SourceStats] = []
    rejections: list[Rejection] = []

    for cfg in sources:
        if cfg.state != "on":
            stats.append(SourceStats(cfg.code, cfg.title, cfg.state, "", 0, 0, 0))
            continue
        try:
            # Шаблон в конфиге задан относительно корня проекта, а не рабочего
            # каталога: иначе запуск не из корня молча не нашёл бы ни одного
            # прайса. Абсолютный путь Path сохраняет как есть.
            path = find_source_file(str(paths.root / cfg.file_glob))
        except FileNotFoundError as exc:
            stats.append(SourceStats(cfg.code, cfg.title, cfg.state, "", 0, 0, 0,
                                     {str(exc): 1}))
            continue

        try:
            rows = read_source(cfg, path)
        except KeyError as exc:
            # Поставщик переименовал колонку. Источник выпадает из выгрузки с
            # внятной причиной в отчёте, но сборка остальных продолжается — и,
            # что важнее, его позиции не попадают под снятие с продажи.
            stats.append(SourceStats(cfg.code, cfg.title, cfg.state, path.name, 0, 0, 0,
                                     {str(exc.args[0]): 1}))
            continue

        items, rejected = normalize_source(cfg, rows, idmap, stoplist, units, aliases)
        by_source[cfg.code] = items
        rejections.extend(rejected)
        stats.append(SourceStats(
            code=cfg.code, title=cfg.title, state=cfg.state, file_name=path.name,
            read=len(rows), rejected=len(rejected), accepted=len(items),
            reasons=dict(Counter(r.reason for r in rejected)),
        ))
    return by_source, stats, rejections


def build(
    paths: Paths,
    sources: dict[str, SourceConfig],
    companies: dict[str, CompanyConfig],
    only: list[str] | None = None,
    skip: list[str] | None = None,
    company_codes: list[str] | None = None,
    stoplist: set[tuple[str, str]] | None = None,
    photo_publisher=None,
    dry_run: bool = False,
) -> BuildResult:
    active = _active(sources, only, skip)
    idmap = IdMap(paths.state / "id_map.csv")
    okpd2 = load_okpd2(paths.reference / "okpd2.csv")
    units = load_units(paths.reference / "okei.csv")

    by_source, stats, rejections = collect_items(paths, active, idmap, stoplist or set())
    if not dry_run:
        # Идентификаторы выданы на этом шаге и уже могут уйти в записанный файл.
        # Карта только пополняется, поэтому сохраняем сразу: если сборка упадёт
        # дальше, повторный запуск не выдаст те же номера другим артикулам.
        idmap.save()
    result = BuildResult(stats=stats, rejections=rejections)

    resolver = (
        PhotoResolver(paths.photos, photo_publisher, paths.state / "photos.json")
        if photo_publisher else None
    )

    for company_code, company in companies.items():
        if company_codes and company_code not in company_codes:
            continue

        rows: list[list[object]] = []
        seen_ids: set[int] = set()
        for cfg in active:
            if company_code not in cfg.companies:
                continue
            for item in by_source.get(cfg.code, []):
                urls = resolver.urls_for(item) if resolver else []
                candidate = render_row(item, cfg, company, urls)
                fixed, issues = validate_and_fix(candidate, units, okpd2, seen_ids)
                result.issues.extend((cfg.code, item.article, i) for i in issues)
                if fixed is None:
                    result.rejections.append(Rejection(
                        cfg.code, item.article, 0, issues[0].field, issues[0].reason,
                        issues[0].value))
                    continue
                rows.append(fixed)

        snapshot_path = paths.state / f"last_{company_code}.csv"
        previous = load_snapshot(snapshot_path)
        current_ids = {int(r[C_ID]) for r in rows}

        # Снимать с продажи можно только позиции тех источников, которые в этом
        # запуске действительно пересобирались: прочитанных без ошибок и явно
        # выключенных. Всё прочее — замороженное, отсеянное ключами --only и
        # --skip, сломанное сменой колонок, убранное из конфига — отсутствует в
        # текущей выгрузке не потому, что товар кончился, а потому что его не
        # читали. Без этой границы «build --only promet» пометил бы к удалению
        # весь товар остальных поставщиков.
        refreshed = {
            cfg.prefix for cfg in active
            if company_code in cfg.companies
            and (cfg.code in by_source or cfg.state == "off")
        }
        untouched = {prefix_of(rts_id) for rts_id in previous} - refreshed

        diff = DiffStats(deleted=0)
        for row in rows:
            rts_id = int(row[C_ID])
            if rts_id not in previous:
                diff.new += 1
            elif previous[rts_id][C_PRICE] != row[C_PRICE]:
                diff.changed_price += 1
            else:
                diff.unchanged += 1

        removals = deletion_rows(previous, current_ids, untouched)
        diff.deleted = len(removals)
        result.diffs[company_code] = diff

        if dry_run:
            continue

        result.files[company_code] = write_price_file(
            rows + removals, paths.output / f"{company_code}.xlsx"
        )
        # Снимок описывает то, что сейчас стоит на площадке, а не то, что
        # записано в этот раз. Позиции непересобиравшихся источников остаются
        # на витрине и переносятся в новый снимок: иначе они выпали бы из
        # записи, и снять их потом стало бы нечем.
        carried = [
            row for rts_id, row in sorted(previous.items())
            if prefix_of(rts_id) in untouched
        ]
        save_snapshot(snapshot_path, rows + carried)

    if not dry_run:
        if resolver:
            resolver.save()
        write_errors_xlsx(paths.output / "errors.xlsx", result.rejections, result.issues)
        write_report_md(paths.output / "report.md", result.stats, result.diffs)
    return result
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_pipeline.py -v`
Ожидается: PASS, 11 тестов

- [ ] **Шаг 5: Прогнать весь набор тестов**

Выполнить: `python -m pytest -q`
Ожидается: PASS, все тесты задач 1–14

- [ ] **Шаг 6: Зафиксировать**

```bash
git add -A
git commit -m "feat: оркестровка сборки с расчётом удалений и отчётами"
```

---

### Задача 15: Командный интерфейс

**Файлы:**
- Создать: `src/rtsprice/cli.py`, `src/rtsprice/__main__.py`
- Тест: `tests/test_cli.py`

**Интерфейсы:**
- Потребляет: `build`, `Paths` (задача 14), загрузчики конфигурации (задача 1)
- Производит: `main(argv) -> int`, `format_status(root) -> str`,
  `set_state(path, code, state)`

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_cli.py
from pathlib import Path

import pytest

import openpyxl
from rtsprice.cli import main, set_state
from rtsprice.config import load_sources

SOURCES = """
promet:
  title: "Промет"
  state: on
  prefix: 10
  file: "input/promet/*.xlsx"
  columns:
    article: "Артикул"
    name: "Наименование"
    price: "Цена"
brinex:
  title: "Бринэкс"
  state: frozen
  prefix: 11
  file: "input/brinex/*.xlsx"
  columns:
    article: "Артикул"
    name: "Наименование"
    price: "Цена"
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "sources.yml").write_text(SOURCES, encoding="utf-8")
    companies = tmp_path / "companies"
    companies.mkdir()
    (companies / "ooo_tlt.yml").write_text("title: ООО\nvat_mode: vat22\n", encoding="utf-8")
    reference = tmp_path / "reference"
    reference.mkdir()
    (reference / "okei.csv").write_text("code,symbol,name\n796,ШТ,Штука\n", encoding="utf-8-sig")
    (reference / "okpd2.csv").write_text("code,name\n", encoding="utf-8-sig")
    return tmp_path


def test_status_lists_sources_with_states(project: Path, capsys):
    assert main(["--root", str(project), "status"]) == 0
    out = capsys.readouterr().out
    assert "Промет" in out and "on" in out
    assert "Бринэкс" in out and "frozen" in out


def test_off_command_changes_state_in_file(project: Path):
    assert main(["--root", str(project), "off", "promet"]) == 0
    assert load_sources(project / "sources.yml")["promet"].state == "off"


def test_freeze_and_on_round_trip(project: Path):
    main(["--root", str(project), "freeze", "promet"])
    assert load_sources(project / "sources.yml")["promet"].state == "frozen"
    main(["--root", str(project), "on", "promet"])
    assert load_sources(project / "sources.yml")["promet"].state == "on"


def test_set_state_matches_block_indentation(tmp_path: Path):
    p = tmp_path / "sources.yml"
    p.write_text(
        'promet:
'
        '    title: "Промет"
'
        '    prefix: 10
'
        '    file: "input/promet/*.xlsx"
',
        encoding="utf-8",
    )
    set_state(p, "promet", "off")
    assert "    state: off" in p.read_text(encoding="utf-8")
    assert load_sources(p)["promet"].state == "off"


def test_status_resolves_source_glob_against_root(project: Path, capsys):
    directory = project / "input" / "promet"
    directory.mkdir(parents=True)
    wb = openpyxl.Workbook()
    wb.active.append(["Артикул", "Наименование", "Цена"])
    wb.save(directory / "price.xlsx")

    assert main(["--root", str(project), "status"]) == 0
    assert "price.xlsx" in capsys.readouterr().out


def test_set_state_rejects_unknown_source(project: Path):
    with pytest.raises(KeyError):
        set_state(project / "sources.yml", "нет-такого", "off")


def test_unknown_state_is_rejected(project: Path):
    with pytest.raises(ValueError):
        set_state(project / "sources.yml", "promet", "paused")


def test_check_runs_without_writing_output(project: Path, capsys):
    assert main(["--root", str(project), "check"]) == 0
    assert not (project / "output").exists()
    assert "Прочитано" in capsys.readouterr().out
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Выполнить: `python -m pytest tests/test_cli.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'rtsprice.cli'`

- [ ] **Шаг 3: Реализовать `cli.py` и `__main__.py`**

```python
# src/rtsprice/cli.py
"""Команды управления сборкой прайс-листов."""
from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

from .config import VALID_STATES, load_companies, load_sources, load_stoplist
from .pipeline import Paths, build
from .readers import find_source_file


def set_state(path: Path, code: str, state: str) -> None:
    """Поменять состояние источника, сохранив форматирование файла."""
    if state not in VALID_STATES:
        raise ValueError(f"недопустимое состояние {state!r}, ожидается одно из {VALID_STATES}")
    text = Path(path).read_text(encoding="utf-8")
    if not re.search(rf"^{re.escape(code)}:\s*$", text, re.M):
        raise KeyError(f"источник {code!r} не найден в {path}")

    lines, inside, replaced = text.splitlines(), False, False
    for i, line in enumerate(lines):
        if re.fullmatch(rf"{re.escape(code)}:\s*", line):
            inside = True
            continue
        if inside and line and not line[0].isspace():
            break
        if inside and re.match(r"\s+state:", line):
            indent = line[: len(line) - len(line.lstrip())]
            lines[i] = f"{indent}state: {state}"
            replaced = True
            break
    if not replaced:
        index = next(i for i, l in enumerate(lines) if re.fullmatch(rf"{re.escape(code)}:\s*", l))
        # Отступ берётся у соседнего ключа того же блока: файл может быть
        # размечен и двумя пробелами, и четырьмя, а вставка с чужим отступом
        # молча ломает YAML — команда отрапортует успех, а конфиг перестанет
        # читаться при следующем запуске.
        indent = "  "
        for line in lines[index + 1:]:
            if not line.strip():
                continue
            if not line[0].isspace():
                break
            indent = line[: len(line) - len(line.lstrip())]
            break
        lines.insert(index + 1, f"{indent}state: {state}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_status(root: Path) -> str:
    sources = load_sources(root / "sources.yml")
    rows = [("Источник", "Состояние", "Компании", "Файл", "Дата")]
    for cfg in sorted(sources.values(), key=lambda c: c.code):
        try:
            path = find_source_file(str(root / cfg.file_glob))
            name = path.name
            stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m.%Y")
        except FileNotFoundError:
            name, stamp = "— файл не найден —", "—"
        rows.append((cfg.title, cfg.state, ", ".join(cfg.companies), name, stamp))

    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join(
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows
    )


def _run_build(root: Path, args, dry_run: bool) -> int:
    sources = load_sources(root / "sources.yml")
    companies = load_companies(root / "companies")
    result = build(
        Paths(root=root), sources, companies,
        only=args.only, skip=args.skip, company_codes=args.company,
        stoplist=load_stoplist(root / "stoplist.csv"), dry_run=dry_run,
    )
    for s in result.stats:
        print(f"{s.title}: Прочитано {s.read}, отсеяно {s.rejected}, принято {s.accepted}")
    for company, diff in result.diffs.items():
        print(f"{company}: новых {diff.new}, изменилась цена {diff.changed_price}, "
              f"снимается {diff.deleted}")
    for company, files in result.files.items():
        for path in files:
            print(f"записан {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rtsprice")
    parser.add_argument("--root", default=".", help="корень проекта")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="состояние источников")
    for name, help_text in (("build", "собрать файлы"), ("check", "проверить без записи")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--only", nargs="*", help="только эти источники")
        p.add_argument("--skip", nargs="*", help="пропустить эти источники")
        p.add_argument("--company", nargs="*", help="только эти юридические лица")
    for name in ("on", "off", "freeze"):
        p = sub.add_parser(name, help=f"перевести источник в состояние {name}")
        p.add_argument("source")

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    if args.command == "status":
        print(format_status(root))
        return 0
    if args.command in ("on", "off", "freeze"):
        state = "frozen" if args.command == "freeze" else args.command
        set_state(root / "sources.yml", args.source, state)
        print(f"{args.source}: состояние изменено на {state}")
        return 0
    return _run_build(root, args, dry_run=args.command == "check")
```

```python
# src/rtsprice/__main__.py
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Выполнить: `python -m pytest tests/test_cli.py -v`
Ожидается: PASS, 8 тестов

- [ ] **Шаг 5: Зафиксировать**

```bash
git add -A
git commit -m "feat: команды status, build, check и смены состояния источника"
```

---

### Задача 16: Настройка реальных источников и проверочный импорт

**Файлы:**
- Изменить: `sources.yml`, `companies/ooo_tlt.yml`, `companies/ip.yml`
- Создать: `README.md`
- Тест: `tests/test_real_sources.py`

**Интерфейсы:**
- Потребляет: всё построенное ранее
- Производит: рабочую конфигурацию шести известных поставщиков

Разбор реальных файлов дал следующие параметры. Префиксы закрепляются навсегда: они входят в
идентификаторы позиций, уже загруженных на площадку.

| Источник | Префикс | Лист | header_rows | data_starts_at | Признак товара |
|---|---|---|---|---|---|
| promet | 10 | `база` | `[1]` | 2 | price_not_empty |
| brinex_wheels | 11 | `Автодиски` | `[6]` | 8 | price_not_empty |
| brinex_tires | 12 | `Автошины` | `[6]` | 8 | price_not_empty |
| gurinenko_bakaleya | 13 | `Лист_1` | `[1, 2]` | 3 | barcode13 |
| gurinenko_grushevo | 14 | `Лист_1` | `[2, 3]` | 4 | barcode13 |
| opt | 15 | `Лист_1` | `[3, 4]` | 5 | price_not_empty |
| priceopt | 16 | `Лист_1` | `[1]` | 4 | price_not_empty |

- [ ] **Шаг 1: Разложить прайсы по папкам**

```bash
cd "C:/Users/TLT-1/Documents/GitHub/RTS_tender"
mkdir -p input/promet input/brinex input/gurinenko_bakaleya input/gurinenko_grushevo input/opt input/priceopt
cp "C:/Users/TLT-1/Downloads/Прайc Промет 13.07.2026 дилер (1).xlsx" input/promet/
cp "C:/Users/TLT-1/Downloads/ПрайсБринэкс.xlsx" input/brinex/
cp "C:/Users/TLT-1/Downloads/Прайс Гуриненко Бакалея.xlsx" input/gurinenko_bakaleya/
cp "C:/Users/TLT-1/Downloads/прайс Гуриненко Грушево.xlsx" input/gurinenko_grushevo/
cp "C:/Users/TLT-1/Downloads/Прайс-лист_ОПТ_без фото_остатки (XLSX) (1).xlsx" input/opt/
cp "C:/Users/TLT-1/Downloads/Telegram Desktop/priceopt_20260901.xls" input/priceopt/
```

- [ ] **Шаг 2: Написать тест, проверяющий конфигурацию на настоящих файлах**

```python
# tests/test_real_sources.py
from pathlib import Path

import pytest

from rtsprice.config import load_sources
from rtsprice.identity import IdMap
from rtsprice.normalize import normalize_source
from rtsprice.readers import find_source_file, read_source
from rtsprice.reference import load_unit_aliases, load_units

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MINIMUM = {
    "promet": 12000,
    "brinex_wheels": 7000,
    "brinex_tires": 4000,
    "gurinenko_bakaleya": 1200,
    "gurinenko_grushevo": 4000,
    "opt": 400,
    "priceopt": 1600,
}


@pytest.mark.parametrize("code", sorted(EXPECTED_MINIMUM))
def test_source_reads_and_normalizes(code: str, tmp_path: Path):
    sources = load_sources(ROOT / "sources.yml")
    cfg = sources[code]
    path = find_source_file(cfg.file_glob)

    rows = read_source(cfg, path)
    assert len(rows) >= EXPECTED_MINIMUM[code], f"{code}: прочитано слишком мало строк"

    units = load_units(ROOT / "reference" / "okei.csv")
    aliases = load_unit_aliases(ROOT / "reference" / "unit_aliases.yml")
    items, rejected = normalize_source(cfg, rows, IdMap(tmp_path / f"{code}.csv"),
                                       set(), units, aliases)
    assert len(items) >= 0.8 * len(rows), (
        f"{code}: отсеяно больше 20% строк, причины: "
        f"{ {r.reason for r in rejected} }"
    )
    for item in items[:50]:
        assert item.name and len(item.name) <= 200
        assert item.description
        assert item.price_in > 0
        assert item.unit in units
```

- [ ] **Шаг 3: Заполнить `sources.yml`**

```yaml
promet:
  title: "Промет"
  state: on
  prefix: 10
  file: "input/promet/*.xlsx"
  sheets: ["база"]
  header_rows: [1]
  data_starts_at: 2
  row_is_product: price_not_empty
  columns:
    article: "Артикул"
    name: "Наименование"
    supplier_id: "ID"
    price: "Розничная цена"
  unit_default: "ШТ"
  price_includes_vat: true
  description_template: |
    {name}
    Артикул производителя: {article}

brinex_wheels:
  title: "Бринэкс, автодиски"
  state: on
  prefix: 11
  file: "input/brinex/*.xlsx"
  sheets: ["Автодиски"]
  header_rows: [6]
  data_starts_at: 8
  row_is_product: price_not_empty
  columns:
    article: "Артикул"
    name: "Номенклатура"
    price: "Цена"
    stock: "Остаток общий"
    brand: "Производитель"
    diameter: "Диаметр"
  min_stock: 1
  unit_default: "ШТ"
  description_template: |
    {name}
    Производитель: {brand}
    Диаметр: {diameter}
    Артикул производителя: {article}

brinex_tires:
  title: "Бринэкс, автошины"
  state: on
  prefix: 12
  file: "input/brinex/*.xlsx"
  sheets: ["Автошины"]
  header_rows: [6]
  data_starts_at: 8
  row_is_product: price_not_empty
  columns:
    article: "Артикул"
    name: "Номенклатура"
    price: "Цена"
    stock: "Остаток общий"
    brand: "Производитель"
    width: "Ширина"
  min_stock: 1
  unit_default: "ШТ"
  description_template: |
    {name}
    Производитель: {brand}
    Ширина профиля: {width}
    Артикул производителя: {article}

gurinenko_bakaleya:
  title: "Гуриненко, бакалея"
  state: on
  prefix: 13
  file: "input/gurinenko_bakaleya/*.xlsx"
  sheets: ["Лист_1"]
  header_rows: [1, 2]
  data_starts_at: 3
  row_is_product: barcode13
  columns:
    barcode: "Штрихкод"
    name: "Номенклатура"
    price: "Цена клиента"
    stock: "Остаток"
  min_stock: 1
  unit_default: "ШТ"
  description_template: "{name}"

gurinenko_grushevo:
  title: "Гуриненко, Грушево"
  state: on
  prefix: 14
  file: "input/gurinenko_grushevo/*.xlsx"
  sheets: ["Лист_1"]
  header_rows: [2, 3]
  data_starts_at: 4
  row_is_product: barcode13
  columns:
    barcode: "Штрихкод"
    name: "Номенклатура"
    price: "Цена клиента"
  unit_default: "ШТ"
  description_template: "{name}"

opt:
  title: "Прайс-лист ОПТ"
  state: on
  prefix: 15
  file: "input/opt/*.xlsx"
  sheets: ["Лист_1"]
  header_rows: [3, 4]
  data_starts_at: 5
  row_is_product: price_not_empty
  columns:
    article: "Артикул"
    name: "Номенклатура"
    price: "Закуп Цена"
    stock: "Склад основной ООО Остаток"
  unit_default: "ШТ"
  description_template: "{name}"

priceopt:
  title: "Посуда и кухня"
  state: on
  prefix: 16
  file: "input/priceopt/*.xls"
  sheets: ["Лист_1"]
  header_rows: [1]
  data_starts_at: 4
  row_is_product: price_not_empty
  columns:
    article: "Код"
    name: "Номенклатура 01-09-26"
    price: "Цена"
    group: "Группа"
    brand: "Производитель"
  unit_default: "ШТ"
  description_template: |
    {name}
    Производитель: {brand}
    Категория: {group}
```

- [ ] **Шаг 4: Прогнать тест на настоящих файлах и починить конфигурацию**

Выполнить: `python -m pytest tests/test_real_sources.py -v`

Заголовок колонки у поставщика может отличаться от ожидаемого — сообщение `KeyError` перечисляет
фактические заголовки листа. Поправить значение в `columns` и повторить. Заголовок
`Номенклатура 01-09-26` у источника `priceopt` содержит дату и меняется при каждой выгрузке
поставщика; если тест упал именно на нём, взять актуальный заголовок из сообщения об ошибке.

Ожидается: PASS, 7 тестов.

- [ ] **Шаг 5: Собрать пробный файл на трёх позициях**

```bash
python -m rtsprice --root . status
python -m rtsprice --root . check
```

Просмотреть вывод: доля отсева по каждому источнику должна быть небольшой. Затем собрать полностью:

```bash
python -m rtsprice --root . build
```

- [ ] **Шаг 6: Проверочный импорт на площадке**

Из собранного файла ИП оставить три строки, сохранить как `output/проверка_ип.xlsx` и загрузить
в личный кабинет ИП через «Добавить товар или услугу» → «Импорт из XLS (Excel)». Проверить:

1. Принята ли единица измерения `ШТ`.
2. Принято ли значение `Не облагается НДС` — оно отсутствует в списке проверки шаблона, и это
   блокирующий вопрос для всей выгрузки ИП.
3. Принял ли импорт файл, собранный с нуля, а не копированием шаблона.

Затем загрузить тот же файл повторно и убедиться, что позиции обновились, а не задвоились.

Результат записать в раздел 14 спецификации. Если `Не облагается НДС` не принимается, отметить это
в спецификации и обратиться в поддержку РТС по телефону 8 800 775 9959 — до ответа выгрузка ИП
не запускается, выгрузка ООО от этого не зависит.

- [ ] **Шаг 7: Написать `README.md`**

```markdown
# Сборка прайс-листов для РТС-маркет

## Ежедневная работа

1. Положить свежий прайс поставщика в `input/<источник>/`, старый удалить или оставить —
   берётся самый свежий файл.
2. Проверить, что получится: `python -m rtsprice check`
3. Собрать файлы: `python -m rtsprice build`
4. Прочитать `output/<дата>/report.md` — там доля отсева по каждому поставщику и что изменилось.
5. Загрузить `output/<дата>/ooo_tlt.xlsx` и `output/<дата>/ip.xlsx` в личные кабинеты.

## Управление составом

| Команда | Что делает |
|---|---|
| `python -m rtsprice status` | показать все источники, их состояние и дату прайса |
| `python -m rtsprice off <источник>` | снять позиции источника с площадки |
| `python -m rtsprice freeze <источник>` | заморозить: не обновлять и не удалять |
| `python -m rtsprice on <источник>` | вернуть в работу |
| `python -m rtsprice build --only promet` | собрать один источник |
| `python -m rtsprice build --company ip` | собрать только для ИП |

Отдельные позиции исключаются через `stoplist.csv` — строка `источник,артикул`.

## Фотографии

Класть в `photos/<источник>/<артикул>_1.jpg`, до пяти файлов на позицию. Ссылки подставляются
сами при следующей сборке.

## Важное

- Не редактировать `state/id_map.csv` — он связывает артикулы с идентификаторами позиций на
  площадке. Его потеря приводит к дублям на витрине.
- Не править файлы в `output/` — они пересоздаются при каждой сборке.
- Наценка задаётся в `companies/*.yml` и уточняется в `sources.yml`.
```

- [ ] **Шаг 8: Зафиксировать**

```bash
git add -A
git commit -m "feat: конфигурация реальных источников и руководство пользователя"
```

---

## Самопроверка плана

**Покрытие спецификации.** Разделы 4–13 спецификации закрыты задачами 1–15; раздел 14
(проверочный импорт) — задачей 16, шагом 6; раздел 15 (вопросы к поставщикам) отражён в конфигурации
задачи 16 через выбранные умолчания: у Гуриненко взята «Цена клиента», у ОПТ — «Закуп Цена», у
Промета статусы не используются как фильтр.

**Незакрытая часть спецификации.** Подключение `SftpPublisher` к сборке в задаче 14 остаётся
необязательным параметром `photo_publisher`: пока у продавца нет домена и доступа к серверу,
фотографий нет, и передавать издателя некому. Как только доступ появится, в `cli.py` добавляется
чтение настроек SFTP и передача издателя в `build`. Команда `feedback` для разбора выгрузки из РТС
с колонкой «Ошибки заполнения» из плана исключена намеренно: её ценность проявится только после
первых массовых загрузок, когда станет видно, какие именно ошибки возвращает площадка.

**Согласованность типов.** `Item` и `Rejection` определены в задаче 6 и используются задачами 7,
8, 12, 13, 14. `Issue` определён в задаче 9 и используется задачами 13 и 14. Индексы колонок
`C_*` определены в задаче 8 и используются задачами 9, 10, 11, 14. Константы `NAME_LIMIT` и
`DESCRIPTION_LIMIT` определены в задаче 5 и используются задачами 6 и 9. `prefix_of` определён в
задаче 4 и используется задачей 10.
