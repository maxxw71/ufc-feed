#!/usr/bin/env python3
import csv,gzip,json,math
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"maturity"
OUT.mkdir(parents=True,exist_ok=True)
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]

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
def pft(a,out):
    a=n(a)
    if a is None or abs(a)<100:return None
    if out==0.5:return 0.0
    if out==0:return -1.0
    return a/100 if a>0 else 100/abs(a)
def met(rr,total=False):
    vals=[]
    for r in rr:
        p=pft(r.get("price"),r.get("result")) if total else pf(r.get("price"),r.get("won"))
        if p is not None: vals.append((r,p))
    if not vals:return {"n":0,"wins":0,"losses":0,"hit":None,"roi":None}
    if total:
        dec=[r for r,_ in vals if r.get("result")!=0.5];w=sum(1 for r in dec if r.get("result")==1);nn=len(dec)
    else:
        w=sum(1 for r,_ in vals if r.get("won"));nn=len(vals)
    pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"losses":nn-w,"hit":w/nn if nn else None,"roi":pr/len(vals)}

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in SEASONS and t(g.get("completed")):games[g["game_id"]]=g

def idx(name):return {(r.get("game_id"),r.get("team_id")):r for r in rgz(F/name)}
roll=idx("team_rolling.csv.gz");ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}
phys=idx("starter_physical.csv.gz");core=idx("starter_core_pregame.csv.gz");line=idx("lineup_pregame.csv.gz")
work=idx("team_workload_pregame.csv.gz");travel=idx("travel_pregame.csv.gz");lq=idx("lineup_quality_pregame.csv.gz")
stand=idx("standings_pregame.csv.gz");role=idx("starter_bench_rolling.csv.gz");shape=idx("scoring_shape_rolling.csv.gz");style=idx("team_style_rolling.csv.gz")

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));aas=n(g.get("away_score"))
    if hs is None or aas is None:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
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
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        med=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        prior=n(tr.get("prior_games_available"))
        if med is None:continue
        r={"season":g.get("season"),"game_id":gid,"team_id":tid,"home":home,"won":hs>aas if home else aas>hs,"price":med,"prior_games":prior,
           "net5_gap":d(tr,orr,"_net_rating_est_last5_avg"),"def5_adv":d(orr,tr,"_def_rating_est_last5_avg"),"ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg"),
           "rest_diff":rest-orest if rest is not None and orest is not None else None,"guard_weight_gap":d(tp,op,"guard_weight"),"big_weight_gap":d(tp,op,"big_weight"),"market_prob":prob,
           "starter_pm5_gap":d(cc,co,"starter_plusminus5_avg"),"starter_overlap_gap":d(tl,ol,"starter_overlap_prev_game"),"work_top5_3d_adv":d(ow,tw,"top5_minutes_prev_3d"),
           "travel7d_adv":d(ov,tv,"travel_miles_prev_7d_including_arrival"),
           "lq_pm48_gap":d(tq,oq,"current_start5_prior_pm48"),"lq_volatility_adv":d(oq,tq,"stint_pm48_std_last5_avg"),
           "opp_seed6_gap":n(os.get("winpct_gap_to_seed6")),"streak_gap":d(ts,os,"current_streak"),
           "bench_share5_gap":d(rr,ro,"bench_points_share_last5_avg"),"bench_used5_gap":d(rr,ro,"bench_used_last5_avg"),"clutch10_gap":d(sh,sho,"clutch_margin_last10_avg")}
        rows.append(r)

