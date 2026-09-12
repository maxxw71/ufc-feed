#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/nfl-predictor-v1"
VENV="$ROOT/venv"
DATA="$ROOT/data"
CACHE="$ROOT/.nflreadpy-cache"
mkdir -p "$ROOT" "$DATA/raw" "$DATA/processed" "$ROOT/logs" "$CACHE"

progress(){ printf '[%3s%%] %s\n' "$1" "$2"; }

progress 2 "Checking swap..."
if swapon --show=NAME --noheadings 2>/dev/null | grep -q .; then
  echo "Swap already active:"
  swapon --show
else
  progress 5 "Creating 2 GB swap file..."
  sudo fallocate -l 2G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  if ! grep -q '^/swapfile ' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  fi
fi

progress 10 "Creating NFL Python environment..."
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"
python -m pip install -q --upgrade pip
progress 18 "Installing NFL/data-science packages..."
python -m pip install -q nflreadpy polars pyarrow pandas numpy scipy scikit-learn duckdb requests joblib

cat > "$ROOT/bootstrap_nfl_data.py" <<'PY'
from pathlib import Path
import json, os, sys, traceback
import polars as pl
import nflreadpy as nfl
from nflreadpy.config import update_config

ROOT=Path.home()/"nfl-predictor-v1"
RAW=ROOT/"data"/"raw"
RAW.mkdir(parents=True,exist_ok=True)
CACHE=ROOT/".nflreadpy-cache"
CACHE.mkdir(parents=True,exist_ok=True)
update_config(cache_mode="filesystem", cache_dir=CACHE, cache_duration=86400, verbose=False, timeout=120)

START=2006
END=2026
seasons=list(range(START,END+1))

def p(pct,msg):
    print(f"[{pct:3d}%] {msg}",flush=True)

def save(df,name):
    path=RAW/f"{name}.parquet"
    df.write_parquet(path,compression="zstd")
    return path

def safe(label, fn, outfile, pct):
    p(pct,f"Downloading {label}...")
    try:
        df=fn()
        path=save(df,outfile)
        print(f"      {label}: {df.height:,} rows x {df.width} cols -> {path}")
        return df
    except Exception as e:
        print(f"      WARNING: {label} failed: {e}")
        return None

p(25,"Loading 2006-2026 schedules/results/market lines...")
schedules=nfl.load_schedules(seasons)
save(schedules,"schedules_2006_2026")
print(f"      schedules: {schedules.height:,} games x {schedules.width} cols")

p(38,"Loading weekly team stats for every season...")
team=nfl.load_team_stats(seasons,summary_level="week")
save(team,"team_weekly_2006_2026")
print(f"      team-week rows: {team.height:,} x {team.width} cols")

inj=safe("injuries 2009-2026",lambda:nfl.load_injuries(list(range(2009,END+1))),"injuries_2009_2026",50)
snaps=safe("snap counts 2012-2026",lambda:nfl.load_snap_counts(list(range(2012,END+1))),"snap_counts_2012_2026",58)
ngsp=safe("Next Gen passing 2016-2026",lambda:nfl.load_nextgen_stats(list(range(2016,END+1)),"passing"),"ngs_passing_2016_2026",64)
ngsr=safe("Next Gen rushing 2016-2026",lambda:nfl.load_nextgen_stats(list(range(2016,END+1)),"rushing"),"ngs_rushing_2016_2026",68)
ngsrec=safe("Next Gen receiving 2016-2026",lambda:nfl.load_nextgen_stats(list(range(2016,END+1)),"receiving"),"ngs_receiving_2016_2026",72)

# Current/historical roster context. Weekly rosters can be useful for late scratches / depth changes.
rosters=safe("weekly rosters 2006-2026",lambda:nfl.load_rosters_weekly(seasons),"weekly_rosters_2006_2026",78)

