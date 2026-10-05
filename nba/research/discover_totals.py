#!/usr/bin/env python3
import csv,gzip,itertools,json,math,statistics
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data"; F=NBA/"features"; OUT=NBA/"research"/"totals"
OUT.mkdir(parents=True,exist_ok=True)
TRAIN={"2018-19","2019-20","2020-21","2021-22","2022-23"}
VALID={"2023-24"}; HOLDOUT={"2024-25","2025-26"}; ALL=TRAIN|VALID|HOLDOUT

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    if v in (None,"","None","nan","NaN"):return None
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
def metrics(rr,out_key,price_key):
    vals=[]
    for r in rr:
        p=profit(r.get(price_key),r.get(out_key))
        if p is not None:vals.append((r,p))
    decisive=[r for r,_ in vals if r.get(out_key)!=0.5]
    w=sum(1 for r in decisive if r[out_key]==1)
    prof=sum(p for _,p in vals)
    return {"n":len(vals),"decisive":len(decisive),"wins":w,
            "hit":w/len(decisive) if decisive else None,"roi":prof/len(vals) if vals else None,
            "profit":prof,"wilson_low":wilson(w,len(decisive))}
def q(vals,qq):
    vals=sorted(x for x in vals if x is not None)
    return vals[min(len(vals)-1,int(qq*(len(vals)-1)))] if vals else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in ALL and t(g.get("completed")):games[g["game_id"]]=g
