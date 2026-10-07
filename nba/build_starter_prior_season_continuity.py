#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";LINE=NBA/"features"/"lineup_pregame.csv.gz";OUT=NBA/"features"
OUT.mkdir(parents=True,exist_ok=True)
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26","2026-27"]

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
def n(v):
    try:return float(v)
    except:return 0.0
def truth(v):return str(v).lower() in ("true","1","yes")
def prev_season(s):
    if not s or "-" not in s:return None
    a=int(s[:4]);return f"{a-1}-{str(a)[-2:]}"

# Previous-season player/team totals.
pstats=defaultdict(lambda:{"games":0,"minutes":0.0,"starts":0,"points":0.0})
teamtot=defaultdict(lambda:{"minutes":0.0,"points":0.0})
for p in DATA.glob("*_*/regular_season/player_boxscores.csv.gz"):
    for r in rgz(p):
        season=r.get("season");tid=str(r.get("team_id") or "");pid=str(r.get("person_id") or "")
        if not season or not tid or not pid or str(r.get("played")).lower() in ("false","0","dnp","none",""):continue
        key=(season,tid,pid);x=pstats[key]
        x["games"]+=1;x["minutes"]+=n(r.get("minutes"));x["starts"]+=1 if truth(r.get("starter")) else 0;x["points"]+=n(r.get("points"))
        teamtot[(season,tid)]["minutes"]+=n(r.get("minutes"));teamtot[(season,tid)]["points"]+=n(r.get("points"))

rows=[]
for r in rgz(LINE):
    season=r.get("season");tid=str(r.get("team_id") or "");ps=prev_season(season)
    ids=[x for x in (r.get("confirmed_starting_five") or "").split("|") if x]
    row={"season":season,"game_id":r.get("game_id"),"game_date":r.get("game_date"),"team_id":tid,
         "confirmed_starting_five":"|".join(ids),"requires_confirmed_starters":True,"pregame_only_feature":True}
    if ps and ids:
        vals=[pstats.get((ps,tid,pid),{"games":0,"minutes":0.0,"starts":0,"points":0.0}) for pid in ids]
        tm=teamtot.get((ps,tid),{"minutes":0.0,"points":0.0})
        row.update({
          "returning_starters_from_prior_team":sum(1 for x in vals if x["games"]>0),
          "prior_team_starter_games_sum":sum(x["games"] for x in vals),
          "prior_team_starter_starts_sum":sum(x["starts"] for x in vals),
          "prior_team_starter_minutes_sum":sum(x["minutes"] for x in vals),
          "prior_team_starter_points_sum":sum(x["points"] for x in vals),
          "prior_team_starter_minutes_share":sum(x["minutes"] for x in vals)/tm["minutes"] if tm["minutes"]>0 else None,
          "prior_team_starter_points_share":sum(x["points"] for x in vals)/tm["points"] if tm["points"]>0 else None,
          "new_to_team_starters":sum(1 for x in vals if x["games"]==0),
          "returning_starters_20plus_games":sum(1 for x in vals if x["games"]>=20),
          "returning_starters_40plus_games":sum(1 for x in vals if x["games"]>=40),
          "returning_starters_1000plus_minutes":sum(1 for x in vals if x["minutes"]>=1000),
        })
    rows.append(row)

wgz(OUT/"starter_prior_season_continuity.csv.gz",rows)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),
         "policy":"Uses target confirmed starting five plus only their same-team boxscore production from the immediately preceding season. 2018-19 has no prior-season warehouse and remains null."}
(OUT/"starter_prior_season_continuity_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
