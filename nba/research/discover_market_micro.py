#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"market_micro"
OUT.mkdir(parents=True,exist_ok=True)
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"};ALL=A|B|V|H

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def imp(a):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (-a)/((-a)+100) if a<0 else 100/(a+100)
def pf(a,w):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (a/100 if a>0 else 100/abs(a)) if w else -1.0
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;d=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/d
def met(rr):
    vals=[(r,pf(r["ml"],r["won"])) for r in rr];vals=[x for x in vals if x[1] is not None]
    if not vals:return {"n":0,"hit":None,"roi":None,"wilson_low":None}
    w=sum(1 for r,_ in vals if r["won"]);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(vals),"roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,len(vals))}
def q(v,p):
    v=sorted(x for x in v if x is not None)
    return v[min(len(v)-1,int(p*(len(v)-1)))] if v else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in ALL and t(g.get("completed")):games[g["game_id"]]=g
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    if hs is None or as_ is None:continue
    spread_min=n(m.get("closing_spread_min"));spread_max=n(m.get("closing_spread_max"))
    total_min=n(m.get("closing_total_min"));total_max=n(m.get("closing_total_max"))
    for home in (True,False):
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        best=n(m.get("home_moneyline_best") if home else m.get("away_moneyline_best"))
        worst=n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst"))
        prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        if ml is None or prob is None:continue
        ib=imp(best);iw=imp(worst);im=imp(ml)
        rows.append({
          "season":g.get("season"),"ml":ml,"won":hs>as_ if home else as_>hs,
          "market_prob":prob,
          "provider_count":n(m.get("pregame_provider_count")),
          "moneyline_hold":n(m.get("moneyline_hold")),
          "ml_prob_dispersion":abs(iw-ib) if iw is not None and ib is not None else None,
          "best_vs_median_prob_edge":(im-ib) if im is not None and ib is not None else None,
          "worst_vs_median_prob_gap":(iw-im) if iw is not None and im is not None else None,
          "spread_dispersion":abs(spread_max-spread_min) if spread_min is not None and spread_max is not None else None,
          "total_dispersion":abs(total_max-total_min) if total_min is not None and total_max is not None else None,
          "spread_move":n(m.get("spread_move")),
          "total_move":n(m.get("total_move")),
          "is_home":1.0 if home else 0.0
        })

disc=[r for r in rows if r["season"] in A]
features=["market_prob","provider_count","moneyline_hold","ml_prob_dispersion","best_vs_median_prob_edge","worst_vs_median_prob_gap",
          "spread_dispersion","total_dispersion","spread_move","total_move"]
conds=[]
for f in features:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<300:continue
    for qq in (0.15,0.25,0.75,0.85):
        cut=q(vals,qq)
        if cut is not None:conds.append((f,"<=" if qq<0.5 else ">=",float(cut)))
conds += [("is_home","==",0.0),("is_home","==",1.0)]

def meet(r,cs):
    for f,o,v in cs:
        x=r.get(f)
        if x is None:return False
        if o==">=" and x<v:return False
        if o=="<=" and x>v:return False
        if o=="==" and x!=v:return False
    return True
def phase(cs,ss):return met([r for r in rows if r["season"] in ss and meet(r,cs)])

res=[];tested=0
for k in (1,2,3):
    for cs in itertools.combinations(conds,k):
        if len({x[0] for x in cs})!=k:continue
        tested+=1
        a=phase(cs,A);b=phase(cs,B);v=phase(cs,V);h=phase(cs,H)
        if a["n"]<80 or b["n"]<50 or v["n"]<25 or h["n"]<50:continue
        if None in (a["hit"],a["roi"],b["hit"],b["roi"],v["hit"],v["roi"],h["hit"],h["roi"]):continue
        if a["hit"]<0.70 or a["roi"]<0.03:continue
        if b["hit"]<0.69 or b["roi"]<=0:continue
        if v["hit"]<0.69 or v["roi"]<=0:continue
        if h["hit"]<0.72 or h["roi"]<0.04:continue
        res.append({"rule":" AND ".join(f"{f} {o} {vv:.4g}" for f,o,vv in cs),
                    "status":"market_micro_shadow_candidate","discA":a,"discB":b,"validation":v,"holdout":h})
res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
for i,r in enumerate(res,1):r["method_id"]=f"NBA_MKT_{i:03d}"
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"rules_tested":tested,"candidates":len(res),"top_methods":res[:50],
        "policy":"Market-only/microstructure discovery using executable-book dispersion, hold and open/close context. No team performance, player or physical inputs."}
(OUT/"market_micro_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
