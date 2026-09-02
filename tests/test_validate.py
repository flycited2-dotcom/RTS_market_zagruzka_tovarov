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
