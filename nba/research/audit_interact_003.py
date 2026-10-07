#!/usr/bin/env python3
import csv,gzip,json,math
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"interaction_core"
OUT.mkdir(parents=True,exist_ok=True)
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"}
PM=3.0; SO=1.0; W3=45.0

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def pf(a,w):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (a/100 if a>0 else 100/abs(a)) if w else -1.0
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;d=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/d
def met(rr,price="median"):
    vals=[]
    for r in rr:
        p=pf(r.get(price),r["won"])
        if p is not None:vals.append((r,p))
    if not vals:return {"n":0,"wins":0,"losses":0,"hit":None,"roi":None,"profit":0,"wilson_low":None}
    w=sum(1 for r,_ in vals if r["won"]);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"losses":len(vals)-w,"hit":w/len(vals),"roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,len(vals))}
def d(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x-y if x is not None and y is not None else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in SEASONS and t(g.get("completed")):games[g["game_id"]]=g
core={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"starter_core_pregame.csv.gz")}
line={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"lineup_pregame.csv.gz")}
work={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_workload_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid);hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    if not m or hs is None or as_ is None:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tc,oc=core.get((gid,tid),{}),core.get((gid,oid),{})
        tl,ol=line.get((gid,tid),{}),line.get((gid,oid),{})
        tw,ow=work.get((gid,tid),{}),work.get((gid,oid),{})
        pm=d(tc,oc,"starter_plusminus5_avg");so=d(tl,ol,"starter_overlap_prev_game");w3=d(ow,tw,"top5_minutes_prev_3d")
        med=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        best=n(m.get("home_moneyline_best") if home else m.get("away_moneyline_best"))
        worst=n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst"))
        if None in (pm,so,w3,med):continue
        if pm>=PM and so>=SO and w3>=W3:
            rows.append({"season":g.get("season"),"game_id":gid,"team_id":tid,"team":g.get("home_team") if home else g.get("away_team"),
                         "won":hs>as_ if home else as_>hs,"home":home,"median":med,"best":best,"worst":worst,
                         "plusminus5_gap":pm,"starter_overlap_gap":so,"work_top5_3d_adv":w3})

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "method":{"id":"NBA_INTERACT_003","rule":f"plusminus5_gap >= {PM} AND starter_overlap_gap >= {SO} AND work_top5_3d_adv >= {W3}","status":"shadow_only"},
        "overall":met(rows),
        "phases":{"discA":met([r for r in rows if r["season"] in A]),"discB":met([r for r in rows if r["season"] in B]),"validation":met([r for r in rows if r["season"] in V]),"holdout":met([r for r in rows if r["season"] in H])},
        "price_stress":{p:met(rows,p) for p in ("best","median","worst")},
        "by_season":{s:met([r for r in rows if r["season"]==s]) for s in SEASONS},
        "location":{"home":met([r for r in rows if r["home"]]),"away":met([r for r in rows if not r["home"]])},
        "exclude_covid":met([r for r in rows if r["season"] not in {"2019-20","2020-21"}]),
        "leave_one_season_out":{s:met([r for r in rows if r["season"]!=s]) for s in SEASONS}}

teams=defaultdict(list)
for r in rows:teams[r["team"]].append(r)
ts=[]
for team,rr in teams.items():
    x=met(rr);x["team"]=team;x["share"]=len(rr)/len(rows) if rows else 0;ts.append(x)
ts.sort(key=lambda x:x["n"],reverse=True)
report["top_teams"]=ts[:15];report["max_team_share"]=ts[0]["share"] if ts else 0

neigh=[]
for pm in (2,3,4,5):
  for so in (0,1,2):
    for w3 in (30,45,60):
      rr=[r for r in rows if r["plusminus5_gap"]>=pm and r["starter_overlap_gap"]>=so and r["work_top5_3d_adv"]>=w3]
      neigh.append({"plusminus5_min":pm,"starter_overlap_min":so,"work_top5_3d_adv_min":w3,**met(rr)})
report["threshold_neighborhood"]=neigh

# Overlap: exact game+team keys against frozen parents if their signal files exist.
def sigkeys(paths):
    out=set()
    for p in paths:
        if not p.exists():continue
        for r in rgz(p):
            gid=r.get("game_id");tid=r.get("team_id") or r.get("selected_team_id")
            if gid and tid:out.add((gid,tid))
    return out
ik={(r["game_id"],r["team_id"]) for r in rows}
ml=sigkeys([NBA/"research"/"ml001_elite"/"elite_001_signals.csv.gz",NBA/"research"/"ml001_elite"/"elite_001_signals.csv"])
ph=sigkeys([NBA/"research"/"physical_coach"/"physcoach_001_signals.csv.gz",NBA/"research"/"physical_coach"/"physcoach_001_signals.csv"])
report["overlap"]={
  "interact_signals":len(ik),"ml001_signal_keys_found":len(ml),"physcoach_signal_keys_found":len(ph),
  "with_ml001":len(ik&ml),"with_physcoach":len(ik&ph),"with_either":len(ik&(ml|ph)),
  "unique_vs_found_parents":len(ik-(ml|ph))}
(OUT/"interact_003_robustness.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
