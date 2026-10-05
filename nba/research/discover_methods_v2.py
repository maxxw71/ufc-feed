#!/usr/bin/env python3
import csv,gzip,itertools,json,math,statistics
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data"
F=NBA/"features"
OUT=NBA/"research"/"v2"
OUT.mkdir(parents=True,exist_ok=True)

TRAIN={"2018-19","2019-20","2020-21","2021-22","2022-23"}
VALID={"2023-24"}
HOLDOUT={"2024-25","2025-26"}
ALL=TRAIN|VALID|HOLDOUT

def read_gz(path):
    if not path.exists():return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))

def write_csv(path,rows):
    rows=list(rows);fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    with open(path,"w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields or ["_empty"]);w.writeheader();w.writerows(rows)

def num(v):
    if v in (None,"","None","nan","NaN"):return None
    try:return float(v)
    except:return None

def truth(v):return str(v).lower() in ("true","1","yes")

def profit(a,won):
    a=num(a)
    if a is None or abs(a)<100:return None
    return (a/100 if a>0 else 100/abs(a)) if won else -1.0

def wilson(w,n,z=1.96):
    if n<=0:return None
    p=w/n;d=1+z*z/n
    return (p+z*z/(2*n)-z*math.sqrt((p*(1-p)+z*z/(4*n))/n))/d

def diff(a,b,k):
    x=num(a.get(k) if a else None);y=num(b.get(k) if b else None)
    return x-y if x is not None and y is not None else None

def metrics(rr):
    rr=[r for r in rr if r.get("moneyline") is not None]
    n=len(rr);w=sum(1 for r in rr if r["won"]);p=sum(profit(r["moneyline"],r["won"]) for r in rr)
    return {"n":n,"wins":w,"hit":w/n if n else None,"roi":p/n if n else None,
            "profit":p,"wilson_low":wilson(w,n)}

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in read_gz(p):
        if g.get("season") in ALL and truth(g.get("completed")):games[g["game_id"]]=g
team={(r.get("game_id"),r.get("team_id")):r for r in read_gz(F/"team_rolling.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in read_gz(NBA/"team_game_context.csv.gz")}
line={(r.get("game_id"),r.get("team_id")):r for r in read_gz(F/"lineup_pregame.csv.gz")}
market={r.get("game_id"):r for r in read_gz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=num(g.get("home_score"));aas=num(g.get("away_score"))
    if hs is None or aas is None:continue
    for side in ("home","away"):
        home=side=="home";tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tr=team.get((gid,tid),{});orr=team.get((gid,oid),{})
        tc=ctx.get((gid,tid),{});oc=ctx.get((gid,oid),{})
        tl=line.get((gid,tid),{});ol=line.get((gid,oid),{})
        ml=num(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        if ml is None or abs(ml)<100:continue
        score=hs if home else aas;oscore=aas if home else hs
        rest=num(tc.get("days_since_prev_game"));orest=num(oc.get("days_since_prev_game"))
        row={
          "season":g.get("season"),"game_id":gid,"team_id":tid,"side":side,"moneyline":ml,"won":score>oscore,
          "market_prob":num(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig")),
          "spread_move":num(m.get("spread_move")),"total_move":num(m.get("total_move")),
          "net5_gap":diff(tr,orr,"_net_rating_est_last5_avg"),"net10_gap":diff(tr,orr,"_net_rating_est_last10_avg"),
          "off5_gap":diff(tr,orr,"_off_rating_est_last5_avg"),"def5_adv":diff(orr,tr,"_def_rating_est_last5_avg"),
          "efg5_gap":diff(tr,orr,"_e_fg_last5_avg"),"ts5_gap":diff(tr,orr,"_true_shooting_est_last5_avg"),
          "threepa5_gap":diff(tr,orr,"_three_pa_rate_last5_avg"),
          "rest_diff":rest-orest if rest is not None and orest is not None else None,
          "opp_b2b":1.0 if truth(oc.get("back_to_back")) else 0.0,
          "side_b2b":1.0 if truth(tc.get("back_to_back")) else 0.0,
          # Safe historical lineup state: all derived from games before the target game.
          "lineup_stability_gap":diff(tl,ol,"prior5_top_lineup_share"),
          "lineup_diversity_adv":diff(ol,tl,"prior5_distinct_lineups"),
          "rotation_size_adv":diff(ol,tl,"prior5_rotation_players"),
          "starter_churn_adv":diff(ol,tl,"prior5_distinct_starters"),
          "stint_complexity_adv":diff(ol,tl,"prior5_avg_stints_per_game"),
          # Separate confirmed-starter lane.
          "starter_overlap_gap":diff(tl,ol,"starter_overlap_prev_game"),
          "closing_overlap_gap":diff(tl,ol,"starter_overlap_prev_closing_lineup"),
          "is_home":1.0 if home else 0.0,
        }
        rows.append(row)

safe_features=[
 "net5_gap","net10_gap","off5_gap","def5_adv","efg5_gap","ts5_gap","threepa5_gap","rest_diff",
 "lineup_stability_gap","lineup_diversity_adv","rotation_size_adv","starter_churn_adv","stint_complexity_adv"
]
confirmed_features=["starter_overlap_gap","closing_overlap_gap"]
categorical=[("opp_b2b","==",1.0),("side_b2b","==",0.0),("is_home","==",1.0),("is_home","==",0.0)]
market_filters=[("market_prob",">=",x) for x in (0.50,0.55,0.60,0.65,0.70,0.75)]

def qconds(features):
    out=[]
    tr=[r for r in rows if r["season"] in TRAIN]
    for feat in features:
        vals=sorted(r[feat] for r in tr if r.get(feat) is not None)
        if len(vals)<200:continue
        for q in (0.60,0.70,0.80,0.90):
            v=float(vals[min(len(vals)-1,int(q*(len(vals)-1)))])
            out.append((feat,">=",v))
    return out

def meets(r,conds):
    for f,op,t in conds:
        v=r.get(f)
        if v is None:return False
        if op==">=" and v<t:return False
        if op=="<=" and v>t:return False
        if op=="==" and v!=t:return False
    return True

def desc(cs):return " AND ".join(f"{f} {op} {t:.4g}" if isinstance(t,float) else f"{f} {op} {t}" for f,op,t in cs)

def evaluate(conds,lane):
    def pick(ss):return [r for r in rows if r["season"] in ss and meets(r,conds)]
    a=metrics(pick(TRAIN));b=metrics(pick(VALID));h=metrics(pick(HOLDOUT))
    if a["n"]<180 or b["n"]<30 or h["n"]<60:return None
    if a["hit"] is None or a["roi"] is None:return None
    if a["hit"]<0.67 or a["roi"]<=0:return None
    status="research_only"
    if b["roi"] is not None and h["roi"] is not None and b["roi"]>0 and h["roi"]>0 and h["hit"]>=0.67 and h["wilson_low"]>=0.57:
        status="shadow_candidate"
    return {
      "lane":lane,"rule":desc(conds),"conditions":json.dumps(conds),"status":status,
      "train_n":a["n"],"train_hit":a["hit"],"train_roi":a["roi"],
      "validation_n":b["n"],"validation_hit":b["hit"],"validation_roi":b["roi"],
      "holdout_n":h["n"],"holdout_hit":h["hit"],"holdout_roi":h["roi"],"holdout_wilson_low":h["wilson_low"]
    }

safe=qconds(safe_features)+categorical+market_filters
confirmed=qconds(confirmed_features)

rules=[]
# New pair search on safe features.
for a,b in itertools.combinations(safe,2):
    if a[0]==b[0]:continue
    rules.append((a,b,"pregame_safe"))
# Focused 3-condition extensions of ML001 with safe lineup/market features only.
base=(("net5_gap",">=",7.759),("rest_diff",">=",2.0))
for c in safe:
    if c[0] not in {"net5_gap","rest_diff"}:
        rules.append((base[0],base[1],c,"ml001_extension"))
# Confirmed-starter variants kept separate.
for c in confirmed:
    rules.append((base[0],base[1],c,"confirmed_starter_ml001"))

results=[]
seen=set()
for item in rules:
    lane=item[-1];conds=tuple(item[:-1])
    key=(lane,tuple((x[0],x[1],round(x[2],8) if isinstance(x[2],float) else x[2]) for x in conds))
    if key in seen:continue
    seen.add(key)
    e=evaluate(conds,lane)
    if e:results.append(e)

results.sort(key=lambda r:(1 if r["status"]=="shadow_candidate" else 0,r["holdout_wilson_low"] or 0,r["holdout_roi"] or -9,r["holdout_n"]),reverse=True)
for i,r in enumerate(results,1):r["method_id"]=f"NBA_V2_{i:03d}"
shadow=[r for r in results if r["status"]=="shadow_candidate"]
write_csv(OUT/"v2_candidates.csv",results[:300]);write_csv(OUT/"v2_shadow_candidates.csv",shadow[:100])
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"rules_tested":len(seen),
        "candidates":len(results),"shadow_candidates":len(shadow),
        "lanes":{"pregame_safe":"No current-game starter identity used.","ml001_extension":"ML001 plus one pregame-safe filter.",
                 "confirmed_starter_ml001":"Requires confirmed target-game starting five before bet."},
        "top_methods":results[:30]}
(OUT/"v2_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
