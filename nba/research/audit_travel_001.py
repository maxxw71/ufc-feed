#!/usr/bin/env python3
import csv,gzip,json,math
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"travel"
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"}
TRAVEL=576.9;PROB=0.7023

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
    p=w/nn;dd=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/dd
def met(rr,price="median"):
    vals=[(r,pf(r.get(price),r["won"])) for r in rr];vals=[x for x in vals if x[1] is not None]
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
travel={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"travel_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}
universe=[]
for gid,g in games.items():
    m=market.get(gid);hs=n(g.get("home_score"));aas=n(g.get("away_score"))
    if not m or hs is None or aas is None:continue
    # Away side only: frozen family definition.
    tid=g.get("away_team_id");oid=g.get("home_team_id")
    tt,ot=travel.get((gid,tid),{}),travel.get((gid,oid),{})
    med=n(m.get("away_moneyline_median"));prob=n(m.get("away_implied_probability_devig"))
    if med is None or prob is None:continue
    universe.append({
      "season":g.get("season"),"game_id":gid,"team_id":tid,"team":g.get("away_team"),"opponent":g.get("home_team"),
      "won":aas>hs,"median":med,"best":n(m.get("away_moneyline_best")),"worst":n(m.get("away_moneyline_worst")),
      "market_prob":prob,"travel7d_adv":d(ot,tt,"travel_miles_prev_7d_including_arrival"),
      "side_travel7d":n(tt.get("travel_miles_prev_7d_including_arrival")),"opp_travel7d":n(ot.get("travel_miles_prev_7d_including_arrival")),
      "side_travel":n(tt.get("travel_miles_from_prev")),"opp_travel":n(ot.get("travel_miles_from_prev")),
      "side_tz_shift":n(tt.get("timezone_shift_hours")),"opp_tz_shift":n(ot.get("timezone_shift_hours"))
    })
def qual(tr=TRAVEL,pr=PROB):
    return [r for r in universe if r["travel7d_adv"] is not None and r["travel7d_adv"]>=tr and r["market_prob"]>=pr]
rows=qual()
report={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "method":{"id":"NBA_TRAVEL_001","status":"shadow_only","rule":f"away side AND opponent_minus_side_travel7d >= {TRAVEL} miles AND market_prob >= {PROB}"},
 "overall":met(rows),"phases":{"discA":met([r for r in rows if r["season"] in A]),"discB":met([r for r in rows if r["season"] in B]),
 "validation":met([r for r in rows if r["season"] in V]),"holdout":met([r for r in rows if r["season"] in H])},
 "price_stress":{p:met(rows,p) for p in ("best","median","worst")},
 "by_season":{s:met([r for r in rows if r["season"]==s]) for s in SEASONS},
 "exclude_covid":met([r for r in rows if r["season"] not in {"2019-20","2020-21"}]),
 "leave_one_season_out":{s:met([r for r in rows if r["season"]!=s]) for s in SEASONS}
}
# Compare against the market-probability/away-favorite baseline without travel filter.
baseline=[r for r in universe if r["market_prob"]>=PROB]
report["baseline_same_price_gate"]={"away_favorite_without_travel_filter":met(baseline),"travel_filtered":met(rows)}

teams=defaultdict(list)
for r in rows:teams[r["team"]].append(r)
ts=[]
for team,rr in teams.items():
    x=met(rr);x["team"]=team;x["share"]=len(rr)/len(rows) if rows else 0;ts.append(x)
ts.sort(key=lambda x:x["n"],reverse=True)
report["top_teams"]=ts[:15];report["max_team_share"]=ts[0]["share"] if ts else 0

neigh=[]
for tr in (400,500,576.9,650,750,900):
  for pr in (0.68,0.70,0.7023,0.72,0.75):
    rr=qual(tr,pr)
    neigh.append({"travel7d_adv_min":tr,"market_prob_min":pr,**met(rr)})
report["threshold_neighborhood"]=neigh

# Exact overlap with live parents.
keys={(r["game_id"],r["team_id"]) for r in rows}
ml=set()
for p in [NBA/"research"/"ml001_elite"/"elite_001_signals.csv.gz",NBA/"research"/"ml001_elite"/"elite_001_signals.csv"]:
    if p.exists():
        for r in rgz(p):
            if r.get("game_id") and r.get("team_id"):ml.add((r["game_id"],r["team_id"]))

phys={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"starter_physical.csv.gz")}
ph=set()
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tp,op=phys.get((gid,tid),{}),phys.get((gid,oid),{})
        gw=d(tp,op,"guard_weight");bw=d(tp,op,"big_weight");pr=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        if None not in (gw,bw,pr) and gw<=-10 and bw>=8 and pr>=0.5968:ph.add((gid,tid))

core={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"starter_core_pregame.csv.gz")}
line={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"lineup_pregame.csv.gz")}
work={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_workload_pregame.csv.gz")}
inter=set()
for gid,g in games.items():
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tc,oc=core.get((gid,tid),{}),core.get((gid,oid),{});tl,ol=line.get((gid,tid),{}),line.get((gid,oid),{});tw,ow=work.get((gid,tid),{}),work.get((gid,oid),{})
        pm=d(tc,oc,"starter_plusminus5_avg");so=d(tl,ol,"starter_overlap_prev_game");w3=d(ow,tw,"top5_minutes_prev_3d")
        if None not in (pm,so,w3) and pm>=3 and so>=1 and w3>=45:inter.add((gid,tid))
report["overlap"]={"travel001":len(keys),"ml001":len(keys&ml),"physcoach":len(keys&ph),"interact003":len(keys&inter),
                   "with_any_live_parent":len(keys&(ml|ph|inter)),"unique_vs_live_parents":len(keys-(ml|ph|inter))}
(OUT/"travel_001_robustness.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
