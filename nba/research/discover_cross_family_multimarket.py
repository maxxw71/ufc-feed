#!/usr/bin/env python3
import csv,gzip,itertools,json,math,statistics
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"cross_family"
OUT.mkdir(parents=True,exist_ok=True)
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"};ALL=A|B|V|H

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
def q(vals,p):
    vals=sorted(x for x in vals if x is not None)
    return vals[min(len(vals)-1,int(p*(len(vals)-1)))] if vals else None
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;den=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/den
def pf(a,out):
    a=n(a)
    if a is None or abs(a)<100:return None
    if out==0.5:return 0.0
    if out==0:return -1.0
    return a/100 if a>0 else 100/abs(a)
def metrics(rr,outk,pk):
    vals=[]
    for r in rr:
        p=pf(r.get(pk),r.get(outk))
        if p is not None:vals.append((r,p))
    dec=[r for r,_ in vals if r.get(outk)!=0.5]
    w=sum(1 for r in dec if r.get(outk)==1);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"losses":len(dec)-w,"hit":w/len(dec) if dec else None,"roi":pr/len(vals) if vals else None,
            "profit":pr,"wilson_low":wilson(w,len(dec))}

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in ALL and t(g.get("completed")):games[g["game_id"]]=g

def idx(name):return {(r.get("game_id"),r.get("team_id")):r for r in rgz(F/name)}
roll=idx("team_rolling.csv.gz");ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}
phys=idx("starter_physical.csv.gz");core=idx("starter_core_pregame.csv.gz");line=idx("lineup_pregame.csv.gz")
work=idx("team_workload_pregame.csv.gz");travel=idx("travel_pregame.csv.gz");lq=idx("lineup_quality_pregame.csv.gz")
stand=idx("standings_pregame.csv.gz");role=idx("starter_bench_rolling.csv.gz");shape=idx("scoring_shape_rolling.csv.gz")
style=idx("team_style_rolling.csv.gz");elo=idx("elo_sos_pregame.csv.gz");coach=idx("coach_event_pregame.csv.gz")
tx=idx("transaction_pregame.csv.gz");official={r.get("game_id"):r for r in rgz(F/"official_crew_pregame.csv.gz")}

DOMAIN={
 "market_prob":"market","spread":"market","is_home":"context","is_dog":"market",
 "net5_gap":"efficiency","ts5_gap":"efficiency","def5_adv":"efficiency","threepa5_gap":"efficiency",
 "rest_diff":"schedule","opp_b2b":"schedule",
 "travel7d_adv":"travel","side_travel":"travel","tz_shift_adv":"travel",
 "lq_pm48_gap":"lineup","lq_volatility_adv":"lineup",
 "starter_pm5_gap":"starter","starter_overlap_gap":"lineup","work_top5_3d_adv":"workload",
 "opp_seed6_gap":"standings","streak_gap":"standings","rank_gap":"standings",
 "bench_share5_gap":"rotation","bench_used5_gap":"rotation","clutch10_gap":"clutch",
 "paint_share5_gap":"style","oreb5_gap":"style","forced_tov5_gap":"style","foulrate5_adv":"style",
 "guard_weight_gap":"physical","big_weight_gap":"physical",
 "elo_gap":"strength","sos5_gap":"strength",
 "coach_change30_gap":"coaching","tx30_gap":"transactions"
}

