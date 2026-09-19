#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=/home/anestishkurti92/mls-predictor-v1
PY="$ROOT/venv/bin/python"
MODE="${1:-daily}"
LOCK="$ROOT/auto_research/state/pipeline.lock"
LOG="$ROOT/auto_research/pipeline.log"
mkdir -p "$ROOT/auto_research/state" "$ROOT/auto_research"

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date -u +%FT%TZ) MLS pipeline skipped: another run holds lock" >> "$LOG"
  exit 0
fi

resource_guard() {
  local mem_kb load
  mem_kb=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
  load=$(awk '{print $1}' /proc/loadavg)
  "$PY" - "$mem_kb" "$load" <<'PY'
import sys
mem=float(sys.argv[1])/1024
load=float(sys.argv[2])
if mem < 1200 or load > 1.70:
    raise SystemExit(1)
print(f"resource_guard PASS mem_available_mb={mem:.0f} load1={load:.2f}")
PY
}

run() {
  local limit="$1"
  shift
  echo "$(date -u +%FT%TZ) RUN $*" | tee -a "$LOG"
  OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 nice -n 14 timeout "$limit" "$@" 2>&1 | tee -a "$LOG"
}

cd "$ROOT"
resource_guard | tee -a "$LOG" || {
  echo "$(date -u +%FT%TZ) MLS pipeline skipped by resource guard" >> "$LOG"
  exit 0
}

case "$MODE" in
  daily)
    run 15m "$PY" mls_bootstrap_warehouse.py
    run 3m "$PY" mls_current_asa.py
    if [ -f data/processed/asa_mls_game_xgoals_2013_present.parquet ]; then
      run 10m "$PY" mls_join_asa_xg.py
    fi
    run 20m "$PY" mls_autoresearch.py daily
    ;;
  weekly)
    run 20m "$PY" mls_asa_enrich.py
    run 10m "$PY" mls_join_asa_xg.py
    run 25m "$PY" mls_weekly_combos.py
    run 20m "$PY" mls_autoresearch.py weekly
    ;;
  status)
    "$PY" mls_autoresearch.py status
    cat "$ROOT/live/current/latest_summary.json" 2>/dev/null || true
    ;;
  *)
    echo "usage: $0 daily|weekly|status" >&2
    exit 2
    ;;
esac
echo "$(date -u +%FT%TZ) MLS pipeline $MODE complete" | tee -a "$LOG"
