#!/usr/bin/env python3
"""Historical UFC age × reach × height × wrestling/striking interaction audit.

Exploratory observational analysis. All match predictors are historical
pre-fight reconstructions; do not describe them as frozen prospective data.
Discovery: 2010-2023, untouched chronological test: 2024-2026-10-09.
Research-only: does not write into watcher, method catalog or betting ledger.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd

CUTOFF=pd.Timestamp("2026-10-10")
HOLDOUT=pd.Timestamp("2024-01-01")
NEEDED=["event_date","favorite","opponent","age_adv","reach_adv","f3_height_adv",
        "market_prob","fav_decimal","profit100","won","f_fights","o_fights",
        "f_td_a15","f_ctrl15","o_td_a15","o_td_def","o_sig_l_pm","o_sig_diff_pm",
        "f_td_def","f_sig_diff_pm","o_ctrl15"]
RNG=np.random.default_rng(20261010)

def load(path):
    d=pd.read_csv(path,low_memory=False)
    missing=[x for x in NEEDED if x not in d]
    if missing:raise ValueError("Missing columns: "+repr(missing))
    d["date"]=pd.to_datetime(d.event_date,errors="coerce")
    d=d[d.date.notna() & (d.date<CUTOFF)].copy()
    for c in NEEDED:
        if c not in ("event_date","favorite","opponent","won"):
            d[c]=pd.to_numeric(d[c],errors="coerce")
    d["win"]=d.won.astype(str).str.lower().map({"true":1,"false":0,"1":1,"0":0})
    d["fight_key"]=d["date"].dt.strftime("%Y-%m-%d")+"|"+d.favorite.astype(str).str.casefold()+"|"+d.opponent.astype(str).str.casefold()
    dup=int(d.duplicated("fight_key").sum())
    d=d.drop_duplicates("fight_key")
    # Not all base rows have documented real quote capture timestamps. These
    # are historical reconstructed prices, not a live performance test.
    d=d[d.win.isin([0,1]) & d.market_prob.between(.50001,.98) &
        d.fav_decimal.between(1.01,15) & d.profit100.notna()].copy()
    d["expected_profit100"] = np.where(d.win==1,100*(d.fav_decimal-1),-100)
    bad=(d.expected_profit100-d.profit100).abs()>1.01
    bad_count=int(bad.sum())
    d=d[~bad].copy()
    d["period"]=np.where(d.date<HOLDOUT,"discovery","holdout")
    d["era"]=np.select([d.date.dt.year<=2019,d.date.dt.year<=2023],["2010-19","2020-23"],default="2024-26")
    d["height_disadv"]= -d.f3_height_adv
    d["reach_disadv"]= -d.reach_adv
    return d,dict(duplicates_removed=dup,profit_inconsistent_excluded=bad_count)

def summary(d,year=False):
    n=len(d)
    if not n:return dict(n=0,wins=0,losses=0,win_pct=None,roi_pct=None,profit_units=None,median_price=None,periods={})
    p=d.profit100.to_numpy(dtype=float)/100.0
    wr=d.win.mean()
    se=1.96*np.sqrt(wr*(1-wr)/n) if n>1 else None
    q=dict(n=n,wins=int(d.win.sum()),losses=int(n-d.win.sum()),
           win_pct=round(100*wr,2),win_ci_approx=[round(100*max(0,wr-se),2),round(100*min(1,wr+se),2)] if se is not None else None,
           roi_pct=round(100*p.mean(),2),profit_units=round(float(p.sum()),3),
           median_decimal=round(float(d.fav_decimal.median()),3),
           avg_market_prob=round(float(d.market_prob.mean()*100),2))
    if n>=8:
        samples=RNG.choice(p,size=(1800,n),replace=True).mean(axis=1)*100
        q["roi_bootstrap_95pct"]=[round(float(x),2) for x in np.percentile(samples,[2.5,97.5])]
    if year:
        q["periods"]={}
        for name,g in d.groupby("era"):
            q["periods"][name]={k:v for k,v in summary(g,False).items() if k in ("n","wins","losses","win_pct","roi_pct","profit_units")}
    return q

def masks(d):
    complete=d[["age_adv","f3_height_adv","reach_adv","f_td_a15","o_td_a15","o_sig_l_pm",
                "f_ctrl15","f_fights","o_fights"]].notna().all(axis=1)
    valid=complete & (d.f_fights>=2)&(d.o_fights>=2)
    # These are definitions fixed before seeing the subgroup results.
    age=valid & (d.age_adv>=4)
    wrestle=age & (d.f_td_a15>=2.5)&(d.f_ctrl15>=.5)
    striker=wrestle & (d.o_td_a15<=3)&(d.o_sig_l_pm>=2.5)
    return {
      "01 priced favorites": pd.Series(True,index=d.index),
      "02 both fighters 2+ UFC bouts, context complete":valid,
      "03 younger favorite by 4+y":age,
      "04 younger favorite with wrestling activity":wrestle,
      "05 versus low-TD long-range striker proxy":striker,
      "06 + shorter and less reach (any)":striker & (d.height_disadv>0)&(d.reach_disadv>0),
      "07 + shorter 2in, reach deficit 4in":striker & (d.height_disadv>=2)&(d.reach_disadv>=4),
      "08 + shorter 3in, reach deficit 6in":striker & (d.height_disadv>=3)&(d.reach_disadv>=6),
      "09 Camilo-like (age 6+, reach 6+, TD tries 4+)":striker & (d.age_adv>=6)&(d.height_disadv>=2)&(d.reach_disadv>=6)&(d.f_td_a15>=4),
      "10 age 4+, reach deficit 4+, wrestling fav":wrestle & (d.height_disadv>=1)&(d.reach_disadv>=4),
      "11 younger but shorter with large reach gap":age & (d.height_disadv>=2)&(d.reach_disadv>=6),
    }

def subset_summaries(d,ms):
    out=[]
    for name,m in ms.items():
        z=d[m].copy()
        out.append({"cohort":name,**{k:v for k,v in summary(z,True).items() if k!="periods"},
                    "discovery":summary(z[z.date<HOLDOUT]),"holdout":summary(z[z.date>=HOLDOUT]),
                    "by_era":summary(z,True).get("periods",{})})
    return out

def tdd_effects(d,ms):
    rows=[]
    edges=[(-.01,.50,"<50%"),(.50,.60,"50-59%"),(.60,.70,"60-69%"),(.70,.80,"70-79%"),(.80,1.01,"80%+")]
    for cohort in ("04 younger favorite with wrestling activity",
                   "05 versus low-TD long-range striker proxy",
                   "06 + shorter and less reach (any)",
                   "07 + shorter 2in, reach deficit 4in"):
        z=d[ms[cohort]]
        for lo,hi,label in edges:
            part=z[(z.o_td_def>=lo)&(z.o_td_def<hi)]
            rows.append(dict(cohort=cohort,defense_band=label,**summary(part)))
    return rows

def loss_profiles(d,ms):
    rows=[]
    for cohort in ("05 versus low-TD long-range striker proxy",
                   "06 + shorter and less reach (any)",
                   "07 + shorter 2in, reach deficit 4in",
                   "09 Camilo-like (age 6+, reach 6+, TD tries 4+)"):
        z=d[ms[cohort]]
        for target in (0,1):
            part=z[z.win==target]
            record={"cohort":cohort,"result":"loss" if target==0 else "win","n":len(part)}
            for c in ["age_adv","height_disadv","reach_disadv","f_td_a15","f_ctrl15",
                      "o_td_def","o_td_a15","o_sig_l_pm","o_sig_diff_pm",
                      "f_td_def","f_sig_diff_pm","market_prob","fav_decimal","f_fights","o_fights"]:
                record[c+"_median"]=round(float(part[c].median()),3) if part[c].notna().any() else None
            rows.append(record)
    return rows

def candidates(d):
    # Fixed candidate family, train on 2010-2023 only. Never tune on 2024+.
    # If sample support insufficient, do not promote/recommend a threshold.
    base=(d.f_fights>=2)&(d.o_fights>=2)&(d.age_adv>=4)&(d.f_td_a15>=2.5)&(d.f_ctrl15>=.5)&(d.o_td_a15<=3)&(d.o_sig_l_pm>=2.5)&(d.height_disadv>=1)&(d.reach_disadv>=3)
    rows=[]
    for market in (.50,.60,.65):
      for tdd in (.55,.65,.75):
        for reach in (3,5):
          for direction in ("avoid_high_tdd","prefer_high_tdd"):
            cond=(d.o_td_def<tdd) if direction=="avoid_high_tdd" else (d.o_td_def>=tdd)
            x=d[base & (d.market_prob>=market) & (d.reach_disadv>=reach) & cond]
            discovery=x[x.date<HOLDOUT]; hold=x[x.date>=HOLDOUT]
            sd=summary(discovery);sh=summary(hold)
            train_a=summary(discovery[discovery.date.dt.year<=2019])
            train_b=summary(discovery[discovery.date.dt.year>=2020])
            eligible=all([sd["n"]>=20, train_a["n"]>=8,train_b["n"]>=8,
                          sh["n"]>=8,sd["roi_pct"]>0,train_a["roi_pct"]>0,train_b["roi_pct"]>0])
            rows.append(dict(market_floor=market,tdd_threshold=tdd,reach_deficit_min=reach,
                             action=direction,train_n=sd["n"],train_wins=sd["wins"],
                             train_roi=sd["roi_pct"],early_train_n=train_a["n"],
                             early_train_roi=train_a["roi_pct"],late_train_n=train_b["n"],
                             late_train_roi=train_b["roi_pct"],holdout_n=sh["n"],
                             holdout_win_pct=sh["win_pct"],holdout_roi=sh["roi_pct"],
                             size_and_discovery_screen=eligible))
    return pd.DataFrame(rows)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",type=Path,required=True)
    ap.add_argument("--outdir",type=Path,required=True)
    args=ap.parse_args()
    out=args.outdir;out.mkdir(parents=True,exist_ok=True)
    d,checks=load(args.input)
    ms=masks(d)
    cohort=subset_summaries(d,ms)
    defense=tdd_effects(d,ms)
    losses=loss_profiles(d,ms)
    grid=candidates(d)
    selected=grid[grid.size_and_discovery_screen].sort_values(["train_roi","train_n"],ascending=[False,False])
    verdict="NO PRODUCTION CANDIDATE" if selected.empty else "RESEARCH ONLY: CANDIDATES PASS SAMPLE SCREEN; HOLDOUT STILL REQUIRES JUDGMENT"
    names=["event_date","favorite","opponent","weightclass","win","profit100","market_prob","fav_decimal",
           "age_adv","height_disadv","reach_disadv","f_td_a15","f_ctrl15","o_td_a15","o_td_def",
           "o_sig_l_pm","o_sig_diff_pm","f_td_def","f_sig_diff_pm","f_fights","o_fights","period"]
    for k in ["06 + shorter and less reach (any)","07 + shorter 2in, reach deficit 4in","09 Camilo-like (age 6+, reach 6+, TD tries 4+)"]:
        z=d[ms[k]][[n for n in names if n in d]].copy().sort_values("event_date")
        z.insert(0,"cohort",k)
        fname=k[:2]+"_cases.csv"
        z.to_csv(out/fname,index=False)
    pd.DataFrame(cohort).to_json(out/"cohort_results.json",orient="records",indent=2)
    pd.DataFrame(defense).to_csv(out/"takedown_defense_bands.csv",index=False)
    pd.DataFrame(losses).to_csv(out/"win_loss_feature_comparison.csv",index=False)
    grid.to_csv(out/"filter_discovery_holdout.csv",index=False)
    report={
      "version":"UFC_AGE_REACH_WRESTLER_STRIKER_V1_20261010",
      "data":str(args.input),"historical_reconstruction":True,"prospective_capture_not_required":True,
      "strictly_exclude_event_date_on_or_after":"2026-10-10",
      "discovery_before":"2024-01-01","holdout_since":"2024-01-01",
      "quality_checks":checks,"n_valid_favorite_rows":len(d),
      "n_pre2024":int((d.date<HOLDOUT).sum()),"n_holdout":int((d.date>=HOLDOUT).sum()),
      "source_prefight_price_caveat":"Historical reconstructed odds and features; not prospective execution slippage/availability",
      "cohorts":cohort,"opponent_tdd_bands":defense,
      "winner_loser_feature_profiles":losses,
      "candidate_count_sample_screen":len(selected),
      "best_training_candidates":selected.head(8).to_dict("records"),
      "verdict":verdict}
    (out/"research_report.json").write_text(json.dumps(report,indent=2,default=str))
    lines=["UFC YOUNGER SHORTER WRESTLER vs OLDER LONG REACH STRIKER","="*80,
      "SOURCE "+str(args.input),
      "N "+str(len(d))+" | discovery "+str(report["n_pre2024"])+" | holdout "+str(report["n_holdout"]),
      "CAUTION reconstructed point-in-time historical features and historical odds; NOT frozen prospective prices",
      "Dates >=2026-10-10 excluded. Historical favorites only. 2024+ never used to pick thresholds.",""]
    for c in cohort:
        dis=c["discovery"];hol=c["holdout"]
        lines.append(f'{c["cohort"]}: n={c["n"]} {c["wins"]}-{c["losses"]} win={c["win_pct"]}% ROI={c["roi_pct"]}% | train={dis["n"]}/{dis["roi_pct"]}% holdout={hol["n"]}/{hol["roi_pct"]}%')
    lines.append("\nOPPONENT TD DEFENSE BANDS")
    for x in defense:
        lines.append(f'{x["cohort"]} | {x["defense_band"]} | n={x["n"]} win={x["win_pct"]}% ROI={x["roi_pct"]}%')
    lines.append("\nLOSS / WIN PROFILES")
    for x in losses:
        lines.append(json.dumps(x,sort_keys=True))
    lines.append("\nCANDIDATE SCREEN "+str(len(selected))+" / "+str(len(grid))+" | "+verdict)
    if not selected.empty:lines.append(selected.head(8).to_string(index=False))
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print("\n".join(lines)[:23000])
    print("OUTPUT_FILES",sorted(x.name for x in out.iterdir()))
if __name__=="__main__":main()
