#!/usr/bin/env bash
# Loop wrapper for the daily V3 archive research scan on the research VM.
# Each pass runs collector_runner.py v3 over the retained archive window.
# The V3 checkpoint store skips unchanged, already-successful (symbol,date)
# units and retries failed/unavailable ones, so repeat passes are idempotent
# and only newly-available archive data is processed. The loop sleeps between
# passes; systemd Restart=always brings it back if it crashes.
#
# Research-only: no order placement, no credentials, no trading.

set -u

REPO_DIR=/home/andrei/agent/projects/claude-scanner-cloud
RECEIPTS_DIR="${RESEARCH_SCANNER_RECEIPTS_DIR:-/home/andrei/agent/data/research-collectors/current/scanner}"
SYMBOLS="${RESEARCH_SCANNER_V3_SYMBOLS:-BTCUSDT}"
START_DATE="${RESEARCH_SCANNER_V3_START:-2026-08-01}"
END_DATE="${RESEARCH_SCANNER_V3_END:-2026-08-31}"
PASS_SLEEP_S="${RESEARCH_SCANNER_V3_SLEEP_S:-21600}"
PYTHON="${RESEARCH_SCANNER_PYTHON:-/usr/bin/python3}"

mkdir -p "${RECEIPTS_DIR}"

while true; do
  "${PYTHON}" "${REPO_DIR}/collector_runner.py" \
    --python "${PYTHON}" \
    --receipts-dir "${RECEIPTS_DIR}" \
    v3 --symbols "${SYMBOLS}" --start "${START_DATE}" --end "${END_DATE}"
  # Non-zero only means "this pass failed"; keep the loop and retry next pass.
  sleep "${PASS_SLEEP_S}"
done
