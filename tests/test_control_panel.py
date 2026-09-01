"""Tests for the research control panel (offline logic, no terminal)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

import research_control_panel as ctl


class RetentionTests(unittest.TestCase):
    def test_parse_plain_hours(self) -> None:
        self.assertEqual(ctl.parse_retention_hours("48"), 48)

    def test_parse_h_suffix(self) -> None:
        self.assertEqual(ctl.parse_retention_hours("2h"), 2)

    def test_parse_bounds_min(self) -> None:
        self.assertEqual(ctl.parse_retention_hours("0"), ctl.RETENTION_MIN_H)
        # A naked digit is extracted; only the final value is bounded.
        self.assertEqual(ctl.parse_retention_hours("-5"), 5)

    def test_parse_bounds_max(self) -> None:
        self.assertEqual(ctl.parse_retention_hours("99"), ctl.RETENTION_MAX_H)

    def test_parse_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            ctl.parse_retention_hours("abc")

    def test_bounded_retention(self) -> None:
        self.assertEqual(ctl._bounded_retention(0), 1)
        self.assertEqual(ctl._bounded_retention(90), 72)
        self.assertEqual(ctl._bounded_retention(24), 24)


class ConfigTests(unittest.TestCase):
    def test_save_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "panel.json"
            cfg = ctl.PanelConfig(retention_hours=12)
            cfg.save(path)
            loaded = ctl.PanelConfig.load(path)
            self.assertEqual(loaded.retention_hours, 12)

    def test_load_defaults_on_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded = ctl.PanelConfig.load(Path(tmp) / "nope.json")
            self.assertEqual(loaded.retention_hours, ctl.RETENTION_DEFAULT_H)

    def test_load_bounds_crazy_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "panel.json"
            path.write_text('{"retention_hours": 9999}', encoding="utf-8")
            loaded = ctl.PanelConfig.load(path)
            self.assertEqual(loaded.retention_hours, ctl.RETENTION_MAX_H)


class SnapshotStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = ctl.open_monitor_db(Path(self._tmp.name) / "monitor.db")

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _snap(self, ts: str) -> dict:
        return {"ts": ts, "ram_total_mb": 3891, "ram_used_mb": 1500,
                "ram_avail_mb": 2300, "cpu_pct": 5.0, "procs": [],
                "services": {"live": "active", "v3": "inactive"},
                "receipts": {"live": {"result": "success"}}}

    def test_record_and_read(self) -> None:
        ctl.record_snapshot(self.db, self._snap("2026-08-31T10:00:00Z"), 48)
        ctl.record_snapshot(self.db, self._snap("2026-08-31T11:00:00Z"), 48)
        snaps = ctl.read_snapshots(self.db, limit=5)
        self.assertEqual(len(snaps), 2)
        self.assertEqual(snaps[0].ts, "2026-08-31T11:00:00Z")

    def test_purge_only_expired(self) -> None:
        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=50)).strftime("%Y-%m-%dT%H:%M:%SZ")
        fresh = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ctl.record_snapshot(self.db, self._snap(old), 48)
        ctl.record_snapshot(self.db, self._snap(fresh), 48)
        deleted = ctl.purge_expired(self.db, 48, now=now)
        self.assertEqual(deleted, 1)
        remaining = ctl.read_snapshots(self.db, limit=10)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].ts, fresh)


class OverridesTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live.env"
            values = {"RESEARCH_SCANNER_MAX_SCAN": "50",
                      "RESEARCH_SCANNER_LIVE_SLEEP_S": "3600"}
            ctl.write_overrides_at(path, values)
            loaded = ctl.read_overrides_at(path, {"RESEARCH_SCANNER_MAX_SCAN": "20"})
            self.assertEqual(loaded["RESEARCH_SCANNER_MAX_SCAN"], "50")

    def test_defaults_used_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live.env"  # does not exist
            loaded = ctl.read_overrides_at(path, {"RESEARCH_SCANNER_MAX_SCAN": "20"})
            self.assertEqual(loaded["RESEARCH_SCANNER_MAX_SCAN"], "20")


if __name__ == "__main__":
    unittest.main()
