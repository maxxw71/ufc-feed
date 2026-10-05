#!/usr/bin/env python3
import csv,gzip,json,math,statistics
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"totals"
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
TRAIN=set(SEASONS[:5]);VALID={"2023-24"};HOLDOUT={"2024-25","2025-26"}

RULES={
 "TOT_001":lambda r:r["pace5"] is not None and r["ts5_sum"] is not None and r["pace5"]<=101 and r["ts5_sum"]<=1.113,
 "TOT_002":lambda r:r["threepa5_sum"] is not None and r["lineup_churn_sum"] is not None and r["threepa5_sum"]>=0.811 and r["lineup_churn_sum"]>=16,
 "TOT_003":lambda r:r["lineup_churn_sum"] is not None and r["def5_sum"] is not None and r["lineup_churn_sum"]>=14 and r["def5_sum"]<=217.8,
}

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def pf(a,out):
    a=n(a)
    if a is None or abs(a)<100:return None
    if out==0.5:return 0.0
    return (a/100 if a>0 else 100/abs(a)) if out==1 else -1.0
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;d=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/d
def met(rr,price):
    vals=[]
    for r in rr:
        p=pf(r.get(price),r["over_result"])
        if p is not None:vals.append((r,p))
    dec=[r for r,_ in vals if r["over_result"]!=0.5]
    w=sum(1 for r in dec if r["over_result"]==1);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(dec) if dec else None,"roi":pr/len(vals) if vals else None,"profit":pr,"wilson_low":wilson(w,len(dec))}
def sumv(a,b,k):
    x=n(a.get(k));y=n(b.get(k))
    return x+y if x is not None and y is not None else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in SEASONS and t(g.get("completed")):games[g["game_id"]]=g
roll={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_rolling.csv.gz")}
line={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"lineup_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    h=g.get("home_team_id");a=g.get("away_team_id");hr=roll.get((gid,h),{});ar=roll.get((gid,a),{});hl=line.get((gid,h),{});al=line.get((gid,a),{})
    hs=n(g.get("home_score"));as_=n(g.get("away_score"));tot=n(m.get("closing_total_median"))
    if None in (hs,as_,tot):continue
    actual=hs+as_;over=1 if actual>tot else (0 if actual<tot else 0.5)
    paces=[n(hr.get("_possessions_est_last5_avg")),n(ar.get("_possessions_est_last5_avg"))];paces=[x for x in paces if x is not None]
    rows.append({
      "season":g.get("season"),"game_id":gid,"over_result":over,
      "median":n(m.get("over_price_median")),"best":n(m.get("over_price_best")),"worst":n(m.get("over_price_worst")),
      "pace5":statistics.mean(paces) if paces else None,
      "ts5_sum":sumv(hr,ar,"_true_shooting_est_last5_avg"),
      "threepa5_sum":sumv(hr,ar,"_three_pa_rate_last5_avg"),
      "lineup_churn_sum":sumv(hl,al,"prior5_distinct_starters"),
      "def5_sum":sumv(hr,ar,"_def_rating_est_last5_avg")
    })

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"methods":{}}
for name,fn in RULES.items():
    rr=[r for r in rows if fn(r)]
    report["methods"][name]={
      "overall":met(rr,"median"),
      "discovery":met([r for r in rr if r["season"] in TRAIN],"median"),
      "validation":met([r for r in rr if r["season"] in VALID],"median"),
      "holdout":met([r for r in rr if r["season"] in HOLDOUT],"median"),
      "worst_price":{
        "overall":met(rr,"worst"),
        "discovery":met([r for r in rr if r["season"] in TRAIN],"worst"),
        "validation":met([r for r in rr if r["season"] in VALID],"worst"),
        "holdout":met([r for r in rr if r["season"] in HOLDOUT],"worst"),
      },
      "by_season":{s:met([r for r in rr if r["season"]==s],"median") for s in SEASONS}
    }

(OUT/"totals_lead_audit.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
