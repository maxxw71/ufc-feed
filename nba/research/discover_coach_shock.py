#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from pathlib import Path
from datetime import datetime,timezone
NBA=Path(__file__).resolve().parents[1];DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"coach_shock";OUT.mkdir(parents=True,exist_ok=True)
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"};ALL=A|B|V|H
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
def met(rr):
    vals=[(r,pf(r["ml"],r["won"])) for r in rr];vals=[x for x in vals if x[1] is not None]
    if not vals:return {"n":0,"hit":None,"roi":None,"wilson_low":None}
    w=sum(1 for r,_ in vals if r["won"]);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(vals),"roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,len(vals))}
def d(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x-y if x is not None and y is not None else None
def q(vals,p):
    vals=sorted(x for x in vals if x is not None)
    return vals[min(len(vals)-1,int(p*(len(vals)-1)))] if vals else None
games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in ALL and t(g.get("completed")):games[g["game_id"]]=g
coach={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"coach_event_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}
rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    if hs is None or as_ is None:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tc,oc=coach.get((gid,tid),{}),coach.get((gid,oid),{})
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"));prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        if ml is None or prob is None:continue
        rows.append({"season":g.get("season"),"ml":ml,"won":hs>as_ if home else as_>hs,"market_prob":prob,"is_home":1.0 if home else 0.0,
          "coach_events30_gap":d(tc,oc,"coach_events_prev_30d"),
          "coach_events60_gap":d(tc,oc,"coach_events_prev_60d"),
          "coach_hires60_gap":d(tc,oc,"coach_hires_prev_60d"),
          "coach_fires60_gap":d(tc,oc,"coach_fires_prev_60d"),
          "coach_events90_gap":d(tc,oc,"coach_events_prev_90d"),
          "side_recent_change30":1.0 if (n(tc.get("coach_events_prev_30d")) or 0)>0 else 0.0,
          "opp_recent_change30":1.0 if (n(oc.get("coach_events_prev_30d")) or 0)>0 else 0.0,
          "side_recent_change60":1.0 if (n(tc.get("coach_events_prev_60d")) or 0)>0 else 0.0,
          "opp_recent_change60":1.0 if (n(oc.get("coach_events_prev_60d")) or 0)>0 else 0.0})
disc=[r for r in rows if r["season"] in A];features=["market_prob","coach_events30_gap","coach_events60_gap","coach_hires60_gap","coach_fires60_gap","coach_events90_gap"];conds=[]
for f in features:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<300:continue
    for qq in (0.2,0.35,0.65,0.8):
        cut=q(vals,qq)
        if cut is not None:conds.append((f,"<=" if qq<.5 else ">=",float(cut)))
for f in ("side_recent_change30","opp_recent_change30","side_recent_change60","opp_recent_change60","is_home"):conds.append((f,"==",1.0))
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
    if not any(x[0].startswith("coach_") or "recent_change" in x[0] for x in cs):continue
    tested+=1;a=phase(cs,A);b=phase(cs,B);v=phase(cs,V);h=phase(cs,H)
    if a["n"]<45 or b["n"]<25 or v["n"]<12 or h["n"]<25:continue
    if None in (a["hit"],a["roi"],b["hit"],b["roi"],v["hit"],v["roi"],h["hit"],h["roi"]):continue
    if a["hit"]<.68 or a["roi"]<.03 or b["hit"]<.66 or b["roi"]<=0 or v["hit"]<.66 or v["roi"]<=0 or h["hit"]<.70 or h["roi"]<.04:continue
    res.append({"rule":" AND ".join(f"{f} {o} {vv:.4g}" for f,o,vv in cs),"status":"coach_shock_shadow_candidate","discA":a,"discB":b,"validation":v,"holdout":h})
res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
for i,r in enumerate(res,1):r["method_id"]=f"NBA_COACH_{i:03d}"
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"rules_tested":tested,"candidates":len(res),"top_methods":res[:50],
        "policy":"Dated head-coach transaction shock lane. Same-day coach events are excluded; no future coaching data is used."}
(OUT/"coach_shock_report.json").write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2))
