from pathlib import Path

import pytest

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
