#!/usr/bin/env python3
import csv,gzip,json,re
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
AVAIL=ROOT/"availability"/"availability_snapshots.csv.gz"
PLAYER=ROOT/"features"/"player_rolling.csv.gz"
ROSTER=ROOT/"rosters"/"current_roster_profiles.csv.gz"
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

def norm_name(v):
    s=(v or "").lower()
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return " ".join(s.split())

avail=read(AVAIL)
pf=read(PLAYER)
roster=read(ROSTER)

# Current roster is only an identity/team fallback; performance still comes
# exclusively from point-in-time rolling game rows.
roster_by_name={}
for r in roster:
    name=norm_name(r.get("player_name"))
    if name: roster_by_name[name]=r

# Latest rolling feature row per player ID and normalized name.
latest_player={}
latest_player_name={}
for r in pf:
    key=(r.get("game_date") or "",r.get("game_id") or "")
    pid=r.get("person_id")
    if pid:
        old=latest_player.get(pid)
        if old is None or key>(old[0],old[1]):
            latest_player[pid]=(key[0],key[1],r)
    name=norm_name(r.get("player_name"))
    if name:
        old=latest_player_name.get(name)
        if old is None or key>(old[0],old[1]):
            latest_player_name[name]=(key[0],key[1],r)

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
identity_matches=0
rolling_matches=0

for a in current:
    name_key=norm_name(a.get("player_name"))
    rr=roster_by_name.get(name_key) or {}
    pid=a.get("person_id") or rr.get("person_id") or ""
    team_id=a.get("team_id") or rr.get("team_id") or ""
    team_abbr=a.get("team_abbreviation") or rr.get("team_tricode") or ""
    team_name=a.get("team_name") or rr.get("team") or ""
    if pid or team_id: identity_matches+=1

    feature_entry=latest_player.get(pid) if pid else None
    if feature_entry is None and name_key:
        feature_entry=latest_player_name.get(name_key)
    feat=(feature_entry or (None,None,{}))[2]
    if feat: rolling_matches+=1

    s=(a.get("status") or "")
    w=severity(s)
    mins=num(feat.get("minutes_last5_avg"))
    pts=num(feat.get("points_last5_avg"))
    ast=num(feat.get("assists_last5_avg"))
    reb=num(feat.get("rebounds_total_last5_avg"))

    tid=team_id or team_abbr or f"unresolved:{name_key}"
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
      "captured_at_utc":latest_capture,"team_id":team_id,"team":team_name,
      "team_abbreviation":team_abbr,"person_id":pid,"player_name":a.get("player_name"),
      "status":s,"severity_weight":w,"minutes_last5_avg":mins,"points_last5_avg":pts,
      "assists_last5_avg":ast,"rebounds_last5_avg":reb,
      "identity_resolved":bool(pid or team_id),"rolling_feature_matched":bool(feat)
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
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "availability_capture":latest_capture,
 "listed_players":len(detail),"teams":len(team_rows),
 "identity_resolved_players":identity_matches,
 "players_matched_to_rolling_features":rolling_matches,
 "unresolved_identity_players":len(detail)-identity_matches,
 "note":"Current derived impact view; source availability snapshots remain immutable point-in-time history."
}
(OUT/"availability_impact_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
