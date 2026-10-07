#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"officials"
OUT.mkdir(parents=True,exist_ok=True)
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"};ALL=A|B|V|H
def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def profit(a,out):
    a=n(a)
    if a is None or abs(a)<100:return None
    if out==0.5:return 0.0
    if not out:return -1.0
    return a/100 if a>0 else 100/abs(a)
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;d=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/d
def met(rr,outk,pricek):
    vals=[(r,profit(r.get(pricek),r.get(outk))) for r in rr];vals=[x for x in vals if x[1] is not None]
    dec=[r for r,_ in vals if r.get(outk)!=0.5];w=sum(1 for r in dec if r.get(outk)==1);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(dec) if dec else None,"roi":pr/len(vals) if vals else None,"profit":pr,"wilson_low":wilson(w,len(dec))}
def q(v,p):
    v=sorted(x for x in v if x is not None)
    return v[min(len(v)-1,int(p*(len(v)-1)))] if v else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in ALL and t(g.get("completed")):games[g["game_id"]]=g
crew={r.get("game_id"):r for r in rgz(F/"official_crew_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}
rows=[]
for gid,g in games.items():
    c=crew.get(gid);m=market.get(gid)
    if not c or not m:continue
    hs=n(g.get("home_score"));as_=n(g.get("away_score"));tot=n(m.get("closing_total_median"))
    if None in (hs,as_,tot):continue
    actual=hs+as_;over=1 if actual>tot else (0 if actual<tot else 0.5);under=1 if actual<tot else (0 if actual>tot else 0.5)
    rows.append({
      "season":g.get("season"),"game_id":gid,
      "crew_prior_games_min":n(c.get("crew_prior_games_min")),"crew_prior_games_mean":n(c.get("crew_prior_games_mean")),
      "crew_total_avg":n(c.get("crew_total_avg")),"crew_fouls_avg":n(c.get("crew_fouls_avg")),"crew_fta_avg":n(c.get("crew_fta_avg")),
      "crew_home_margin_avg":n(c.get("crew_home_margin_avg")),"crew_home_win_pct":n(c.get("crew_home_win_pct")),
      "total_line":tot,"over_result":over,"under_result":under,"over_price":n(m.get("over_price_median")),"under_price":n(m.get("under_price_median")),
      "home_result":1 if hs>as_ else 0,"home_price":n(m.get("home_moneyline_median")),"home_prob":n(m.get("home_implied_probability_devig"))
    })
disc=[r for r in rows if r["season"] in A]
features=["crew_prior_games_min","crew_total_avg","crew_fouls_avg","crew_fta_avg","crew_home_margin_avg","crew_home_win_pct","total_line","home_prob"]
conds=[]
for f in features:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<300:continue
    for qq in (0.20,0.35,0.65,0.80):
        cut=q(vals,qq)
        if cut is not None:conds.append((f,"<=" if qq<0.5 else ">=",float(cut)))
def meet(r,cs):
    for f,o,v in cs:
        x=r.get(f)
        if x is None:return False
        if o==">=" and x<v:return False
        if o=="<=" and x>v:return False
    return True
def phase(cs,ss,outk,pk):return met([r for r in rows if r["season"] in ss and meet(r,cs)],outk,pk)

res=[];tested=0
for lane,outk,pk,minhit in (("over","over_result","over_price",0.54),("under","under_result","under_price",0.54),("home_ml","home_result","home_price",0.70)):
  for k in (1,2,3):
    for cs in itertools.combinations(conds,k):
      if len({x[0] for x in cs})!=k:continue
      tested+=1
      a=phase(cs,A,outk,pk);b=phase(cs,B,outk,pk);v=phase(cs,V,outk,pk);h=phase(cs,H,outk,pk)
      if a["n"]<80 or b["n"]<45 or v["n"]<22 or h["n"]<45:continue
      if None in (a["hit"],a["roi"],b["hit"],b["roi"],v["hit"],v["roi"],h["hit"],h["roi"]):continue
      if a["hit"]<minhit or a["roi"]<0.025:continue
      if b["hit"]<minhit or b["roi"]<=0:continue
      if v["hit"]<minhit or v["roi"]<=0:continue
      if h["hit"]<minhit or h["roi"]<0.03:continue
      res.append({"lane":lane,"rule":" AND ".join(f"{f} {o} {vv:.4g}" for f,o,vv in cs),"status":"officials_shadow_candidate",
                  "discA":a,"discB":b,"validation":v,"holdout":h})
res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
for i,r in enumerate(res,1):r["method_id"]=f"NBA_REF_{i:03d}"
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"games":len(rows),"rules_tested":tested,"candidates":len(res),"top_methods":res[:50],
        "policy":"Target-game crew identities are required, but every referee tendency input is calculated only from earlier games. Prospective promotion requires pregame official assignments."}
(OUT/"officials_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
