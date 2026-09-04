"""Команды управления сборкой прайс-листов."""
from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

from .config import (
    VALID_STATES, load_companies, load_photo_server, load_sources, load_stoplist,
)
from .pipeline import Paths, build, clear_pending
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


def _photo_publisher(root: Path, dry_run: bool):
    """Издатель фотографий, если сервер настроен и сборка не пробная.

    При `check` соединение не открывается: команда обязана быть без
    побочных действий, а вход по SSH — это уже действие.
    """
    if dry_run:
        return None
    server = load_photo_server(root / "photos.yml")
    if server is None:
        return None
    from .photos import SftpPublisher

    return SftpPublisher(
        host=server.host, user=server.user, key_path=server.key_path,
        remote_root=server.remote_root, base_url=server.base_url, port=server.port,
    )


def _photo_sink(root: Path):
    """Куда складывать полученные снимки.

    С настроенным сервером — сразу туда, на рабочей машине остаётся только
    карта ссылок: у одного Бринэкса это около трёх гигабайт файлов, которые
    нужны ровно один раз. Без сервера остаётся каталог photos/.
    """
    from .photobank import DiskSink, PublishSink

    server = load_photo_server(root / "photos.yml")
    if server is None:
        print("photos.yml не найден: снимки лягут в photos/ и займут около 3 ГБ")
        return DiskSink(root / "photos"), None
    publisher = _photo_publisher(root, dry_run=False)
    return (
        PublishSink(publisher, root / "state" / "photos.json",
                    root / "photos" / "urls.json"),
        publisher,
    )


def _image_index(api: str, root: Path, args):
    """Поставщик ссылок на снимки и то, что нужно закрыть после работы."""
    if api == "brinex":
        from .brinex import BrinexClient, SshOpener, load_brinex

        config = load_brinex(root / "brinex.yml")
        if config is None:
            raise SystemExit("не найден brinex.yml — нет доступа к API поставщика")
        opener = None
        if config.via_photo_server:
            server = load_photo_server(root / "photos.yml")
            if server is None:
                raise SystemExit("brinex.yml просит идти через сервер фотографий, "
                                 "но photos.yml не найден")
            opener = SshOpener(host=server.host, user=server.user,
                               key_path=server.key_path, port=server.port)
            print(f"запросы к API идут через {server.host}: "
                  f"токен привязан к его адресу")
        return BrinexClient(config, **({"opener": opener} if opener else {})), opener

    if api == "openfoodfacts":
        from .openfoodfacts import DUMP_URL, DumpIndex

        if args.dump:
            dumps = [Path(p) for p in args.dump]
        else:
            dumps = sorted((root / "input" / "openfoodfacts").glob("*.csv.gz"))
        missing = [p for p in dumps if not p.exists()]
        if missing or not dumps:
            raise SystemExit(
                f"не найдены выгрузки базы"
                f"{': ' + ', '.join(str(p) for p in missing) if missing else ''}. "
                f"Положите их в input/openfoodfacts/ или укажите ключом --dump. "
                f"Пищевая: {DUMP_URL} (около 1,3 ГБ); рядом стоит взять "
                f"openbeautyfacts и openproductsfacts — они на два порядка меньше "
                f"и дают четверть находок сверху. "
                f"Опрашивать их API нельзя: он отвечает 429 уже при одном запросе "
                f"в секунду, и отказ неотличим от «товара нет в базе»."
            )
        return DumpIndex(dumps, log=print), None

    raise SystemExit(f"неизвестный источник снимков: {api!r}")


