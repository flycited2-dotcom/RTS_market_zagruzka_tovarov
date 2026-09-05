"""Фотографии Промета из каталога safe.ru по разметке поставщика."""
from pathlib import Path

import pytest

from rtsprice.promet import (
    Position, PrometClient, agrees, cards_of, is_missing, measurements,
    model_code, normalize, parent_section, photo_of, read_catalog_map,
)


def test_normalize_transliterates_instead_of_dropping():
    """«Рубеж» и «РУБЕЖ» — одна модель.

    Отбрасывание незнакомых кириллических букв вместо транслитерации
    оставляло от обоих «ре» и однажды дало неверный вывод, будто четверти
    моделей фотобанка нет в прайсе.
    """
    assert normalize("Рубеж-99 EL") == normalize("РУБЕЖ 99 EL")
    assert normalize("Рубеж") == "rube" + "zh"


def test_model_code_drops_category_word():
    """В прайсе «Сейф X», на сайте «Мебельный сейф X» — код один."""
    assert model_code("Сейф MDTB ES-30.Е") == "mdtbes30e"
    assert model_code("Шкаф для раздевалок LH-600 C") == "razdevaloklh600c"


def test_parent_section_climbs_one_level():
    """Раздел из прайса исчез — ищем в разделе уровнем выше."""
    assert parent_section(
        "https://www.safe.ru/catalog/seyfy/oruzheynye-shkafy-i-seyfy/tiger/"
    ) == "https://www.safe.ru/catalog/seyfy/oruzheynye-shkafy-i-seyfy/"


def test_parent_section_refuses_to_climb_to_a_root():
    """Подъём до корневого раздела вернул бы тысячу карточек и снял бы
    сужение области, ради которого разметка поставщика и нужна."""
    assert parent_section("https://www.safe.ru/catalog/seyfy/ognestoykie-seyfy/") is None


def book(tmp_path: Path) -> Path:
    """Книга прайса: заголовок группы со ссылкой и позиции под ним."""
    import openpyxl

    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Сейфы"
    sheet.append(["артикул", "Наименование", "Высота", "Ширина", "Глубина", "Вес"])
    sheet.append([None, "MDTB класса S2 ES", None, None, None, None])
    sheet.append(["S1", "Сейф MDTB ES-30.Е", 300, 440, 354, 27])
    sheet.append(["S2", "Сейф MDTB ES-46.Е", 460, 440, 354, 35])
    sheet.append([None, "Оружейные", None, None, None, None])
    sheet.append(["S3", "Сейф Чирок 1015", 1000, 213, 153, 5.9])
    sheet.cell(row=2, column=2).hyperlink = (
        "https://www.safe.ru/catalog/seyfy/seyfy-evropeyskoy-sertifikatsii/mdtb-klassa-s1-es/")
    sheet.cell(row=5, column=2).hyperlink = (
        "https://www.safe.ru/catalog/seyfy/oruzheynye-shkafy-i-seyfy/aiko-seriya-chirok/")
    path = tmp_path / "price.xlsx"
    wb.save(path)
    return path


def test_catalog_map_reads_supplier_hyperlinks(tmp_path: Path):
    catalog = read_catalog_map(book(tmp_path))
    assert set(catalog) == {"S1", "S2", "S3"}
    assert catalog["S1"].section.endswith("mdtb-klassa-s1-es/")
    assert catalog["S1"].height == 300 and catalog["S1"].weight == 27


def test_catalog_map_carries_section_until_the_next_heading(tmp_path: Path):
    """Ссылка стоит на заголовке группы и действует до следующего."""
    catalog = read_catalog_map(book(tmp_path))
    assert catalog["S2"].section == catalog["S1"].section
    assert catalog["S3"].section.endswith("aiko-seriya-chirok/")


