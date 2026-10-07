#!/usr/bin/env python3
from __future__ import annotations
import math,re,json
from pathlib import Path
import numpy as np,pandas as pd

ROOT=Path(".")
MASTER=ROOT/"dwcs/frozen/dwcs_historical_master_frozen.csv"
OUT=ROOT/"dwcs/research/method_discovery_frozen"
OUT.mkdir(parents=True,exist_ok=True)

def num(v):
    x=pd.to_numeric(pd.Series([v]),errors="coerce").iloc[0]
    return float(x) if pd.notna(x) else np.nan

def imp(o):
    o=num(o)
    if pd.isna(o):return np.nan
    return (-o)/((-o)+100) if o<0 else 100/(o+100)

def profit100(o,won):
    o=num(o)
    if pd.isna(o):return np.nan
    return -100.0 if not won else (10000/abs(o) if o<0 else o)

FEATURES={
 "age":"age",
 "exp":"prior_fights",
 "win":"prior_win_pct",
 "finish":"prior_finish_rate",
 "r1finish":"prior_first_round_finish_rate",
 "oppqual":"prior_avg_opponent_win_pct",
 "elo":"prefight_global_elo",
 "sos":"avg_opponent_pre_elo",
 "layoff":"prior_days_since_last_fight",
 "height":"height_in",
 "reach":"reach_in",
 "tech_n":"dwcs_prior_technical_fights",
 "sigdiff":"dwcs_prior_sig_diff",
 "sigdef":"dwcs_prior_sig_defense",
 "tddef":"dwcs_prior_td_defense",
 "ctrl":"dwcs_prior_control_diff",
 "kd":"dwcs_prior_knockdowns",
 "sub":"dwcs_prior_sub_attempts",
}

def v(r,side,key):
    col=f"{side}_{FEATURES[key]}"
    return num(r.get(col,np.nan))

def odds(r,side,which="close"):
    return num(r.get(f"{side}_{which}_odds",np.nan))

def movement(r,side):
    a=imp(odds(r,side,"open")); b=imp(odds(r,side,"close"))
    return (b-a)*100 if pd.notna(a) and pd.notna(b) else np.nan

def other(side): return "b" if side=="a" else "a"

def edge_side(r,key,threshold,larger=True):
    a,b=v(r,"a",key),v(r,"b",key)
    if pd.isna(a) or pd.isna(b):return None
    d=a-b
    if larger:
        if d>=threshold:return "a"
        if d<=-threshold:return "b"
    else:
        if d<=-threshold:return "a"
        if d>=threshold:return "b"
    return None

def younger(r,threshold):
    a,b=v(r,"a","age"),v(r,"b","age")
    if pd.isna(a) or pd.isna(b):return None
    if b-a>=threshold:return "a"
    if a-b>=threshold:return "b"
    return None

def price_ok(r,s,lo=None,hi=None):
    o=odds(r,s)
    if pd.isna(o):return False
    if lo is not None and o<lo:return False
    if hi is not None and o>hi:return False
    return True

def same_side_edges(r,side,requirements):
    for key,thr,direction in requirements:
        a=v(r,side,key); b=v(r,other(side),key)
        if pd.isna(a) or pd.isna(b):return False
        d=a-b
        if direction=="ge" and d<thr:return False
        if direction=="le" and d>thr:return False
    return True

RULES=[]
def add(family,label,chooser,lane_hint="both"):
    RULES.append({"family":family,"rule":label,"chooser":chooser,"lane_hint":lane_hint})

# Pure context edges.
for t in [2,3,4,5,6,7,8]:
    add("AGE_EDGE",f"younger_by>={t}y",lambda r,t=t:younger(r,t))
for t in [2,4,6,8,10]:
    add("EXPERIENCE_EDGE",f"experience_gap>={t}",lambda r,t=t:edge_side(r,"exp",t))
for t in [.10,.15,.20,.25,.30]:
    add("WIN_RATE_EDGE",f"win_pct_gap>={t:.2f}",lambda r,t=t:edge_side(r,"win",t))
for t in [.05,.10,.15,.20,.25]:
    add("OPP_QUALITY_EDGE",f"opponent_quality_gap>={t:.2f}",lambda r,t=t:edge_side(r,"oppqual",t))
for t in [25,40,50,60,75,100]:
    add("GLOBAL_ELO_EDGE",f"global_elo_gap>={t}",lambda r,t=t:edge_side(r,"elo",t))
for t in [25,40,50,75,100]:
    add("SOS_EDGE",f"avg_opponent_elo_gap>={t}",lambda r,t=t:edge_side(r,"sos",t))
for t in [.15,.25,.35,.45]:
    add("FINISH_EDGE",f"finish_rate_gap>={t:.2f}",lambda r,t=t:edge_side(r,"finish",t))
