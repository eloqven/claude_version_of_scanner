#!/usr/bin/env bash
# Loop wrapper for the daily V3 archive research scan on the research VM.
# Each pass runs collector_runner.py v3 without a fixed start/end so the
# scanner resolves the window dynamically from the archive watermark (day
# after the last completed unit) up to the latest archive date on disk. The
# V3 checkpoint store skips unchanged, already-successful (symbol,date) units
# and retries failed/unavailable ones, so repeat passes are idempotent and
# only newly-available archive data is processed. The loop sleeps between
# passes; systemd Restart=always brings it back if it crashes.
#
# Research-only: no order placement, no credentials, no trading.

set -u

REPO_DIR=/home/andrei/agent/projects/claude-scanner-cloud
RECEIPTS_DIR="${RESEARCH_SCANNER_RECEIPTS_DIR:-/data/scanner/receipts}"
DATA_DIR="${RESEARCH_SCANNER_DATA_DIR:-/data/scanner}"
SYMBOLS="${RESEARCH_SCANNER_V3_SYMBOLS:-BTCUSDT}"
PASS_SLEEP_S="${RESEARCH_SCANNER_V3_SLEEP_S:-21600}"
PYTHON="${RESEARCH_SCANNER_PYTHON:-/usr/bin/python3}"

mkdir -p "${RECEIPTS_DIR}"

while true; do
  "${PYTHON}" "${REPO_DIR}/collector_runner.py" \
    --python "${PYTHON}" \
    --receipts-dir "${RECEIPTS_DIR}" \
    --data-dir "${DATA_DIR}" \
    v3 --symbols "${SYMBOLS}"
  # Non-zero only means "this pass failed"; keep the loop and retry next pass.
  sleep "${PASS_SLEEP_S}"
done
