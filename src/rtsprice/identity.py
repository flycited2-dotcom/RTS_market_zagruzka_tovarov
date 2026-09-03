"""Стабильное соответствие «источник плюс артикул» и числового идентификатора РТС."""
from __future__ import annotations

import csv
from pathlib import Path

BLOCK = 10_000_000


def prefix_of(rts_id: int) -> int:
    return int(rts_id) // BLOCK


class IdMap:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._by_key: dict[tuple[str, str], int] = {}
        self._max_in_prefix: dict[int, int] = {}
        self._dirty = False
        if self.path.exists():
            with self.path.open(encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    rts_id = int(row["rts_id"])
                    self._by_key[(row["source"], row["article"])] = rts_id
                    p = prefix_of(rts_id)
                    self._max_in_prefix[p] = max(self._max_in_prefix.get(p, 0), rts_id % BLOCK)
        # Сколько соответствий было в файле на момент загрузки. Счётчики
        # нумерации восстанавливаются только отсюда, поэтому пустая карта при
        # непустой витрине — не «первый запуск», а потеря состояния.
        self.loaded_count = len(self._by_key)

    def get_or_create(self, source: str, prefix: int, article: str) -> int:
        if not 1 <= int(prefix) <= 99:
            raise ValueError(f"префикс источника должен быть от 1 до 99, получено {prefix}")
        key = (source, str(article))
        if key in self._by_key:
            return self._by_key[key]
        nxt = self._max_in_prefix.get(int(prefix), 0) + 1
        if nxt >= BLOCK:
            raise ValueError(f"исчерпан диапазон идентификаторов для префикса {prefix}")
        rts_id = int(prefix) * BLOCK + nxt
        self._by_key[key] = rts_id
        self._max_in_prefix[int(prefix)] = nxt
        self._dirty = True
        return rts_id

    def save(self) -> None:
        if not self._dirty and self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["source", "article", "rts_id"])
            for (source, article), rts_id in sorted(self._by_key.items(), key=lambda kv: kv[1]):
                writer.writerow([source, article, rts_id])
        tmp.replace(self.path)
        self._dirty = False
