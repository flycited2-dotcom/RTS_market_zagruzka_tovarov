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
        'promet:\n'
        '    title: "Промет"\n'
        '    prefix: 10\n'
        '    file: "input/promet/*.xlsx"\n',
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
    _price_book(project, [["A-1", "Товар", 100]])
    assert main(["--root", str(project), "check"]) == 0
    assert not (project / "output").exists()
    assert "Прочитано" in capsys.readouterr().out


def test_uploaded_clears_pending_deletions(project: Path, capsys):
    from rtsprice.render import COLUMN_COUNT, C_ID
    from rtsprice.state import load_snapshot, save_snapshot

    row = [None] * COLUMN_COUNT
    row[C_ID] = 100_000_001
    pending = project / "state" / "pending_delete_ooo_tlt.csv"
    save_snapshot(pending, [row])

    assert main(["--root", str(project), "uploaded", "ooo_tlt"]) == 0
    assert not pending.exists()
    assert "1" in capsys.readouterr().out
    assert load_snapshot(pending) == {}


def _price_book(project: Path, rows: list[list]) -> None:
    directory = project / "input" / "promet"
    directory.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Артикул", "Наименование", "Цена"])
    for row in rows:
        ws.append(row)
    wb.save(directory / "price.xlsx")


def test_check_returns_nonzero_when_an_active_source_read_nothing(project: Path, capsys):
    """check должен годиться как ворота: молчащий источник — не успех."""
    assert main(["--root", str(project), "check"]) != 0
    assert "Промет" in capsys.readouterr().out


def test_check_returns_zero_when_every_active_source_read_rows(project: Path):
    _price_book(project, [["A-1", "Товар", 100]])
    assert main(["--root", str(project), "check"]) == 0


def test_build_returns_nonzero_when_an_active_source_read_nothing(project: Path):
    assert main(["--root", str(project), "build"]) != 0