def test_cards_of_takes_addresses_without_repeats():
    """Адрес, а не текст ссылки: на части разделов текст приходит пустым,
    а модель есть в самом адресе."""
    html = ('<a href="/products/seyf-mdtb-es-30-e/"></a>'
            '<a href="/products/seyf-mdtb-es-30-e/">повтор</a>'
            '<a href="/products/seyf-mdtb-es-46-e/">Сейф</a>')
    assert cards_of(html) == ["/products/seyf-mdtb-es-30-e/",
                              "/products/seyf-mdtb-es-46-e/"]


def test_missing_page_is_recognised_despite_http_200():
    assert is_missing("<h1>Страница не найдена</h1>")
    assert not is_missing("<h1>Сейфы</h1>")


def test_measurements_reads_characteristics():
    html = ("<div>Размеры внешние, мм (ВхШхГ):</div><div>300x440x354</div>"
            "<div>Вес, кг:</div><div>27</div>")
    assert measurements(html) == ((300, 440, 354), 27.0)


def test_photo_prefers_the_detail_picture():
    html = ('<img src="/upload/iblock/aaa/bbb/preview_picture.jpg">'
            '<img src="/upload/iblock/ccc/ddd/detail_picture.jpg">')
    assert photo_of(html) == "https://www.safe.ru/upload/iblock/ccc/ddd/detail_picture.jpg"


SECTION_URL = "https://www.safe.ru/catalog/seyfy/mdtb/"
ES30 = Position("S1", "Сейф MDTB ES-30.Е", SECTION_URL, 300, 440, 354, 27)


def test_agrees_accepts_rounding_but_not_a_neighbour_model():
    assert agrees(ES30, (300, 440, 354), 27.0)
    assert agrees(ES30, (302, 441, 350), 27.4)
    assert not agrees(ES30, (460, 440, 354), 35.0)


def test_agrees_survives_a_swapped_height_and_width():
    """«Экран WDS» в прайсе 746x500, на карточке 500x746 — один экран.
    Порядок ВхШхГ поставщик соблюдает не везде."""
    screen = Position("S8", "Экран WDS", SECTION_URL, 746, 500, 20)
    assert agrees(screen, (500, 746, 20), None)


def test_agrees_still_refuses_a_different_size():
    """Устойчивость к перестановке не должна пропускать чужой товар:
    у соседней модели размеры отличаются в разы, а не порядком."""
    couch = Position("S7", "Кушетка медицинская МД КС", SECTION_URL, 560, 1950, 600)
    assert not agrees(couch, (780, 1950, 600), None)


def test_agrees_refuses_when_the_price_has_no_measurements():
    """Проверить нечем — значит снимок не берём: неверная фотография в
    карточке хуже отсутствующей."""
    bare = Position("S9", "Сейф X", "https://www.safe.ru/catalog/seyfy/mdtb/")
    assert not agrees(bare, (300, 440, 354), 27.0)


SECTION = "https://www.safe.ru/catalog/seyfy/mdtb/"
CARD = ("<h1>Мебельный сейф MDTB ES-30.Е</h1>"
        "Размеры внешние, мм (ВхШхГ): 300x440x354 Вес, кг: 27"
        '<img src="/upload/iblock/a/b/detail_picture.jpg">')


def client(pages, **kwargs) -> PrometClient:
    catalog = kwargs.pop("catalog", {"S1": ES30})
    return PrometClient(catalog, opener=lambda url: pages[url], pause=0,
                        sleep=lambda _: None, **kwargs)


def test_client_finds_the_photo_through_the_supplier_section():
    pages = {SECTION: '<a href="/products/seyf-mdtb-es-30-e/">Сейф</a>',
             "https://www.safe.ru/products/seyf-mdtb-es-30-e/": CARD}
    assert client(pages).images(["S1"]) == {
        "S1": "https://www.safe.ru/upload/iblock/a/b/detail_picture.jpg"}