methods=defaultdict(list)
for r in rows:
    if None not in (r["net5_gap"],r["rest_diff"],r["def5_adv"],r["ts5_gap"]) and r["net5_gap"]>=7.759 and r["rest_diff"]>=2 and r["def5_adv"]<=5.5 and r["ts5_gap"]>=0.05603199206897114:methods["NBA_ML001_DEF_TS"].append(r)
    if None not in (r["guard_weight_gap"],r["big_weight_gap"],r["market_prob"]) and r["guard_weight_gap"]<=-10 and r["big_weight_gap"]>=8 and r["market_prob"]>=0.5968:methods["NBA_PHYSCOACH_001"].append(r)
    if None not in (r["starter_pm5_gap"],r["starter_overlap_gap"],r["work_top5_3d_adv"]) and r["starter_pm5_gap"]>=3 and r["starter_overlap_gap"]>=1 and r["work_top5_3d_adv"]>=45:methods["NBA_INTERACT_003"].append(r)
    if not r["home"] and None not in (r["travel7d_adv"],r["market_prob"]) and r["travel7d_adv"]>=576.9 and r["market_prob"]>=0.7023:methods["NBA_TRAVEL_001"].append(r)
    if None not in (r["lq_pm48_gap"],r["lq_volatility_adv"],r["market_prob"]) and r["lq_pm48_gap"]>=10.55 and r["lq_volatility_adv"]>=10.97 and r["market_prob"]>=0.5968:methods["NBA_LQ_003"].append(r)
    if None not in (r["opp_seed6_gap"],r["streak_gap"],r["market_prob"]) and r["opp_seed6_gap"]>=0 and r["streak_gap"]<=-2 and r["market_prob"]>=0.5968:methods["NBA_STAND_002"].append(r)
    if None not in (r["bench_share5_gap"],r["bench_used5_gap"],r["clutch10_gap"]) and r["bench_share5_gap"]<=-0.08308 and r["bench_used5_gap"]>=1 and r["clutch10_gap"]>=0.5:methods["NBA_ROTSHAPE_001"].append(r)

# totals
for gid,g in games.items():
    m=market.get(gid);h=g.get("home_team_id");a=g.get("away_team_id")
    if not m:continue
    hs=n(g.get("home_score"));aas=n(g.get("away_score"));linev=n(m.get("closing_total_median"));price=n(m.get("over_price_median"))
    if None in (hs,aas,linev,price):continue
    sth,sta=style.get((gid,h),{}),style.get((gid,a),{});hc,ac=ctx.get((gid,h),{}),ctx.get((gid,a),{})
    rh=n(hc.get("days_since_prev_game"));ra=n(ac.get("days_since_prev_game"))
    ph=n(sth.get("paint_share_last5_avg"));pa=n(sta.get("paint_share_last5_avg"));fh=n(sth.get("fouls_per100_last5_avg"));fa=n(sta.get("fouls_per100_last5_avg"))
    prh=n(roll.get((gid,h),{}).get("prior_games_available"));pra=n(roll.get((gid,a),{}).get("prior_games_available"))
    if None in (ph,pa,fh,fa,rh,ra):continue
    if ph+pa>=0.8907 and fh+fa<=38.5 and rh+ra<=3:
        actual=hs+aas;res=1 if actual>linev else (0 if actual<linev else 0.5)
        methods["NBA_STYLE_TOT_001"].append({"season":g.get("season"),"game_id":gid,"price":price,"result":res,"prior_games":min(x for x in (prh,pra) if x is not None) if prh is not None and pra is not None else None})

cuts=[0,1,2,3,4,5,7,10,15,20]
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"methods":{},"policy":"History count is current-season prior completed games only. No prior-season box scores are carried into rolling windows."}
for mid,rr in methods.items():
    total=(mid=="NBA_STYLE_TOT_001")
    buckets={}
    for c in cuts:
        z=[r for r in rr if r.get("prior_games") is not None and r["prior_games"]>=c]
        buckets[str(c)]=met(z,total)
    exact={}
    for lo,hi in [(0,2),(3,4),(5,9),(10,19),(20,999)]:
        z=[r for r in rr if r.get("prior_games") is not None and lo<=r["prior_games"]<=hi]
        exact[f"{lo}-{hi if hi<999 else 'plus'}"]=met(z,total)
    report["methods"][mid]={"overall":met(rr,total),"min_history_thresholds":buckets,"history_buckets":exact,
                            "min_prior_games_observed":min([r["prior_games"] for r in rr if r.get("prior_games") is not None],default=None)}

(OUT/"early_season_method_maturity.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
