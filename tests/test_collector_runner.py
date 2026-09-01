"""Tests for the append-only collector runner and run-receipt ledger."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from collector_runner import (
    append_receipt,
    config_hash,
    current_git_sha,
    read_receipts,
    run_cycle,
    _acquire_lock,
    _v1_candidate_count,
)


class AppendReceiptTests(unittest.TestCase):
    def test_appends_not_truncates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live.jsonl"
            append_receipt(path, {"a": 1, "workload": "live"})
            append_receipt(path, {"a": 2, "workload": "live"})
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2, "both receipts must be preserved")
            self.assertEqual(len(read_receipts(path)), 2)

    def test_ignores_trailing_partial_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live.jsonl"
            append_receipt(path, {"a": 1, "workload": "live"})
            with open(path, "a", encoding="utf-8") as fh:
                fh.write('{"a": 2, "workload": "' + "partial-no-newline")
            receipts = read_receipts(path)
            self.assertEqual(len(receipts), 1, "partial last line is not counted")
            self.assertEqual(receipts[0]["a"], 1)

    def test_receipt_has_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipts_dir = Path(tmp)
            lock_path = receipts_dir / "live.lock"
            receipt_file = receipts_dir / "live.jsonl"

            counter = {"calls": 0}

            def invoke():
                counter["calls"] += 1
                return {
                    "workload": "live",
                    "v1": {"ok": True, "exit_code": 0},
                    "v2": {"ok": True, "exit_code": 0},
                }

            receipt = run_cycle("live", invoke, lock_path, receipt_file, max_scan=20)

            for field in ("workload", "run_id", "git_sha", "config_hash",
                          "utc_start", "utc_end", "duration_s", "result"):
                self.assertIn(field, receipt, f"missing required field {field}")
            self.assertEqual(receipt["result"], "success")
            self.assertEqual(counter["calls"], 1, "invoke must run exactly once under lock")

    def test_failed_invoke_records_failed_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live.lock"
            receipt_file = Path(tmp) / "live.jsonl"

            def invoke():
                return {
                    "workload": "live",
                    "v1": {"ok": False, "exit_code": 1, "error": "boom"},
                    "v2": {"ok": True, "exit_code": 0},
                }

            receipt = run_cycle("live", invoke, path, receipt_file, max_scan=20)
            self.assertEqual(receipt["result"], "failed")
            self.assertEqual(len(read_receipts(receipt_file)), 1)

    def test_config_hash_deterministic(self) -> None:
        self.assertEqual(config_hash("live", max_scan=20), config_hash("live", max_scan=20))
        self.assertNotEqual(config_hash("live", max_scan=20), config_hash("live", max_scan=21))


class LockTests(unittest.TestCase):
    def test_lock_is_mutually_exclusive_across_threads(self) -> None:
        # Uses the mkdir fallback on non-POSIX platforms and fcntl on POSIX;
        # either way a second lock while the first is held must block.
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "w.lock"
            in_critical = threading.Event()
            allow_release = threading.Event()
            released = {"order": []}

            def worker_a():
                lock = _acquire_lock(lock_path)
                try:
                    released["order"].append("a")
                    in_critical.set()
                    allow_release.wait(timeout=5)
                finally:
                    lock.release()

            ta = threading.Thread(target=worker_a)
            ta.start()
            self.assertTrue(in_critical.wait(timeout=5), "worker A should enter")

            # B must not be able to acquire until A releases.
            b_acquired = {"ok": False}

            def worker_b():
                lock = _acquire_lock(lock_path)
                try:
                    b_acquired["ok"] = True
                finally:
                    lock.release()

            tb = threading.Thread(target=worker_b)
            tb.start()
            time.sleep(0.5)
            self.assertFalse(b_acquired["ok"], "lock must block second acquirer")
            allow_release.set()
            tb.join(timeout=5)
            self.assertTrue(b_acquired["ok"], "worker B should acquire after release")
            ta.join(timeout=5)


class V1CandidateCountTests(unittest.TestCase):
    def test_missing_db_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_v1_candidate_count(Path(tmp) / "nope.db"), 0)

    def test_latest_run_n_candidates(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "scanner.db"
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "CREATE TABLE scan_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " n_candidates INTEGER)"
                )
                conn.execute("INSERT INTO scan_runs (n_candidates) VALUES (3)")
                conn.execute("INSERT INTO scan_runs (n_candidates) VALUES (7)")
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(_v1_candidate_count(db_path), 7,
                             "must reflect the latest run, not an earlier one")

    def test_null_n_candidates_returns_zero(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "scanner.db"
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "CREATE TABLE scan_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " n_candidates INTEGER)"
                )
                conn.execute("INSERT INTO scan_runs (id, n_candidates) VALUES (1, NULL)")
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(_v1_candidate_count(db_path), 0)


if __name__ == "__main__":
    unittest.main()
