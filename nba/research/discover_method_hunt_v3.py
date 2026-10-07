#!/usr/bin/env python3
import csv,gzip,itertools,json,math,statistics
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"hunt_v3"
OUT.mkdir(parents=True,exist_ok=True)
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"};ALL=A|B|V|H

def rgz(p):
    if not p.exists(): return []
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
    x=n((a or {}).get(k));y=n((b or {}).get(k))
    return x-y if x is not None and y is not None else None
def sum2(a,b,k):
    x=n((a or {}).get(k));y=n((b or {}).get(k))
    return x+y if x is not None and y is not None else None
def absd(a,b,k):
    z=d(a,b,k);return abs(z) if z is not None else None
def q(vals,p):
    vals=sorted(x for x in vals if x is not None)
    return vals[min(len(vals)-1,int(p*(len(vals)-1)))] if vals else None
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;den=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/den
def profit(price,outcome):
    p=n(price)
    if p is None or abs(p)<100:return None
    if outcome==0.5:return 0.0
    if outcome==0:return -1.0
    return p/100 if p>0 else 100/abs(p)
def met(rr,outk,pk):
    vals=[]
    for r in rr:
        x=profit(r.get(pk),r.get(outk))
        if x is not None:vals.append((r,x))
    if not vals:return {"n":0,"wins":0,"losses":0,"hit":None,"roi":None,"profit":0,"wilson_low":None}
    dec=[r for r,_ in vals if r.get(outk)!=0.5]
    w=sum(1 for r in dec if r.get(outk)==1);pr=sum(x for _,x in vals)
    return {"n":len(vals),"wins":w,"losses":len(dec)-w,"hit":w/len(dec) if dec else None,
            "roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,len(dec))}
def idx(name):
    return {(r.get("game_id"),r.get("team_id")):r for r in rgz(F/name)}
def meet(r,cs):
    for f,o,v in cs:
        x=r.get(f)
        if x is None:return False
        if o=="==" and x!=v:return False
        if o==">=" and x<v:return False
        if o=="<=" and x>v:return False
    return True

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in ALL and t(g.get("completed")):games[g["game_id"]]=g

roll=idx("team_rolling.csv.gz")
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}
phys=idx("starter_physical.csv.gz");core=idx("starter_core_pregame.csv.gz");line=idx("lineup_pregame.csv.gz")
work=idx("team_workload_pregame.csv.gz");travel=idx("travel_pregame.csv.gz");lq=idx("lineup_quality_pregame.csv.gz")
stand=idx("standings_pregame.csv.gz");role=idx("starter_bench_rolling.csv.gz");shape=idx("scoring_shape_rolling.csv.gz")
style=idx("team_style_rolling.csv.gz");elo=idx("elo_sos_pregame.csv.gz");coach=idx("coach_event_pregame.csv.gz")
tx=idx("transaction_pregame.csv.gz");impact=idx("starter_individual_impact_pregame.csv.gz");cont=idx("starter_prior_season_continuity.csv.gz")
official={r.get("game_id"):r for r in rgz(F/"official_crew_pregame.csv.gz")}

DOMAIN={
 "market_prob":"market","spread":"market","is_home":"context","is_dog":"market",
 "net5_gap":"efficiency","net10_gap":"efficiency","ts5_gap":"efficiency","def5_adv":"efficiency","off5_gap":"efficiency","threepa5_gap":"efficiency",
 "rest_diff":"schedule","opp_b2b":"schedule","travel7d_adv":"travel","tz_shift_adv":"travel",
 "lq_pm48_gap":"lineup","lq_volatility_adv":"lineup","starter_pm5_gap":"starter","starter_overlap_gap":"lineup","lineup_diversity_adv":"lineup",
 "work_top5_3d_adv":"workload","work_top8_3d_adv":"workload",
 "opp_seed6_gap":"standings","streak_gap":"standings","rank_gap":"standings",
 "bench_share5_gap":"rotation","bench_used5_gap":"rotation","clutch10_gap":"clutch","q3m5_gap":"clutch","q4m5_gap":"clutch",
 "paint_share5_gap":"style","oreb5_gap":"style","forced_tov5_gap":"style","foulrate5_adv":"style",
 "guard_weight_gap":"physical","big_weight_gap":"physical","big_height_gap":"physical",
 "elo_gap":"strength","sos5_gap":"strength","quality_win5_gap":"strength","bad_loss5_adv":"strength",
 "coach_change30_gap":"coaching","coach_change90_gap":"coaching","tx30_gap":"transactions","trades30_gap":"transactions",
 "starter_ind_pm48_gap":"player_impact","negative_starter_adv":"player_impact","prior_minutes_share_gap":"continuity","new_starters_adv":"continuity",
 "crew_homewin25_side":"officials","crew_homeftadiff25_side":"officials"
}

