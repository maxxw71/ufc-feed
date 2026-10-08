#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"shot_profile_v2"
OUT.mkdir(parents=True,exist_ok=True)
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"};ALL=A|B|V|H
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def american_profit(price,outcome):
    p=n(price)
    if p is None or abs(p)<100:return None
    if outcome==0.5:return 0.0
    if outcome==0:return -1.0
    return p/100 if p>0 else 100/abs(p)
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;den=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/den
def met(rr,outk,pk):
    vals=[]
    for r in rr:
        p=american_profit(r.get(pk),r.get(outk))
        if p is not None:vals.append((r,p))
    if not vals:return {"n":0,"wins":0,"losses":0,"pushes":0,"hit":None,"roi":None,"profit":0,"wilson_low":None}
    dec=[r for r,_ in vals if r.get(outk)!=0.5]
    w=sum(1 for r in dec if r.get(outk)==1);loss=len(dec)-w;push=len(vals)-len(dec);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"losses":loss,"pushes":push,"hit":w/len(dec) if dec else None,
            "roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,len(dec))}
def d(a,b,k):
    x=n((a or {}).get(k));y=n((b or {}).get(k))
    return x-y if x is not None and y is not None else None
def s2(a,b,k):
    x=n((a or {}).get(k));y=n((b or {}).get(k))
    return x+y if x is not None and y is not None else None
def absd(a,b,k):
    x=d(a,b,k);return abs(x) if x is not None else None
