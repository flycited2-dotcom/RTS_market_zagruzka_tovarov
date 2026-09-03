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
from .render import C_ARTICLE, C_ID, C_PRICE, render_row
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


def pending_path(paths: Paths, company_code: str) -> Path:
    return paths.state / f"pending_delete_{company_code}.csv"


def clear_pending(paths: Paths, company_code: str) -> int:
    """Забыть неподтверждённые удаления компании: файл загружен на площадку."""
    path = pending_path(paths, company_code)
    count = len(load_snapshot(path))
    path.unlink(missing_ok=True)
    return count


def merge_pending(
    pending: dict[int, list[object]],
    removals: list[list[object]],
    current_ids: set[int],
) -> list[list[object]]:
    """Слить неподтверждённые удаления с найденными в этом запуске.

    Удаление — это указание площадке, а не свершившийся факт: файл могли ещё
    не загрузить. Снимок же после первой сборки уже не помнит снятую позицию,
    поэтому вторая сборка того же дня перезаписала бы файл без строки
    удаления, и позиция осталась бы на витрине по старой цене навсегда.

    Повторить удаление позиции, которую площадка уже сняла, ничего не стоит:
    импорт просто не найдёт её. Потерять удаление стоит проданного по
    неверной цене товара. Эта асимметрия и есть причина всей конструкции —
    здесь дублируем, но не теряем.
    """
    merged = dict(pending)
    for row in removals:
        merged[int(row[C_ID])] = row  # свежая строка вытесняет старую
    # Позиция, вернувшаяся в прайс, больше не удаляется: иначе один файл
    # содержал бы для неё и строку обновления, и строку снятия.
    for rts_id in current_ids:
        merged.pop(rts_id, None)
    return [row for _, row in sorted(merged.items())]


def _guard_id_map_present(
    map_path: Path,
    idmap: IdMap,
    snapshots: dict[str, dict[int, list[object]]],
) -> None:
    """Отказать в сборке, если карта идентификаторов утрачена.

    Счётчики нумерации восстанавливаются только из самой карты, поэтому при
    пустом файле нумерация начинается заново — и идентификатор, известный
    площадке как артикул A, достаётся артикулу B. Следующий импорт перепишет
    существующие позиции чужими названиями и ценами, а в отчёте это выглядит
    как обычная неделя: «изменилась цена».
    """
    if idmap.loaded_count:
        return
    occupied = [code for code, rows in snapshots.items() if rows]
    if not occupied:
        return
    raise RuntimeError(
        f"{map_path} пуст или отсутствует, а снимки выгрузок ({', '.join(sorted(occupied))}) "
        f"не пусты: карта идентификаторов утрачена. Сборка остановлена — продолжение "
        f"перепривязало бы существующие позиции площадки к другим товарам. "
        f"Восстановите state/ из резервной копии."
    )


def _guard_articles_unchanged(
    snapshots: dict[str, dict[int, list[object]]],
    by_source: dict[str, list[Item]],
) -> None:
    """Отказать, если идентификатор из снимка теперь принадлежит другому артикулу.

    Снимок хранит артикул рядом с идентификатором, так что проверка стоит
    одного сравнения на строку — а ловит ровно тот случай, ради которого
    существует карта: подмену привязки.
    """
    for items in by_source.values():
        for item in items:
            for company_code, previous in snapshots.items():
                row = previous.get(item.rts_id)
                if row is None:
                    continue
                was = row[C_ARTICLE]
                if was is None or str(was) == item.article:
                    continue
                raise RuntimeError(
                    f"идентификатор {item.rts_id} в снимке last_{company_code}.csv "
                    f"принадлежит артикулу {was!r}, а сейчас назначен артикулу "
                    f"{item.article!r} источника {item.source}. Сборка остановлена: "
                    f"импорт переписал бы существующую позицию другим товаром. "
                    f"Проверьте state/id_map.csv."
                )


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
    map_path = paths.state / "id_map.csv"
    idmap = IdMap(map_path)
    okpd2 = load_okpd2(paths.reference / "okpd2.csv")
    units = load_units(paths.reference / "okei.csv")

    targets = [c for c in companies if not company_codes or c in company_codes]
    snapshots = {
        code: load_snapshot(paths.state / f"last_{code}.csv") for code in targets
    }
    _guard_id_map_present(map_path, idmap, snapshots)

    by_source, stats, rejections = collect_items(paths, active, idmap, stoplist or set())
    _guard_articles_unchanged(snapshots, by_source)
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
        previous = snapshots[company_code]
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

        pending_file = pending_path(paths, company_code)
        outstanding = merge_pending(load_snapshot(pending_file), removals, current_ids)
        diff.pending = len(outstanding)
        result.diffs[company_code] = diff

        if dry_run:
            continue

        result.files[company_code] = write_price_file(
            rows + outstanding, paths.output / f"{company_code}.xlsx"
        )
        if outstanding:
            save_snapshot(pending_file, outstanding)
        else:
            pending_file.unlink(missing_ok=True)
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
