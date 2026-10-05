#!/usr/bin/env python3
import csv,gzip,json,math,statistics
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data"
F=NBA/"features"
OUT=NBA/"research"/"ml001"
OUT.mkdir(parents=True,exist_ok=True)

SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
TRAIN=set(SEASONS[:5]); VALID={"2023-24"}; HOLDOUT={"2024-25","2025-26"}
BASE_NET=7.759
BASE_REST=2.0

def read_gz(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def write_csv(path,rows):
    rows=list(rows); fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    with open(path,"w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields or ["_empty"]);w.writeheader();w.writerows(rows)

def num(v):
    if v in (None,"","None","nan","NaN"):return None
    try:return float(v)
    except:return None

def truth(v):return str(v).lower() in ("true","1","yes")

def implied(a):
    a=num(a)
    if a is None or abs(a)<100:return None
    return (-a)/((-a)+100) if a<0 else 100/(a+100)

def profit(a,won):
    a=num(a)
    if a is None or abs(a)<100:return None
    if not won:return -1.0
    return a/100.0 if a>0 else 100.0/abs(a)

def wilson(w,n,z=1.96):
    if n<=0:return None
    p=w/n; den=1+z*z/n
    return (p+z*z/(2*n)-z*math.sqrt((p*(1-p)+z*z/(4*n))/n))/den

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in read_gz(p):
        if g.get("season") in SEASONS and truth(g.get("completed")):
            games[g["game_id"]]=g
team_roll={(r.get("game_id"),r.get("team_id")):r for r in read_gz(F/"team_rolling.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in read_gz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in read_gz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=num(g.get("home_score")); aas=num(g.get("away_score"))
    if hs is None or aas is None:continue
    for side in ("home","away"):
        home=side=="home"
        tid=g.get("home_team_id") if home else g.get("away_team_id")
        oid=g.get("away_team_id") if home else g.get("home_team_id")
        tr=team_roll.get((gid,tid),{}); orr=team_roll.get((gid,oid),{})
        tc=ctx.get((gid,tid),{}); oc=ctx.get((gid,oid),{})
        net=num(tr.get("_net_rating_est_last5_avg")); onet=num(orr.get("_net_rating_est_last5_avg"))
        rest=num(tc.get("days_since_prev_game")); orest=num(oc.get("days_since_prev_game"))
        ml=num(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        if None in (net,onet,rest,orest,ml):continue
        score=hs if home else aas; oscore=aas if home else hs
        rows.append({
          "season":g.get("season"),"game_id":gid,"game_date":g.get("game_date"),"side":side,
          "team_id":tid,"opponent_id":oid,"team":g.get("home_team") if home else g.get("away_team"),
          "opponent":g.get("away_team") if home else g.get("home_team"),
          "net5_gap":net-onet,"rest_diff":rest-orest,"moneyline":ml,
          "implied":implied(ml),"won":score>oscore,"is_home":home,
          "profit":profit(ml,score>oscore)
        })

def select(net_t=BASE_NET,rest_t=BASE_REST,seasons=None):
    ss=set(seasons) if seasons else set(SEASONS)
    return [r for r in rows if r["season"] in ss and r["net5_gap"]>=net_t and r["rest_diff"]>=rest_t]

def metrics(rr):
    rr=[r for r in rr if r.get("profit") is not None]
    n=len(rr); w=sum(1 for r in rr if r["won"])
    prof=sum(r["profit"] for r in rr)
    avg_imp=statistics.mean(r["implied"] for r in rr if r["implied"] is not None) if rr else None
    hr=w/n if n else None
    seq=sorted(rr,key=lambda r:(r.get("game_date") or "",r.get("game_id") or ""))
    equity=0.0; peak=0.0; max_dd=0.0; losing=0; max_losing=0
    for r in seq:
        equity+=r["profit"];peak=max(peak,equity);max_dd=max(max_dd,peak-equity)
        if r["won"]:losing=0
        else:losing+=1;max_losing=max(max_losing,losing)
    return {
      "n":n,"wins":w,"losses":n-w,"hit_rate":hr,"wilson_low":wilson(w,n),
      "profit_units":prof,"roi":prof/n if n else None,
      "avg_implied_probability":avg_imp,
      "actual_minus_implied":(hr-avg_imp) if hr is not None and avg_imp is not None else None,
      "max_drawdown_units":max_dd,"max_losing_streak":max_losing
    }

base=select()
report={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "method":{"id":"NBA_ML_001","rule":f"net5_gap >= {BASE_NET} AND rest_diff >= {BASE_REST}","status":"shadow_research"},
 "overall":metrics(base),
 "phases":{
   "discovery":metrics(select(seasons=TRAIN)),
   "validation":metrics(select(seasons=VALID)),
   "holdout":metrics(select(seasons=HOLDOUT)),
   "post_covid_2021_22_on":metrics(select(seasons={"2021-22","2022-23","2023-24","2024-25","2025-26"})),
   "exclude_covid_2019_20_2020_21":metrics([r for r in base if r["season"] not in {"2019-20","2020-21"}])
 }
}

report["by_season"]={s:metrics([r for r in base if r["season"]==s]) for s in SEASONS}
report["by_location"]={
 "home":metrics([r for r in base if r["is_home"]]),
 "away":metrics([r for r in base if not r["is_home"]])
}

price_bands=[
 ("heavy_fav_le_-300",lambda x:x<=-300),
 ("fav_-299_-200",lambda x:-299<=x<=-200),
 ("fav_-199_-120",lambda x:-199<=x<=-120),
 ("near_even_-119_100",lambda x:-119<=x<=100),
 ("dog_101_200",lambda x:101<=x<=200),
 ("dog_gt_200",lambda x:x>200),
]
report["by_price_band"]={name:metrics([r for r in base if fn(r["moneyline"])]) for name,fn in price_bands}
report["favorite_underdog"]={
 "favorite":metrics([r for r in base if r["moneyline"]<0]),
 "underdog":metrics([r for r in base if r["moneyline"]>0])
}

# Team concentration and team-level results.
teams=defaultdict(list)
for r in base:teams[r["team"]].append(r)
team_stats=[]
for team,rr in teams.items():
    m=metrics(rr);m["team"]=team;m["share_of_signals"]=len(rr)/len(base) if base else 0
    team_stats.append(m)
team_stats.sort(key=lambda x:x["n"],reverse=True)
report["top_team_concentration"]=team_stats[:10]
report["max_single_team_share"]=team_stats[0]["share_of_signals"] if team_stats else 0

# Season leave-one-out on the frozen rule.
report["leave_one_season_out"]={}
for s in SEASONS:
    report["leave_one_season_out"][s]=metrics([r for r in base if r["season"]!=s])

# Threshold sensitivity around the discovered rule.
sensitivity=[]
for net_t in [4.0,5.0,6.0,7.0,7.759,8.5,10.0,12.0]:
    for rest_t in [1.0,2.0,3.0,4.0]:
        rr=select(net_t,rest_t)
        m=metrics(rr);m.update({"net5_threshold":net_t,"rest_diff_threshold":rest_t})
        hm=metrics([r for r in rr if r["season"] in HOLDOUT])
        m.update({"holdout_n":hm["n"],"holdout_hit":hm["hit_rate"],"holdout_roi":hm["roi"],"holdout_wilson_low":hm["wilson_low"]})
        sensitivity.append(m)
report["threshold_sensitivity"]=sensitivity

# Market-implied probability buckets.
imp_bands=[
 ("lt_55",0,0.55),("55_60",0.55,0.60),("60_65",0.60,0.65),("65_70",0.65,0.70),
 ("70_75",0.70,0.75),("75_80",0.75,0.80),("ge_80",0.80,1.01)
]
report["by_implied_probability"]={name:metrics([r for r in base if r["implied"] is not None and lo<=r["implied"]<hi]) for name,lo,hi in imp_bands}

# Signal list for auditability.
write_csv(OUT/"ml001_signals.csv",base)
write_csv(OUT/"ml001_threshold_sensitivity.csv",sensitivity)
write_csv(OUT/"ml001_team_breakdown.csv",team_stats)
(OUT/"ml001_robustness_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