def _fetch_photos(root: Path, args) -> int:
    """Получить фотографии у поставщиков и опубликовать.

    Отдельная команда, а не часть сборки: это сетевая работа на часы, её
    прерывают и продолжают, а сборка обязана оставаться быстрой и работать
    без интернета. Полученное подхватит следующая сборка.
    """
    from .photobank import PublishSink, fetch_photos, targets
    from .readers import read_source

    sources = load_sources(root / "sources.yml")
    chosen = args.source or [c for c, s in sources.items() if s.photo_api]
    unknown = [c for c in chosen if c not in sources]
    if unknown:
        raise SystemExit(f"неизвестные источники: {', '.join(unknown)}")
    without_api = [c for c in chosen if not sources[c].photo_api]
    if without_api:
        print(f"без источника снимков, пропускаю: {', '.join(without_api)}")

    # Источники сгруппированы по поставщику снимков: одно соединение и один
    # проход выгрузки на всю группу, а не на каждый прайс.
    groups: dict[str, list[str]] = {}
    for code in chosen:
        api = sources[code].photo_api
        if api:
            groups.setdefault(api, []).append(code)
    if not groups:
        print("нечего делать: ни один из выбранных источников не объявил photo_api")
        return 1

    sink, publisher = _photo_sink(root)
    status = 0
    try:
        for api, codes in groups.items():
            print()
            print(f"=== {api} ===")
            wanted = []
            for code in codes:
                cfg = sources[code]
                rows, _ = read_source(cfg, find_source_file(str(root / cfg.file_glob)))
                found = targets(cfg, rows, key=cfg.photo_key)
                print(f"{cfg.title}: строк {len(rows)}, с ключом {len(found)}")
                wanted.extend(found)

            index, closable = _image_index(api, root, args)
            try:
                report = fetch_photos(wanted, index, sink, limit=args.limit, log=print)
            finally:
                close = getattr(closable, "close", None)
                if close:
                    close()

            print()
            for line in report.lines():
                print(line)
            for failure in report.failed[:20]:
                print("  сбой:", failure)
            if len(report.failed) > 20:
                print(f"  … и ещё {len(report.failed) - 20}")
            if report.asked and not report.saved:
                status = 1
    finally:
        if isinstance(sink, PublishSink):
            print()
            print(f"загружено на сервер файлов: {sink.uploaded}")
        close = getattr(publisher, "close", None)
        if close:
            close()
    return status


def _run_build(root: Path, args, dry_run: bool) -> int:
    sources = load_sources(root / "sources.yml")
    companies = load_companies(root / "companies")
    publisher = _photo_publisher(root, dry_run)
    try:
        result = build(
            Paths(root=root), sources, companies,
            only=args.only, skip=args.skip, company_codes=args.company,
            stoplist=load_stoplist(root / "stoplist.csv"),
            photo_publisher=publisher, dry_run=dry_run,
        )
    finally:
        close = getattr(publisher, "close", None)
        if close:
            close()
    for s in result.stats:
        print(f"{s.title}: Прочитано {s.read}, отсеяно {s.rejected}, принято {s.accepted}")
    for company, diff in result.diffs.items():
        print(f"{company}: новых {diff.new}, изменилась цена {diff.changed_price}, "
              f"снимается {diff.deleted}, ожидают подтверждения {diff.pending}")
    for company, files in result.files.items():
        for path in files:
            print(f"записан {path}")

    # Включённый источник, не давший ни строки, — отказ, а не результат:
    # файл не положили или поставщик переименовал колонку. Ненулевой код
    # позволяет ставить `check` воротами перед сборкой.
    silent = [s.title for s in result.stats if s.state == "on" and s.read == 0]
    if silent:
        print(f"ВНИМАНИЕ: ни одной строки не прочитано из источников: {', '.join(silent)}")
        return 1
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
    p = sub.add_parser("uploaded", help="файл компании загружен: забыть удаления")
    p.add_argument("company")
    p = sub.add_parser("photos", help="получить фотографии у поставщиков")
    p.add_argument("--source", nargs="*", help="только эти источники")
    p.add_argument("--limit", type=int, help="ограничить число позиций (для пробы)")
    p.add_argument("--dump", nargs="*",
                   help="выгрузки Open Food Facts (.csv.gz), можно несколько")

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    if args.command == "status":
        print(format_status(root))
        return 0
    if args.command == "uploaded":
        count = clear_pending(Paths(root=root), args.company)
        print(f"{args.company}: подтверждено удалений — {count}")
        return 0
    if args.command == "photos":
        return _fetch_photos(root, args)
    if args.command in ("on", "off", "freeze"):
        state = "frozen" if args.command == "freeze" else args.command
        set_state(root / "sources.yml", args.source, state)
        print(f"{args.source}: состояние изменено на {state}")
        return 0
    return _run_build(root, args, dry_run=args.command == "check")
