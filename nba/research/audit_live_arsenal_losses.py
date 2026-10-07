#!/usr/bin/env python3
import csv,gzip,json,math,statistics
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"live_loss_lab"
OUT.mkdir(parents=True,exist_ok=True)
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"}

def rgz(p):
    if not p.exists():return []
    op=gzip.open if p.suffix==".gz" else open
    try:
        with op(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
    except gzip.BadGzipFile:
        with open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    if v in (None,"","None","nan","NaN"):return None
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def d(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x-y if x is not None and y is not None else None
def pf(a,w):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (a/100 if a>0 else 100/abs(a)) if w else -1.0
def pftotal(a,out):
    a=n(a)
    if a is None or abs(a)<100:return None
    if out==0.5:return 0.0
    if out==0:return -1.0
    return a/100 if a>0 else 100/abs(a)
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;den=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/den
def met(rr,total=False):
    vals=[]
    for r in rr:
        p=pftotal(r.get("price"),r.get("result")) if total else pf(r.get("price"),r.get("won"))
        if p is not None:vals.append((r,p))
    if not vals:return {"n":0,"wins":0,"losses":0,"hit":None,"roi":None,"profit":0,"wilson_low":None}
    if total:
        dec=[r for r,_ in vals if r.get("result")!=0.5];w=sum(1 for r in dec if r.get("result")==1);nn=len(dec)
    else:
        w=sum(1 for r,_ in vals if r.get("won"));nn=len(vals)
    pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"losses":nn-w,"hit":w/nn if nn else None,"roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,nn)}
def q(vals,p):
    vals=sorted(v for v in vals if v is not None)
    return vals[min(len(vals)-1,int(p*(len(vals)-1)))] if vals else None
def median(vals):
    vals=[x for x in vals if x is not None]
    return statistics.median(vals) if vals else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in SEASONS and t(g.get("completed")):games[g["game_id"]]=g

def idx(name):
    return {(r.get("game_id"),r.get("team_id")):r for r in rgz(F/name)}
roll=idx("team_rolling.csv.gz");ctx=idx("dummy") if False else {(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}
phys=idx("starter_physical.csv.gz");core=idx("starter_core_pregame.csv.gz");line=idx("lineup_pregame.csv.gz")
work=idx("team_workload_pregame.csv.gz");travel=idx("travel_pregame.csv.gz");lq=idx("lineup_quality_pregame.csv.gz")
stand=idx("standings_pregame.csv.gz");role=idx("starter_bench_rolling.csv.gz");shape=idx("scoring_shape_rolling.csv.gz")
style=idx("team_style_rolling.csv.gz");elo=idx("elo_sos_pregame.csv.gz");coach=idx("coach_event_pregame.csv.gz")
tx=idx("transaction_pregame.csv.gz")

def side_features(gid,g,home):
    tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
    m=market.get(gid,{})
    tr,orr=roll.get((gid,tid),{}),roll.get((gid,oid),{})
    tc,oc=ctx.get((gid,tid),{}),ctx.get((gid,oid),{})
    tp,op=phys.get((gid,tid),{}),phys.get((gid,oid),{})
    cc,co=core.get((gid,tid),{}),core.get((gid,oid),{})
    tl,ol=line.get((gid,tid),{}),line.get((gid,oid),{})
    tw,ow=work.get((gid,tid),{}),work.get((gid,oid),{})
    tv,ov=travel.get((gid,tid),{}),travel.get((gid,oid),{})
    tq,oq=lq.get((gid,tid),{}),lq.get((gid,oid),{})
    ts,os=stand.get((gid,tid),{}),stand.get((gid,oid),{})
    rr,ro=role.get((gid,tid),{}),role.get((gid,oid),{})
    sh,sho=shape.get((gid,tid),{}),shape.get((gid,oid),{})
    sty,sto=style.get((gid,tid),{}),style.get((gid,oid),{})
    ee,eo=elo.get((gid,tid),{}),elo.get((gid,oid),{})
    ch,cho=coach.get((gid,tid),{}),coach.get((gid,oid),{})
    txs,txo=tx.get((gid,tid),{}),tx.get((gid,oid),{})
    rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
    hs=n(g.get("home_score"));aas=n(g.get("away_score"))
    med=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
    prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
    f={
      "season":g.get("season"),"game_id":gid,"team_id":tid,"team":g.get("home_team") if home else g.get("away_team"),
      "home":1.0 if home else 0.0,"won":hs>aas if home else aas>hs,"price":med,"market_prob":prob,
      "net5_gap":d(tr,orr,"_net_rating_est_last5_avg"),"net10_gap":d(tr,orr,"_net_rating_est_last10_avg"),
      "ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg"),"def5_adv":d(orr,tr,"_def_rating_est_last5_avg"),
      "efg5_gap":d(tr,orr,"_e_fg_last5_avg"),"threepa5_gap":d(tr,orr,"_three_pa_rate_last5_avg"),
      "rest_diff":rest-orest if rest is not None and orest is not None else None,
      "guard_weight_gap":d(tp,op,"guard_weight"),"big_weight_gap":d(tp,op,"big_weight"),"big_height_gap":d(tp,op,"big_height"),
      "starter_pm5_gap":d(cc,co,"starter_plusminus5_avg"),"starter_overlap_gap":d(tl,ol,"starter_overlap_prev_game"),
      "lineup_diversity_adv":d(ol,tl,"prior5_distinct_lineups"),"starter_churn_adv":d(ol,tl,"prior5_distinct_starters"),
      "work_top5_3d_adv":d(ow,tw,"top5_minutes_prev_3d"),"work_top8_3d_adv":d(ow,tw,"top8_minutes_prev_3d"),
      "travel7d_adv":d(ov,tv,"travel_miles_prev_7d_including_arrival"),"side_travel":n(tv.get("travel_miles_from_prev")),
      "tz_shift_adv":d(ov,tv,"timezone_shift_hours"),
      "lq_pm48_gap":d(tq,oq,"current_start5_prior_pm48"),"lq_volatility_adv":d(oq,tq,"stint_pm48_std_last5_avg"),
      "opp_seed6_gap":n(os.get("winpct_gap_to_seed6")),"streak_gap":d(ts,os,"current_streak"),
      "rank_gap":d(os,ts,"conference_rank"),
      "bench_share5_gap":d(rr,ro,"bench_points_share_last5_avg"),"bench_used5_gap":d(rr,ro,"bench_used_last5_avg"),
      "clutch10_gap":d(sh,sho,"clutch_margin_last10_avg"),"q4m5_gap":d(sh,sho,"q4_margin_last5_avg"),
      "paint_share5_gap":d(sty,sto,"paint_share_last5_avg"),"oreb5_gap":d(sty,sto,"oreb_rate_last5_avg"),
      "forced_tov5_gap":d(sty,sto,"forced_turnovers_last5_avg"),"foulrate5_adv":d(sto,sty,"fouls_per100_last5_avg"),
      "elo_gap":d(ee,eo,"elo_pre"),"sos5_gap":d(ee,eo,"sos_last5"),
      "quality_win5_gap":d(ee,eo,"quality_win_rate_last5"),"bad_loss5_adv":d(eo,ee,"bad_loss_rate_last5"),
      "coach_change30_gap":d(ch,cho,"coach_events_prev_30d"),"coach_change60_gap":d(ch,cho,"coach_events_prev_60d"),
      "tx30_gap":d(txs,txo,"transactions_prev_30d"),"trades30_gap":d(txs,txo,"trades_prev_30d")
    }
    return f

side_rows=[]
for gid,g in games.items():
    if gid not in market:continue
    hs=n(g.get("home_score"));aas=n(g.get("away_score"))
    if hs is None or aas is None:continue
    for home in (True,False):
        r=side_features(gid,g,home)
        if r["price"] is not None:side_rows.append(r)

methods=defaultdict(list)
for r in side_rows:
    # Existing + newly live moneyline families.
    if None not in (r["net5_gap"],r["rest_diff"],r["def5_adv"],r["ts5_gap"]) and r["net5_gap"]>=7.759 and r["rest_diff"]>=2 and r["def5_adv"]<=5.5 and r["ts5_gap"]>=0.05603199206897114:
        methods["NBA_ML001_DEF_TS"].append(r)
    if None not in (r["guard_weight_gap"],r["big_weight_gap"],r["market_prob"]) and r["guard_weight_gap"]<=-10 and r["big_weight_gap"]>=8 and r["market_prob"]>=0.5968:
        methods["NBA_PHYSCOACH_001"].append(r)
    if None not in (r["starter_pm5_gap"],r["starter_overlap_gap"],r["work_top5_3d_adv"]) and r["starter_pm5_gap"]>=3 and r["starter_overlap_gap"]>=1 and r["work_top5_3d_adv"]>=45:
        methods["NBA_INTERACT_003"].append(r)
    if r["home"]==0 and None not in (r["travel7d_adv"],r["market_prob"]) and r["travel7d_adv"]>=576.9 and r["market_prob"]>=0.7023:
        methods["NBA_TRAVEL_001"].append(r)
    if None not in (r["lq_pm48_gap"],r["lq_volatility_adv"],r["market_prob"]) and r["lq_pm48_gap"]>=10.55 and r["lq_volatility_adv"]>=10.97 and r["market_prob"]>=0.5968:
        methods["NBA_LQ_003"].append(r)
    if None not in (r["opp_seed6_gap"],r["streak_gap"],r["market_prob"]) and r["opp_seed6_gap"]>=0 and r["streak_gap"]<=-2 and r["market_prob"]>=0.5968:
        methods["NBA_STAND_002"].append(r)
    if None not in (r["bench_share5_gap"],r["bench_used5_gap"],r["clutch10_gap"]) and r["bench_share5_gap"]<=-0.08308 and r["bench_used5_gap"]>=1 and r["clutch10_gap"]>=0.5:
        methods["NBA_ROTSHAPE_001"].append(r)

# Totals method.
total_rows=[]
for gid,g in games.items():
    m=market.get(gid);h=g.get("home_team_id");a=g.get("away_team_id")
    if not m:continue
    hs=n(g.get("home_score"));aas=n(g.get("away_score"));linev=n(m.get("closing_total_median"));price=n(m.get("over_price_median"))
    if None in (hs,aas,linev,price):continue
    st,sa=style.get((gid,h),{}),style.get((gid,a),{});hc,ac=ctx.get((gid,h),{}),ctx.get((gid,a),{})
    rh=n(hc.get("days_since_prev_game"));ra=n(ac.get("days_since_prev_game"))
    paint=None;foul=None;rest=None
    x=n(st.get("paint_share_last5_avg"));y=n(sa.get("paint_share_last5_avg"))
    if x is not None and y is not None:paint=x+y
    x=n(st.get("fouls_per100_last5_avg"));y=n(sa.get("fouls_per100_last5_avg"))
    if x is not None and y is not None:foul=x+y
    if rh is not None and ra is not None:rest=rh+ra
    actual=hs+aas;res=1 if actual>linev else (0 if actual<linev else 0.5)
    trh,tra=travel.get((gid,h),{}),travel.get((gid,a),{})
    shh,sha=shape.get((gid,h),{}),shape.get((gid,a),{})
    erh,era=elo.get((gid,h),{}),elo.get((gid,a),{})
    total_rows.append({"season":g.get("season"),"game_id":gid,"price":price,"result":res,
      "paint_sum":paint,"foul_sum":foul,"rest_sum":rest,"total_line":linev,
      "travel7d_sum":sum(v for v in [n(trh.get("travel_miles_prev_7d_including_arrival")),n(tra.get("travel_miles_prev_7d_including_arrival"))] if v is not None) if any(n(z.get("travel_miles_prev_7d_including_arrival")) is not None for z in (trh,tra)) else None,
      "clutch10_abs_gap":abs(d(shh,sha,"clutch_margin_last10_avg")) if d(shh,sha,"clutch_margin_last10_avg") is not None else None,
      "elo_abs_gap":abs(d(erh,era,"elo_pre")) if d(erh,era,"elo_pre") is not None else None})
methods["NBA_STYLE_TOT_001"]=[r for r in total_rows if None not in (r["paint_sum"],r["foul_sum"],r["rest_sum"]) and r["paint_sum"]>=0.8907 and r["foul_sum"]<=38.5 and r["rest_sum"]<=3]

base_rule_features={
 "NBA_ML001_DEF_TS":{"net5_gap","rest_diff","def5_adv","ts5_gap"},
 "NBA_PHYSCOACH_001":{"guard_weight_gap","big_weight_gap","market_prob"},
 "NBA_INTERACT_003":{"starter_pm5_gap","starter_overlap_gap","work_top5_3d_adv"},
 "NBA_TRAVEL_001":{"home","travel7d_adv","market_prob"},
 "NBA_LQ_003":{"lq_pm48_gap","lq_volatility_adv","market_prob"},
 "NBA_STAND_002":{"opp_seed6_gap","streak_gap","market_prob"},
 "NBA_ROTSHAPE_001":{"bench_share5_gap","bench_used5_gap","clutch10_gap"},
 "NBA_STYLE_TOT_001":{"paint_sum","foul_sum","rest_sum"}
}

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"methods":{},"policy":"Loss filters are learned only from 2018-21 parent signals. A filter is reported only when later discovery, validation and holdout remain positive; no team- or season-specific exclusions are tested."}
for mid,rr in methods.items():
    total=(mid=="NBA_STYLE_TOT_001")
    parent={name:met([r for r in rr if r["season"] in ss],total) for name,ss in [("discA",A),("discB",B),("validation",V),("holdout",H)]}
    parent["overall"]=met(rr,total)
    wins=[r for r in rr if (r.get("result")==1 if total else r.get("won"))]
    losses=[r for r in rr if (r.get("result")==0 if total else not r.get("won"))]
    numeric=[k for k in (rr[0].keys() if rr else []) if k not in {"season","game_id","team_id","team","won","result","price"}]
    contrasts=[]
    for f in numeric:
        wv=[n(r.get(f)) for r in wins];lv=[n(r.get(f)) for r in losses]
        wv=[x for x in wv if x is not None];lv=[x for x in lv if x is not None]
        if len(wv)>=8 and len(lv)>=5:
            wm=statistics.mean(wv);lm=statistics.mean(lv)
            scale=statistics.pstdev(wv+lv) if len(wv+lv)>1 else 0
            effect=(wm-lm)/scale if scale>0 else 0
            contrasts.append({"feature":f,"win_mean":wm,"loss_mean":lm,"win_median":median(wv),"loss_median":median(lv),"standardized_win_minus_loss":effect})
    contrasts.sort(key=lambda x:abs(x["standardized_win_minus_loss"]),reverse=True)

    # Single extra-filter search, excluding frozen parent-rule fields.
    candidates=[]
    disc=[r for r in rr if r["season"] in A]
    for f in numeric:
        if f in base_rule_features.get(mid,set()):continue
        vals=[n(r.get(f)) for r in disc];vals=[x for x in vals if x is not None]
        if len(vals)<max(25,int(.5*len(disc))):continue
        for qq in (.20,.35,.65,.80):
            cut=q(vals,qq);op="<=" if qq<.5 else ">="
            if cut is None:continue
            def keep(r):
                x=n(r.get(f))
                return x is not None and (x<=cut if op=="<=" else x>=cut)
            ph={name:met([r for r in rr if r["season"] in ss and keep(r)],total) for name,ss in [("discA",A),("discB",B),("validation",V),("holdout",H)]}
            pa=parent["discA"];pb=parent["discB"];pv=parent["validation"];phold=parent["holdout"]
            if ph["discA"]["n"]<max(15,int(.35*max(pa["n"],1))) or ph["discB"]["n"]<10 or ph["validation"]["n"]<8 or ph["holdout"]["n"]<12:continue
            if any(x["roi"] is None or x["hit"] is None for x in ph.values()):continue
            if ph["discA"]["roi"] < (pa["roi"] or -9)+0.01:continue
            if ph["discA"]["hit"] < (pa["hit"] or 0)+0.01:continue
            if ph["discB"]["roi"]<=0 or ph["validation"]["roi"]<=0 or ph["holdout"]["roi"]<=0:continue
            if ph["holdout"]["hit"] < (phold["hit"] or 0)-0.01:continue
            if ph["holdout"]["roi"] < (phold["roi"] or -9):continue
            candidates.append({"feature":f,"op":op,"cut":cut,"phases":ph,
                               "holdout_roi_lift":ph["holdout"]["roi"]-(phold["roi"] or 0),
                               "holdout_hit_lift":ph["holdout"]["hit"]-(phold["hit"] or 0)})
    candidates.sort(key=lambda x:(x["holdout_roi_lift"],x["holdout_hit_lift"],x["phases"]["holdout"]["n"]),reverse=True)
    report["methods"][mid]={"parent":parent,"win_loss_contrasts":contrasts[:15],"validated_extra_filter_candidates":candidates[:20]}

(OUT/"live_loss_forensics.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({k:{"parent":v["parent"]["overall"],"filters":len(v["validated_extra_filter_candidates"]),"top_contrasts":v["win_loss_contrasts"][:3]} for k,v in report["methods"].items()},indent=2))
