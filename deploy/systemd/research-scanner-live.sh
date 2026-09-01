#!/usr/bin/env bash
# Loop wrapper for the hourly V1 then V2 live research scan on the research VM.
# Each cycle runs collector_runner.py live (append-only receipts, disk lock),
# then sleeps for the cadence. A non-zero cycle exit does not kill the loop:
# the runner's lock + append-only ledger make a retry safe. systemd
# Restart=always brings the whole loop back if it crashes.
#
# Research-only: no order placement, no credentials, no trading.

set -u

REPO_DIR=/home/andrei/agent/projects/claude-scanner-cloud
RECEIPTS_DIR="${RESEARCH_SCANNER_RECEIPTS_DIR:-/home/andrei/agent/data/research-collectors/current/scanner}"
MAX_SCAN="${RESEARCH_SCANNER_MAX_SCAN:-20}"
CAN_UPLOAD="${RESEARCH_SCANNER_CAN_UPLOAD:-false}"
CYCLE_SLEEP_S="${RESEARCH_SCANNER_LIVE_SLEEP_S:-3600}"
PYTHON="${RESEARCH_SCANNER_PYTHON:-/usr/bin/python3}"

if [ "${CAN_UPLOAD}" != "true" ]; then
  # Untracked binaries are provided by /usr/bin/python3; nothing to upload.
  :
fi

mkdir -p "${RECEIPTS_DIR}"

while true; do
  "${PYTHON}" "${REPO_DIR}/collector_runner.py" \
    --python "${PYTHON}" \
    --receipts-dir "${RECEIPTS_DIR}" \
    live --max-scan "${MAX_SCAN}"
  # Non-zero here only means "this cycle failed"; keep the loop and retry next
  # cycle after sleeping. Exit code intentionally ignored.
  sleep "${CYCLE_SLEEP_S}"
done