for t in [.15,.25,.35,.45]:
    add("R1_FINISH_EDGE",f"first_round_finish_gap>={t:.2f}",lambda r,t=t:edge_side(r,"r1finish",t))
for t in [2,4,6]:
    add("REACH_EDGE",f"reach_gap>={t}in",lambda r,t=t:edge_side(r,"reach",t))

# Youth + proof.
for ag in [3,4,5,6]:
    for eg in [0,2,4]:
        add("YOUTH_PLUS_EXP",f"younger>={ag}y & exp_gap>={eg}",
            lambda r,ag=ag,eg=eg: (lambda s:s if s and same_side_edges(r,s,[("exp",eg,"ge")]) else None)(younger(r,ag)))
    for oq in [.05,.10,.15,.20]:
        add("YOUTH_PLUS_OPPQUAL",f"younger>={ag}y & oppqual_gap>={oq:.2f}",
            lambda r,ag=ag,oq=oq: (lambda s:s if s and same_side_edges(r,s,[("oppqual",oq,"ge")]) else None)(younger(r,ag)))
    for el in [25,50,75]:
        add("YOUTH_PLUS_ELO",f"younger>={ag}y & elo_gap>={el}",
            lambda r,ag=ag,el=el: (lambda s:s if s and same_side_edges(r,s,[("elo",el,"ge")]) else None)(younger(r,ag)))

# Quality combinations.
for el in [25,50,75]:
    for oq in [.05,.10,.15]:
        add("ELO_PLUS_OPPQUAL",f"elo_gap>={el} & oppqual_gap>={oq:.2f}",
            lambda r,el=el,oq=oq: (lambda s:s if s and same_side_edges(r,s,[("oppqual",oq,"ge")]) else None)(edge_side(r,"elo",el)))
for eg in [2,4,6]:
    for oq in [.05,.10,.15]:
        add("EXP_PLUS_OPPQUAL",f"exp_gap>={eg} & oppqual_gap>={oq:.2f}",
            lambda r,eg=eg,oq=oq: (lambda s:s if s and same_side_edges(r,s,[("oppqual",oq,"ge")]) else None)(edge_side(r,"exp",eg)))
for wg in [.10,.15,.20]:
    for oq in [.05,.10,.15]:
        add("WIN_PLUS_OPPQUAL",f"win_gap>={wg:.2f} & oppqual_gap>={oq:.2f}",
            lambda r,wg=wg,oq=oq: (lambda s:s if s and same_side_edges(r,s,[("oppqual",oq,"ge")]) else None)(edge_side(r,"win",wg)))

# Price corridors attached to context.
for ag in [3,4,5,6]:
    for maxfav in [200,250,300,400]:
        add("YOUTH_MOD_FAV",f"younger>={ag}y & favorite -{maxfav}..-100",
            lambda r,ag=ag,maxfav=maxfav: (lambda s:s if s and price_ok(r,s,-maxfav,-100) else None)(younger(r,ag)))
    for dogmax in [175,200,250,300]:
        add("YOUTH_VALUE_DOG",f"younger>={ag}y & dog +100..+{dogmax}",
            lambda r,ag=ag,dogmax=dogmax: (lambda s:s if s and price_ok(r,s,100,dogmax) else None)(younger(r,ag)),
            "value")

for eg in [2,4,6]:
    for maxfav in [175,200,250,300]:
        add("EXP_MOD_FAV",f"exp_gap>={eg} & favorite -{maxfav}..-100",
            lambda r,eg=eg,maxfav=maxfav: (lambda s:s if s and price_ok(r,s,-maxfav,-100) else None)(edge_side(r,"exp",eg)))
for oq in [.05,.10,.15,.20]:
    for dogmax in [175,200,250,300]:
        add("OPPQUAL_VALUE_DOG",f"oppqual_gap>={oq:.2f} & dog +100..+{dogmax}",
            lambda r,oq=oq,dogmax=dogmax: (lambda s:s if s and price_ok(r,s,100,dogmax) else None)(edge_side(r,"oppqual",oq)),
            "value")

# Line movement / steam.
for mv in [5,10,15,20]:
    def steam_choice(r,mv=mv):
        ma,mb=movement(r,"a"),movement(r,"b")
        opts=[]
        if pd.notna(ma) and ma>=mv:opts.append(("a",ma))
        if pd.notna(mb) and mb>=mv:opts.append(("b",mb))
        if not opts:return None
        return max(opts,key=lambda x:x[1])[0]
    add("STEAM",f"implied_prob_steam>={mv}pp",steam_choice)
    for el in [25,50]:
        add("STEAM_PLUS_ELO",f"steam>={mv}pp & elo_edge>={el}",
            lambda r,mv=mv,el=el: (lambda s:s if s and same_side_edges(r,s,[("elo",el,"ge")]) else None)(steam_choice(r,mv)))