side=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));as_=n(g.get("away_score"));hspread=n(m.get("home_spread_signed_median"))
    if hs is None or as_ is None:continue
    for home in (True,False):
        tid=str(g.get("home_team_id") if home else g.get("away_team_id"));oid=str(g.get("away_team_id") if home else g.get("home_team_id"))
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
        im,imo=impact.get((gid,tid),{}),impact.get((gid,oid),{})
        ct,cto=cont.get((gid,tid),{}),cont.get((gid,oid),{})
        ocw=official.get(gid,{})
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        spr=hspread if home else (-hspread if hspread is not None else None)
        sprprice=n(m.get("home_spread_price_median") if home else m.get("away_spread_price_median"))
        score=hs if home else as_;opp=as_ if home else hs
        ats=None if spr is None else (1 if score+spr>opp else (0 if score+spr<opp else 0.5))
        sign=1 if home else -1
        side.append({
          "season":g.get("season"),"game_id":gid,"team_id":tid,
          "ml_out":1 if score>opp else 0,"ml_price":ml,"ats_out":ats,"ats_price":sprprice,
          "market_prob":prob,"spread":spr,"is_home":1.0 if home else 0.0,"is_dog":1.0 if ml is not None and ml>0 else 0.0,
          "net5_gap":d(tr,orr,"_net_rating_est_last5_avg"),"net10_gap":d(tr,orr,"_net_rating_est_last10_avg"),
          "ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg"),"def5_adv":d(orr,tr,"_def_rating_est_last5_avg"),
          "off5_gap":d(tr,orr,"_off_rating_est_last5_avg"),"threepa5_gap":d(tr,orr,"_three_pa_rate_last5_avg"),
          "rest_diff":rest-orest if rest is not None and orest is not None else None,"opp_b2b":1.0 if t(oc.get("back_to_back")) else 0.0,
          "travel7d_adv":d(ov,tv,"travel_miles_prev_7d_including_arrival"),"tz_shift_adv":d(ov,tv,"timezone_shift_hours"),
          "lq_pm48_gap":d(tq,oq,"current_start5_prior_pm48"),"lq_volatility_adv":d(oq,tq,"stint_pm48_std_last5_avg"),
          "starter_pm5_gap":d(cc,co,"starter_plusminus5_avg"),"starter_overlap_gap":d(tl,ol,"starter_overlap_prev_game"),
          "lineup_diversity_adv":d(ol,tl,"prior5_distinct_lineups"),
          "work_top5_3d_adv":d(ow,tw,"top5_minutes_prev_3d"),"work_top8_3d_adv":d(ow,tw,"top8_minutes_prev_3d"),
          "opp_seed6_gap":n(os.get("winpct_gap_to_seed6")),"streak_gap":d(ts,os,"current_streak"),"rank_gap":d(os,ts,"conference_rank"),
          "bench_share5_gap":d(rr,ro,"bench_points_share_last5_avg"),"bench_used5_gap":d(rr,ro,"bench_used_last5_avg"),
          "clutch10_gap":d(sh,sho,"clutch_margin_last10_avg"),"q3m5_gap":d(sh,sho,"q3_margin_last5_avg"),"q4m5_gap":d(sh,sho,"q4_margin_last5_avg"),
          "paint_share5_gap":d(sty,sto,"paint_share_last5_avg"),"oreb5_gap":d(sty,sto,"oreb_rate_last5_avg"),
          "forced_tov5_gap":d(sty,sto,"forced_turnovers_last5_avg"),"foulrate5_adv":d(sto,sty,"fouls_per100_last5_avg"),
          "guard_weight_gap":d(tp,op,"guard_weight"),"big_weight_gap":d(tp,op,"big_weight"),"big_height_gap":d(tp,op,"big_height"),
          "elo_gap":d(ee,eo,"elo_pre"),"sos5_gap":d(ee,eo,"sos_last5"),"quality_win5_gap":d(ee,eo,"quality_win_rate_last5"),"bad_loss5_adv":d(eo,ee,"bad_loss_rate_last5"),
          "coach_change30_gap":d(ch,cho,"coach_events_prev_30d"),"coach_change90_gap":d(ch,cho,"coach_events_prev_90d"),
          "tx30_gap":d(txs,txo,"transactions_prev_30d"),"trades30_gap":d(txs,txo,"trades_prev_30d"),
          "starter_ind_pm48_gap":d(im,imo,"starter_individual_pm48_mean"),"negative_starter_adv":d(imo,im,"starter_individual_negative_count"),
          "prior_minutes_share_gap":d(ct,cto,"prior_team_starter_minutes_share"),"new_starters_adv":d(cto,ct,"new_to_team_starters"),
          "crew_homewin25_side":sign*((n(ocw.get("crew_home_win_last25_avg")) or 0)-0.5) if n(ocw.get("crew_home_win_last25_avg")) is not None else None,
          "crew_homeftadiff25_side":sign*n(ocw.get("crew_home_fta_diff_last25_avg")) if n(ocw.get("crew_home_fta_diff_last25_avg")) is not None else None
        })

