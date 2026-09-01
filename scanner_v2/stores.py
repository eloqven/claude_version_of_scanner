"""Operational V2 scan storage and a deliberately no-op research seam."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY,
    started_at_us INTEGER NOT NULL,
    cutoff_us INTEGER NOT NULL,
    strategy_version TEXT NOT NULL,
    indicator_version TEXT NOT NULL,
    parameter_hash TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    completed_at_us INTEGER
);
CREATE TABLE IF NOT EXISTS pair_scans (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES scan_runs(id),
    symbol TEXT NOT NULL,
    signal_state TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


class ScanStore:
    """SQLite persistence isolated from V1's scanner.db schema."""

    def __init__(self, path: str = "scanner_v2.db") -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(_SCHEMA)
        self.connection.commit()

    def start_run(self, *, started_at_us: int, cutoff_us: int, strategy_version: str,
                  indicator_version: str, parameter_hash: str,
                  provenance: Mapping[str, Any]) -> int:
        cursor = self.connection.execute(
            """INSERT INTO scan_runs
               (started_at_us, cutoff_us, strategy_version, indicator_version,
                parameter_hash, provenance_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (started_at_us, cutoff_us, strategy_version, indicator_version,
             parameter_hash, json.dumps(dict(provenance), sort_keys=True, default=str)),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def record_pair(self, run_id: int, symbol: str, signal_state: str,
                    payload: Mapping[str, Any]) -> None:
        """Insert one pair result and commit it for existing callers."""
        self.record_pairs(run_id, ((symbol, signal_state, payload),))

    def record_pairs(self, run_id: int,
                     pairs: Iterable[tuple[str, str, Mapping[str, Any]]]) -> None:
        """Insert pair results atomically, committing the complete batch once."""
        values = tuple(
            self._pair_values(run_id, symbol, signal_state, payload)
            for symbol, signal_state, payload in pairs
        )
        with self.connection:
            self.connection.executemany(
                "INSERT INTO pair_scans (run_id, symbol, signal_state, payload_json) "
                "VALUES (?, ?, ?, ?)",
                values,
            )

    @staticmethod
    def _pair_values(run_id: int, symbol: str, signal_state: str,
                     payload: Mapping[str, Any]) -> tuple[int, str, str, str]:
        if signal_state not in {"ACTIVE", "INACTIVE"}:
            raise ValueError("signal_state must be ACTIVE or INACTIVE")
        return (
            run_id,
            symbol,
            signal_state,
            json.dumps(dict(payload), sort_keys=True, default=str),
        )

    def finish_run(self, run_id: int, completed_at_us: int) -> None:
        self.connection.execute(
            "UPDATE scan_runs SET completed_at_us=? WHERE id=?", (completed_at_us, run_id))
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def history_rows(path: str):
        """Read run history without creating or modifying a V2 database."""
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            return connection.execute(
                "SELECT id, started_at_us, cutoff_us, strategy_version, completed_at_us "
                "FROM scan_runs ORDER BY id DESC"
            ).fetchall()
        finally:
            connection.close()


class ResearchStore:
    """Future research storage seam; intentionally no-op in V2 initial delivery."""

    def record(self, trace: Any) -> None:
        del trace
