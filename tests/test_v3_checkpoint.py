"""Tests for the restart-safe V3 checkpoint store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scanner_v2.v3_checkpoint import (
    V3CheckpointStore,
    V3_COLLECTOR_VERSION,
    Outcome,
    EvaluationType,
    archive_checksum,
)


class V3CheckpointStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "v3_checkpoints.db")
        self.store = V3CheckpointStore(self.db)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_new_date_processes(self) -> None:
        decision = self.store.should_process("BTCUSDT", "2026-08-10", "abc")
        self.assertEqual(decision["action"], "process")

    def test_unchanged_success_is_skipped(self) -> None:
        self.store.record(
            "BTCUSDT", "2026-08-10", "abc", Outcome.SUCCESS, EvaluationType.FIRST,
        )
        decision = self.store.should_process("BTCUSDT", "2026-08-10", "abc")
        self.assertEqual(decision["action"], "skip", "unchanged success must be skipped")

    def test_failed_date_is_retried(self) -> None:
        self.store.record(
            "BTCUSDT", "2026-08-10", "abc", Outcome.FAILED, EvaluationType.RETRY,
        )
        decision = self.store.should_process("BTCUSDT", "2026-08-10", "abc")
        self.assertEqual(decision["action"], "process", "failed date must be retried")

    def test_unavailable_date_is_retried(self) -> None:
        self.store.record(
            "BTCUSDT", "2026-08-10", "unavailable", Outcome.UNAVAILABLE,
            EvaluationType.RETRY,
        )
        decision = self.store.should_process("BTCUSDT", "2026-08-10", "abc")
        self.assertEqual(decision["action"], "process", "unavailable date must be retried")

    def test_changed_checksum_is_separately_labelled(self) -> None:
        self.store.record(
            "BTCUSDT", "2026-08-10", "checksum-old", Outcome.SUCCESS, EvaluationType.FIRST,
        )
        decision = self.store.should_process("BTCUSDT", "2026-08-10", "checksum-new")
        self.assertEqual(decision["action"], "changed",
                         "changed checksum must be a separate labelled evaluation")
        # After re-recording as changed, it must be skipped when unchanged again.
        self.store.record(
            "BTCUSDT", "2026-08-10", "checksum-new", Outcome.CHANGED, EvaluationType.CHANGED,
        )
        decision2 = self.store.should_process("BTCUSDT", "2026-08-10", "checksum-new")
        self.assertEqual(decision2["action"], "skip")

    def test_changed_version_is_separately_labelled(self) -> None:
        self.store.record(
            "BTCUSDT", "2026-08-10", "abc", Outcome.SUCCESS, EvaluationType.FIRST,
        )
        decision = self.store.should_process("BTCUSDT", "2026-08-10", "abc",
                                             version="2.0.0")
        self.assertEqual(decision["action"], "changed",
                         "changed collector version must be re-evaluated distinctly")

    def test_symbol_and_date_are_isolated(self) -> None:
        self.store.record(
            "BTCUSDT", "2026-08-10", "abc", Outcome.SUCCESS, EvaluationType.FIRST,
        )
        # Different symbol, same date -> process.
        self.assertEqual(
            self.store.should_process("ETHUSDT", "2026-08-10", "abc")["action"],
            "process",
        )
        # Same symbol, different date -> process.
        self.assertEqual(
            self.store.should_process("BTCUSDT", "2026-08-11", "abc")["action"],
            "process",
        )

    def test_missing_source_checksum_uses_unavailable_key(self) -> None:
        self.store.record(
            "BTCUSDT", "2026-08-10", "unavailable", Outcome.SUCCESS, EvaluationType.FIRST,
        )
        # None checksum resolves to "unavailable" and matches the stored success.
        decision = self.store.should_process("BTCUSDT", "2026-08-10", None)
        self.assertEqual(decision["action"], "skip",
                         "None checksum maps to 'unavailable' and skips matching success")


class ArchiveChecksumTests(unittest.TestCase):
    def test_computes_sha256_of_local_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_dir = tmp
            base = (Path(archive_dir) / "raw" / "spot" / "daily" / "klines"
                    / "BTCUSDT" / "1s")
            base.mkdir(parents=True)
            zip_path = base / "BTCUSDT-1s-2026-08-10.zip"
            zip_path.write_bytes(b"hello archive")
            checksum = archive_checksum("BTCUSDT", "2026-08-10", archive_dir)
            self.assertIsNotNone(checksum)
            self.assertEqual(len(checksum), 64)

    def test_returns_none_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(archive_checksum("BTCUSDT", "2026-08-10", tmp))


if __name__ == "__main__":
    unittest.main()