def test_client_refuses_when_two_cards_both_match_the_measurements():
    """Выбор наугад между двумя карточками — это и есть чужая фотография.

    Спор разрешают габариты, но только вчистую: если размеры сошлись у обеих
    карточек, различить их нечем и снимок не берётся."""
    pages = {SECTION: ('<a href="/products/seyf-mdtb-es-30-e/">a</a>'
                       '<a href="/products/seyf-mdtb-es-30-e-el/">b</a>'),
             "https://www.safe.ru/products/seyf-mdtb-es-30-e/": CARD,
             "https://www.safe.ru/products/seyf-mdtb-es-30-e-el/": CARD}
    index = client(pages)
    assert index.images(["S1"]) == {}
    assert index.skipped_ambiguous == 1


def test_client_picks_the_card_whose_measurements_agree():
    """Две карточки на один код модели — раньше отказ, теперь габариты
    выбирают ту, что подходит. Это 74 позиции прогона 05.09.2026."""
    other = CARD.replace("300x440x354", "900x440x354").replace("Вес, кг: 27",
                                                               "Вес, кг: 60")
    pages = {SECTION: ('<a href="/products/seyf-mdtb-es-30-e/">a</a>'
                       '<a href="/products/seyf-mdtb-es-30-e-el/">b</a>'),
             "https://www.safe.ru/products/seyf-mdtb-es-30-e/": CARD,
             "https://www.safe.ru/products/seyf-mdtb-es-30-e-el/": other}
    assert client(pages).images(["S1"]) == {
        "S1": "https://www.safe.ru/upload/iblock/a/b/detail_picture.jpg"}


def test_client_refuses_when_measurements_disagree():
    """Совпал код модели, но размеры чужие — снимок не берём."""
    other = CARD.replace("300x440x354", "900x440x354").replace("Вес, кг: 27",
                                                               "Вес, кг: 60")
    pages = {SECTION: '<a href="/products/seyf-mdtb-es-30-e/">Сейф</a>',
             "https://www.safe.ru/products/seyf-mdtb-es-30-e/": other}
    index = client(pages)
    assert index.images(["S1"]) == {}
    assert index.skipped_mismatch == 1


def test_client_climbs_to_the_parent_when_the_section_is_gone():
    """Прайс живёт дольше структуры сайта: на 04.09.2026 двенадцать разделов
    из девяноста шести отдавали «Страница не найдена»."""
    gone = "https://www.safe.ru/catalog/seyfy/oruzheynye-shkafy-i-seyfy/tiger/"
    parent = "https://www.safe.ru/catalog/seyfy/oruzheynye-shkafy-i-seyfy/"
    position = Position("S3", "Сейф Tiger 60", gone, 1400, 400, 300, 60)
    card = ("<h1>Оружейный сейф TIGER 60</h1>"
            "Размеры внешние, мм (ВхШхГ): 1400x400x300 Вес, кг: 60"
            '<img src="/upload/iblock/t/g/detail_picture.jpg">')
    pages = {gone: "<h1>Страница не найдена</h1>",
             parent: '<a href="/products/oruzheynyy-seyf-tiger-60/">Tiger</a>',
             "https://www.safe.ru/products/oruzheynyy-seyf-tiger-60/": card}
    assert client(pages, catalog={"S3": position}).images(["S3"]) == {
        "S3": "https://www.safe.ru/upload/iblock/t/g/detail_picture.jpg"}


def test_client_skips_articles_the_supplier_never_marked_up():
    """84% прайса Промета — комплектующие, их на витринных листах нет."""
    index = client({})
    assert index.images(["нет-такого"]) == {}
    assert index.skipped_no_section == 1


def test_client_reads_each_page_once():
    """Одна карточка обслуживает все свои артикулы, страница раздела — все
    свои позиции: прогон и без того идёт по сотням адресов."""
    pages = {SECTION: '<a href="/products/seyf-mdtb-es-30-e/">Сейф</a>',
             "https://www.safe.ru/products/seyf-mdtb-es-30-e/": CARD}
    index = client(pages, catalog={"S1": ES30,
                                   "S2": Position("S2", "Сейф MDTB ES-30.Е",
                                                  SECTION, 300, 440, 354, 27)})
    assert len(index.images(["S1", "S2", "S1"])) == 2
    assert index.requests == 2


