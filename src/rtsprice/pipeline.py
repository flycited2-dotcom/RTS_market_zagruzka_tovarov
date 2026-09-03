# src/rtsprice/pipeline.py
"""Оркестровка полной сборки прайс-листов."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import CompanyConfig, SourceConfig
from .identity import IdMap
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
            path = find_source_file(cfg.file_glob)
        except FileNotFoundError as exc:
            stats.append(SourceStats(cfg.code, cfg.title, cfg.state, "", 0, 0, 0,
                                     {str(exc): 1}))
            continue

        rows = read_source(cfg, path)
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
    result = BuildResult(stats=stats, rejections=rejections)

    resolver = (
        PhotoResolver(paths.photos, photo_publisher, paths.state / "photos.json")
        if photo_publisher else None
    )
    frozen_prefixes = {c.prefix for c in active if c.state == "frozen"}

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

        diff = DiffStats(deleted=0)
        for row in rows:
            rts_id = int(row[C_ID])
            if rts_id not in previous:
                diff.new += 1
            elif previous[rts_id][C_PRICE] != row[C_PRICE]:
                diff.changed_price += 1
            else:
                diff.unchanged += 1

        removals = deletion_rows(previous, current_ids, frozen_prefixes)
        diff.deleted = len(removals)
        result.diffs[company_code] = diff

        if dry_run:
            continue

        result.files[company_code] = write_price_file(
            rows + removals, paths.output / f"{company_code}.xlsx"
        )
        save_snapshot(snapshot_path, rows)

    if not dry_run:
        idmap.save()
        if resolver:
            resolver.save()
        write_errors_xlsx(paths.output / "errors.xlsx", result.rejections, result.issues)
        write_report_md(paths.output / "report.md", result.stats, result.diffs)
    return result
