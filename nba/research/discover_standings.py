#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"standings"
OUT.mkdir(parents=True,exist_ok=True)
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
    p=w/nn;dd=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/dd
def met(rr):
    vals=[(r,pf(r["ml"],r["won"])) for r in rr];vals=[x for x in vals if x[1] is not None]
    if not vals:return {"n":0,"hit":None,"roi":None,"wilson_low":None}
    w=sum(1 for r,_ in vals if r["won"]);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(vals),"roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,len(vals))}
def d(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x-y if x is not None and y is not None else None
def q(v,p):
    v=sorted(x for x in v if x is not None)
    return v[min(len(v)-1,int(p*(len(v)-1)))] if v else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in ALL and t(g.get("completed")):games[g["game_id"]]=g
stand={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"standings_pregame.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}
rows=[]
for gid,g in games.items():
    m=market.get(gid);hs=n(g.get("home_score"));aas=n(g.get("away_score"))
    if not m or hs is None or aas is None:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        ts,os=stand.get((gid,tid),{}),stand.get((gid,oid),{})
        tc,oc=ctx.get((gid,tid),{}),ctx.get((gid,oid),{})
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"));prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        if ml is None or prob is None:continue
        side_gp=n(ts.get("prior_games"));opp_gp=n(os.get("prior_games"))
        rows.append({
          "season":g.get("season"),"ml":ml,"won":hs>aas if home else aas>hs,"market_prob":prob,"is_home":1.0 if home else 0.0,
          "rank_adv":d(os,ts,"conference_rank"),"winpct_gap":d(ts,os,"prior_win_pct"),"last10_gap":d(ts,os,"last10_win_pct"),
          "streak_gap":d(ts,os,"current_streak"),
          "seed6_proximity_adv":d(os,ts,"abs_gap_seed6"),"seed10_proximity_adv":d(os,ts,"abs_gap_seed10"),
          "side_rank":n(ts.get("conference_rank")),"opp_rank":n(os.get("conference_rank")),
          "side_seed6_gap":n(ts.get("winpct_gap_to_seed6")),"opp_seed6_gap":n(os.get("winpct_gap_to_seed6")),
          "side_seed10_gap":n(ts.get("winpct_gap_to_seed10")),"opp_seed10_gap":n(os.get("winpct_gap_to_seed10")),
          "side_gp":side_gp,"both_40plus":1.0 if side_gp is not None and opp_gp is not None and side_gp>=40 and opp_gp>=40 else 0.0,
          "both_55plus":1.0 if side_gp is not None and opp_gp is not None and side_gp>=55 and opp_gp>=55 else 0.0,
          "rest_diff":d(tc,oc,"days_since_prev_game")
        })
stand_feats={"rank_adv","winpct_gap","last10_gap","streak_gap","seed6_proximity_adv","seed10_proximity_adv","side_rank","opp_rank",
             "side_seed6_gap","opp_seed6_gap","side_seed10_gap","opp_seed10_gap","side_gp","both_40plus","both_55plus"}
disc=[r for r in rows if r["season"] in A]
conds=[]
for f in list(stand_feats-{"both_40plus","both_55plus"})+["market_prob","rest_diff"]:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<500:continue
    for qq in (0.20,0.35,0.65,0.80):
        cut=q(vals,qq)
        if cut is not None:conds.append((f,"<=" if qq<0.5 else ">=",float(cut)))
conds += [("both_40plus","==",1.0),("both_55plus","==",1.0),("is_home","==",0.0),("is_home","==",1.0)]
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
        fs={x[0] for x in cs}
        if len(fs)!=k or not (fs & stand_feats):continue
        tested+=1
        aa=phase(cs,A);bb=phase(cs,B);vv=phase(cs,V);hh=phase(cs,H)
        if aa["n"]<70 or bb["n"]<45 or vv["n"]<22 or hh["n"]<45:continue
        if None in (aa["hit"],aa["roi"],bb["hit"],bb["roi"],vv["hit"],vv["roi"],hh["hit"],hh["roi"]):continue
        if aa["hit"]<0.72 or aa["roi"]<0.035 or bb["hit"]<0.70 or bb["roi"]<=0 or vv["hit"]<0.70 or vv["roi"]<=0 or hh["hit"]<0.72 or hh["roi"]<0.04:continue
        res.append({"rule":" AND ".join(f"{f} {o} {v:.4g}" for f,o,v in cs),"status":"standings_shadow_candidate","discA":aa,"discB":bb,"validation":vv,"holdout":hh})
res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
for i,r in enumerate(res,1):r["method_id"]=f"NBA_STAND_{i:03d}"
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"rules_tested":tested,"candidates":len(res),"top_methods":res[:50],
        "policy":"Standings/motivation lane uses only point-in-time conference records/ranks and basic market/rest context. No future standings or season-end seed are used."}
(OUT/"standings_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
