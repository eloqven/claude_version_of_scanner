# HANDOFF — Fib Matrix V3 branch (ongoing)

> Living handoff for any agent picking up `feat/adaptive-tp-scanner-v2`.
> Last updated: 2026-08-23 ~19:35 UTC. Update this file as state changes.

## TL;DR

The V3 research engine + 1s archive pipeline were **non-functional**; four
critical bugs are now fixed and validated against live Binance. A **48-hour
hourly scan (V1+V2+V3)** is currently running detached (PID 2036) and is
healthy. Tests: **211 OK**.

## What was broken & is now fixed

| # | Sev | File | Bug | Fix |
|---|-----|------|-----|-----|
| 1 | CRITICAL | `scanner_v2/fib_matrix.py` | `build_matrix` queried `5m/8m…` against a `1s`-only `ArchiveCandleSource`, raised, swallowed → 0 events | Fetch `1s`, `resample_candles` per Fibonacci interval |
| 2 | HIGH | `scanner_v2/fib_matrix.py` | `V3Event.symbol` set to an interval string | Thread real `symbol` through `detect_events` |
| 3 | CRITICAL | `binance_1s_scraper.py` | Required a `.CHECKSUM` that Binance **does not publish** for daily klines → never downloaded any archive | Checksum optional; proceed without it |
| 4 | CRITICAL | `binance_scanner_v3.py` | Imported `IndicatorEngine` from wrong module → CLI crashed at startup | Import from `scanner_v2.indicators` |

Also: `scanner_v2/archive.py` `build_archive_url`/`build_local_path` simplified to
use the `date` string directly (cosmetic, correct).

## Test status

`python3 -m unittest discover -s tests` → **211 tests OK** (3 new integration
tests in `tests/test_fib_matrix_v3.py` assert the resample path + symbol).

## 48-hour scan job (running)

- **Command:** `run_48h_scan.sh` (repo root), launched detached via `setsid`.
- **PID:** 2036 (reparented; survives normal shell exit).
- **Config:** `ITERATIONS=48`, `SLEEP_S=3000` (~50 min), `MAX_SCAN=20`,
  `V3_SYMBOLS=BTCUSDT`, `V3_START/END=2026-08-10`.
- **Progress:** 19/48 iterations done as of last check — every iteration
  V1=0 V2=0 V3=0, **938 V3 events** each. Next iteration ~every 50 min.
- **ETA:** ~2026-08-25 04:00 UTC.
- **Outputs:** `results/48h/run_<ts>/` (v1/v2/v3 logs + `v3_events.jsonl`),
  `results/48h/manifest.tsv` (ledger), `results/48h/48h_report.md` (final summary,
  written on completion).

### How to check status
```bash
pgrep -af run_48h_scan            # is PID 2036 alive?
cat results/48h/manifest.tsv      # iterations + exit codes + event counts
tail -3 results/48h/48h_run.log   # recent activity
```

## Known constraints (important for next agent)

- **Archive availability:** `data.binance.vision` only serves 1s daily zips for
  *certain historical dates* (2026-08-10 confirmed; 2026-08-18/08-20 → 404 in this
  env). So V3 re-evaluates the same archived day each hour, not fresh ticks. This
  is a data-source limit, not a code bug.
- **V3 per-run cost:** ~10 min/symbol for a full day (matrix rebuilt per 1m
  candle, re-reads 1s archive each build). Follow-up: cache the 1s→interval
  resample across evaluations in a day.
- **Session supervision:** this agent can't stay alive 48h; the run is a detached
  background process. If the host reaps it, `manifest.tsv` shows completed
  iterations and it can be re-launched manually with the same script + env vars.

## Review docs

- `docs/reviews/code-review-v3-20260822.md` — full code-review findings (incl.
  the two extra bugs found during live validation).
- `docs/reviews/doc-review-plans-20260822.md` — planning/requirements review.
- `results/48h/thorough_report.md` — detailed session report (uncommitted, in
  `results/`, which is gitignored).

## Next steps

1. Let the 48h job finish; review `48h_report.md` + per-hour `v3_events.jsonl`.
2. Consider broadening V3 symbols/dates (needs archive dates that exist) and the
   resample-cache optimization.
3. These fixes are committed (see latest commit). `results/` and
   `COMPOUND_REVIEW_20260822.md` were intentionally left uncommitted (runtime
   artifacts / stale).