# Repeat-DWCS technical form.
for sd in [0,5,10,20]:
    add("REPEAT_SIG_DIFF",f"prior_DWCS_tech & sig_diff>={sd}",
        lambda r,sd=sd: (lambda s:s if s and v(r,s,"tech_n")>=1 and v(r,s,"sigdiff")>=sd else None)(
            edge_side(r,"sigdiff",max(1,sd if sd>0 else 1))
        ))
for td in [.50,.60,.70,.80]:
    def repeat_td(r,td=td):
        opts=[]
        for s in ("a","b"):
            if v(r,s,"tech_n")>=1 and pd.notna(v(r,s,"tddef")) and v(r,s,"tddef")>=td:
                opts.append(s)
        if len(opts)==1:return opts[0]
        if len(opts)==2:
            return "a" if v(r,"a","tddef")>v(r,"b","tddef") else "b"
        return None
    add("REPEAT_TD_DEF",f"prior_DWCS_tech & td_def>={td:.2f}",repeat_td)
for danger in [1,2,3]:
    def repeat_danger(r,danger=danger):
        opts=[]
        for s in ("a","b"):
            if v(r,s,"tech_n")>=1 and (v(r,s,"kd") if pd.notna(v(r,s,"kd")) else 0)+(v(r,s,"sub") if pd.notna(v(r,s,"sub")) else 0)>=danger:
                opts.append(s)
        if len(opts)==1:return opts[0]
        return None
    add("REPEAT_DANGER",f"prior_DWCS_tech & KD+SUB>={danger}",repeat_danger,"value")

def evaluate(df,rule):
    picks=[]
    for _,r in df.iterrows():
        s=rule["chooser"](r)
        if s not in ("a","b"):continue
        o=odds(r,s)
        if pd.isna(o):continue
        fighter=r[f"fighter_{s}"]
        winner_raw=r.get("winner","")
        if pd.isna(winner_raw) or not str(winner_raw).strip() or str(winner_raw).strip().lower() in {"nan","none","draw","nc","no contest"}:
            continue
        winner=str(winner_raw).strip()
        won=(str(fighter).strip()==winner)
        picks.append({
          "season":int(r.season),"event_date":r.event_date,"event_name":r.event_name,
          "pick":fighter,"opponent":r[f"fighter_{other(s)}"],"close_odds":o,
          "won":won,"profit100":profit100(o,won)
        })
    if not picks:return None,[]
    p=pd.DataFrame(picks)
    ss=[]
    for s,g in p.groupby("season"):
        ss.append({
          "season":int(s),"n":len(g),"wins":int(g.won.sum()),
          "win_rate":float(g.won.mean()),"roi":float(g.profit100.sum()/(100*len(g)))
        })
    return {
      "family":rule["family"],"rule":rule["rule"],"lane_hint":rule["lane_hint"],
      "n":len(p),"wins":int(p.won.sum()),"win_rate":float(p.won.mean()),
      "roi":float(p.profit100.sum()/(100*len(p))),
      "seasons":len(ss),"profitable_seasons":sum(x["roi"]>0 for x in ss),
      "worst_season_roi":min(x["roi"] for x in ss),
      "season_detail":"|".join(f"{x['season']}:{x['n']}:{x['win_rate']:.3f}:{x['roi']:.3f}" for x in ss)
    },picks

def candidate_lane(x):
    stable=x["seasons"]>=3 and x["profitable_seasons"]>=max(2,math.ceil(x["seasons"]/2))
    high=stable and x["n"]>=15 and x["win_rate"]>=.68 and x["roi"]>0
    value=stable and x["n"]>=20 and x["win_rate"]>=.45 and x["roi"]>=.12
    if high:return "high_probability"
    if value:return "value"
    return ""

def score_candidate(x,lane):
    stability=x["profitable_seasons"]/max(1,x["seasons"])
    if lane=="high_probability":
        return (x["win_rate"]-.5)*math.sqrt(x["n"])*(1+x["roi"])*stability
    return x["roi"]*math.sqrt(x["n"])*stability

