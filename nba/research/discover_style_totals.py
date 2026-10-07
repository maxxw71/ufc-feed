#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"style_totals"
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
    if out==0:return -1.0
    return a/100 if a>0 else 100/abs(a)
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;d=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/d
def met(rr,outk,pk):
    vals=[(r,profit(r.get(pk),r.get(outk))) for r in rr];vals=[x for x in vals if x[1] is not None]
    dec=[r for r,_ in vals if r.get(outk)!=0.5];w=sum(1 for r in dec if r.get(outk)==1);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(dec) if dec else None,"roi":pr/len(vals) if vals else None,"profit":pr,"wilson_low":wilson(w,len(dec))}
def q(v,p):
    v=sorted(x for x in v if x is not None)
    return v[min(len(v)-1,int(p*(len(v)-1)))] if v else None
def sumv(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x+y if x is not None and y is not None else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in ALL and t(g.get("completed")):games[g["game_id"]]=g
style={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_style_rolling.csv.gz")}
roll={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_rolling.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid);h=g.get("home_team_id");a=g.get("away_team_id")
    hs=n(g.get("home_score"));as_=n(g.get("away_score"));line=n(m.get("closing_total_median") if m else None)
    if None in (hs,as_,line):continue
    st,sa=style.get((gid,h),{}),style.get((gid,a),{})
    rt,ra=roll.get((gid,h),{}),roll.get((gid,a),{})
    hc,ac=ctx.get((gid,h),{}),ctx.get((gid,a),{})
    actual=hs+as_;over=1 if actual>line else (0 if actual<line else 0.5);under=1 if actual<line else (0 if actual>line else 0.5)
    rh=n(hc.get("days_since_prev_game"));rr=n(ac.get("days_since_prev_game"))
    rows.append({
      "season":g.get("season"),"total_line":line,"over_result":over,"under_result":under,
      "over_price":n(m.get("over_price_median")),"under_price":n(m.get("under_price_median")),
      "paint_share_sum":sumv(st,sa,"paint_share_last5_avg"),
      "fastbreak_share_sum":sumv(st,sa,"fastbreak_share_last5_avg"),
      "turnover_pts_share_sum":sumv(st,sa,"turnover_points_share_last5_avg"),
      "oreb_rate_sum":sumv(st,sa,"oreb_rate_last5_avg"),
      "turnover_rate_sum":sumv(st,sa,"turnover_rate_last5_avg"),
      "forced_tov_sum":sumv(st,sa,"forced_turnovers_last5_avg"),
      "foulrate_sum":sumv(st,sa,"fouls_per100_last5_avg"),
      "paint_def_sum":sumv(st,sa,"opp_paint_share_last5_avg"),
      "pace_sum":sumv(rt,ra,"_possessions_est_last5_avg"),
      "threepa_sum":sumv(rt,ra,"_three_pa_rate_last5_avg"),
      "ft_rate_sum":sumv(rt,ra,"_ft_rate_last5_avg"),
      "rest_sum":rh+rr if rh is not None and rr is not None else None,
      "total_move":n(m.get("total_move"))
    })
disc=[r for r in rows if r["season"] in A]
features=["paint_share_sum","fastbreak_share_sum","turnover_pts_share_sum","oreb_rate_sum","turnover_rate_sum","forced_tov_sum","foulrate_sum","paint_def_sum",
          "pace_sum","threepa_sum","ft_rate_sum","rest_sum","total_line","total_move"]
conds=[]
for f in features:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<500:continue
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
for lane,outk,pk in (("over","over_result","over_price"),("under","under_result","under_price")):
  for k in (2,3):
    for cs in itertools.combinations(conds,k):
      if len({x[0] for x in cs})!=k:continue
      # Every rule must contain at least one new style variable.
      if not any(x[0] in {"paint_share_sum","fastbreak_share_sum","turnover_pts_share_sum","oreb_rate_sum","turnover_rate_sum","forced_tov_sum","foulrate_sum","paint_def_sum"} for x in cs):continue
      tested+=1
      a=phase(cs,A,outk,pk);b=phase(cs,B,outk,pk);v=phase(cs,V,outk,pk);h=phase(cs,H,outk,pk)
      if a["n"]<120 or b["n"]<70 or v["n"]<35 or h["n"]<100:continue
      if None in (a["hit"],a["roi"],b["hit"],b["roi"],v["hit"],v["roi"],h["hit"],h["roi"]):continue
      if a["hit"]<0.54 or a["roi"]<0.025:continue
      if b["hit"]<0.535 or b["roi"]> -0.005 is False:continue
      if v["hit"]<0.535 or v["roi"]<=0:continue
      if h["hit"]<0.545 or h["roi"]<0.035:continue
      res.append({"side":lane,"rule":" AND ".join(f"{f} {o} {vv:.4g}" for f,o,vv in cs),"status":"style_totals_shadow_candidate",
                  "discA":a,"discB":b,"validation":v,"holdout":h})
res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
for i,r in enumerate(res,1):r["method_id"]=f"NBA_STYLE_TOT_{i:03d}"
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"games":len(rows),"rules_tested":tested,"candidates":len(res),"top_methods":res[:50],
        "policy":"Totals lane requires at least one new style feature. Discovery 2018-21, confirmation 2021-23, validation 2023-24, holdout 2024-26."}
(OUT/"style_totals_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
