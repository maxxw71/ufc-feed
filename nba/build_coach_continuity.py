#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parents[1]
SRC=NBA/"coaching"/"historical_coaches.csv.gz"; OUT=NBA/"features"

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def wgz(p,rows):
    rows=list(rows);fs=[]
    for r in rows:
        for k in r:
            if k not in fs:fs.append(k)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fs or ["_empty"]);w.writeheader();w.writerows(rows)

rows=rgz(SRC)
by_team=defaultdict(list)
for r in rows:
    if r.get("team_id") and r.get("season") and r.get("coach_id"):
        by_team[r["team_id"]].append(r)

out=[]
for tid,arr in by_team.items():
    arr.sort(key=lambda r:r.get("season") or "")
    prev=None;tenure=0
    for r in arr:
        same=bool(prev and prev.get("coach_id")==r.get("coach_id"))
        tenure=tenure+1 if same else 1
        out.append({
          "season":r.get("season"),"team_id":tid,"coach_id":r.get("coach_id"),"coach_name":r.get("coach_name"),
          "coach_continuity_from_prior_season":same if prev else None,
          "new_coach_vs_prior_season":(not same) if prev else None,
          "consecutive_seasons_same_coach":tenure,
          "team_assignment_source":r.get("team_assignment_source"),
          "season_level_only":True
        })
        prev=r
wgz(OUT/"coach_continuity.csv.gz",out)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(out),"teams":len(by_team),
         "policy":"Season-level coach assignment/continuity only. Not used to infer exact midseason coaching-change dates."}
(OUT/"coach_continuity_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