def main():
    d=pd.read_csv(MASTER,low_memory=False)
    disc=d[d.research_split.eq("discovery")].copy()
    val=d[d.research_split.eq("locked_validation")].copy()

    allres=[]
    rule_lookup={}
    for rule in RULES:
        z,_=evaluate(disc,rule)
        if z is None:continue
        z["candidate_lane"]=candidate_lane(z)
        allres.append(z)
        rule_lookup[(z["family"],z["rule"])]=rule
    all_df=pd.DataFrame(allres)
    all_df.to_csv(OUT/"discovery_all_rules.csv",index=False)

    eligible=all_df[all_df.candidate_lane.ne("")].copy()
    frozen=[]
    if len(eligible):
        for (fam,lane),g in eligible.groupby(["family","candidate_lane"]):
            g=g.copy()
            g["selection_score"]=[score_candidate(x,lane) for x in g.to_dict("records")]
            best=g.sort_values(["selection_score","n"],ascending=[False,False]).iloc[0].to_dict()
            frozen.append(best)
    frozen_df=pd.DataFrame(frozen)
    if len(frozen_df):
        frozen_df=frozen_df.sort_values(["candidate_lane","selection_score"],ascending=[True,False])
    frozen_df.to_csv(OUT/"frozen_candidates_from_s1_s6.csv",index=False)

    validation=[]
    pick_rows=[]
    for _,x in frozen_df.iterrows():
        rule=rule_lookup[(x.family,x.rule)]
        vz,vp=evaluate(val,rule)
        fz,fp=evaluate(d,rule)
        rec={
          "family":x.family,"rule":x.rule,"candidate_lane":x.candidate_lane,
          "discovery_n":int(x.n),"discovery_win_rate":float(x.win_rate),"discovery_roi":float(x.roi),
          "discovery_profitable_seasons":int(x.profitable_seasons),"discovery_seasons":int(x.seasons),
          "validation_n":0,"validation_wins":0,"validation_win_rate":np.nan,"validation_roi":np.nan,
          "full_n":0,"full_win_rate":np.nan,"full_roi":np.nan,
          "thresholds_frozen_before_validation":True
        }
        if vz:
            rec.update({
              "validation_n":vz["n"],"validation_wins":vz["wins"],
              "validation_win_rate":vz["win_rate"],"validation_roi":vz["roi"],
              "validation_profitable_seasons":vz["profitable_seasons"],"validation_seasons":vz["seasons"],
              "validation_worst_season_roi":vz["worst_season_roi"]
            })
            for p in vp:
                p.update({"family":x.family,"rule":x.rule,"dataset_split":"locked_validation"})
                pick_rows.append(p)
        if fz:
            rec.update({"full_n":fz["n"],"full_win_rate":fz["win_rate"],"full_roi":fz["roi"]})
        # Validation classification is descriptive only; it cannot change frozen threshold.
        if rec["validation_n"]>=10 and rec["validation_roi"]>0 and rec["validation_win_rate"]>=.60:
            rec["validation_status"]="passed_initial_holdout"
        elif rec["validation_n"]<10:
            rec["validation_status"]="insufficient_holdout_sample"
        else:
            rec["validation_status"]="failed_initial_holdout"
        validation.append(rec)

    vdf=pd.DataFrame(validation)
    if len(vdf):
        vdf=vdf.sort_values(["validation_status","validation_roi","validation_win_rate","validation_n"],ascending=[True,False,False,False])
    vdf.to_csv(OUT/"locked_validation_results.csv",index=False)
    pd.DataFrame(pick_rows).to_csv(OUT/"locked_validation_picks.csv",index=False)

    report=[
      "DWCS FROZEN METHOD DISCOVERY — S1-6 DISCOVERY / S7-9 LOCKED VALIDATION",
      "="*112,
      f"Discovery rows: {len(disc)}",
      f"Locked validation rows: {len(val)}",
      f"Rules tested on discovery only: {len(all_df)}",
      f"Frozen candidates: {len(frozen_df)}",
      "",
      "FROZEN CANDIDATES + LOCKED VALIDATION",
      "-"*112
    ]
    for _,x in vdf.iterrows():
        report.append(
          f"{x.candidate_lane:<16} {x.family:<24} "
          f"DISC n={int(x.discovery_n):3d} win={x.discovery_win_rate*100:5.1f}% ROI={x.discovery_roi*100:+6.1f}% | "
          f"VAL n={int(x.validation_n):3d} win={(x.validation_win_rate*100 if pd.notna(x.validation_win_rate) else float('nan')):5.1f}% "
          f"ROI={(x.validation_roi*100 if pd.notna(x.validation_roi) else float('nan')):+6.1f}% "
          f"{x.validation_status} | {x.rule}"
        )
    (OUT/"report.txt").write_text("\n".join(report)+"\n")

    meta={
      "discovery_seasons":[1,2,3,4,5,6],
      "locked_validation_seasons":[7,8,9],
      "season10_used_for_tuning":False,
      "threshold_selection_used_validation":False,
      "rules_tested":len(all_df),
      "frozen_candidates":len(frozen_df),
      "validation_results":len(vdf)
    }
    (OUT/"method_discovery_meta.json").write_text(json.dumps(meta,indent=2)+"\n")
    print("\n".join(report))

if __name__=="__main__":
    main()