pfrdef=safe("advanced defensive stats 2018-2026",lambda:nfl.load_pfr_advstats(list(range(2018,END+1)),"def","week"),"pfr_adv_def_2018_2026",82)
pfrpass=safe("advanced passing stats 2018-2026",lambda:nfl.load_pfr_advstats(list(range(2018,END+1)),"pass","week"),"pfr_adv_pass_2018_2026",85)

p(88,"Writing data inventory...")
sc=schedules.columns
odds_cols=[c for c in sc if any(x in c.lower() for x in ["spread","moneyline","total_line","over","under"])]
context_cols=[c for c in sc if any(x in c.lower() for x in ["score","rest","roof","temp","wind","surface","stadium","div_game"])]
team_interest=[c for c in team.columns if any(x in c.lower() for x in ["pass","rush","sack","interception","fumble","turnover","yard","touchdown","attempt","carry","completion"])]

def range_info(df):
    if df is None:return None
    out={"rows":df.height,"columns":df.width}
    if "season" in df.columns and df.height:
        try:
            out["season_min"]=df.select(pl.col("season").min()).item()
            out["season_max"]=df.select(pl.col("season").max()).item()
        except Exception:pass
    return out

inventory={
  "goal":"Discover NFL betting categories with >=10% historical ROI, then require walk-forward/era stability before live use.",
  "primary_roi_period":"2006-2026 because historical moneylines are generally available from 2006 onward in the schedule dataset.",
  "pregame_rule":"Every feature for a target game must use only information available before kickoff. No same-game or future leakage.",
  "later_season_focus":"Explicitly test week floors 5+, 6+, 7+, 8+, 9+, 10+ because team offense/defense profiles become more informative as sample size grows.",
  "schedules":range_info(schedules),
  "team_weekly":range_info(team),
  "injuries":range_info(inj),
  "snap_counts":range_info(snaps),
  "ngs_passing":range_info(ngsp),
  "ngs_rushing":range_info(ngsr),
  "ngs_receiving":range_info(ngsrec),
  "weekly_rosters":range_info(rosters),
  "pfr_adv_def":range_info(pfrdef),
  "pfr_adv_pass":range_info(pfrpass),
  "schedule_market_columns":odds_cols,
  "schedule_context_columns":context_cols,
  "team_stat_columns_of_interest":team_interest,
}
(ROOT/"data_inventory.json").write_text(json.dumps(inventory,indent=2,default=str))

p(94,"Checking historical market coverage by season...")
# Determine which market columns are actually populated. This avoids assuming column names/content.
market_report=[]
for c in odds_cols:
    try:
        nonnull=schedules.group_by("season").agg(pl.col(c).is_not_null().sum().alias("non_null")).sort("season")
        market_report.append({"column":c,"total_non_null":int(schedules.select(pl.col(c).is_not_null().sum()).item())})
    except Exception:pass
(ROOT/"market_columns.json").write_text(json.dumps(market_report,indent=2))

p(100,"NFL foundation ready.")
print()
print("NFL PROJECT READY")
print(f"Project: {ROOT}")
print(f"Schedules: {RAW/'schedules_2006_2026.parquet'}")
print(f"Weekly team stats: {RAW/'team_weekly_2006_2026.parquet'}")
print(f"Inventory: {ROOT/'data_inventory.json'}")
print("Next phase: build strict PRE-GAME rolling offense/defense/turnover/EPA rankings and backtest ROI by week-of-season.")
PY

export NFLREADPY_CACHE=filesystem
export NFLREADPY_CACHE_DIR="$CACHE"
export NFLREADPY_TIMEOUT=120

progress 22 "Bootstrapping historical NFL data (2006-2026)..."
python "$ROOT/bootstrap_nfl_data.py"

progress 100 "Setup complete."
echo
echo "=== SERVER AFTER NFL SETUP ==="
df -h /
free -h
swapon --show
printf '\nNFL project size: '
du -sh "$ROOT" | awk '{print $1}'
