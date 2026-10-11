#!/usr/bin/env python3
"""UFC opponent-adjusted wrestling/striking/submission interaction research.

No live U-method mutation. Snapshots of UFCStats per fighter are taken at the
START of each UTC event date (all same-date bouts use the same prior state).
Input favorite table itself is a historical research reconstruction.
This is hypothesis-generating, not a genuine untouched prospective validation.
"""
from __future__ import annotations
import argparse,csv,json,re
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd

LIMIT=pd.Timestamp("2026-10-10")
TRAIN_END=pd.Timestamp("2024-01-01")
LATE_END=pd.Timestamp("2025-01-01")
SALT="UFC_OPPONENT_ADJUSTED_STYLES_V1_20261010"
def nm(x):return re.sub(r"[^a-z0-9]+"," ",str(x or "").lower()).strip()
def num(x):
    try:
        a=float(x)
        return a if np.isfinite(a) else np.nan
    except (TypeError,ValueError):return np.nan
def parse_of(x):
    m=re.search(r"(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)",str(x or ""),re.I)
    return (float(m.group(1)),float(m.group(2))) if m else (0.,0.)
def stat(row,side,field):
    return tuple(sum(parse_of(row.get(f"{side}_rd{n}_{field}"))[j] for n in range(1,6)) for j in (0,1))
def minutes(row):
    rnd=num(row.get("round"));m=re.match(r"^\s*(\d+):(\d+)\s*$",str(row.get("time") or ""))
    if not np.isfinite(rnd) or not m:return np.nan
    return max(.1,(max(1,int(rnd))-1)*5+int(m.group(1))+int(m.group(2))/60)
def newstate():
    return dict(fights=0,subw=0,subl=0,kow=0,kol=0,
                td_faced=0.,td_allowed=0.,td_l=0.,td_a=0.,
                adj_faced=0.,adj_expected=0.,adj_allowed=0.,adj_bouts=0,
                sig_l=0.,sig_abs=0.,kd=0.,mins=0.)
def feature(s):
    ta=s["td_a"];tf=s["td_faced"]
    return dict(
      fights=int(s["fights"]),subwins=int(s["subw"]),subloss=int(s["subl"]),
      kowins=int(s["kow"]),koloss=int(s["kol"]),
      td_faced=tf,td_allowed=s["td_allowed"],
      td_def_raw=(1-s["td_allowed"]/tf) if tf>=1 else np.nan,
      td_def_shrunk=(tf-s["td_allowed"]+5)/(tf+10),
      opp_adj_tdd=(s["adj_expected"]-s["adj_allowed"])/(s["adj_faced"]+12) if s["adj_faced"]>=5 else np.nan,
      adj_faced=s["adj_faced"],adj_bouts=int(s["adj_bouts"]),
      td_acc=(s["td_l"]+4)/(ta+10) if ta>=1 else np.nan,
      td_a15=s["td_a"]*15/s["mins"] if s["mins"]>0 else np.nan,
      kd15=s["kd"]*15/s["mins"] if s["mins"]>0 else np.nan,
      sig_lpm=s["sig_l"]/s["mins"] if s["mins"]>0 else np.nan,
      sig_abs_pm=s["sig_abs"]/s["mins"] if s["mins"]>0 else np.nan)
def raw_features(raw):
    raw=raw.copy()
    raw["date"]=pd.to_datetime(raw.event_date,errors="coerce").dt.normalize()
    raw=raw[raw.date.notna() & (raw.date<LIMIT)]
    raw=raw.sort_values("date")
    state=defaultdict(newstate); snaps={};duplicates=0
    for day,grp in raw.groupby("date",sort=True):
        dayrows=[]
        for r in grp.to_dict("records"):
            a,b=nm(r.get("player1")),nm(r.get("player2"))
            if not a or not b or a==b:continue
            key=(day.strftime("%Y-%m-%d"),)+tuple(sorted((a,b)))
            if key in snaps:duplicates+=1;continue
            fs=feature(state[a]);os=feature(state[b])
            snaps[key]={a:fs,b:os}
            dayrows.append((r,a,b,dict(state[a]),dict(state[b])))
        # snapshot all first; never include another fight from the same date.
        for r,a,b,sa,sb in dayrows:
            win=str(r.get("result") or "").upper().strip()
            if not (win.startswith("W") or win.startswith("L")):continue
            winner=a if win.startswith("W") else b
            method=str(r.get("method") or "").lower()
            sub="submission" in method
            ko=("ko" in method or "tko" in method)
            mins=minutes(r)
            pairs=[(a,b,"p1","p2",sa,sb),(b,a,"p2","p1",sb,sa)]
            for fighter,opp,side,oside,old,opp_old in pairs:
                tdland,tdatt=stat(r,side,"Td")
                tdopp_land,tdopp_att=stat(r,oside,"Td")
                sigland,_=stat(r,side,"Sig_str")
                sigopp,_=stat(r,oside,"Sig_str")
                kd=sum(max(0,num(r.get(f"{side}_rd{x}_KD"))) if np.isfinite(num(r.get(f"{side}_rd{x}_KD"))) else 0 for x in range(1,6))
                q=state[fighter]
                q["fights"]+=1
                q["subw"]+=int(sub and fighter==winner)
                q["subl"]+=int(sub and fighter!=winner)
                q["kow"]+=int(ko and fighter==winner)
                q["kol"]+=int(ko and fighter!=winner)
                q["td_faced"]+=tdopp_att;q["td_allowed"]+=tdopp_land
                q["td_l"]+=tdland;q["td_a"]+=tdatt
                if np.isfinite(mins):
                    q["mins"]+=mins;q["sig_l"]+=sigland;q["sig_abs"]+=sigopp;q["kd"]+=kd
                if tdopp_att>0 and opp_old["fights"]>=2 and opp_old["td_a"]>=5:
                    prior_opp_td_acc=(opp_old["td_l"]+4)/(opp_old["td_a"]+10)
                    q["adj_faced"]+=tdopp_att
                    q["adj_expected"]+=tdopp_att*prior_opp_td_acc
                    q["adj_allowed"]+=tdopp_land
                    q["adj_bouts"]+=1
    return snaps,duplicates