def test_number_reads_a_price_cell_with_two_variants():
    """Высота регулируемой мебели записана как «728/652,5», сушильного
    шкафа — как «1950*/2000». Без разбора позиция теряла размеры целиком."""
    from rtsprice.promet import _number

    assert _number("728/652,5") == 728
    assert _number("1950*/2000") == 1950
    assert _number("300") == 300
    assert _number(None) is None
    assert _number("нет") is None


def test_weight_blocks_only_a_gross_mismatch():
    """223 кг в прайсе и 270 на карточке — это нетто и брутто одного сейфа,
    габариты при этом сходятся до сантиметра. А вдвое — уже другой товар."""
    heavy = Position("S5", "Сейф AMH-125/2T", SECTION_URL, 1250, 450, 395, 223)
    assert agrees(heavy, (1258, 450, 400), 270.0)
    assert not agrees(heavy, (1258, 450, 400), 500.0)


def test_client_tells_which_articles_it_can_cover():
    """Из 13 255 позиций прайса размечены 1 722, и разбросаны они по всему
    файлу. Без этого отбора «--limit 20» берёт двадцать первых строк —
    комплектующие, которых на витринных листах нет, — и проба даёт ноль."""
    index = client({})
    assert index.covers("S1")
    assert not index.covers("00000001688")


def test_normalize_resolves_cyrillic_twins_inside_a_model_code():
    """В прайсе «FRS-36.СL» набрано с кириллической «С», на сайте — с
    латинской. Глазом не отличить, для машины это разные строки, и чистая
    транслитерация даёт «frs36sl», теряя совпадение."""
    assert model_code("Сейф FRS-36.СL") == model_code("Сейф FRS-36.CL")
    assert model_code("Сейф MDTB ES-63Т.Е") == model_code("Сейф MDTB ES-63T.E")


def test_normalize_still_transliterates_russian_words():
    """Двойники не должны съесть транслит: на сайте разделы и товары
    записаны как «stellazh» и «stol», а не «ctellazh» и «ctol»."""
    assert normalize("Стеллаж") == "stellazh"
    assert normalize("Стол") == "stol"
    assert normalize("Сейф") == "seif"


GONE = "https://www.safe.ru/catalog/seyfy/oruzheynye-shkafy-i-seyfy/tiger/"
SITEMAP = ("<urlset><url><loc>https://www.safe.ru/products/seyf-mdtb-es-30-e/</loc></url>"
           "<url><loc>https://www.safe.ru/products/shkaf-lh-600-c/</loc></url></urlset>")


def test_client_falls_back_to_the_whole_catalogue_when_the_section_died():
    """Раздел исчез вместе с родителем — так пропала 351 позиция прогона
    05.09.2026. Остаётся весь каталог сайта, и размеры решают."""
    position = Position("S1", "Сейф MDTB ES-30.Е", GONE, 300, 440, 354, 27)
    pages = {GONE: "<h1>Страница не найдена</h1>",
             "https://www.safe.ru/catalog/seyfy/oruzheynye-shkafy-i-seyfy/":
                 "<h1>Страница не найдена</h1>",
             "https://www.safe.ru/sitemap-news-2.xml": SITEMAP,
             "https://www.safe.ru/products/seyf-mdtb-es-30-e/": CARD}
    index = client(pages, catalog={"S1": position})
    assert index.images(["S1"]) == {
        "S1": "https://www.safe.ru/upload/iblock/a/b/detail_picture.jpg"}
    assert index.found_outside_section == 1