roll={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_rolling.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
line={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"lineup_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    h=g.get("home_team_id");a=g.get("away_team_id")
    hr=roll.get((gid,h),{});ar=roll.get((gid,a),{})
    hc=ctx.get((gid,h),{});ac=ctx.get((gid,a),{})
    hl=line.get((gid,h),{});al=line.get((gid,a),{})
    hs=n(g.get("home_score"));aas=n(g.get("away_score"));line_total=n(m.get("closing_total_median"))
    op=n(m.get("over_price_median"));up=n(m.get("under_price_median"))
    if None in (hs,aas,line_total):continue
    actual=hs+aas
    over=1 if actual>line_total else (0 if actual<line_total else 0.5)
    under=1 if actual<line_total else (0 if actual>line_total else 0.5)
    pace_vals=[n(hr.get("_possessions_est_last5_avg")),n(ar.get("_possessions_est_last5_avg"))]
    pace=[x for x in pace_vals if x is not None];pace5=statistics.mean(pace) if pace else None
    hoff=n(hr.get("_off_rating_est_last5_avg"));aoff=n(ar.get("_off_rating_est_last5_avg"))
    hdef=n(hr.get("_def_rating_est_last5_avg"));adef=n(ar.get("_def_rating_est_last5_avg"))
    expected=None
    if None not in (pace5,hoff,aoff,hdef,adef):
        expected=pace5*((hoff+adef)/2+(aoff+hdef)/2)/100
    def sumv(obj1,obj2,key):
        x=n(obj1.get(key));y=n(obj2.get(key))
        return x+y if x is not None and y is not None else None
    resth=n(hc.get("days_since_prev_game"));resta=n(ac.get("days_since_prev_game"))
    rows.append({
      "season":g.get("season"),"game_id":gid,"actual_total":actual,"total":line_total,
      "over_price":op,"under_price":up,"over_result":over,"under_result":under,
      "expected_total":expected,"model_edge":expected-line_total if expected is not None else None,
      "pace5":pace5,
      "off5_sum":sumv(hr,ar,"_off_rating_est_last5_avg"),
      "def5_sum":sumv(hr,ar,"_def_rating_est_last5_avg"),
      "ts5_sum":sumv(hr,ar,"_true_shooting_est_last5_avg"),
      "efg5_sum":sumv(hr,ar,"_e_fg_last5_avg"),
      "threepa5_sum":sumv(hr,ar,"_three_pa_rate_last5_avg"),
      "rest_sum":resth+resta if resth is not None and resta is not None else None,
      "any_b2b":1.0 if t(hc.get("back_to_back")) or t(ac.get("back_to_back")) else 0.0,
      "both_b2b":1.0 if t(hc.get("back_to_back")) and t(ac.get("back_to_back")) else 0.0,
      "lineup_churn_sum":sumv(hl,al,"prior5_distinct_starters"),
      "lineup_diversity_sum":sumv(hl,al,"prior5_distinct_lineups"),
      "top_lineup_share_sum":sumv(hl,al,"prior5_top_lineup_share"),
      "total_move":n(m.get("total_move")),
    })

train=[r for r in rows if r["season"] in TRAIN]
features=["pace5","off5_sum","def5_sum","ts5_sum","efg5_sum","threepa5_sum","rest_sum","lineup_churn_sum","lineup_diversity_sum","top_lineup_share_sum","total","total_move"]
high=[];low=[]
for f in features:
    vals=[r.get(f) for r in train if r.get(f) is not None]
    if len(vals)<300:continue
    for qq in (0.60,0.70,0.80,0.90):
        hi=q(vals,qq);lo=q(vals,1-qq)
        if hi is not None:high.append((f,">=",float(hi)))
        if lo is not None:low.append((f,"<=",float(lo)))

# Edge thresholds only learned from discovery seasons.
edges=[abs(r["model_edge"]) for r in train if r.get("model_edge") is not None]
edgecuts=sorted(set(round(q(edges,qq),4) for qq in (0.50,0.60,0.70,0.80,0.90) if q(edges,qq) is not None))

def meet(r,cs):
    for f,op,v in cs:
        x=r.get(f)
        if x is None:return False
        if op==">=" and x<v:return False
        if op=="<=" and x>v:return False
        if op=="==" and x!=v:return False
    return True
def desc(cs):return " AND ".join(f"{f} {op} {v:.4g}" for f,op,v in cs)
def eval_rule(side,cs):
    outk="over_result" if side=="over" else "under_result";pk="over_price" if side=="over" else "under_price"
    def sset(ss):return [r for r in rows if r["season"] in ss and meet(r,cs)]
    A=metrics(sset(TRAIN),outk,pk);V=metrics(sset(VALID),outk,pk);H=metrics(sset(HOLDOUT),outk,pk)
    if A["n"]<180 or V["n"]<35 or H["n"]<80:return None
    if A["roi"] is None or A["hit"] is None or A["roi"]<=0 or A["hit"]<0.535:return None
    status="research_only"
    if V["roi"] is not None and H["roi"] is not None and V["roi"]>0 and H["roi"]>0 and H["hit"]>=0.535 and H["wilson_low"]>=0.48:
        status="shadow_candidate"
    return {"side":side,"rule":desc(cs),"status":status,
            "train_n":A["n"],"train_hit":A["hit"],"train_roi":A["roi"],
            "validation_n":V["n"],"validation_hit":V["hit"],"validation_roi":V["roi"],
            "holdout_n":H["n"],"holdout_hit":H["hit"],"holdout_roi":H["roi"],"holdout_wilson_low":H["wilson_low"]}

results=[];tested=0
for side in ("over","under"):
    # Core model-edge lane.
    for e in edgecuts:
        base=(("model_edge",">=",e),) if side=="over" else (("model_edge","<=",-e),)
        tested+=1
        x=eval_rule(side,base)
        if x:results.append(x)
        # One additional contextual filter.
        pool=high+low+[("any_b2b","==",1.0),("any_b2b","==",0.0)]
        for c in pool:
            if c[0]=="model_edge":continue
            tested+=1
            x=eval_rule(side,base+(c,))
            if x:results.append(x)
    # Context-only pair rules.
    pool=high+low
    for a,b in itertools.combinations(pool,2):
        if a[0]==b[0]:continue
        tested+=1
        x=eval_rule(side,(a,b))
        if x:results.append(x)

# Dedup and rank.
uniq={}
for r in results:uniq[(r["side"],r["rule"])]=r
results=list(uniq.values())
results.sort(key=lambda r:(1 if r["status"]=="shadow_candidate" else 0,r["holdout_wilson_low"] or 0,r["holdout_roi"] or -9,r["holdout_n"]),reverse=True)
for i,r in enumerate(results,1):r["method_id"]=f"NBA_TOT_V2_{i:03d}"
shadow=[r for r in results if r["status"]=="shadow_candidate"]

def wcsv(path,rr):
    fields=[]
    for r in rr:
        for k in r:
            if k not in fields:fields.append(k)
    with open(path,"w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields or ["_empty"]);w.writeheader();w.writerows(rr)
wcsv(OUT/"totals_candidates.csv",results[:300]);wcsv(OUT/"totals_shadow_candidates.csv",shadow[:100])
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"games":len(rows),"rules_tested":tested,
        "candidates":len(results),"shadow_candidates":len(shadow),"top_methods":results[:30],
        "policy":"Thresholds learned on 2018-19 through 2022-23 only; 2023-24 validation and 2024-26 holdout evaluation only."}
(OUT/"totals_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
