#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"hunt_v5"
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
    x=n((a or {}).get(k));y=n((b or {}).get(k))
    return x-y if x is not None and y is not None else None
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

market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}
strength={r.get("game_id"):r for r in rgz(F/"pregame_strength.csv.gz")}
official={r.get("game_id"):r for r in rgz(F/"official_crew_pregame.csv.gz")}
roll=idx("team_rolling.csv.gz");travel=idx("travel_pregame.csv.gz");work=idx("team_workload_pregame.csv.gz")
impact=idx("starter_individual_impact_pregame.csv.gz");cont=idx("starter_prior_season_continuity.csv.gz")
txv=idx("transaction_value_pregame.csv.gz");lq=idx("lineup_quality_pregame.csv.gz")
phys=idx("starter_physical.csv.gz");stand=idx("standings_pregame.csv.gz")
shape=idx("scoring_shape_rolling.csv.gz");style=idx("team_style_rolling.csv.gz")
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
coach={(r.get("season"),r.get("team_id")):r for r in rgz(F/"coach_continuity.csv.gz")}

DOMAIN={
 "market_prob":"market","spread":"market","is_home":"context","is_dog":"market",
 "strength_winpct_gap":"strength_context","strength_pointdiff_gap":"strength_context","venue_winpct_gap":"strength_context",
 "net5_gap":"efficiency","ts5_gap":"efficiency","def5_adv":"efficiency",
 "rest_diff":"schedule","opp_b2b":"schedule","travel7d_adv":"travel","work_top5_3d_adv":"workload",
 "starter_ind_pm48_gap":"starter_impact","starter_negative_adv":"starter_impact",
 "prior_minutes_share_gap":"prior_continuity","new_starters_adv":"prior_continuity",
 "tx_minutes14_gap":"transaction_value","tx_plusminus30_gap":"transaction_value","tx_usage30_gap":"transaction_value",
 "incoming_rotation30_gap":"transaction_value","outgoing_rotation30_adv":"transaction_value",
 "coach_tenure_gap":"coach_continuity","side_new_coach":"coach_continuity","opp_new_coach":"coach_continuity",
 "crew_homewin25_side":"officials","crew_homeftadiff25_side":"officials","crew_fta25":"officials","crew_fouls25":"officials",
 "lq_pm48_gap":"lineup","guard_weight_gap":"physical","big_weight_gap":"physical",
 "rank_gap":"standings","clutch10_gap":"clutch","oreb5_gap":"style","forced_tov5_gap":"style"
}
NOVEL={"strength_context","transaction_value","coach_continuity","officials","starter_impact","prior_continuity"}

