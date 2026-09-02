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