side_rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));aas=n(g.get("away_score"));home_line=n(m.get("home_spread_signed_median"))
    if hs is None or aas is None:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tr,orr=roll.get((gid,tid),{}),roll.get((gid,oid),{});tc,oc=ctx.get((gid,tid),{}),ctx.get((gid,oid),{})
        tp,op=phys.get((gid,tid),{}),phys.get((gid,oid),{});cc,co=core.get((gid,tid),{}),core.get((gid,oid),{})
        tl,ol=line.get((gid,tid),{}),line.get((gid,oid),{});tw,ow=work.get((gid,tid),{}),work.get((gid,oid),{})
        tv,ov=travel.get((gid,tid),{}),travel.get((gid,oid),{});tq,oq=lq.get((gid,tid),{}),lq.get((gid,oid),{})
        ts,os=stand.get((gid,tid),{}),stand.get((gid,oid),{});rr,ro=role.get((gid,tid),{}),role.get((gid,oid),{})
        sh,sho=shape.get((gid,tid),{}),shape.get((gid,oid),{});sty,sto=style.get((gid,tid),{}),style.get((gid,oid),{})
        ee,eo=elo.get((gid,tid),{}),elo.get((gid,oid),{});ch,cho=coach.get((gid,tid),{}),coach.get((gid,oid),{})
        txs,txo=tx.get((gid,tid),{}),tx.get((gid,oid),{})
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        spread=home_line if home else (-home_line if home_line is not None else None)
        sprprice=n(m.get("home_spread_price_median") if home else m.get("away_spread_price_median"))
        score=hs if home else aas;opp=aas if home else hs
        ats=None if spread is None else (1 if score+spread>opp else (0 if score+spread<opp else 0.5))
        side_rows.append({
          "season":g.get("season"),"game_id":gid,"team_id":tid,
          "ml_out":1 if score>opp else 0,"ml_price":ml,"ats_out":ats,"ats_price":sprprice,
          "market_prob":prob,"spread":spread,"is_home":1.0 if home else 0.0,"is_dog":1.0 if ml is not None and ml>0 else 0.0,
          "net5_gap":d(tr,orr,"_net_rating_est_last5_avg"),"ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg"),"def5_adv":d(orr,tr,"_def_rating_est_last5_avg"),
          "threepa5_gap":d(tr,orr,"_three_pa_rate_last5_avg"),
          "rest_diff":rest-orest if rest is not None and orest is not None else None,"opp_b2b":1.0 if t(oc.get("back_to_back")) else 0.0,
          "travel7d_adv":d(ov,tv,"travel_miles_prev_7d_including_arrival"),"side_travel":n(tv.get("travel_miles_from_prev")),"tz_shift_adv":d(ov,tv,"timezone_shift_hours"),
          "lq_pm48_gap":d(tq,oq,"current_start5_prior_pm48"),"lq_volatility_adv":d(oq,tq,"stint_pm48_std_last5_avg"),
          "starter_pm5_gap":d(cc,co,"starter_plusminus5_avg"),"starter_overlap_gap":d(tl,ol,"starter_overlap_prev_game"),"work_top5_3d_adv":d(ow,tw,"top5_minutes_prev_3d"),
          "opp_seed6_gap":n(os.get("winpct_gap_to_seed6")),"streak_gap":d(ts,os,"current_streak"),"rank_gap":d(os,ts,"conference_rank"),
          "bench_share5_gap":d(rr,ro,"bench_points_share_last5_avg"),"bench_used5_gap":d(rr,ro,"bench_used_last5_avg"),"clutch10_gap":d(sh,sho,"clutch_margin_last10_avg"),
          "paint_share5_gap":d(sty,sto,"paint_share_last5_avg"),"oreb5_gap":d(sty,sto,"oreb_rate_last5_avg"),
          "forced_tov5_gap":d(sty,sto,"forced_turnovers_last5_avg"),"foulrate5_adv":d(sto,sty,"fouls_per100_last5_avg"),
          "guard_weight_gap":d(tp,op,"guard_weight"),"big_weight_gap":d(tp,op,"big_weight"),
          "elo_gap":d(ee,eo,"elo_pre"),"sos5_gap":d(ee,eo,"sos_last5"),
          "coach_change30_gap":d(ch,cho,"coach_events_prev_30d"),"tx30_gap":d(txs,txo,"transactions_prev_30d")
        })

SIDE_FEATURES=list(DOMAIN)
disc=[r for r in side_rows if r["season"] in A]
conds=[]
for f in SIDE_FEATURES:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<500:continue
    if f in ("is_home","is_dog","opp_b2b"):
        conds.append((f,"==",1.0));conds.append((f,"==",0.0));continue
    for qq in (.20,.35,.65,.80):
        cut=q(vals,qq)
        if cut is not None:conds.append((f,"<=" if qq<.5 else ">=",float(cut)))

def meet(r,cs):
    for f,o,v in cs:
        x=r.get(f)
        if x is None:return False
        if o=="==" and x!=v:return False
        if o==">=" and x<v:return False
        if o=="<=" and x>v:return False
    return True
def phase(rows,cs,ss,outk,pk):return metrics([r for r in rows if r["season"] in ss and meet(r,cs)],outk,pk)

