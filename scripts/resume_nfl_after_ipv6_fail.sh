#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/nfl-predictor-v1"
RAW="$ROOT/data/raw"
VENV="$ROOT/venv"
mkdir -p "$RAW" "$ROOT/data/processed" "$ROOT/logs"

progress(){ printf '[%3s%%] %s\n' "$1" "$2"; }

progress 5 "Resuming NFL setup using IPv6-safe GitHub raw relay..."
[ -x "$VENV/bin/python" ] || { echo "ERROR: Missing $VENV/bin/python — rerun the original setup first."; exit 2; }
source "$VENV/bin/activate"

BASE="https://raw.githubusercontent.com/maxxw71/ufc-feed/main/relay/nfl"

progress 20 "Downloading 2006-2026 schedules/results/market lines..."
curl -6 -L --fail --retry 3 --connect-timeout 20 "$BASE/schedules_2006_2026.parquet" -o "$RAW/schedules_2006_2026.parquet"

progress 45 "Downloading 2006-2026 weekly team stats..."
curl -6 -L --fail --retry 3 --connect-timeout 20 "$BASE/team_weekly_2006_2026.parquet" -o "$RAW/team_weekly_2006_2026.parquet"

progress 65 "Downloading relay manifest..."
curl -6 -L --fail --retry 3 --connect-timeout 20 "$BASE/manifest.json" -o "$ROOT/relay_manifest.json"

progress 75 "Validating NFL data..."
python - <<'PY'
from pathlib import Path
import json
import polars as pl
root=Path.home()/"nfl-predictor-v1"
raw=root/"data"/"raw"
s=pl.read_parquet(raw/"schedules_2006_2026.parquet")
t=pl.read_parquet(raw/"team_weekly_2006_2026.parquet")
print(f"Schedules: {s.height:,} rows x {s.width} cols")
print(f"Team weekly: {t.height:,} rows x {t.width} cols")

market=[c for c in s.columns if any(k in c.lower() for k in ['spread','moneyline','total_line','over','under'])]
context=[c for c in s.columns if any(k in c.lower() for k in ['score','rest','roof','temp','wind','surface','stadium','div_game'])]
interest=[c for c in t.columns if any(k in c.lower() for k in ['pass','rush','sack','interception','fumble','turnover','yard','touchdown','attempt','carry','completion'])]
inv={
  'goal':'Find NFL betting categories with >=10% historical ROI using strict pregame-only features and stability validation.',
  'historical_core':'2006-2026',
  'pregame_rule':'For every target game, only prior games/weeks are allowed in team features.',
  'later_season_focus':'Test week floors 5+, 6+, 7+, 8+, 9+, 10+, 11+, 12+.',
  'schedules_rows':s.height,'schedules_cols':s.width,
  'team_weekly_rows':t.height,'team_weekly_cols':t.width,
  'market_columns':market,'context_columns':context,'team_stat_columns_of_interest':interest,
}
(root/'data_inventory.json').write_text(json.dumps(inv,indent=2,default=str))
print('Market columns:', ', '.join(market) if market else '(none found)')
print('Inventory:', root/'data_inventory.json')
PY

progress 90 "Checking server resources..."
df -h /
free -h
swapon --show || true

progress 100 "NFL core foundation ready."
echo
echo "Core historical data is now local under: $RAW"
echo "Next phase: build rolling pregame offense/defense/turnover/EPA rankings and search for stable 10%+ ROI rules."
echo "Additional injuries/snap/NextGen/PFR/play-by-play datasets will be relayed in stages so the IPv6-only VM does not depend on github.com release downloads."