def q(vals,p):
    vals=sorted(x for x in vals if x is not None)
    return vals[min(len(vals)-1,int(p*(len(vals)-1)))] if vals else None
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
shot={(r.get("game_id"),str(r.get("team_id") or "")):r for r in rgz(F/"shot_profile_rolling.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

side=[];game_rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hid=str(g.get("home_team_id") or "");aid=str(g.get("away_team_id") or "")
    h=shot.get((gid,hid),{});a=shot.get((gid,aid),{})
    hs=n(g.get("home_score"));aas=n(g.get("away_score"))
    if hs is None or aas is None:continue

    total=n(m.get("closing_total_median"));actual=hs+aas
    op=n(m.get("over_price_median"));up=n(m.get("under_price_median"))
    if total is not None:
        out_over=1 if actual>total else (0 if actual<total else 0.5)
        out_under=1 if actual<total else (0 if actual>total else 0.5)
        game_rows.append({
          "season":g.get("season"),"game_id":gid,
          "over_out":out_over,"under_out":out_under,
          "over_price":op,"over_best":n(m.get("over_price_best")),"over_worst":n(m.get("over_price_worst")),
          "under_price":up,"under_best":n(m.get("under_price_best")),"under_worst":n(m.get("under_price_worst")),
          "closing_total":total,
          "rimrate_sum5":s2(h,a,"rim_rate_last5_avg"),
          "rimeff_sum5":s2(h,a,"rim_fg_pct_last5_avg"),
          "shortrate_sum5":s2(h,a,"short_rate_last5_avg"),
          "shorteff_sum5":s2(h,a,"short_fg_pct_last5_avg"),
          "midrate_sum5":s2(h,a,"midrange_rate_last5_avg"),
          "mideff_sum5":s2(h,a,"mid_fg_pct_last5_avg"),
          "threerate_sum5":s2(h,a,"three_rate_last5_avg"),
          "threeeff_sum5":s2(h,a,"three_fg_pct_last5_avg"),
          "avgdist_sum5":s2(h,a,"avg_shot_distance_last5_avg"),
          "paintish_sum5":s2(h,a,"paintish_rate_last5_avg"),
          "rimrate_gap5":absd(h,a,"rim_rate_last5_avg"),
          "rimeff_gap5":absd(h,a,"rim_fg_pct_last5_avg"),
          "threerate_gap5":absd(h,a,"three_rate_last5_avg"),
          "threeeff_gap5":absd(h,a,"three_fg_pct_last5_avg"),
          "paintish_gap10":absd(h,a,"paintish_rate_last10_avg"),
          "avgdist_gap10":absd(h,a,"avg_shot_distance_last10_avg")
        })

    hspread=n(m.get("home_spread_signed_median"))
    for home in (True,False):
        own=h if home else a;opp=a if home else h
        score=hs if home else aas;oscore=aas if home else hs
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        mlb=n(m.get("home_moneyline_best") if home else m.get("away_moneyline_best"))
        mlw=n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst"))
        prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        spr=hspread if home else (-hspread if hspread is not None else None)
        spp=n(m.get("home_spread_price_median") if home else m.get("away_spread_price_median"))
        spb=n(m.get("home_spread_price_best") if home else m.get("away_spread_price_best"))
        spw=n(m.get("home_spread_price_worst") if home else m.get("away_spread_price_worst"))
        ats=None if spr is None else (1 if score+spr>oscore else (0 if score+spr<oscore else 0.5))
        side.append({
          "season":g.get("season"),"game_id":gid,
          "ml_out":1 if score>oscore else 0,"ml_price":ml,"ml_best":mlb,"ml_worst":mlw,
          "ats_out":ats,"ats_price":spp,"ats_best":spb,"ats_worst":spw,
          "market_prob":prob,"spread":spr,"is_home":1.0 if home else 0.0,
          "rimrate5_gap":d(own,opp,"rim_rate_last5_avg"),
          "rimeff5_gap":d(own,opp,"rim_fg_pct_last5_avg"),
          "shortrate5_gap":d(own,opp,"short_rate_last5_avg"),
          "shortfg5_gap":d(own,opp,"short_fg_pct_last5_avg"),
          "midrate5_adv":d(opp,own,"midrange_rate_last5_avg"),
          "midfg5_gap":d(own,opp,"mid_fg_pct_last5_avg"),
          "threerate5_gap":d(own,opp,"three_rate_last5_avg"),
          "threefg5_gap":d(own,opp,"three_fg_pct_last5_avg"),
          "shotdist5_adv":d(opp,own,"avg_shot_distance_last5_avg"),
          "paintish10_gap":d(own,opp,"paintish_rate_last10_avg"),
          "threerate10_gap":d(own,opp,"three_rate_last10_avg"),
          "threefg10_gap":d(own,opp,"three_fg_pct_last10_avg")
        })

DOM_SIDE={
 "market_prob":"market","spread":"market","is_home":"context",
 "rimrate5_gap":"rim","rimeff5_gap":"rim",
 "shortrate5_gap":"short","shortfg5_gap":"short",
 "midrate5_adv":"mid","midfg5_gap":"mid",
 "threerate5_gap":"three","threefg5_gap":"three",
 "shotdist5_adv":"distance","paintish10_gap":"paint",
 "threerate10_gap":"three","threefg10_gap":"three"
}
DOM_TOTAL={
 "closing_total":"market",
 "rimrate_sum5":"rim","rimeff_sum5":"rim",
 "shortrate_sum5":"short","shorteff_sum5":"short",
 "midrate_sum5":"mid","mideff_sum5":"mid",
 "threerate_sum5":"three","threeeff_sum5":"three",
 "avgdist_sum5":"distance","paintish_sum5":"paint",
 "rimrate_gap5":"rim_gap","rimeff_gap5":"rim_gap",
 "threerate_gap5":"three_gap","threeeff_gap5":"three_gap",
 "paintish_gap10":"paint_gap","avgdist_gap10":"distance_gap"
}

def conditions(rows,domains,minvals=500):
    disc=[r for r in rows if r["season"] in A];out=[]
    for f in domains:
        vals=[r.get(f) for r in disc if r.get(f) is not None]
        if len(vals)<minvals:continue
        if f=="is_home":
            out += [(f,"==",0.0),(f,"==",1.0)];continue
        for qq in (.15,.25,.35,.65,.75,.85):
            cut=q(vals,qq)
            if cut is not None:out.append((f,"<=" if qq<.5 else ">=",float(cut)))
    return out

def hunt(rows,domains,outk,pk,bestk,worstk,lane):
    conds=conditions(rows,domains)
    screened=[]
    for c in conds:
        mm=met([r for r in rows if r["season"] in A and meet(r,(c,))],outk,pk)
        if mm["n"]<120 or mm["roi"] is None or mm["hit"] is None:continue
        gate=(mm["hit"]>=.64 or mm["roi"]>=.025) if lane in ("ml","ats") else (mm["hit"]>=.525 or mm["roi"]>=.015)
        if gate:screened.append((mm["wilson_low"] or 0,mm["roi"],c))
    screened.sort(reverse=True,key=lambda z:(z[0],z[1]))
    pool=[z[2] for z in screened[:56]]
    rules=[];seen=set()
    for k,limit in ((2,56),(3,36)):
        for cs in itertools.combinations(pool[:limit],k):
            fs={x[0] for x in cs}
            if len(fs)!=k:continue
            dom={domains[f] for f in fs}
            shotdom={x for x in dom if x not in {"market","context"}}
            if len(shotdom)<2:continue
            key=tuple((f,o,round(v,8)) for f,o,v in cs)
            if key in seen:continue
            seen.add(key);rules.append(cs)
    res=[]
    for cs in rules:
        ph={name:met([r for r in rows if r["season"] in ss and meet(r,cs)],outk,pk)
            for name,ss in (("discA",A),("discB",B),("validation",V),("holdout",H))}
        a,b,v,h=[ph[x] for x in ("discA","discB","validation","holdout")]
        if lane=="ml":
            if a["n"]<65 or b["n"]<35 or v["n"]<18 or h["n"]<35:continue
            if any(x["hit"] is None or x["roi"] is None for x in (a,b,v,h)):continue
            if a["hit"]<.72 or a["roi"]<.04 or b["hit"]<.69 or b["roi"]<=0 or v["hit"]<.69 or v["roi"]<=0 or h["hit"]<.72 or h["roi"]<.04:continue
        elif lane=="ats":
            if a["n"]<90 or b["n"]<50 or v["n"]<25 or h["n"]<50:continue
            if any(x["hit"] is None or x["roi"] is None for x in (a,b,v,h)):continue
            if a["hit"]<.56 or a["roi"]<.035 or b["hit"]<.54 or b["roi"]<=0 or v["hit"]<.54 or v["roi"]<=0 or h["hit"]<.55 or h["roi"]<.03:continue
        else:
            if a["n"]<95 or b["n"]<55 or v["n"]<25 or h["n"]<55:continue
            if any(x["hit"] is None or x["roi"] is None for x in (a,b,v,h)):continue
            if a["hit"]<.55 or a["roi"]<.04 or b["hit"]<.535 or b["roi"]<=0 or v["hit"]<.535 or v["roi"]<=0 or h["hit"]<.55 or h["roi"]<.035:continue
        overall=[r for r in rows if meet(r,cs)];hold=[r for r in rows if r["season"] in H and meet(r,cs)]
        stress={"best":met(overall,outk,bestk),"median":met(overall,outk,pk),"worst":met(overall,outk,worstk),
                "holdout_best":met(hold,outk,bestk),"holdout_median":met(hold,outk,pk),"holdout_worst":met(hold,outk,worstk)}
        if stress["worst"]["roi"] is None or stress["worst"]["roi"]<=0:continue
        if stress["holdout_worst"]["roi"] is None or stress["holdout_worst"]["roi"]<=0:continue
        byseason={s:met([r for r in overall if r["season"]==s],outk,pk) for s in SEASONS}
        res.append({"rule":" AND ".join(f"{f} {o} {vv:.6g}" for f,o,vv in cs),
                    "domains":sorted({domains[f] for f,o,v in cs}),"overall":met(overall,outk,pk),
                    **ph,"price_stress":stress,"by_season":byseason})
    res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
    return {"screened_conditions":len(screened),"rules_tested":len(rules),"candidates":res[:100]}

lanes={
 "moneyline":hunt([r for r in side if r["ml_price"] is not None],DOM_SIDE,"ml_out","ml_price","ml_best","ml_worst","ml"),
 "ats":hunt([r for r in side if r["ats_price"] is not None and r["ats_out"] is not None],DOM_SIDE,"ats_out","ats_price","ats_best","ats_worst","ats"),
 "over":hunt([r for r in game_rows if r["over_price"] is not None],DOM_TOTAL,"over_out","over_price","over_best","over_worst","over"),
 "under":hunt([r for r in game_rows if r["under_price"] is not None],DOM_TOTAL,"under_out","under_price","under_best","under_worst","under")
}
prefix={"moneyline":"NBA_SHOT_ML","ats":"NBA_SHOT_ATS","over":"NBA_SHOT_OVER","under":"NBA_SHOT_UNDER"}
for lane,x in lanes.items():
    for i,r in enumerate(x["candidates"],1):r["method_id"]=f"{prefix[lane]}_{i:03d}"

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "side_rows":len(side),"game_rows":len(game_rows),"lanes":lanes,
        "policy":"Shot-profile V2 uses only prior completed-game coordinate-derived shot zones. Thresholds and screening are learned on 2018-21 only; 2021-23 confirmation, 2023-24 validation and 2024-26 holdout are evaluation-only. Rules require at least two distinct shot-profile subdomains and must remain profitable at worst executable historical prices overall and in holdout."}
(OUT/"shot_profile_v2_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({lane:{"screened":x["screened_conditions"],"tested":x["rules_tested"],"candidates":len(x["candidates"]),"top":x["candidates"][:5]} for lane,x in lanes.items()},indent=2))