def regional(path):
    if not path.exists():return {},{"present":False,"rows":0}
    out=defaultdict(list)
    with path.open(newline="",encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            dt=pd.to_datetime(r.get("event_date"),errors="coerce")
            if pd.isna(dt) or dt>=LIMIT:continue
            name=nm(r.get("fighter"));opp=nm(r.get("opponent"))
            if not name or not opp:continue
            out[name].append((dt.normalize(),opp,
                              str(r.get("result") or "").upper(),
                              str(r.get("method") or "").lower(),
                              str(r.get("verification") or "")))
    for k in out:out[k].sort()
    return out,{"present":True,"rows":sum(map(len,out.values())),"fighters":len(out)}

def regional_snap(reg,name,date,ufc_seen):
    events=[r for r in reg.get(nm(name),[]) if r[0]<date and
            (r[0].strftime("%Y-%m-%d"),r[1]) not in ufc_seen]
    return dict(regional_rows=len(events),
         verified_rows=sum(x[4]=="independently_checked" for x in events),
         derived_rows=sum(x[4]=="archive_derived" for x in events),
         regional_subwins=sum(x[2]=="W" and "sub" in x[3] for x in events),
         regional_subloss=sum(x[2]=="L" and "sub" in x[3] for x in events))

def prepare(base_path,raw_path,regional_path):
    d=pd.read_csv(base_path,low_memory=False)
    d["date"]=pd.to_datetime(d.event_date,errors="coerce").dt.normalize()
    d=d[d.date.notna() & (d.date<LIMIT)].copy()
    d["win"]=d.won.astype(str).str.lower().map({"true":1,"false":0,"1":1,"0":0})
    numeric=["fav_decimal","profit100","market_prob","age_adv","reach_adv","f3_height_adv",
             "f_td_a15","f_td_acc","f_td_l15","f_ctrl15","f_td_def","f_sig_diff_pm",
             "o_td_a15","o_td_def","o_sig_l_pm","o_sig_diff_pm","o_kd15",
             "f_fights","o_fights","o_ctrl15","o_ctrl_allowed15"]
    for c in numeric:d[c]=pd.to_numeric(d[c],errors="coerce")
    d=d[d.win.isin([0,1])&d.fav_decimal.between(1.01,15)&d.market_prob.between(.50001,.98)]
    d["expected_profit"]=np.where(d.win==1,(d.fav_decimal-1)*100,-100)
    d=d[(d.expected_profit-d.profit100).abs()<1.01].copy()
    d["k"]=d.date.dt.strftime("%Y-%m-%d")+"|"+d.favorite.map(nm)+"|"+d.opponent.map(nm)
    duplicate=int(d.duplicated("k").sum())
    d=d.drop_duplicates("k")
    raw=pd.read_csv(raw_path,low_memory=False)
    raw["date"]=pd.to_datetime(raw.event_date,errors="coerce").dt.normalize()
    # Collect UFC fight identities to avoid double count in regional source.
    seen=defaultdict(set)
    for _,r in raw[raw.date<LIMIT].iterrows():
        a,b=nm(r.player1),nm(r.player2)
        if a and b and pd.notna(r.date):
            dt=r.date.strftime("%Y-%m-%d")
            seen[a].add((dt,b));seen[b].add((dt,a))
    snaps,rawdups=raw_features(raw)
    reg,reg_meta=regional(regional_path)
    records=[];unmatched=0
    for r in d.to_dict("records"):
        a,b=nm(r["favorite"]),nm(r["opponent"])
        key=(r["date"].strftime("%Y-%m-%d"),)+tuple(sorted((a,b)))
        z=snaps.get(key)
        if z is None or a not in z or b not in z:
            unmatched+=1;continue
        v={**r}
        for prefix,name in (("f",a),("o",b)):
            v.update({prefix+"_hist_"+k:val for k,val in z[name].items()})
            v.update({prefix+"_"+k:val for k,val in regional_snap(reg,name,r["date"],seen[name]).items()})
        records.append(v)
    e=pd.DataFrame(records)
    e["height_disadv"]=-e.f3_height_adv
    e["reach_disadv"]=-e.reach_adv
    e["period"]=np.select([e.date<TRAIN_END,e.date<LATE_END],["train","reused_2024"],default="later_2025_26")
    meta=dict(raw_favorite_rows=len(d),matched=len(e),unmatched=unmatched,
              duplicate_fav_rows=duplicate,duplicate_raw_bouts=rawdups,
              regional=reg_meta)
    return e,meta

def met(z):
    n=len(z)
    if not n:return dict(n=0,wins=0,losses=0,roi_pct=None,win_pct=None)
    return dict(n=n,wins=int(z.win.sum()),losses=int(n-z.win.sum()),
                win_pct=round(100*float(z.win.mean()),2),
                roi_pct=round(float(z.profit100.mean()),2),
                avg_market_pct=round(float(100*z.market_prob.mean()),2),
                profit_units=round(float(z.profit100.sum()/100),2))

def select_cohort(d):
    return (d.age_adv>=4)&(d.f_hist_fights>=2)&(d.o_hist_fights>=2)&\
      (d.f_td_a15>=2.5)&(d.f_ctrl15>=.5)&(d.o_td_a15<=3)&(d.o_sig_l_pm>=2.5)

def features(d):
    # Explicit evidence grades prevent tiny denominators from masquerading as robust.
    d=d.copy()
    d["o_tdd_attempt_quality"]=np.where(d.o_hist_td_faced>=12,"12+",
                                        np.where(d.o_hist_td_faced>=5,"5-11","<5"))
    d["o_adjusted_valid"]=(d.o_hist_adj_faced>=12)&(d.o_hist_adj_bouts>=2)
    d["o_reliable_tdd"]=d.o_hist_td_faced>=12
    d["o_high_def_shrunk"]=(d.o_reliable_tdd)&(d.o_hist_td_def_shrunk>=.68)
    d["o_above_expected_defense"]=(d.o_adjusted_valid)&(d.o_hist_opp_adj_tdd>=.08)
    d["o_striking_danger"]=(d.o_sig_l_pm>=5.0)&(d.o_sig_diff_pm>=.5)
    d["o_knockout_danger"]=(d.o_hist_kow>=2)|(d.o_kd15>=.5)
    d["f_entry_vulnerability"]=(d.f_hist_subl>=1)|(d.f_regional_subloss>=1)
    d["o_submission_threat"]=(d.o_hist_subwins>=1)|(d.o_regional_subwins>=2)
    d["submission_collision"]=d.f_entry_vulnerability&d.o_submission_threat
    d["f_low_td_conversion"]=(d.f_td_a15>=3)&(d.f_td_acc<=.30)&d.f_td_acc.notna()
    d["entry_vs_defender"]=d.f_low_td_conversion&d.o_high_def_shrunk
    d["high_risk_any"]=d.o_striking_danger|d.submission_collision|d.entry_vs_defender
    d["high_risk_two"]= (d.o_striking_danger.astype(int)+d.submission_collision.astype(int)+d.entry_vs_defender.astype(int))>=2
    d["reach_big"]=d.reach_disadv>=4
    d["shorter_reach"]= (d.height_disadv>0)&(d.reach_disadv>0)
    return d

def compare(d,cohort,tag):
    out=[]
    z=d[cohort]
    for name,mask in [
      ("baseline",pd.Series(True,index=z.index)),
      ("no_strike_danger",~z.o_striking_danger),
      ("no_sub_collision",~z.submission_collision),
      ("no_td_conversion_defense_collision",~z.entry_vs_defender),
      ("no_high_risk_any",~z.high_risk_any),
      ("0_or_1_risk_only",~z.high_risk_two),
      ("opp_tdd_12plus",z.o_reliable_tdd),
      ("opp_adj_tdd_available",z.o_adjusted_valid),
      ("opp_high_shrunk_tdd",z.o_high_def_shrunk),
      ("opp_better_adjusted_tdd",z.o_above_expected_defense),
      ("opp_high_strike_danger",z.o_striking_danger),
      ("opponent_submission_collision",z.submission_collision),
      ("fav_tdd_below_60",z.f_td_def<.60),
      ("large_reach_deficit",z.reach_big)]:
        group=z[mask]
        out.append(dict(cohort=tag,condition=name,all=met(group),
              train=met(group[group.period=="train"]),
              reused_2024=met(group[group.period=="reused_2024"]),
              later_2025_26=met(group[group.period=="later_2025_26"])))
    return out

def loss_rows(d,mask):
    g=d[mask]
    cols=["event_date","favorite","opponent","win","profit100","market_prob","age_adv",
          "height_disadv","reach_disadv","f_td_a15","f_td_acc","f_ctrl15",
          "o_td_def","o_hist_td_faced","o_hist_td_def_shrunk",
          "o_hist_adj_faced","o_hist_opp_adj_tdd","o_sig_l_pm","o_sig_diff_pm",
          "o_kd15","f_hist_subl","f_regional_subloss",
          "o_hist_subwins","o_regional_subwins",
          "o_striking_danger","submission_collision","entry_vs_defender",
          "o_reliable_tdd","o_adjusted_valid","period"]
    return g[[c for c in cols if c in g]].sort_values("event_date")

def main():
    p=argparse.ArgumentParser()
    for s in ("base","raw","regional","outdir"):p.add_argument("--"+s,type=Path,required=True)
    a=p.parse_args();a.outdir.mkdir(parents=True,exist_ok=True)
    d,metadata=prepare(a.base,a.raw,a.regional)
    d=features(d)
    masks={
      "180_prior_base":select_cohort(d),
      "shorter_younger_wrestlers":select_cohort(d)&d.shorter_reach,
      "shorter_and_reach4plus":select_cohort(d)&(d.height_disadv>=2)&(d.reach_disadv>=4),
      "general_young_wrestler":(d.age_adv>=4)&(d.f_hist_fights>=2)&(d.o_hist_fights>=2)&(d.f_td_a15>=2.5)&(d.f_ctrl15>=.5),
    }
    comparisons=[]
    for name,mask in masks.items():
        comparisons.extend(compare(d,mask,name))
        cases=loss_rows(d,mask)
        cases.to_csv(a.outdir/(name+"_cases.csv"),index=False)
    first=next(x for x in comparisons if x["cohort"]=="180_prior_base" and x["condition"]=="baseline")
    # Diagnostic only: prior 2024+ holdout has ALREADY been inspected in earlier
    # research. No hyperparameter cutoffs are optimized against any period here.
    report=dict(version=SALT,policy="RESEARCH ONLY",input_metadata=metadata,
       no_publication_or_method_changes=True,
       historical_prefight_reconstruction=True,not_genuinely_unseen_holdout=True,
       dates_used_before="2026-10-10",groups={k:met(d[m]) for k,m in masks.items()},
       observations=comparisons,
       qualified_new_methods=0,
       warning="Hypotheses motivated by previously seen losses; all validation retrospective and contaminated by prior inspection; do not promote without new prospective evidence.")
    (a.outdir/"results.json").write_text(json.dumps(report,indent=2,default=str))
    pd.DataFrame([{k:v for k,v in x.items() if k not in ("all","train","reused_2024","later_2025_26")}|
                  {f"{period}_{metric}":val for period in ("all","train","reused_2024","later_2025_26")
                   for metric,val in x[period].items()} for x in comparisons]).to_csv(a.outdir/"comparison.csv",index=False)
    with (a.outdir/"REPORT.txt").open("w") as f:
        f.write(SALT+"\n"+json.dumps(metadata,default=str)+"\n")
        for name in masks:
            f.write("\n"+name.upper()+" "+json.dumps(met(d[masks[name]]))+"\n")
            for x in comparisons:
                if x["cohort"]==name:
                    f.write(f"{x['condition']:40} all={x['all']} train={x['train']} reuse24={x['reused_2024']} later25={x['later_2025_26']}\n")
            z=loss_rows(d,masks[name]);z=z[z.win==0]
            f.write("LOSSES "+str(len(z))+"\n")
            for r in z.to_dict("records"):
                f.write("LOSS_CASE "+json.dumps(r,default=str)+"\n")
        f.write("\nNO LIVE PROMOTION; no future unseen validation.\n")
    print("SOURCE_COVERAGE",json.dumps(metadata))
    print("GROUPS",json.dumps(report["groups"]))
    for x in comparisons:
        if x["cohort"] in ("180_prior_base","shorter_younger_wrestlers"):
            print("COMPARISON",json.dumps(x,default=str))
    for name in ("180_prior_base","shorter_younger_wrestlers"):
        z=loss_rows(d,masks[name]);z=z[z.win==0]
        print("LOSSES",name,len(z))
        for r in z.to_dict("records")[:35]:
            print("CASE",json.dumps(r,default=str))
    print("RESULT_PATH",str(a.outdir))
if __name__=="__main__":main()
