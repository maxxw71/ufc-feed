#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent;DATA=NBA/"data";OUT=NBA/"features";OUT.mkdir(parents=True,exist_ok=True)
WINDOWS=(3,5,10)

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
    except:return None
def ni(v):
    try:return int(float(v))
    except:return 0
def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:return None
def truth(v):return str(v).lower() in ("true","1","yes","made")

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):games[g.get("game_id")]=g

shots=defaultdict(list)
for p in DATA.glob("*_*/regular_season/playbyplay.csv.gz"):
    for r in rgz(p):
        if not r.get("game_id") or not r.get("team_id") or not truth(r.get("is_field_goal")):continue
        dist=n(r.get("shot_distance"))
        if dist is None:continue
        shots[(r["game_id"],r["team_id"])].append(r)

team_games=defaultdict(list)
for (gid,tid),arr in shots.items():
    g=games.get(gid);when=dt(g.get("game_date") if g else None)
    if not g or not when:continue
    bins={"rim":(0,5),"short":(5,15),"mid":(15,23),"long":(23,1000)}
    row={"season":g.get("season"),"game_id":gid,"game_date":g.get("game_date"),"team_id":tid}
    total=len(arr)
    for name,(lo,hi) in bins.items():
        z=[r for r in arr if (n(r.get("shot_distance")) or 0)>=lo and (n(r.get("shot_distance")) or 0)<hi]
        made=sum(1 for r in z if truth(r.get("shot_result")))
        row[f"{name}_fga"]=len(z);row[f"{name}_fgm"]=made
        row[f"{name}_rate"]=len(z)/total if total else None
        row[f"{name}_fg_pct"]=made/len(z) if z else None
    row["avg_shot_distance"]=sum(n(r.get("shot_distance")) or 0 for r in arr)/total if total else None
    row["paintish_rate"]=(row["rim_fga"]+row["short_fga"])/total if total else None
    row["midrange_rate"]=row["mid_fga"]/total if total else None
    row["long_rate"]=row["long_fga"]/total if total else None
    team_games[(g.get("season"),tid)].append((when,row))

metrics=["rim_rate","rim_fg_pct","short_rate","short_fg_pct","midrange_rate","mid_fg_pct","long_rate","long_fg_pct","avg_shot_distance","paintish_rate"]
out=[]
for (season,tid),arr in team_games.items():
    arr.sort(key=lambda z:z[0]);hist=[]
    for when,x in arr:
        row={"season":season,"game_id":x["game_id"],"game_date":x["game_date"],"team_id":tid,"pregame_only_feature":True}
        for w in WINDOWS:
            prior=hist[-w:]
            for m in metrics:
                vals=[z.get(m) for z in prior if z.get(m) is not None]
                row[f"{m}_last{w}_avg"]=sum(vals)/len(vals) if vals else None
        out.append(row);hist.append(x)

wgz(OUT/"shot_profile_rolling.csv.gz",out)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(out),"team_seasons":len(team_games),"windows":list(WINDOWS),
         "metrics":metrics,"distance_bins_ft":{"rim":"0-4.9","short":"5-14.9","mid":"15-22.9","long":"23+"},
         "policy":"Distance bins use only prior completed regular-season field-goal attempts. They are research bins, not official NBA shot-zone labels."}
(OUT/"shot_profile_rolling_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
