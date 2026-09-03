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


def test_report_shows_outstanding_deletions(tmp_path: Path):
    text = write_report_md(
        tmp_path / "report.md",
        [],
        {"ooo_tlt": DiffStats(new=1, changed_price=2, unchanged=3, deleted=4, pending=7)},
    )
    assert "снимается: 4" in text
    assert "ожидают подтверждения: 7" in text


def test_report_flags_source_losing_half_its_catalogue(tmp_path: Path):
    """Обрезанная выгрузка поставщика — 100 строк вместо 13 756 — должна кричать."""
    text = write_report_md(
        tmp_path / "report.md",
        [SourceStats("promet", "Промет", "on", "p.xlsx", 100, 0, 100,
                     previous_count=13756, deleted=13656)],
        {},
    )
    assert "ВНИМАНИЕ" in text
    assert "13656" in text or "13 656" in text


def test_report_quiet_about_ordinary_deletions(tmp_path: Path):
    text = write_report_md(
        tmp_path / "report.md",
        [SourceStats("promet", "Промет", "on", "p.xlsx", 13756, 0, 13756,
                     previous_count=13756, deleted=12)],
        {},
    )
    assert "ВНИМАНИЕ" not in text


def test_report_table_shows_skipped_rows(tmp_path: Path):
    text = write_report_md(
        tmp_path / "report.md",
        [SourceStats("gur", "Гуриненко", "on", "p.xlsx", 1261, 0, 1261, skipped=78)],
        {},
    )
    assert "Пропущено" in text
    assert "| 78 |" in text


def test_changes_heading_is_separated_from_the_table(tmp_path: Path):
    """На чистом прогоне заголовок вплотную к таблице проглатывается разметкой."""
    text = write_report_md(
        tmp_path / "report.md",
        [SourceStats("promet", "Промет", "on", "p.xlsx", 10, 0, 10)],
        {"ooo_tlt": DiffStats(new=1)},
    )
    lines = text.splitlines()
    heading = lines.index("## Изменения относительно прошлой выгрузки")
    assert lines[heading - 1] == ""
