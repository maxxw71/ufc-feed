#!/usr/bin/env python3
import csv,gzip,json,re,statistics
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
H=NBA/"market"/"historical"
OUT=NBA/"research"/"diagnostics"
OUT.mkdir(parents=True,exist_ok=True)
SEASONS=["2018_19","2019_20","2020_21","2021_22","2022_23","2023_24","2024_25","2025_26"]

PAT=re.compile(r"\b([A-Z]{2,4})\s*([+-]\d+(?:\.\d+)?)\b")

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"seasons":{}}
all_cov=[]
for s in SEASONS:
    rows=rgz(H/s/"odds_accepted.csv.gz")
    by_game={}
    for r in rows:
        gid=r.get("game_id")
        if not gid:continue
        d=(r.get("details") or "").upper()
        m=PAT.search(d)
        if not m:continue
        team=m.group(1); line=n(m.group(2))
        ht=(r.get("home_tricode") or "").upper(); at=(r.get("away_tricode") or "").upper()
        if line is None:continue
        home_line=None
        if team==ht:home_line=line
        elif team==at:home_line=-line
        else:continue
        by_game.setdefault(gid,[]).append(home_line)
    accepted=len(set(r.get("game_id") for r in rows if r.get("game_id")))
    parsed=len(by_game)
    cov=parsed/accepted if accepted else 0
    all_cov.append(cov)
    report["seasons"][s.replace("_","-")]={
      "accepted_games":accepted,"games_with_signed_spread_from_details":parsed,"coverage":cov,
      "median_provider_rows_per_parsed_game":statistics.median([len(v) for v in by_game.values()]) if by_game else 0
    }
report["min_season_coverage"]=min(all_cov) if all_cov else 0
report["ats_ready"]=bool(all_cov and min(all_cov)>=0.95)
(OUT/"spread_semantics_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
