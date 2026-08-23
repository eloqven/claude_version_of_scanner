#!/usr/bin/env bash
#
# 48-hour hourly scan harness for the Binance Spot Scanner.
# Runs V1 + V2 (live REST) and V3 (archive-backed) once per hour for
# ITERATIONS cycles, saving per-iteration outputs and a final report.
#
# Override any variable on the command line, e.g.:
#   MAX_SCAN=50 ITERATIONS=48 SYMBOLS=BTCUSDT,ETHUSDT bash run_48h_scan.sh
#
set -u

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

SYMBOLS="${SYMBOLS:-BTCUSDT,ETHUSDT}"
V3_SYMBOLS="${V3_SYMBOLS:-BTCUSDT}"
V3_START="${V3_START:-2026-08-10}"
V3_END="${V3_END:-2026-08-10}"
MAX_SCAN="${MAX_SCAN:-20}"
ITERATIONS="${ITERATIONS:-48}"
SLEEP_S="${SLEEP_S:-3600}"

export PYTHONUNBUFFERED=1

RESULTS_DIR="${RESULTS_DIR:-results/48h}"
mkdir -p "$RESULTS_DIR"

# Bootstrap the V3 1s archive once. Dates that 404 on data.binance.vision are
# skipped; already-validated files are not re-downloaded.
echo "[$(date -u)] Bootstrapping V3 archive ${V3_SYMBOLS} ${V3_START}..${V3_END}"
python3 binance_1s_scraper.py --symbols "$V3_SYMBOLS" --start "$V3_START" --end "$V3_END" || true

MANIFEST="$RESULTS_DIR/manifest.tsv"
echo -e "iteration\tstart_utc\tv1_exit\tv2_exit\tv3_exit\tv3_events" > "$MANIFEST"

for i in $(seq 1 "$ITERATIONS"); do
  ts=$(date -u +%Y%m%d_%H%M%S)
  run_dir="$RESULTS_DIR/run_$ts"
  mkdir -p "$run_dir"
  echo "[$(date -u)] === Iteration $i/$ITERATIONS (dir $run_dir) ==="

  python3 binance_scanner_v1.py --max-scan "$MAX_SCAN" > "$run_dir/v1.log" 2>&1
  v1_exit=$?

  python3 binance_scanner_v2.py --max-scan "$MAX_SCAN" > "$run_dir/v2.log" 2>&1
  v2_exit=$?

  python3 binance_scanner_v3.py --symbols "$V3_SYMBOLS" --start "$V3_START" --end "$V3_END" > "$run_dir/v3.log" 2>&1
  v3_exit=$?
  v3_events=$(grep -c "V3_EVENT" "$run_dir/v3.log" || true)
  grep "V3_EVENT" "$run_dir/v3.log" > "$run_dir/v3_events.jsonl" || true

  echo -e "$i\t$ts\t$v1_exit\t$v2_exit\t$v3_exit\t$v3_events" >> "$MANIFEST"
  echo "[$(date -u)] iteration $i done: v1=$v1_exit v2=$v2_exit v3=$v3_exit events=$v3_events"

  if [ "$i" -lt "$ITERATIONS" ]; then
    sleep "$SLEEP_S"
  fi
done

python3 - "$RESULTS_DIR" "$MANIFEST" <<'PY'
import sys, os
res_dir, manifest = sys.argv[1], sys.argv[2]
rows = []
with open(manifest) as f:
    next(f)
    for line in f:
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 6:
            rows.append(parts)
n = len(rows)
v1_ok = sum(1 for r in rows if r[2] == "0")
v2_ok = sum(1 for r in rows if r[3] == "0")
v3_ok = sum(1 for r in rows if r[4] == "0")
total_events = sum(int(r[5]) for r in rows if r[5].isdigit())
report = f"""# 48-Hour Scan Report

Iterations completed: {n}
V1 succeeded: {v1_ok}/{n}
V2 succeeded: {v2_ok}/{n}
V3 succeeded: {v3_ok}/{n}
Total V3 events captured: {total_events}

Artifacts:
- manifest.tsv            per-iteration exit codes and event counts
- run_<timestamp>/v1.log  V1 console + V1_RESULT records
- run_<timestamp>/v2.log  V2 console + V2_RESULT records
- run_<timestamp>/v3.log  V3 console + V3_EVENT/V3_SUMMARY records
- run_<timestamp>/v3_events.jsonl  extracted V3_EVENT stream

Notes:
- V3 reads the local 1s archive, which (on data.binance.vision) is only
  published for certain historical dates; V3 therefore re-evaluates the same
  archived day(s) each hour rather than fresh ticks.
- V1/V2 hit the live Binance REST API and are rate-limit-safe.
"""
with open(os.path.join(res_dir, "48h_report.md"), "w") as f:
    f.write(report)
print(report)
PY

echo "[$(date -u)] 48h scan complete. Report: $RESULTS_DIR/48h_report.md"