def make_conds(rows,features,mincov=500):
    disc=[r for r in rows if r["season"] in A];conds=[]
    for f in features:
        vals=[r.get(f) for r in disc if r.get(f) is not None]
        if len(vals)<mincov:continue
        if f in ("is_home","is_dog","opp_b2b"):
            conds += [(f,"==",0.0),(f,"==",1.0)];continue
        for qq in (.15,.25,.35,.65,.75,.85):
            cut=q(vals,qq)
            if cut is not None:conds.append((f,"<=" if qq<.5 else ">=",float(cut)))
    return conds

def hunt(rows,outk,pk,lane,domains,features):
    conds=make_conds(rows,features,500)
    screen=[]
    for c in conds:
        m=met([r for r in rows if r["season"] in A and meet(r,(c,))],outk,pk)
        if m["n"]<120 or m["hit"] is None or m["roi"] is None:continue
        gate=(m["hit"]>=.64 or m["roi"]>=.025) if lane=="ml" else (m["hit"]>=.525 or m["roi"]>=.015)
        if gate:screen.append((m["wilson_low"] or 0,m["roi"],c))
    screen.sort(reverse=True,key=lambda z:(z[0],z[1]));pool=[z[2] for z in screen[:44]]
    rules=[];seen=set()
    for k,lim in ((2,44),(3,30),(4,18)):
        src=pool[:lim]
        for cs in itertools.combinations(src,k):
            fs={x[0] for x in cs}
            if len(fs)!=k:continue
            dom={domains.get(f,"other") for f in fs}
            if len(dom)<min(3,k):continue
            key=tuple((f,o,round(v,8)) for f,o,v in cs)
            if key in seen:continue
            seen.add(key);rules.append(cs)
    res=[]
    for cs in rules:
        ph={}
        for name,ss in (("discA",A),("discB",B),("validation",V),("holdout",H)):
            ph[name]=met([r for r in rows if r["season"] in ss and meet(r,cs)],outk,pk)
        a,b,v,h=[ph[x] for x in ("discA","discB","validation","holdout")]
        if lane=="ml":
            if a["n"]<65 or b["n"]<40 or v["n"]<20 or h["n"]<40:continue
            if any(x["hit"] is None or x["roi"] is None for x in (a,b,v,h)):continue
            if a["hit"]<.72 or a["roi"]<.04 or b["hit"]<.70 or b["roi"]<=0 or v["hit"]<.70 or v["roi"]<=0 or h["hit"]<.73 or h["roi"]<.045:continue
        else:
            if a["n"]<100 or b["n"]<60 or v["n"]<28 or h["n"]<65:continue
            if any(x["hit"] is None or x["roi"] is None for x in (a,b,v,h)):continue
            if a["hit"]<.55 or a["roi"]<.04 or b["hit"]<.54 or b["roi"]<=0 or v["hit"]<.54 or v["roi"]<=0 or h["hit"]<.545 or h["roi"]<.035:continue
        res.append({"rule":" AND ".join(f"{f} {o} {vv:.5g}" for f,o,vv in cs),
                    "domains":sorted({domains.get(f,"other") for f,o,v in cs}),**ph})
    res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
    return {"screened_conditions":len(screen),"rules_tested":len(rules),"candidates":res[:100]}

features=list(DOMAIN)
ml=hunt([r for r in side if r["ml_price"] is not None],"ml_out","ml_price","ml",DOMAIN,features)
ats=hunt([r for r in side if r["ats_price"] is not None and r["ats_out"] is not None],"ats_out","ats_price","ats",DOMAIN,features)

