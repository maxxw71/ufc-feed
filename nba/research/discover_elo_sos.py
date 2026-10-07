#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from pathlib import Path
from datetime import datetime,timezone
NBA=Path(__file__).resolve().parents[1];DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"elo_sos";OUT.mkdir(parents=True,exist_ok=True)
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
elo={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"elo_sos_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}
rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    if hs is None or as_ is None:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        te,oe=elo.get((gid,tid),{}),elo.get((gid,oid),{})
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"));prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        if ml is None or prob is None:continue
        ep=n(te.get("elo_win_prob"))
        rows.append({"season":g.get("season"),"ml":ml,"won":hs>as_ if home else as_>hs,"market_prob":prob,"is_home":1.0 if home else 0.0,
          "elo_gap":d(te,oe,"elo"),"elo_prob":ep,"elo_market_gap":ep-prob if ep is not None else None,
          "sos5_gap":d(te,oe,"opp_elo_last5_avg"),"sos10_gap":d(te,oe,"opp_elo_last10_avg"),"sos20_gap":d(te,oe,"opp_elo_last20_avg"),
          "qualitywin5_gap":d(te,oe,"quality_win_last5_rate"),"qualitywin10_gap":d(te,oe,"quality_win_last10_rate"),
          "badloss5_adv":d(oe,te,"bad_loss_last5_rate"),"badloss10_adv":d(oe,te,"bad_loss_last10_rate"),
          "season_sos_gap":d(te,oe,"season_sos_elo_avg")})
disc=[r for r in rows if r["season"] in A];features=[k for k in rows[0] if k not in {"season","ml","won","is_home"}] if rows else [];conds=[]
for f in features:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<500:continue
    for qq in (0.2,0.35,0.65,0.8):
        cut=q(vals,qq)
        if cut is not None:conds.append((f,"<=" if qq<.5 else ">=",float(cut)))
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
for k in (2,3):
  for cs in itertools.combinations(conds,k):
    if len({x[0] for x in cs})!=k:continue
    if not any(x[0] not in ("market_prob","is_home") for x in cs):continue
    tested+=1;a=phase(cs,A);b=phase(cs,B);v=phase(cs,V);h=phase(cs,H)
    if a["n"]<70 or b["n"]<45 or v["n"]<22 or h["n"]<45:continue
    if None in (a["hit"],a["roi"],b["hit"],b["roi"],v["hit"],v["roi"],h["hit"],h["roi"]):continue
    if a["hit"]<.72 or a["roi"]<.035 or b["hit"]<.70 or b["roi"]<=0 or v["hit"]<.70 or v["roi"]<=0 or h["hit"]<.72 or h["roi"]<.04:continue
    res.append({"rule":" AND ".join(f"{f} {o} {vv:.4g}" for f,o,vv in cs),"status":"elo_sos_shadow_candidate","discA":a,"discB":b,"validation":v,"holdout":h})
res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
for i,r in enumerate(res,1):r["method_id"]=f"NBA_ELO_{i:03d}"
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"rules_tested":tested,"candidates":len(res),"top_methods":res[:50],
        "policy":"Opponent-adjusted Elo/SOS lane. Elo updates use only completed prior games and no market prices; target market probability is used only as a pregame filter."}
(OUT/"elo_sos_report.json").write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2))
