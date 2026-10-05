#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"ats"
OUT.mkdir(parents=True,exist_ok=True)
TRAIN={"2018-19","2019-20","2020-21","2021-22","2022-23"}
VALID={"2023-24"};HOLDOUT={"2024-25","2025-26"};ALL=TRAIN|VALID|HOLDOUT

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    if v in (None,"","None","nan","NaN"):return None
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
def metrics(rr):
    vals=[]
    for r in rr:
        p=pf(r.get("price"),r.get("ats"))
        if p is not None:vals.append((r,p))
    dec=[r for r,_ in vals if r["ats"]!=0.5]
    w=sum(1 for r in dec if r["ats"]==1);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(dec) if dec else None,"roi":pr/len(vals) if vals else None,"profit":pr,"wilson_low":wilson(w,len(dec))}
def d(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x-y if x is not None and y is not None else None
def q(vals,qq):
    vals=sorted(x for x in vals if x is not None)
    return vals[min(len(vals)-1,int(qq*(len(vals)-1)))] if vals else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in ALL and t(g.get("completed")):games[g["game_id"]]=g
roll={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_rolling.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
lineup={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"lineup_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    home_line=n(m.get("home_spread_signed_median"))
    hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    if None in (home_line,hs,as_):continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tr=roll.get((gid,tid),{});orr=roll.get((gid,oid),{});tc=ctx.get((gid,tid),{});oc=ctx.get((gid,oid),{})
        tl=lineup.get((gid,tid),{});ol=lineup.get((gid,oid),{})
        spread=home_line if home else -home_line
        score=hs if home else as_;opp=as_ if home else hs
        adj=score+spread-opp
        ats=1 if adj>0 else (0 if adj<0 else 0.5)
        price=n(m.get("home_spread_price_median") if home else m.get("away_spread_price_median"))
        if price is None or abs(price)<100:continue
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        rows.append({
          "season":g.get("season"),"game_id":gid,"side":"home" if home else "away","ats":ats,"price":price,"spread":spread,
          "net5_gap":d(tr,orr,"_net_rating_est_last5_avg"),"net10_gap":d(tr,orr,"_net_rating_est_last10_avg"),
          "off5_gap":d(tr,orr,"_off_rating_est_last5_avg"),"def5_adv":d(orr,tr,"_def_rating_est_last5_avg"),
          "efg5_gap":d(tr,orr,"_e_fg_last5_avg"),"ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg"),
          "threepa5_gap":d(tr,orr,"_three_pa_rate_last5_avg"),
          "rest_diff":rest-orest if rest is not None and orest is not None else None,
          "opp_b2b":1.0 if t(oc.get("back_to_back")) else 0.0,"side_b2b":1.0 if t(tc.get("back_to_back")) else 0.0,
          "lineup_stability_gap":d(tl,ol,"prior5_top_lineup_share"),
          "starter_churn_adv":d(ol,tl,"prior5_distinct_starters"),
          "lineup_diversity_adv":d(ol,tl,"prior5_distinct_lineups"),
          "is_home":1.0 if home else 0.0
        })

train=[r for r in rows if r["season"] in TRAIN]
features=["net5_gap","net10_gap","off5_gap","def5_adv","efg5_gap","ts5_gap","threepa5_gap","rest_diff","lineup_stability_gap","starter_churn_adv","lineup_diversity_adv","spread"]
conds=[]
for f in features:
    vals=[r.get(f) for r in train if r.get(f) is not None]
    if len(vals)<300:continue
    for qq in (0.60,0.70,0.80,0.90):
        hi=q(vals,qq);lo=q(vals,1-qq)
        if hi is not None:conds.append((f,">=",float(hi)))
        if lo is not None:conds.append((f,"<=",float(lo)))
conds += [("opp_b2b","==",1.0),("side_b2b","==",0.0),("is_home","==",1.0),("is_home","==",0.0)]

def meet(r,cs):
    for f,op,v in cs:
        x=r.get(f)
        if x is None:return False
        if op==">=" and x<v:return False
        if op=="<=" and x>v:return False
        if op=="==" and x!=v:return False
    return True
def desc(cs):return " AND ".join(f"{f} {op} {v:.4g}" for f,op,v in cs)
def evalr(cs):
    def pick(ss):return [r for r in rows if r["season"] in ss and meet(r,cs)]
    A=metrics(pick(TRAIN));V=metrics(pick(VALID));H=metrics(pick(HOLDOUT))
    if A["n"]<200 or V["n"]<40 or H["n"]<90:return None
    if A["hit"] is None or A["roi"] is None or A["hit"]<0.535 or A["roi"]<=0:return None
    status="research_only"
    if V["roi"] is not None and H["roi"] is not None and V["roi"]>0 and H["roi"]>0 and H["hit"]>=0.535 and H["wilson_low"]>=0.48:
        status="shadow_candidate"
    return {"rule":desc(cs),"status":status,"train_n":A["n"],"train_hit":A["hit"],"train_roi":A["roi"],
            "validation_n":V["n"],"validation_hit":V["hit"],"validation_roi":V["roi"],
            "holdout_n":H["n"],"holdout_hit":H["hit"],"holdout_roi":H["roi"],"holdout_wilson_low":H["wilson_low"]}

results=[];tested=0
for a,b in itertools.combinations(conds,2):
    if a[0]==b[0]:continue
    tested+=1
    x=evalr((a,b))
    if x:results.append(x)
# Add singles as controls.
for a in conds:
    tested+=1
    x=evalr((a,))
    if x:results.append(x)

uniq={r["rule"]:r for r in results};results=list(uniq.values())
results.sort(key=lambda r:(1 if r["status"]=="shadow_candidate" else 0,r["holdout_wilson_low"] or 0,r["holdout_roi"] or -9,r["holdout_n"]),reverse=True)
for i,r in enumerate(results,1):r["method_id"]=f"NBA_ATS_V2_{i:03d}"
shadow=[r for r in results if r["status"]=="shadow_candidate"]

def wc(path,rr):
    fs=[]
    for r in rr:
        for k in r:
            if k not in fs:fs.append(k)
    with open(path,"w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fs or ["_empty"]);w.writeheader();w.writerows(rr)
wc(OUT/"ats_candidates.csv",results[:300]);wc(OUT/"ats_shadow_candidates.csv",shadow[:100])
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"side_rows":len(rows),"rules_tested":tested,
        "candidates":len(results),"shadow_candidates":len(shadow),"top_methods":results[:30],
        "policy":"Signed spread reconstructed from provider details; thresholds learned on discovery seasons only."}
(OUT/"ats_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