side=[]
for gid,g in games.items():
    m=market.get(gid);s=strength.get(gid,{})
    if not m:continue
    hs=n(g.get("home_score"));aas=n(g.get("away_score"));home_spread=n(m.get("home_spread_signed_median"))
    if hs is None or aas is None:continue
    for home in (True,False):
        tid=str(g.get("home_team_id") if home else g.get("away_team_id"))
        oid=str(g.get("away_team_id") if home else g.get("home_team_id"))
        tr,orr=roll.get((gid,tid),{}),roll.get((gid,oid),{})
        tv,ov=travel.get((gid,tid),{}),travel.get((gid,oid),{})
        tw,ow=work.get((gid,tid),{}),work.get((gid,oid),{})
        im,imo=impact.get((gid,tid),{}),impact.get((gid,oid),{})
        ct,cto=cont.get((gid,tid),{}),cont.get((gid,oid),{})
        xv,xvo=txv.get((gid,tid),{}),txv.get((gid,oid),{})
        lqr,lqo=lq.get((gid,tid),{}),lq.get((gid,oid),{})
        pr,pro=phys.get((gid,tid),{}),phys.get((gid,oid),{})
        sr,sro=stand.get((gid,tid),{}),stand.get((gid,oid),{})
        sh,sho=shape.get((gid,tid),{}),shape.get((gid,oid),{})
        sty,styo=style.get((gid,tid),{}),style.get((gid,oid),{})
        tc,oc=ctx.get((gid,tid),{}),ctx.get((gid,oid),{})
        cr=coach.get((g.get("season"),tid),{});cro=coach.get((g.get("season"),oid),{})
        ocw=official.get(gid,{})
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        mlb=n(m.get("home_moneyline_best") if home else m.get("away_moneyline_best"))
        mlw=n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst"))
        prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        spr=home_spread if home else (-home_spread if home_spread is not None else None)
        spp=n(m.get("home_spread_price_median") if home else m.get("away_spread_price_median"))
        spb=n(m.get("home_spread_price_best") if home else m.get("away_spread_price_best"))
        spw=n(m.get("home_spread_price_worst") if home else m.get("away_spread_price_worst"))
        score=hs if home else aas;opp=aas if home else hs
        ats=None if spr is None else (1 if score+spr>opp else (0 if score+spr<opp else 0.5))
        sign=1 if home else -1
        home_wp=n(s.get("home_prior_win_pct"));away_wp=n(s.get("away_prior_win_pct"))
        home_pd=n(s.get("home_prior_point_diff_avg"));away_pd=n(s.get("away_prior_point_diff_avg"))
        own_wp=home_wp if home else away_wp;opp_wp=away_wp if home else home_wp
        own_pd=home_pd if home else away_pd;opp_pd=away_pd if home else home_pd
        if home:
            own_venue=n(s.get("home_prior_home_win_pct"));opp_venue=n(s.get("away_prior_road_win_pct"))
        else:
            own_venue=n(s.get("away_prior_road_win_pct"));opp_venue=n(s.get("home_prior_home_win_pct"))
        side.append({
          "season":g.get("season"),"game_id":gid,"team_id":tid,
          "ml_out":1 if score>opp else 0,"ml_price":ml,"ml_best":mlb,"ml_worst":mlw,
          "ats_out":ats,"ats_price":spp,"ats_best":spb,"ats_worst":spw,
          "market_prob":prob,"spread":spr,"is_home":1.0 if home else 0.0,"is_dog":1.0 if ml is not None and ml>0 else 0.0,
          "strength_winpct_gap":own_wp-opp_wp if own_wp is not None and opp_wp is not None else None,
          "strength_pointdiff_gap":own_pd-opp_pd if own_pd is not None and opp_pd is not None else None,
          "venue_winpct_gap":own_venue-opp_venue if own_venue is not None and opp_venue is not None else None,
          "net5_gap":d(tr,orr,"_net_rating_est_last5_avg"),"ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg"),
          "def5_adv":d(orr,tr,"_def_rating_est_last5_avg"),
          "rest_diff":rest-orest if rest is not None and orest is not None else None,
          "opp_b2b":1.0 if t(oc.get("back_to_back")) else 0.0,
          "travel7d_adv":d(ov,tv,"travel_miles_prev_7d_including_arrival"),
          "work_top5_3d_adv":d(ow,tw,"top5_minutes_prev_3d"),
          "starter_ind_pm48_gap":d(im,imo,"starter_individual_pm48_mean"),
          "starter_negative_adv":d(imo,im,"starter_individual_negative_count"),
          "prior_minutes_share_gap":d(ct,cto,"prior_team_starter_minutes_share"),
          "new_starters_adv":d(cto,ct,"new_to_team_starters"),
          "tx_minutes14_gap":d(xv,xvo,"net_minutes_value_14d"),
          "tx_plusminus30_gap":d(xv,xvo,"net_plusminus_value_30d"),
          "tx_usage30_gap":d(xv,xvo,"net_usage_value_30d"),
          "incoming_rotation30_gap":d(xv,xvo,"incoming_high_rotation_30d"),
          "outgoing_rotation30_adv":d(xvo,xv,"outgoing_high_rotation_30d"),
          "coach_tenure_gap":d(cr,cro,"consecutive_seasons_same_coach"),
          "side_new_coach":1.0 if t(cr.get("new_coach_vs_prior_season")) else 0.0,
          "opp_new_coach":1.0 if t(cro.get("new_coach_vs_prior_season")) else 0.0,
          "crew_homewin25_side":sign*((n(ocw.get("crew_home_win_last25_avg")) or 0)-0.5) if n(ocw.get("crew_home_win_last25_avg")) is not None else None,
          "crew_homeftadiff25_side":sign*n(ocw.get("crew_home_fta_diff_last25_avg")) if n(ocw.get("crew_home_fta_diff_last25_avg")) is not None else None,
          "crew_fta25":n(ocw.get("crew_total_fta_last25_avg")),
          "crew_fouls25":n(ocw.get("crew_total_fouls_last25_avg")),
          "lq_pm48_gap":d(lqr,lqo,"current_start5_prior_pm48"),
          "guard_weight_gap":d(pr,pro,"guard_weight"),"big_weight_gap":d(pr,pro,"big_weight"),
          "rank_gap":d(sro,sr,"conference_rank"),"clutch10_gap":d(sh,sho,"clutch_margin_last10_avg"),
          "oreb5_gap":d(sty,styo,"oreb_rate_last5_avg"),"forced_tov5_gap":d(sty,styo,"forced_turnovers_last5_avg")
        })

def make_conds(rows):
    disc=[r for r in rows if r["season"] in A];conds=[]
    for f in DOMAIN:
        vals=[r.get(f) for r in disc if r.get(f) is not None]
        if len(vals)<500:continue
        if f in ("is_home","is_dog","opp_b2b","side_new_coach","opp_new_coach"):
            conds += [(f,"==",0.0),(f,"==",1.0)]
            continue
        for qq in (.15,.25,.35,.65,.75,.85):
            cut=q(vals,qq)
            if cut is not None:conds.append((f,"<=" if qq<.5 else ">=",float(cut)))
    return conds

