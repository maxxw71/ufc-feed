#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"travel"
OUT.mkdir(parents=True,exist_ok=True)
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"};ALL=A|B|V|H

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def mlprofit(a,w):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (a/100 if a>0 else 100/abs(a)) if w else -1.0
def totprofit(a,out):
    a=n(a)
    if a is None or abs(a)<100:return None
    if out==0.5:return 0.0
    if out==0:return -1.0
    return a/100 if a>0 else 100/abs(a)
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;d=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/d
def metrics(rr,outk,pk,kind):
    vals=[]
    for r in rr:
        out=r.get(outk)
        p=mlprofit(r.get(pk),bool(out)) if kind=="ml" else totprofit(r.get(pk),out)
        if p is not None:vals.append((r,p))
    dec=[r for r,_ in vals if kind=="ml" or r.get(outk)!=0.5]
    w=sum(1 for r in dec if r.get(outk)==1);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(dec) if dec else None,"roi":pr/len(vals) if vals else None,"profit":pr,"wilson_low":wilson(w,len(dec))}
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
travel={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"travel_pregame.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

sides=[];totals=[]
for gid,g in games.items():
    m=market.get(gid);h=g.get("home_team_id");a=g.get("away_team_id")
    if not m:continue
    hs=n(g.get("home_score"));aas=n(g.get("away_score"))
    if hs is None or aas is None:continue
    ht,at=travel.get((gid,h),{}),travel.get((gid,a),{})
    hc,ac=ctx.get((gid,h),{}),ctx.get((gid,a),{})
    for home in (True,False):
        tid=h if home else a;oid=a if home else h
        tt,ot=(ht,at) if home else (at,ht)
        tc,oc=(hc,ac) if home else (ac,hc)
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        if ml is None or prob is None:continue
        sides.append({
          "season":g.get("season"),"ml":ml,"won":1 if (hs>aas if home else aas>hs) else 0,"market_prob":prob,"is_home":1.0 if home else 0.0,
          "travel_gap":d(ot,tt,"travel_miles_from_prev"),
          "travel3d_adv":d(ot,tt,"travel_miles_prev_3d_including_arrival"),
          "travel5d_adv":d(ot,tt,"travel_miles_prev_5d_including_arrival"),
          "travel7d_adv":d(ot,tt,"travel_miles_prev_7d_including_arrival"),
          "timezone_adv":d(ot,tt,"eastward_shift_hours"),
          "opp_travel":n(ot.get("travel_miles_from_prev")),"side_travel":n(tt.get("travel_miles_from_prev")),
          "opp_east_shift":n(ot.get("eastward_shift_hours")),"side_east_shift":n(tt.get("eastward_shift_hours")),
          "rest_diff":d(tc,oc,"days_since_prev_game")
        })
    line=n(m.get("closing_total_median"))
    if line is not None:
        actual=hs+aas
        totals.append({
          "season":g.get("season"),"over_result":1 if actual>line else (0 if actual<line else 0.5),"under_result":1 if actual<line else (0 if actual>line else 0.5),
          "over_price":n(m.get("over_price_median")),"under_price":n(m.get("under_price_median")),"total_line":line,
          "combined_travel":(n(ht.get("travel_miles_from_prev"))+n(at.get("travel_miles_from_prev"))) if None not in (n(ht.get("travel_miles_from_prev")),n(at.get("travel_miles_from_prev"))) else None,
          "combined_travel3d":(n(ht.get("travel_miles_prev_3d_including_arrival"))+n(at.get("travel_miles_prev_3d_including_arrival"))) if None not in (n(ht.get("travel_miles_prev_3d_including_arrival")),n(at.get("travel_miles_prev_3d_including_arrival"))) else None,
          "max_east_shift":max(x for x in [n(ht.get("eastward_shift_hours")),n(at.get("eastward_shift_hours"))] if x is not None) if any(x is not None for x in [n(ht.get("eastward_shift_hours")),n(at.get("eastward_shift_hours"))]) else None,
          "combined_tz_shift":sum(abs(x) for x in [n(ht.get("timezone_shift_hours")),n(at.get("timezone_shift_hours"))] if x is not None) if any(x is not None for x in [n(ht.get("timezone_shift_hours")),n(at.get("timezone_shift_hours"))]) else None,
          "rest_sum":(n(hc.get("days_since_prev_game"))+n(ac.get("days_since_prev_game"))) if None not in (n(hc.get("days_since_prev_game")),n(ac.get("days_since_prev_game"))) else None
        })

def buildconds(rows,features):
    disc=[r for r in rows if r["season"] in A];out=[]
    for f in features:
        vals=[r.get(f) for r in disc if r.get(f) is not None]
        if len(vals)<500:continue
        for qq in (0.20,0.35,0.65,0.80):
            cut=q(vals,qq)
            if cut is not None:out.append((f,"<=" if qq<0.5 else ">=",float(cut)))
    return out
def meet(r,cs):
    for f,o,v in cs:
        x=r.get(f)
        if x is None:return False
        if o==">=" and x<v:return False
        if o=="<=" and x>v:return False
        if o=="==" and x!=v:return False
    return True
def phase(rows,cs,ss,outk,pk,kind):return metrics([r for r in rows if r["season"] in ss and meet(r,cs)],outk,pk,kind)

results=[];tested=0
ml_travel={"travel_gap","travel3d_adv","travel5d_adv","travel7d_adv","timezone_adv","opp_travel","side_travel","opp_east_shift","side_east_shift"}
conds=buildconds(sides,list(ml_travel)+["market_prob","rest_diff"])+[("is_home","==",0.0),("is_home","==",1.0)]
for k in (2,3):
  for cs in itertools.combinations(conds,k):
    if len({x[0] for x in cs})!=k or not any(x[0] in ml_travel for x in cs):continue
    tested+=1
    aa=phase(sides,cs,A,"won","ml","ml");bb=phase(sides,cs,B,"won","ml","ml");vv=phase(sides,cs,V,"won","ml","ml");hh=phase(sides,cs,H,"won","ml","ml")
    if aa["n"]<70 or bb["n"]<45 or vv["n"]<22 or hh["n"]<45:continue
    if None in (aa["hit"],aa["roi"],bb["hit"],bb["roi"],vv["hit"],vv["roi"],hh["hit"],hh["roi"]):continue
    if aa["hit"]<0.72 or aa["roi"]<0.035 or bb["hit"]<0.70 or bb["roi"]<=0 or vv["hit"]<0.70 or vv["roi"]<=0 or hh["hit"]<0.72 or hh["roi"]<0.04:continue
    results.append({"lane":"moneyline","rule":" AND ".join(f"{f} {o} {v:.4g}" for f,o,v in cs),"discA":aa,"discB":bb,"validation":vv,"holdout":hh})

tot_travel={"combined_travel","combined_travel3d","max_east_shift","combined_tz_shift"}
conds2=buildconds(totals,list(tot_travel)+["rest_sum","total_line"])
for lane,outk,pk in (("over","over_result","over_price"),("under","under_result","under_price")):
  for k in (2,3):
    for cs in itertools.combinations(conds2,k):
      if len({x[0] for x in cs})!=k or not any(x[0] in tot_travel for x in cs):continue
      tested+=1
      aa=phase(totals,cs,A,outk,pk,"tot");bb=phase(totals,cs,B,outk,pk,"tot");vv=phase(totals,cs,V,outk,pk,"tot");hh=phase(totals,cs,H,outk,pk,"tot")
      if aa["n"]<120 or bb["n"]<70 or vv["n"]<35 or hh["n"]<100:continue
      if None in (aa["hit"],aa["roi"],bb["hit"],bb["roi"],vv["hit"],vv["roi"],hh["hit"],hh["roi"]):continue
      if aa["hit"]<0.54 or aa["roi"]<0.025 or bb["hit"]<0.53 or bb["roi"]< -0.005 or vv["hit"]<0.53 or vv["roi"]<=0 or hh["hit"]<0.545 or hh["roi"]<0.035:continue
      results.append({"lane":lane,"rule":" AND ".join(f"{f} {o} {v:.4g}" for f,o,v in cs),"discA":aa,"discB":bb,"validation":vv,"holdout":hh})

results.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
for i,r in enumerate(results,1):r["method_id"]=f"NBA_TRAVEL_{i:03d}";r["status"]="travel_shadow_candidate"
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"side_rows":len(sides),"total_rows":len(totals),"rules_tested":tested,"candidates":len(results),"top_methods":results[:50],
        "policy":"Travel rules require at least one actual venue-distance/time-zone feature. All schedule, venue and prior-trip inputs are known before tipoff."}
(OUT/"travel_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
