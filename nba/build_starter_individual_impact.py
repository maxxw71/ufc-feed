#!/usr/bin/env python3
import csv,gzip,json,statistics
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";SRC=NBA/"lineups"/"historical_lineup_stints.csv.gz";LINE=NBA/"features"/"lineup_pregame.csv.gz";OUT=NBA/"features"
OUT.mkdir(parents=True,exist_ok=True)

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
def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:return None
def pm48(pm,sec): return 2880.0*pm/sec if sec and sec>0 else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):games[g.get("game_id")]=g
starters={(r.get("game_id"),r.get("team_id")):set(x for x in (r.get("confirmed_starting_five") or "").split("|") if x) for r in rgz(LINE)}

stints=rgz(SRC)
by_game=defaultdict(list)
for r in stints:
    gid=r.get("game_id");tid=r.get("team_id")
    if gid and tid:by_game[(gid,tid)].append(r)

events=[]
for (gid,tid),arr in by_game.items():
    g=games.get(gid);when=dt(g.get("game_date") if g else None)
    if not g or not when:continue
    events.append((when,g.get("season"),gid,tid,arr))
events.sort(key=lambda z:z[0])

hist=defaultdict(lambda:{"sec":0.0,"pm":0.0,"games":set()})
out=[]
for when,season,gid,tid,arr in events:
    s5=starters.get((gid,tid)) or set()
    vals=[]
    for pid in sorted(s5):
        h=hist[(season,tid,pid)]
        vals.append({
          "pid":pid,"sec":h["sec"],"pm":h["pm"],"games":len(h["games"]),
          "pm48":pm48(h["pm"],h["sec"])
        })
    valid=[v for v in vals if v["pm48"] is not None and v["sec"]>=300]
    row={"season":season,"game_id":gid,"game_date":when.isoformat(),"team_id":tid,
         "confirmed_starting_five":"|".join(sorted(s5)),"starter_impact_players_with_history":len(valid),
         "pregame_only_feature":True,"requires_confirmed_starters":True}
    if valid:
        p=[v["pm48"] for v in valid]
        secs=[v["sec"] for v in valid]
        gamesv=[v["games"] for v in valid]
        row.update({
          "starter_individual_pm48_mean":statistics.mean(p),
          "starter_individual_pm48_min":min(p),
          "starter_individual_pm48_max":max(p),
          "starter_individual_pm48_std":statistics.pstdev(p) if len(p)>1 else 0.0,
          "starter_individual_prior_seconds_mean":statistics.mean(secs),
          "starter_individual_prior_games_mean":statistics.mean(gamesv),
          "starter_individual_negative_count":sum(1 for x in p if x<0),
          "starter_individual_strong_count":sum(1 for x in p if x>=5),
        })
    out.append(row)

    # update individual on-court history after target feature emission
    per=defaultdict(lambda:{"sec":0.0,"pm":0.0})
    for r in arr:
        sec=max(0.0,n(r.get("stint_duration_sec")));pm=n(r.get("stint_plus_minus"))
        for pid in (r.get("lineup_player_ids") or "").split("|"):
            if not pid:continue
            per[pid]["sec"]+=sec;per[pid]["pm"]+=pm
    for pid,v in per.items():
        h=hist[(season,tid,pid)]
        h["sec"]+=v["sec"];h["pm"]+=v["pm"];h["games"].add(gid)

wgz(OUT/"starter_individual_impact_pregame.csv.gz",out)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(out),
         "policy":"Target confirmed starters are allowed pregame; every individual PM/48 and minute total is calculated only from earlier lineup stints in the same season."}
(OUT/"starter_individual_impact_pregame_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
