"""Restart-safe checkpoint store for the V3 archive research collector.

The store records, per ``(symbol, UTC date)``, the collector version and the
archive source checksum that were successfully processed, so a subsequent run
can:

* skip an unchanged, already-successful unit (never silently replay it);
* retry dates whose data was unavailable or whose run failed;
* record a separately-labelled evaluation when the source checksum or the
  collector version changes (so the same symbol/date is re-processed as a
  distinct unit, never overwriting the prior one).

Research-only helper; no trading, no credentials.
"""

from __future__ import annotations

import hashlib
import sqlite3
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Increment when the V3 collector's processing logic changes so that
# previously recorded successes with an older version are re-evaluated as a
# version change rather than silently skipped.
V3_COLLECTOR_VERSION = "1.0.0"


class Outcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    CHANGED = "changed"  # re-processed as a distinct unit due to source/version change


class EvaluationType(str, Enum):
    FIRST = "first"
    RETRY = "retry"
    CHANGED = "changed"


class V3CheckpointStore:
    """SQLite-backed checkpoint store for V3 (symbol, date) processing units."""

    def __init__(self, db_path: str = "v3_checkpoints.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(db_path)
        self._create_tables()
        self.connection.commit()

    def _create_tables(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS v3_checkpoints (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                checksum TEXT NOT NULL,
                version TEXT NOT NULL,
                outcome TEXT NOT NULL,
                evaluation_type TEXT NOT NULL,
                run_id TEXT,
                processed_at_us INTEGER NOT NULL,
                evaluations INTEGER NOT NULL DEFAULT 0,
                event_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (symbol, date, checksum, version)
            );
            CREATE INDEX IF NOT EXISTS idx_v3ck_symbol_date
                ON v3_checkpoints(symbol, date);
        """)

    def _latest_records(self, symbol: str, date: str) -> List[Tuple]:
        return self.connection.execute(
            "SELECT symbol, date, checksum, version, outcome, evaluation_type,"
            " run_id, processed_at_us, evaluations, event_count"
            " FROM v3_checkpoints WHERE symbol=? AND date=?"
            " ORDER BY processed_at_us DESC, rowid DESC",
            (symbol, date),
        ).fetchall()

    def _latest_success(self, symbol: str, date: str) -> Optional[Tuple]:
        # Both SUCCESS and CHANGED are terminal "completed" outcomes: the unit
        # was fully processed for its (checksum, version), so an identical unit
        # must be skipped rather than replayed.
        row = self.connection.execute(
            "SELECT symbol, date, checksum, version, outcome, evaluation_type,"
            " run_id, processed_at_us, evaluations, event_count"
            " FROM v3_checkpoints WHERE symbol=? AND date=? AND outcome IN (?, ?)"
            " ORDER BY processed_at_us DESC, rowid DESC LIMIT 1",
            (symbol, date, Outcome.SUCCESS.value, Outcome.CHANGED.value),
        ).fetchone()
        return row

    def should_process(self, symbol: str, date: str, checksum: Optional[str],
                       version: str = V3_COLLECTOR_VERSION) -> Dict[str, object]:
        """Decide whether/how the given (symbol, date) unit should be processed.

        Returns a dict with keys ``action`` (one of ``process``, ``skip``,
        ``changed``) and ``reason``. ``checksum`` may be None if the source
        archive file is unavailable.
        """
        resolved_checksum = checksum or "unavailable"
        success = self._latest_success(symbol, date)
        if success is None:
            # No prior success: process a new date, or retry after a
            # failed/unavailable outcome.
            prior = self._latest_records(symbol, date)
            if prior:
                latest_outcome = prior[0][4]
                if latest_outcome in (Outcome.FAILED.value, Outcome.UNAVAILABLE.value):
                    return {"action": "process",
                            "reason": f"retry previous {latest_outcome}"}
            return {"action": "process", "reason": "no prior success"}
        prev_checksum, prev_version = success[2], success[3]
        if prev_checksum == resolved_checksum and prev_version == version:
            return {"action": "skip",
                    "reason": "unchanged checksum and version already succeeded"}
        return {"action": "changed",
                "reason": f"source/version changed (checksum {prev_checksum}->{resolved_checksum},"
                          f" version {prev_version}->{version})"}

    def record(self, symbol: str, date: str, checksum: Optional[str],
               outcome: Outcome, evaluation_type: EvaluationType,
               run_id: Optional[str] = None, processed_at_us: Optional[int] = None,
               evaluations: int = 0, event_count: int = 0) -> None:
        """Record a processing unit outcome for (symbol, date)."""
        resolved_checksum = checksum or "unavailable"
        from datetime import datetime, timezone
        processed_at_us = processed_at_us or \
            int(datetime.now(timezone.utc).timestamp() * 1_000_000)
        self.connection.execute(
            "INSERT OR REPLACE INTO v3_checkpoints"
            " (symbol, date, checksum, version, outcome, evaluation_type,"
            "  run_id, processed_at_us, evaluations, event_count)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, date, resolved_checksum, V3_COLLECTOR_VERSION,
             outcome.value, evaluation_type.value, run_id, processed_at_us,
             evaluations, event_count),
        )
        self.connection.commit()

    def last_completed_date(self) -> Optional[str]:
        """Latest date with any completed (SUCCESS/CHANGED) unit, or None."""
        row = self.connection.execute(
            "SELECT date FROM v3_checkpoints WHERE outcome IN (?, ?)"
            " ORDER BY date DESC LIMIT 1",
            (Outcome.SUCCESS.value, Outcome.CHANGED.value),
        ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self.connection.close()


def archive_checksum(symbol: str, date: str, archive_dir: str) -> Optional[str]:
    """SHA-256 of the local 1s archive ZIP for (symbol, date), or None if absent."""
    base = Path(archive_dir) / "raw" / "spot" / "daily" / "klines" / symbol / "1s"
    zip_path = base / f"{symbol}-1s-{date}.zip"
    if not zip_path.exists():
        return None
    h = hashlib.sha256()
    with open(zip_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
