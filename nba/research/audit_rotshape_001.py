#!/usr/bin/env python3
import csv,gzip,json,math
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"rotation_shape"
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"}
BENCH_SHARE=-0.08308;BENCH_USED=1.0;CLUTCH=0.5

def rgz(p):
    if not p.exists():return []
    opener=gzip.open if p.suffix==".gz" else open
    try:
        with opener(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
    except gzip.BadGzipFile:
        with open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
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
    vals=[(r,pf(r.get(price),r["won"])) for r in rr]
    vals=[x for x in vals if x[1] is not None]
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
role={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"starter_bench_rolling.csv.gz")}
shape={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"scoring_shape_rolling.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

universe=[]
for gid,g in games.items():
    m=market.get(gid);hs=n(g.get("home_score"));aas=n(g.get("away_score"))
    if not m or hs is None or aas is None:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tr,orr=role.get((gid,tid),{}),role.get((gid,oid),{})
        ts,os=shape.get((gid,tid),{}),shape.get((gid,oid),{})
        med=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        if med is None:continue
        universe.append({
          "season":g.get("season"),"game_id":gid,"team_id":tid,"team":g.get("home_team") if home else g.get("away_team"),
          "home":home,"won":hs>aas if home else aas>hs,
          "median":med,"best":n(m.get("home_moneyline_best") if home else m.get("away_moneyline_best")),
          "worst":n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst")),
          "bench_share_gap":d(tr,orr,"bench_points_share_last5_avg"),
          "bench_used_gap":d(tr,orr,"bench_used_last5_avg"),
          "clutch10_gap":d(ts,os,"clutch_margin_last10_avg")
        })
def qual(bs=BENCH_SHARE,bu=BENCH_USED,cl=CLUTCH):
    return [r for r in universe if r["bench_share_gap"] is not None and r["bench_used_gap"] is not None and r["clutch10_gap"] is not None
            and r["bench_share_gap"]<=bs and r["bench_used_gap"]>=bu and r["clutch10_gap"]>=cl]
rows=qual()
report={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "method":{"id":"NBA_ROTSHAPE_001","status":"shadow_only","rule":f"bench_points_share5_gap <= {BENCH_SHARE} AND bench_used5_gap >= {BENCH_USED} AND clutch_margin10_gap >= {CLUTCH}"},
 "overall":met(rows),"phases":{"discA":met([r for r in rows if r["season"] in A]),"discB":met([r for r in rows if r["season"] in B]),
 "validation":met([r for r in rows if r["season"] in V]),"holdout":met([r for r in rows if r["season"] in H])},
 "price_stress":{p:met(rows,p) for p in ("best","median","worst")},
 "by_season":{s:met([r for r in rows if r["season"]==s]) for s in SEASONS},
 "location":{"home":met([r for r in rows if r["home"]]),"away":met([r for r in rows if not r["home"]])},
 "exclude_covid":met([r for r in rows if r["season"] not in {"2019-20","2020-21"}]),
 "leave_one_season_out":{s:met([r for r in rows if r["season"]!=s]) for s in SEASONS}
}
teams=defaultdict(list)
for r in rows:teams[r["team"]].append(r)
ts=[]
for team,rr in teams.items():
    x=met(rr);x["team"]=team;x["share"]=len(rr)/len(rows) if rows else 0;ts.append(x)
ts.sort(key=lambda x:x["n"],reverse=True)
report["top_teams"]=ts[:15];report["max_team_share"]=ts[0]["share"] if ts else 0

neigh=[]
for bs in (-0.10,-0.08308,-0.065):
  for bu in (0.5,1.0,1.5):
    for cl in (0.0,0.5,1.0):
      rr=qual(bs,bu,cl)
      neigh.append({"bench_share_max":bs,"bench_used_min":bu,"clutch10_min":cl,**met(rr)})
report["threshold_neighborhood"]=neigh

# Exact parent overlap.
keys={(r["game_id"],r["team_id"]) for r in rows}
ml=set()
for p in [NBA/"research"/"ml001_elite"/"elite_001_signals.csv.gz",NBA/"research"/"ml001_elite"/"elite_001_signals.csv"]:
    if p.exists():
        for r in rgz(p):
            if r.get("game_id") and r.get("team_id"):ml.add((r["game_id"],r["team_id"]))

# Reconstruct PHYSCOACH keys.
phys={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"starter_physical.csv.gz")}
ph=set()
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tp,op=phys.get((gid,tid),{}),phys.get((gid,oid),{})
        gw=d(tp,op,"guard_weight");bw=d(tp,op,"big_weight")
        prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        if None not in (gw,bw,prob) and gw<=-10 and bw>=8 and prob>=0.5968:ph.add((gid,tid))

# Reconstruct INTERACT_003 keys.
core={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"starter_core_pregame.csv.gz")}
line={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"lineup_pregame.csv.gz")}
work={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_workload_pregame.csv.gz")}
inter=set()
for gid,g in games.items():
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tc,oc=core.get((gid,tid),{}),core.get((gid,oid),{})
        tl,ol=line.get((gid,tid),{}),line.get((gid,oid),{})
        tw,ow=work.get((gid,tid),{}),work.get((gid,oid),{})
        pm=d(tc,oc,"starter_plusminus5_avg");so=d(tl,ol,"starter_overlap_prev_game");w3=d(ow,tw,"top5_minutes_prev_3d")
        if None not in (pm,so,w3) and pm>=3 and so>=1 and w3>=45:inter.add((gid,tid))

report["overlap"]={
 "rotshape":len(keys),"ml001":len(keys&ml),"physcoach":len(keys&ph),"interact003":len(keys&inter),
 "with_any_live_parent":len(keys&(ml|ph|inter)),"unique_vs_live_parents":len(keys-(ml|ph|inter))
}
(OUT/"rotshape_001_robustness.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
