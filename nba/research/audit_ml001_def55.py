#!/usr/bin/env python3
import csv,gzip,json,math
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"ml001_elite"
OUT.mkdir(parents=True,exist_ok=True)
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"}
NET=7.759;REST=2.0;CAP=5.5

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
    if not vals:return {"n":0,"hit":None,"roi":None,"profit":0,"wilson_low":None}
    w=sum(1 for r,_ in vals if r["won"]);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(vals),"roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,len(vals))}
def d(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x-y if x is not None and y is not None else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in SEASONS and t(g.get("completed")):games[g["game_id"]]=g
roll={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_rolling.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    if hs is None or as_ is None:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tr=roll.get((gid,tid),{});orr=roll.get((gid,oid),{});tc=ctx.get((gid,tid),{});oc=ctx.get((gid,oid),{})
        net=d(tr,orr,"_net_rating_est_last5_avg");defadv=d(orr,tr,"_def_rating_est_last5_avg")
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        med=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        worst=n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst"))
        if None in (net,defadv,rest,orest,med):continue
        if net<NET or rest-orest<REST or defadv>CAP:continue
        rows.append({"season":g.get("season"),"game_id":gid,"team_id":tid,"home":home,"favorite":med<0,"dog":med>0,
                     "median":med,"worst":worst,"won":hs>as_ if home else as_>hs})

variants={
 "BASE_5_5":lambda r:True,
 "AWAY":lambda r:not r["home"],
 "HOME":lambda r:r["home"],
 "FAVORITE":lambda r:r["favorite"],
 "UNDERDOG":lambda r:r["dog"],
 "AWAY_FAVORITE":lambda r:(not r["home"]) and r["favorite"],
 "AWAY_UNDERDOG":lambda r:(not r["home"]) and r["dog"],
}
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "rule":f"net5_gap >= {NET}, rest_diff >= {REST}, def5_adv <= {CAP}",
        "selection_note":"BASE_5_5 is a practical rounding audit of the discovery-derived defensive cap. Sub-variants are exploratory/post-hoc and require prospective confirmation.",
        "variants":{}}
for name,fn in variants.items():
    rr=[r for r in rows if fn(r)]
    report["variants"][name]={
      "overall":met(rr),"discA":met([r for r in rr if r["season"] in A]),"discB":met([r for r in rr if r["season"] in B]),
      "validation":met([r for r in rr if r["season"] in V]),"holdout":met([r for r in rr if r["season"] in H]),
      "worst_price_overall":met(rr,"worst"),
      "worst_price_discA":met([r for r in rr if r["season"] in A],"worst"),
      "worst_price_discB":met([r for r in rr if r["season"] in B],"worst"),
      "worst_price_validation":met([r for r in rr if r["season"] in V],"worst"),
      "worst_price_holdout":met([r for r in rr if r["season"] in H],"worst"),
      "by_season":{s:met([r for r in rr if r["season"]==s]) for s in SEASONS}
    }
(OUT/"practical_55_variants.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