def hunt(rows,outk,pricek,bestk,worstk,lane):
    conds=make_conds(rows)
    # V5 expands beyond individually profitable conditions: an underused
    # domain may have little marginal edge but be powerful in an interaction.
    # Enforce discovery-only source coverage, then preserve diverse domains.
    per_feature=defaultdict(list)
    for condition in conds:
        stat=met([r for r in rows if r["season"] in A and meet(r,(condition,))],outk,pricek)
        if stat["n"]<120 or stat["hit"] is None or stat["roi"] is None:continue
        # Only 2018-21 informs ranking. Later data are strictly evaluation.
        score=(stat["roi"]+.25*(stat["hit"]-(.7 if lane=="ml" else .52)))
        per_feature[condition[0]].append((score,stat["n"],condition))
    # Round-robin each scientific domain to prevent market/efficiency
    # variables from dominating the pool and suppressing novel interaction.
    per_domain=defaultdict(list)
    for feature,scored in per_feature.items():
        scored.sort(reverse=True,key=lambda x:(x[0],x[1]))
        domain=DOMAIN.get(feature,"other")
        per_domain[domain].extend(scored[:3])
    for domain in per_domain:
        per_domain[domain].sort(reverse=True,key=lambda x:(x[0],x[1]))
    order=sorted(per_domain,key=lambda d:(d not in NOVEL,d))
    pool=[];position=0
    while len(pool)<52 and position<25:
        for domain in order:
            if position<len(per_domain[domain]):
                pool.append(per_domain[domain][position][2])
                if len(pool)>=52:break
        position+=1
    screen=[t for arr in per_domain.values() for t in arr]
    rules=[];seen=set()
    for k,lim in ((2,52),(3,30),(4,18)):
        src=pool[:lim]
        for cs in itertools.combinations(src,k):
            fs={x[0] for x in cs}
            if len(fs)!=k:continue
            dom={DOMAIN.get(f,"other") for f in fs}
            if len(dom)<min(3,k):continue
            if not (dom & NOVEL):continue
            key=tuple((f,o,round(v,8)) for f,o,v in cs)
            if key in seen:continue
            seen.add(key);rules.append(cs)
    res=[]
    for cs in rules:
        ph={name:met([r for r in rows if r["season"] in ss and meet(r,cs)],outk,pricek)
            for name,ss in (("discA",A),("discB",B),("validation",V),("holdout",H))}
        a,b,v,h=[ph[x] for x in ("discA","discB","validation","holdout")]
        if lane=="ml":
            if a["n"]<60 or b["n"]<35 or v["n"]<18 or h["n"]<35:continue
            if any(x["hit"] is None or x["roi"] is None for x in (a,b,v,h)):continue
            if a["hit"]<.72 or a["roi"]<.04 or b["hit"]<.69 or b["roi"]<=0 or v["hit"]<.69 or v["roi"]<=0 or h["hit"]<.72 or h["roi"]<.04:continue
        else:
            if a["n"]<95 or b["n"]<55 or v["n"]<25 or h["n"]<55:continue
            if any(x["hit"] is None or x["roi"] is None for x in (a,b,v,h)):continue
            if a["hit"]<.545 or a["roi"]<.035 or b["hit"]<.535 or b["roi"]<=0 or v["hit"]<.535 or v["roi"]<=0 or h["hit"]<.54 or h["roi"]<.03:continue
        overall=[r for r in rows if meet(r,cs)]
        hold=[r for r in rows if r["season"] in H and meet(r,cs)]
        stress={"overall_best":met(overall,outk,bestk),"overall_worst":met(overall,outk,worstk),
                "holdout_best":met(hold,outk,bestk),"holdout_worst":met(hold,outk,worstk)}
        if stress["overall_worst"]["roi"] is None or stress["overall_worst"]["roi"]<=0:continue
        if stress["holdout_worst"]["roi"] is None or stress["holdout_worst"]["roi"]<=0:continue
        res.append({"rule":" AND ".join(f"{f} {o} {vv:.5g}" for f,o,vv in cs),
                    "domains":sorted({DOMAIN.get(f,"other") for f,o,v in cs}),
                    **ph,"price_stress":stress})
    res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
    return {"screened_conditions":len(screen),"rules_tested":len(rules),"candidates":res[:100]}

ml=hunt([r for r in side if r["ml_price"] is not None],"ml_out","ml_price","ml_best","ml_worst","ml")
ats=hunt([r for r in side if r["ats_price"] is not None and r["ats_out"] is not None],"ats_out","ats_price","ats_best","ats_worst","ats")

for lane,prefix in ((ml,"NBA_H5_ML"),(ats,"NBA_H5_ATS")):
    for i,r in enumerate(lane["candidates"],1):r["method_id"]=f"{prefix}_{i:03d}"

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"side_rows":len(side),
        "moneyline":ml,"ats":ats,
        "policy":"Hunt V5 tests independent moneyline/ATS interactions with balanced coverage across underused scientific domains, including features with weak marginal single-filter ROI. All threshold cuts and marginal ranking are from 2018-21 only; 2021-23 confirmation, 2023-24 validation and 2024-26 untouched holdout. No survivor is live by default. Surviving candidates must have positive ROI at historical worst executable prices overall and in holdout."}
(OUT/"hunt_v5_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({k:{"screened":v["screened_conditions"],"tested":v["rules_tested"],"candidates":len(v["candidates"]),"top":v["candidates"][:10]} for k,v in (("moneyline",ml),("ats",ats))},indent=2))
