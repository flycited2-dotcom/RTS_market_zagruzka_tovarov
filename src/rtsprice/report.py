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
    # Удаления, отправленные в файл, но ещё не подтверждённые командой uploaded.
    pending: int = 0


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
                f"без изменений: {d.unchanged}, снимается: {d.deleted}, "
                f"ожидают подтверждения: {d.pending}"
            )

    text = "\n".join(lines) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text
