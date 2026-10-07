#!/usr/bin/env python3
import csv,gzip,json,statistics
from collections import defaultdict,Counter
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";SRC=NBA/"lineups"/"historical_lineup_stints.csv.gz";OUT=NBA/"features";OUT.mkdir(parents=True,exist_ok=True)

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
def pm48(pm,sec):return 2880*pm/sec if sec and sec>0 else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):games[g.get("game_id")]=g

by=defaultdict(list)
for r in rgz(SRC):
    gid=r.get("game_id");tid=r.get("team_id");g=games.get(gid)
    if gid and tid and g:by[(r.get("season"),tid,gid)].append(r)

game_summ=[]
for (season,tid,gid),arr in by.items():
    g=games.get(gid);when=dt(g.get("game_date"))
    if not when:continue
    arr.sort(key=lambda r:n(r.get("stint_start_action")))
    start_id=arr[0].get("lineup_player_ids") or ""
    lineups=defaultdict(lambda:{"sec":0.0,"pm":0.0})
    total_sec=total_pm=0.0;non_sec=non_pm=0.0
    per_stint=[]
    for r in arr:
        lid=r.get("lineup_player_ids") or "";sec=max(0.0,n(r.get("stint_duration_sec")));pm=n(r.get("stint_plus_minus"))
        lineups[lid]["sec"]+=sec;lineups[lid]["pm"]+=pm;total_sec+=sec;total_pm+=pm
        if lid!=start_id:non_sec+=sec;non_pm+=pm
        if sec>=60:per_stint.append(pm48(pm,sec))
    start=lineups[start_id]
    top_id=max(lineups,key=lambda k:lineups[k]["sec"]) if lineups else ""
    top=lineups[top_id] if top_id else {"sec":0,"pm":0}
    game_summ.append({
      "season":season,"team_id":tid,"game_id":gid,"game_date":g.get("game_date"),"when":when,
      "start_id":start_id,"start_sec":start["sec"],"start_pm":start["pm"],"start_pm48":pm48(start["pm"],start["sec"]),
      "nonstart_sec":non_sec,"nonstart_pm":non_pm,"nonstart_pm48":pm48(non_pm,non_sec),
      "top_id":top_id,"top_sec":top["sec"],"top_pm":top["pm"],"top_pm48":pm48(top["pm"],top["sec"]),
      "all_sec":total_sec,"all_pm":total_pm,"stint_pm48_std":statistics.pstdev(per_stint) if len(per_stint)>=2 else None,
      "lineups":lineups
    })

group=defaultdict(list)
for x in game_summ:group[(x["season"],x["team_id"])].append(x)

out=[]
for (season,tid),arr in group.items():
    arr.sort(key=lambda x:x["when"])
    cumulative=defaultdict(lambda:{"sec":0.0,"pm":0.0,"games":set()})
    hist=[]
    for x in arr:
        cur=cumulative[x["start_id"]]
        row={"season":season,"game_id":x["game_id"],"game_date":x["game_date"],"team_id":tid,
             "confirmed_starting_five":"|".join(sorted((x["start_id"] or "").split("|"))),
             "requires_confirmed_starters":True,"pregame_only_feature":True,
             "current_start5_prior_seconds":cur["sec"],"current_start5_prior_pm":cur["pm"],
             "current_start5_prior_pm48":pm48(cur["pm"],cur["sec"]),"current_start5_prior_games":len(cur["games"])}
        for w in (3,5,10):
            prior=hist[-w:]
            for field in ("start_pm48","nonstart_pm48","top_pm48","stint_pm48_std"):
                vals=[p[field] for p in prior if p.get(field) is not None]
                row[f"{field}_last{w}_avg"]=sum(vals)/len(vals) if vals else None
            row[f"start_seconds_last{w}_avg"]=sum(p["start_sec"] for p in prior)/len(prior) if prior else None
            row[f"nonstart_seconds_last{w}_avg"]=sum(p["nonstart_sec"] for p in prior)/len(prior) if prior else None
        out.append(row)
        # Add every lineup stint from this completed game after target features are emitted.
        for lid,v in x["lineups"].items():
            cumulative[lid]["sec"]+=v["sec"];cumulative[lid]["pm"]+=v["pm"];cumulative[lid]["games"].add(x["game_id"])
        hist.append(x)

wgz(OUT/"lineup_quality_pregame.csv.gz",out)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(out),"team_seasons":len(group),
         "policy":"Target confirmed starting five may be identified pregame. Its prior shared minutes/plus-minus include only earlier games; all rolling unit-quality fields use prior games only."}
(OUT/"lineup_quality_pregame_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