def screen_lane(rows,outk,pk,lane):
    scored=[]
    for c in conds:
        m=phase(rows,(c,),A,outk,pk)
        if m["n"]<120 or m["hit"] is None or m["roi"] is None:continue
        if (lane=="ml" and (m["hit"]>=.64 or m["roi"]>=.025)) or (lane=="ats" and (m["hit"]>=.525 or m["roi"]>=.015)):
            scored.append((m["wilson_low"] or 0,m["roi"],c))
    scored.sort(reverse=True,key=lambda z:(z[0],z[1]))
    pool=[x[2] for x in scored[:36]]
    rules=[]
    for k in (2,3):
        src=pool if k==2 else pool[:26]
        for cs in itertools.combinations(src,k):
            fs={x[0] for x in cs}
            if len(fs)!=k:continue
            if len({DOMAIN.get(f,"other") for f in fs})<2:continue
            rules.append(cs)
    res=[];seen=set()
    for cs in rules:
        key=tuple((f,o,round(v,8)) for f,o,v in cs)
        if key in seen:continue
        seen.add(key)
        a=phase(rows,cs,A,outk,pk);b=phase(rows,cs,B,outk,pk);v=phase(rows,cs,V,outk,pk);h=phase(rows,cs,H,outk,pk)
        if lane=="ml":
            if a["n"]<70 or b["n"]<45 or v["n"]<20 or h["n"]<45:continue
            if any(x["hit"] is None or x["roi"] is None for x in (a,b,v,h)):continue
            if a["hit"]<.72 or a["roi"]<.035 or b["hit"]<.70 or b["roi"]<=0 or v["hit"]<.70 or v["roi"]<=0 or h["hit"]<.72 or h["roi"]<.04:continue
        else:
            if a["n"]<110 or b["n"]<70 or v["n"]<30 or h["n"]<70:continue
            if any(x["hit"] is None or x["roi"] is None for x in (a,b,v,h)):continue
            if a["hit"]<.55 or a["roi"]<.04 or b["hit"]<.54 or b["roi"]<=0 or v["hit"]<.54 or v["roi"]<=0 or h["hit"]<.54 or h["roi"]<.03:continue
        res.append({"rule":" AND ".join(f"{f} {o} {vv:.4g}" for f,o,vv in cs),"domains":sorted({DOMAIN.get(f,"other") for f,o,v in cs}),
                    "discA":a,"discB":b,"validation":v,"holdout":h})
    res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
    return {"screened_conditions":len(scored),"rules_tested":len(seen),"candidates":res[:100]}

ml=screen_lane([r for r in side_rows if r.get("ml_price") is not None],"ml_out","ml_price","ml")
ats=screen_lane([r for r in side_rows if r.get("ats_price") is not None and r.get("ats_out") is not None],"ats_out","ats_price","ats")

# Game-level totals cross-family search.
TOTAL_DOMAIN={
 "total_line":"market","total_move":"market","pace5":"efficiency","ts_sum":"efficiency","threepa_sum":"efficiency",
 "travel7d_sum":"travel","travel7d_abs_gap":"travel","rest_sum":"schedule","any_b2b":"schedule",
 "lq_abs_gap":"lineup","stand_rank_abs_gap":"standings","clutch_abs_gap":"clutch",
 "paint_sum":"style","foul_sum":"style","oreb_sum":"style","elo_abs_gap":"strength",
 "crew_fta25":"officials","crew_fouls25":"officials"
}
tot=[]
for gid,g in games.items():
    m=market.get(gid);h=g.get("home_team_id");a=g.get("away_team_id")
    if not m:continue
    hs=n(g.get("home_score"));aas=n(g.get("away_score"));linev=n(m.get("closing_total_median"))
    op=n(m.get("over_price_median"));up=n(m.get("under_price_median"))
    if None in (hs,aas,linev):continue
    hr,ar=roll.get((gid,h),{}),roll.get((gid,a),{});hc,ac=ctx.get((gid,h),{}),ctx.get((gid,a),{})
    ht,at=travel.get((gid,h),{}),travel.get((gid,a),{});hl,al=lq.get((gid,h),{}),lq.get((gid,a),{})
    hsx,asx=stand.get((gid,h),{}),stand.get((gid,a),{});hsh,ash=shape.get((gid,h),{}),shape.get((gid,a),{})
    hsty,asty=style.get((gid,h),{}),style.get((gid,a),{});he,ae=elo.get((gid,h),{}),elo.get((gid,a),{})
    oc=official.get(gid,{})
    actual=hs+aas
    over=1 if actual>linev else (0 if actual<linev else 0.5);under=1 if actual<linev else (0 if actual>linev else 0.5)
    def sum2(x,y,k):
        a1=n(x.get(k));b1=n(y.get(k));return a1+b1 if a1 is not None and b1 is not None else None
    def absdiff(x,y,k):
        z=d(x,y,k);return abs(z) if z is not None else None
    paces=[n(hr.get("_possessions_est_last5_avg")),n(ar.get("_possessions_est_last5_avg"))];paces=[x for x in paces if x is not None]
    rh=n(hc.get("days_since_prev_game"));ra=n(ac.get("days_since_prev_game"))
    t7h=n(ht.get("travel_miles_prev_7d_including_arrival"));t7a=n(at.get("travel_miles_prev_7d_including_arrival"))
    tot.append({
      "season":g.get("season"),"game_id":gid,"over_out":over,"under_out":under,"over_price":op,"under_price":up,
      "total_line":linev,"total_move":n(m.get("total_move")),"pace5":statistics.mean(paces) if paces else None,
      "ts_sum":sum2(hr,ar,"_true_shooting_est_last5_avg"),"threepa_sum":sum2(hr,ar,"_three_pa_rate_last5_avg"),
      "travel7d_sum":t7h+t7a if t7h is not None and t7a is not None else None,
      "travel7d_abs_gap":abs(t7h-t7a) if t7h is not None and t7a is not None else None,
      "rest_sum":rh+ra if rh is not None and ra is not None else None,"any_b2b":1.0 if t(hc.get("back_to_back")) or t(ac.get("back_to_back")) else 0.0,
      "lq_abs_gap":absdiff(hl,al,"current_start5_prior_pm48"),"stand_rank_abs_gap":absdiff(hsx,asx,"conference_rank"),
      "clutch_abs_gap":absdiff(hsh,ash,"clutch_margin_last10_avg"),"paint_sum":sum2(hsty,asty,"paint_share_last5_avg"),
      "foul_sum":sum2(hsty,asty,"fouls_per100_last5_avg"),"oreb_sum":sum2(hsty,asty,"oreb_rate_last5_avg"),
      "elo_abs_gap":absdiff(he,ae,"elo_pre"),"crew_fta25":n(oc.get("crew_fta_avg")) or n(oc.get("crew_total_fta_last25_avg")),
      "crew_fouls25":n(oc.get("crew_fouls_avg")) or n(oc.get("crew_total_fouls_last25_avg"))
    })

