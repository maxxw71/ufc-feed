#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
AVAIL=ROOT/"availability"/"availability_snapshots.csv.gz"
PLAYER=ROOT/"features"/"player_rolling.csv.gz"
OUT=ROOT/"features"

def read(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def write(path,rows):
    rows=list(rows)
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields: fields=["_empty"]
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

def num(v):
    try: return float(v)
    except: return 0.0

avail=read(AVAIL)
pf=read(PLAYER)

# latest rolling feature row per player by game date
latest_player={}
for r in pf:
    pid=r.get("person_id")
    if not pid: continue
    key=(r.get("game_date") or "",r.get("game_id") or "")
    old=latest_player.get(pid)
    if old is None or key>(old[0],old[1]):
        latest_player[pid]=(key[0],key[1],r)

# retain only the most recent capture timestamp in this derived current-impact view
latest_capture=max((r.get("captured_at_utc") or "" for r in avail),default="")
current=[r for r in avail if r.get("captured_at_utc")==latest_capture]

def severity(status):
    s=(status or "").lower()
    if "out" in s: return 1.0
    if "doubt" in s: return 0.75
    if "question" in s: return 0.5
    if "day-to-day" in s or "day to day" in s: return 0.35
    if "prob" in s: return 0.15
    return 0.25

teams=defaultdict(lambda:{
 "listed_players":0,"out_players":0,"doubtful_players":0,"questionable_players":0,
 "weighted_missing_minutes_last5":0.0,"weighted_missing_points_last5":0.0,
 "weighted_missing_assists_last5":0.0,"weighted_missing_rebounds_last5":0.0
})
detail=[]
for a in current:
    tid=a.get("team_id") or a.get("team_abbreviation") or ""
    pid=a.get("person_id") or ""
    s=(a.get("status") or "")
    w=severity(s)
    feat=(latest_player.get(pid) or (None,None,{}))[2]
    mins=num(feat.get("minutes_last5_avg"))
    pts=num(feat.get("points_last5_avg"))
    ast=num(feat.get("assists_last5_avg"))
    reb=num(feat.get("rebounds_total_last5_avg"))
    t=teams[tid]
    t["listed_players"]+=1
    sl=s.lower()
    if "out" in sl: t["out_players"]+=1
    if "doubt" in sl: t["doubtful_players"]+=1
    if "question" in sl: t["questionable_players"]+=1
    t["weighted_missing_minutes_last5"]+=w*mins
    t["weighted_missing_points_last5"]+=w*pts
    t["weighted_missing_assists_last5"]+=w*ast
    t["weighted_missing_rebounds_last5"]+=w*reb
    detail.append({
      "captured_at_utc":latest_capture,"team_id":a.get("team_id"),"team":a.get("team_name"),
      "team_abbreviation":a.get("team_abbreviation"),"person_id":pid,"player_name":a.get("player_name"),
      "status":s,"severity_weight":w,"minutes_last5_avg":mins,"points_last5_avg":pts,
      "assists_last5_avg":ast,"rebounds_last5_avg":reb
    })

team_rows=[]
for tid,t in teams.items():
    row={"captured_at_utc":latest_capture,"team_key":tid}
    row.update(t)
    team_rows.append(row)

OUT.mkdir(parents=True,exist_ok=True)
write(OUT/"availability_impact_players.csv.gz",detail)
write(OUT/"availability_impact_teams.csv.gz",team_rows)
summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),"availability_capture":latest_capture,
 "listed_players":len(detail),"teams":len(team_rows),
 "players_matched_to_rolling_features":sum(1 for r in detail if r["minutes_last5_avg"]>0),
 "note":"Current derived impact view; source availability snapshots remain immutable point-in-time history."
}
(OUT/"availability_impact_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
