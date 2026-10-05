#!/usr/bin/env python3
import csv,gzip,json,math,statistics
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data"; F=NBA/"features"; OUT=NBA/"research"/"v2"
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
TRAIN=set(SEASONS[:5]); VALID={"2023-24"}; HOLDOUT={"2024-25","2025-26"}

RULES={
 "ML001":{"net5_gap":7.759,"rest_diff":2.0},
 "V2_001":{"net5_gap":9.637235800525477,"starter_churn_adv":2.0},
 "V2_003":{"def5_adv":10.430476353289166,"ts5_gap":0.0362475832398097},
}

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    if v in (None,"","None","nan","NaN"):return None
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def profit(a,won):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (a/100 if a>0 else 100/abs(a)) if won else -1.0
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;d=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/d
def metrics(rr,price="median"):
    vals=[]
    for r in rr:
        p=profit(r[price],r["won"])
        if p is not None:vals.append((r,p))
    if not vals:return {"n":0,"hit":None,"roi":None}
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
line={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"lineup_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));aas=n(g.get("away_score"))
    if hs is None or aas is None:continue
    for side in ("home","away"):
        home=side=="home";tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tr=roll.get((gid,tid),{});orr=roll.get((gid,oid),{})
        tc=ctx.get((gid,tid),{});oc=ctx.get((gid,oid),{})
        tl=line.get((gid,tid),{});ol=line.get((gid,oid),{})
        med=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        best=n(m.get("home_moneyline_best") if home else m.get("away_moneyline_best"))
        worst=n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst"))
        if med is None or abs(med)<100:continue
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        row={
          "season":g.get("season"),"game_id":gid,"team_id":tid,"team":g.get("home_team") if home else g.get("away_team"),
          "side":side,"median":med,"best":best,"worst":worst,"won":(hs>aas) if home else (aas>hs),
          "net5_gap":d(tr,orr,"_net_rating_est_last5_avg"),"def5_adv":d(orr,tr,"_def_rating_est_last5_avg"),
          "ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg"),
          "rest_diff":rest-orest if rest is not None and orest is not None else None,
          "starter_churn_adv":d(ol,tl,"prior5_distinct_starters"),
        }
        rows.append(row)

def match(r,name):
    rule=RULES[name]
    return all(r.get(k) is not None and r[k]>=v for k,v in rule.items())

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rules":RULES,"methods":{}}
signals={}
for name in RULES:
    rr=[r for r in rows if match(r,name)];signals[name]=rr
    by_season={s:metrics([r for r in rr if r["season"]==s]) for s in SEASONS}
    price_stress={p:{
      "overall":metrics(rr,p),
      "discovery":metrics([r for r in rr if r["season"] in TRAIN],p),
      "validation":metrics([r for r in rr if r["season"] in VALID],p),
      "holdout":metrics([r for r in rr if r["season"] in HOLDOUT],p)
    } for p in ("best","median","worst")}
    teams=defaultdict(list)
    for r in rr:teams[r["team"]].append(r)
    top=sorted(({"team":tm,**metrics(x),"share":len(x)/len(rr)} for tm,x in teams.items()),key=lambda x:x["n"],reverse=True)
    report["methods"][name]={
      "overall":metrics(rr),"by_season":by_season,"price_stress":price_stress,
      "max_team_share":top[0]["share"] if top else 0,"top_teams":top[:8],
      "favorite":metrics([r for r in rr if r["median"]<0]),
      "underdog":metrics([r for r in rr if r["median"]>0]),
    }

# Signal overlap and union economics.
overlap={}
names=list(RULES)
for i,a in enumerate(names):
    sa={(r["game_id"],r["team_id"]) for r in signals[a]}
    for b in names[i+1:]:
        sb={(r["game_id"],r["team_id"]) for r in signals[b]}
        inter=sa&sb;union=sa|sb
        lookup={(r["game_id"],r["team_id"]):r for r in rows}
        overlap[f"{a}__{b}"]={
          "a_n":len(sa),"b_n":len(sb),"intersection_n":len(inter),"union_n":len(union),
          "jaccard":len(inter)/len(union) if union else 0,
          "intersection_metrics":metrics([lookup[k] for k in inter]),
          "union_metrics":metrics([lookup[k] for k in union]),
          "holdout_union_metrics":metrics([lookup[k] for k in union if lookup[k]["season"] in HOLDOUT])
        }
report["overlap"]=overlap

(OUT/"lead_robustness_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