tdisc=[r for r in tot if r["season"] in A]
tconds=[]
for f in TOTAL_DOMAIN:
    vals=[r.get(f) for r in tdisc if r.get(f) is not None]
    if len(vals)<400:continue
    if f=="any_b2b":
        tconds += [(f,"==",0.0),(f,"==",1.0)];continue
    for qq in (.20,.35,.65,.80):
        cut=q(vals,qq)
        if cut is not None:tconds.append((f,"<=" if qq<.5 else ">=",float(cut)))

def totals_lane(side):
    outk=side+"_out";pk=side+"_price"
    scored=[]
    for c in tconds:
        m=phase(tot,(c,),A,outk,pk)
        if m["n"]>=120 and m["hit"] is not None and m["roi"] is not None and (m["hit"]>=.525 or m["roi"]>=.015):
            scored.append((m["wilson_low"] or 0,m["roi"],c))
    scored.sort(reverse=True,key=lambda z:(z[0],z[1]));pool=[z[2] for z in scored[:34]]
    rules=[]
    for k in (2,3):
        src=pool if k==2 else pool[:24]
        for cs in itertools.combinations(src,k):
            fs={x[0] for x in cs}
            if len(fs)!=k or len({TOTAL_DOMAIN.get(f,"other") for f in fs})<2:continue
            rules.append(cs)
    res=[];seen=set()
    for cs in rules:
        key=tuple((f,o,round(v,8)) for f,o,v in cs)
        if key in seen:continue
        seen.add(key)
        a=phase(tot,cs,A,outk,pk);b=phase(tot,cs,B,outk,pk);v=phase(tot,cs,V,outk,pk);h=phase(tot,cs,H,outk,pk)
        if a["n"]<100 or b["n"]<60 or v["n"]<28 or h["n"]<65:continue
        if any(x["hit"] is None or x["roi"] is None for x in (a,b,v,h)):continue
        if a["hit"]<.55 or a["roi"]<.04 or b["hit"]<.535 or b["roi"]<=0 or v["hit"]<.535 or v["roi"]<=0 or h["hit"]<.54 or h["roi"]<.03:continue
        res.append({"side":side,"rule":" AND ".join(f"{f} {o} {vv:.4g}" for f,o,vv in cs),
                    "domains":sorted({TOTAL_DOMAIN.get(f,"other") for f,o,v in cs}),"discA":a,"discB":b,"validation":v,"holdout":h})
    res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
    return {"screened_conditions":len(scored),"rules_tested":len(seen),"candidates":res[:100]}

over=totals_lane("over");under=totals_lane("under")
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"side_rows":len(side_rows),"total_games":len(tot),
        "moneyline":ml,"ats":ats,"over":over,"under":under,
        "policy":"Cross-family engine requires every rule to span at least two distinct feature domains. Thresholds originate only from 2018-21; 2021-23 confirmation, 2023-24 validation and 2024-26 holdout are evaluation-only."}
for lane,prefix in ((ml,"NBA_XML"),(ats,"NBA_XATS"),(over,"NBA_XOVER"),(under,"NBA_XUNDER")):
    for i,r in enumerate(lane["candidates"],1):r["method_id"]=f"{prefix}_{i:03d}"
(OUT/"cross_family_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({k:{"screened":v["screened_conditions"],"tested":v["rules_tested"],"candidates":len(v["candidates"]),"top":v["candidates"][:3]} for k,v in [("moneyline",ml),("ats",ats),("over",over),("under",under)]},indent=2))