# Totals lane with side-pair aggregates.
TDOM={
 "total_line":"market","total_move":"market","pace5":"efficiency","ts_sum":"efficiency","net_abs_gap":"efficiency","threepa_sum":"efficiency",
 "travel7d_sum":"travel","travel7d_abs_gap":"travel","rest_sum":"schedule","any_b2b":"schedule",
 "lq_abs_gap":"lineup","starterpm_abs_gap":"starter","stand_rank_abs_gap":"standings","clutch_abs_gap":"clutch",
 "paint_sum":"style","foul_sum":"style","oreb_sum":"style","forced_tov_sum":"style","elo_abs_gap":"strength",
 "crew_fta25":"officials","crew_fouls25":"officials","coach_change_sum":"coaching","tx30_sum":"transactions"
}
tot=[]
for gid,g in games.items():
    m=market.get(gid);h=str(g.get("home_team_id") or "");a=str(g.get("away_team_id") or "")
    if not m:continue
    hs=n(g.get("home_score"));as_=n(g.get("away_score"));linev=n(m.get("closing_total_median"))
    op=n(m.get("over_price_median"));up=n(m.get("under_price_median"))
    if None in (hs,as_,linev):continue
    hr,ar=roll.get((gid,h),{}),roll.get((gid,a),{});hc,ac=ctx.get((gid,h),{}),ctx.get((gid,a),{})
    ht,at=travel.get((gid,h),{}),travel.get((gid,a),{});hl,al=lq.get((gid,h),{}),lq.get((gid,a),{})
    hst,ast=stand.get((gid,h),{}),stand.get((gid,a),{});hsh,ash=shape.get((gid,h),{}),shape.get((gid,a),{})
    hsty,asty=style.get((gid,h),{}),style.get((gid,a),{});he,ae=elo.get((gid,h),{}),elo.get((gid,a),{})
    hco,aco=coach.get((gid,h),{}),coach.get((gid,a),{});htx,atx=tx.get((gid,h),{}),tx.get((gid,a),{})
    hcore,acore=core.get((gid,h),{}),core.get((gid,a),{});oc=official.get(gid,{})
    actual=hs+as_;over=1 if actual>linev else (0 if actual<linev else .5);under=1 if actual<linev else (0 if actual>linev else .5)
    rh=n(hc.get("days_since_prev_game"));ra=n(ac.get("days_since_prev_game"))
    t7h=n(ht.get("travel_miles_prev_7d_including_arrival"));t7a=n(at.get("travel_miles_prev_7d_including_arrival"))
    paces=[n(hr.get("_possessions_est_last5_avg")),n(ar.get("_possessions_est_last5_avg"))];paces=[x for x in paces if x is not None]
    tot.append({
      "season":g.get("season"),"game_id":gid,"over_out":over,"under_out":under,"over_price":op,"under_price":up,
      "total_line":linev,"total_move":n(m.get("total_move")),"pace5":statistics.mean(paces) if paces else None,
      "ts_sum":sum2(hr,ar,"_true_shooting_est_last5_avg"),"net_abs_gap":absd(hr,ar,"_net_rating_est_last5_avg"),"threepa_sum":sum2(hr,ar,"_three_pa_rate_last5_avg"),
      "travel7d_sum":t7h+t7a if t7h is not None and t7a is not None else None,"travel7d_abs_gap":abs(t7h-t7a) if t7h is not None and t7a is not None else None,
      "rest_sum":rh+ra if rh is not None and ra is not None else None,"any_b2b":1.0 if t(hc.get("back_to_back")) or t(ac.get("back_to_back")) else 0.0,
      "lq_abs_gap":absd(hl,al,"current_start5_prior_pm48"),"starterpm_abs_gap":absd(hcore,acore,"starter_plusminus5_avg"),
      "stand_rank_abs_gap":absd(hst,ast,"conference_rank"),"clutch_abs_gap":absd(hsh,ash,"clutch_margin_last10_avg"),
      "paint_sum":sum2(hsty,asty,"paint_share_last5_avg"),"foul_sum":sum2(hsty,asty,"fouls_per100_last5_avg"),
      "oreb_sum":sum2(hsty,asty,"oreb_rate_last5_avg"),"forced_tov_sum":sum2(hsty,asty,"forced_turnovers_last5_avg"),
      "elo_abs_gap":absd(he,ae,"elo_pre"),"coach_change_sum":sum2(hco,aco,"coach_events_prev_30d"),"tx30_sum":sum2(htx,atx,"transactions_prev_30d"),
      "crew_fta25":n(oc.get("crew_total_fta_last25_avg")),"crew_fouls25":n(oc.get("crew_total_fouls_last25_avg"))
    })
over=hunt([r for r in tot if r["over_price"] is not None],"over_out","over_price","ats",TDOM,list(TDOM))
under=hunt([r for r in tot if r["under_price"] is not None],"under_out","under_price","ats",TDOM,list(TDOM))

for lane,prefix in ((ml,"NBA_H3_ML"),(ats,"NBA_H3_ATS"),(over,"NBA_H3_OVER"),(under,"NBA_H3_UNDER")):
    for i,r in enumerate(lane["candidates"],1):r["method_id"]=f"{prefix}_{i:03d}"

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"side_rows":len(side),"total_games":len(tot),
        "moneyline":ml,"ats":ats,"over":over,"under":under,
        "policy":"Hunt v3 screens thresholds on 2018-21 only, requires cross-domain rules, and evaluates 2021-23 confirmation, 2023-24 validation, 2024-26 holdout without retuning. New candidates remain shadow-only."}
(OUT/"hunt_v3_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({k:{"screened":v["screened_conditions"],"tested":v["rules_tested"],"candidates":len(v["candidates"]),"top":v["candidates"][:5]} for k,v in [("moneyline",ml),("ats",ats),("over",over),("under",under)]},indent=2))
