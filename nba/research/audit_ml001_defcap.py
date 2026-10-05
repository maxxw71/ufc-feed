#!/usr/bin/env python3
import csv,gzip,json,math,statistics
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"ml001_elite"
OUT.mkdir(parents=True,exist_ok=True)

SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
DISC_A={"2018-19","2019-20","2020-21"};DISC_B={"2021-22","2022-23"};VALID={"2023-24"};HOLDOUT={"2024-25","2025-26"}
BASE_NET=7.759;BASE_REST=2.0;BASE_DEF_CAP=5.492107833295265

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    if v in (None,"","None","nan","NaN"):return None
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
def metrics(rr,price="median"):
    vals=[]
    for r in rr:
        p=pf(r.get(price),r["won"])
        if p is not None:vals.append((r,p))
    if not vals:return {"n":0,"hit":None,"roi":None,"profit":0,"wilson_low":None}
    w=sum(1 for r,_ in vals if r["won"]);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"losses":len(vals)-w,"hit":w/len(vals),"roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,len(vals))}
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
        net5=d(tr,orr,"_net_rating_est_last5_avg");defadv=d(orr,tr,"_def_rating_est_last5_avg")
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        med=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        best=n(m.get("home_moneyline_best") if home else m.get("away_moneyline_best"));worst=n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst"))
        if None in (net5,defadv,rest,orest,med):continue
        if net5<BASE_NET or rest-orest<BASE_REST:continue
        rows.append({
          "season":g.get("season"),"game_id":gid,"game_date":g.get("game_date"),"team_id":tid,
          "team":g.get("home_team") if home else g.get("away_team"),"opponent":g.get("away_team") if home else g.get("home_team"),
          "median":med,"best":best,"worst":worst,"won":hs>as_ if home else as_>hs,
          "is_home":home,"def5_adv":defadv,"net5_gap":net5,"rest_diff":rest-orest
        })

def select(cap):
    return [r for r in rows if r["def5_adv"]<=cap]
def phase(rr,ss,price="median"):return metrics([r for r in rr if r["season"] in ss],price)

base=select(BASE_DEF_CAP)
report={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "method":{"id":"NBA_ML001_DEF_CAP","rule":f"net5_gap >= {BASE_NET} AND rest_diff >= {BASE_REST} AND def5_adv <= {BASE_DEF_CAP}","status":"elite_research"},
 "overall":metrics(base),
 "phases":{"discA":phase(base,DISC_A),"discB":phase(base,DISC_B),"validation":phase(base,VALID),"holdout":phase(base,HOLDOUT)},
 "price_stress":{p:{"overall":metrics(base,p),"discA":phase(base,DISC_A,p),"discB":phase(base,DISC_B,p),"validation":phase(base,VALID,p),"holdout":phase(base,HOLDOUT,p)} for p in ("best","median","worst")},
 "by_season":{s:metrics([r for r in base if r["season"]==s]) for s in SEASONS},
 "by_location":{"home":metrics([r for r in base if r["is_home"]]),"away":metrics([r for r in base if not r["is_home"]])}
}

# Nearby cap audit. This is robustness only; no new threshold is selected from later phases.
caps=[2.0,3.0,4.0,5.0,BASE_DEF_CAP,6.0,7.0,8.0]
sens=[]
for cap in caps:
    rr=select(cap)
    a=phase(rr,DISC_A);b=phase(rr,DISC_B);v=phase(rr,VALID);h=phase(rr,HOLDOUT)
    sens.append({"def5_cap":cap,"overall_n":len(rr),"overall_hit":metrics(rr)["hit"],"overall_roi":metrics(rr)["roi"],
                 "discA_n":a["n"],"discA_hit":a["hit"],"discA_roi":a["roi"],
                 "discB_n":b["n"],"discB_hit":b["hit"],"discB_roi":b["roi"],
                 "validation_n":v["n"],"validation_hit":v["hit"],"validation_roi":v["roi"],
                 "holdout_n":h["n"],"holdout_hit":h["hit"],"holdout_roi":h["roi"]})
report["cap_sensitivity"]=sens

# Team concentration / leave-one-season-out.
teams=defaultdict(list)
for r in base:teams[r["team"]].append(r)
teamstats=[]
for team,rr in teams.items():
    m=metrics(rr);m.update({"team":team,"share":len(rr)/len(base) if base else 0});teamstats.append(m)
teamstats.sort(key=lambda x:x["n"],reverse=True)
report["top_teams"]=teamstats[:15];report["max_team_share"]=teamstats[0]["share"] if teamstats else 0
report["leave_one_season_out"]={s:metrics([r for r in base if r["season"]!=s]) for s in SEASONS}
report["exclude_covid"]=metrics([r for r in base if r["season"] not in {"2019-20","2020-21"}])

# Audit signal list.
fields=[]
for r in base:
    for k in r:
        if k not in fields:fields.append(k)
with open(OUT/"signals.csv","w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields or ["_empty"]);w.writeheader();w.writerows(base)
with open(OUT/"cap_sensitivity.csv","w",encoding="utf-8",newline="") as f:
    fs=list(sens[0].keys()) if sens else ["_empty"];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows(sens)
(OUT/"robustness_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
