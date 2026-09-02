from pathlib import Path

from rtsprice.render import COLUMN_COUNT, C_DELETE, C_ID, C_NAME, C_PRICE
from rtsprice.state import deletion_rows, load_snapshot, save_snapshot


def _row(rts_id: int, name: str = "Товар") -> list[object]:
    row: list[object] = [None] * COLUMN_COUNT
    row[C_ID] = rts_id
    row[C_NAME] = name
    row[C_PRICE] = 100.0
    return row


def test_snapshot_round_trip(tmp_path: Path):
    p = tmp_path / "last.csv"
    save_snapshot(p, [_row(100_000_001), _row(100_000_002, "Второй")])
    loaded = load_snapshot(p)
    assert set(loaded) == {100_000_001, 100_000_002}
    assert loaded[100_000_002][C_NAME] == "Второй"
    assert loaded[100_000_001][C_PRICE] == 100.0


def test_load_snapshot_missing_file_is_empty(tmp_path: Path):
    assert load_snapshot(tmp_path / "нет.csv") == {}


def test_deletion_rows_marks_disappeared_positions():
    previous = {100_000_001: _row(100_000_001), 100_000_002: _row(100_000_002)}
    rows = deletion_rows(previous, current_ids={100_000_001}, keep_prefixes=set())
    assert len(rows) == 1
    assert rows[0][C_ID] == 100_000_002
    assert rows[0][C_DELETE] == 1


def test_deletion_rows_skips_frozen_prefixes():
    previous = {100_000_001: _row(100_000_001), 110_000_001: _row(110_000_001)}
    rows = deletion_rows(previous, current_ids=set(), keep_prefixes={11})
    assert [r[C_ID] for r in rows] == [100_000_001]


def test_deletion_rows_empty_when_nothing_disappeared():
    previous = {100_000_001: _row(100_000_001)}
    assert deletion_rows(previous, {100_000_001}, set()) == []