def test_fallback_demands_measurements_even_when_they_are_switched_off():
    """Вне своего раздела код модели — единственный признак, и подтвердить
    его больше нечем. Поэтому сверка там обязательна всегда."""
    position = Position("S1", "Сейф MDTB ES-30.Е", GONE, 900, 440, 354, 60)
    pages = {GONE: "<h1>Страница не найдена</h1>",
             "https://www.safe.ru/catalog/seyfy/oruzheynye-shkafy-i-seyfy/":
                 "<h1>Страница не найдена</h1>",
             "https://www.safe.ru/sitemap-news-2.xml": SITEMAP,
             "https://www.safe.ru/products/seyf-mdtb-es-30-e/": CARD}
    index = client(pages, catalog={"S1": position}, require_measurements=False)
    assert index.images(["S1"]) == {}


def test_fallback_skipped_when_the_price_has_no_measurements():
    """Без размеров запасной поиск не начинается вовсе: подтверждать нечем,
    а лишние запросы к сайту ничего не дадут."""
    position = Position("S1", "Сейф MDTB ES-30.Е", GONE)
    pages = {GONE: "<h1>Страница не найдена</h1>",
             "https://www.safe.ru/catalog/seyfy/oruzheynye-shkafy-i-seyfy/":
                 "<h1>Страница не найдена</h1>"}
    index = client(pages, catalog={"S1": position})
    assert index.images(["S1"]) == {}
    assert index.skipped_dead_section == 1
    assert index.requests == 2      # карту сайта не запрашивали


def test_client_refuses_a_model_code_that_is_too_broad():
    """Код, попадающий в десяток карточек, — это не поиск, а перебор."""
    cards = "".join(f'<a href="/products/seyf-mdtb-es-30-e-{n}/">x</a>'
                    for n in range(8))
    index = client({SECTION: cards})
    assert index.images(["S1"]) == {}
    assert index.skipped_ambiguous == 1
    assert index.requests == 1      # ни одной карточки не открывали


CARD_NO_SIZE = ("<h1>Взломостойкий сейф VALBERG Гранит-65Т</h1>"
                "Класс взломостойкости: класс III"
                '<img src="/upload/iblock/a/b/detail_picture.jpg">')


def test_card_without_measurements_is_accepted_inside_its_own_section():
    """У сейфов Гранит, Гарант и серии TM на карточке нет блока размеров.
    Внутри своего раздела сверять нечем — но раздел назначил поставщик, а
    код модели совпал, и этих двух признаков довольно."""
    pages = {SECTION: '<a href="/products/seyf-mdtb-es-30-e/">Сейф</a>',
             "https://www.safe.ru/products/seyf-mdtb-es-30-e/": CARD_NO_SIZE}
    assert client(pages).images(["S1"]) == {
        "S1": "https://www.safe.ru/upload/iblock/a/b/detail_picture.jpg"}


def test_card_without_measurements_is_refused_outside_the_section():
    """Вне раздела код модели — единственный признак, и карточка без
    размеров его не подтверждает."""
    position = Position("S1", "Сейф MDTB ES-30.Е", GONE, 300, 440, 354, 27)
    pages = {GONE: "<h1>Страница не найдена</h1>",
             "https://www.safe.ru/catalog/seyfy/oruzheynye-shkafy-i-seyfy/":
                 "<h1>Страница не найдена</h1>",
             "https://www.safe.ru/sitemap-news-2.xml": SITEMAP,
             "https://www.safe.ru/products/seyf-mdtb-es-30-e/": CARD_NO_SIZE}
    index = client(pages, catalog={"S1": position})
    assert index.images(["S1"]) == {}
    assert index.skipped_no_measurements == 1


def test_card_without_measurements_is_refused_when_there_are_rivals():
    """Два кандидата и ни у одного размеров — различить нечем."""
    pages = {SECTION: ('<a href="/products/seyf-mdtb-es-30-e/">a</a>'
                       '<a href="/products/seyf-mdtb-es-30-e-el/">b</a>'),
             "https://www.safe.ru/products/seyf-mdtb-es-30-e/": CARD_NO_SIZE,
             "https://www.safe.ru/products/seyf-mdtb-es-30-e-el/": CARD_NO_SIZE}
    index = client(pages)
    assert index.images(["S1"]) == {}
    assert index.skipped_no_measurements == 2
