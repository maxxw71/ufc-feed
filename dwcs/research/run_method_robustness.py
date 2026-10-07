#!/usr/bin/env python3
from __future__ import annotations
import json,math
from pathlib import Path
import numpy as np,pandas as pd

ROOT=Path(".")
MASTER=ROOT/"dwcs/frozen/dwcs_historical_master_frozen.csv"
OUT=ROOT/"dwcs/research/method_robustness"
OUT.mkdir(parents=True,exist_ok=True)

METHODS={
 "AGE6":{"family":"AGE_EDGE","rule":"younger_by>=6y"},
 "AGE5":{"family":"AGE_EDGE","rule":"younger_by>=5y"},
 "YOUNG_DOG":{"family":"YOUTH_VALUE_DOG","rule":"younger>=3y & dog +100..+250"},
 "YOUTH_EXP":{"family":"YOUTH_PLUS_EXP","rule":"younger>=5y & exp_gap>=0"},
}

def n(v):
    x=pd.to_numeric(pd.Series([v]),errors="coerce").iloc[0]
    return float(x) if pd.notna(x) else np.nan
def imp(o):
    o=n(o)
    if pd.isna(o):return np.nan
    return (-o)/((-o)+100) if o<0 else 100/(o+100)
def p100(o,won):
    o=n(o)
    if pd.isna(o):return np.nan
    return -100. if not won else (10000/abs(o) if o<0 else o)
def younger(r,g):
    aa,bb=n(r.a_age),n(r.b_age)
    if pd.isna(aa) or pd.isna(bb):return None
    if bb-aa>=g:return "a"
    if aa-bb>=g:return "b"
    return None
def pick_rule(r,key):
    if key=="AGE6": return younger(r,6)
    if key=="AGE5": return younger(r,5)
    if key=="YOUNG_DOG":
        s=younger(r,3)
        if not s:return None
        o=n(r[f"{s}_close_odds"])
        return s if pd.notna(o) and 100<=o<=250 else None
    if key=="YOUTH_EXP":
        s=younger(r,5)
        if not s:return None
        oth="b" if s=="a" else "a"
        a=n(r.get(f"{s}_prior_fights"));b=n(r.get(f"{oth}_prior_fights"))
        return s if pd.notna(a) and pd.notna(b) and a-b>=0 else None
    return None

def picks(df,key,split):
    rows=[]
    for _,r in df.iterrows():
        s=pick_rule(r,key)
        if not s:continue
        winner=r.get("winner","")
        if pd.isna(winner) or not str(winner).strip():continue
        fighter=r[f"fighter_{s}"];opp=r[f"fighter_{'b' if s=='a' else 'a'}"]
        o=n(r[f"{s}_close_odds"])
        if pd.isna(o):continue
        won=str(fighter).strip()==str(winner).strip()
        rows.append({
          "method":key,"family":METHODS[key]["family"],"rule":METHODS[key]["rule"],
          "dataset_split":split,"season":int(r.season),"event_date":r.event_date,
          "event_name":r.event_name,"pick":fighter,"opponent":opp,"close_odds":o,
          "won":won,"profit100":p100(o,won)
        })
    return pd.DataFrame(rows)

def max_losing_streak(p):
    best=cur=0
    for w in p.sort_values(["event_date","event_name"]).won:
        if w:cur=0
        else:cur+=1;best=max(best,cur)
    return best

def max_drawdown_units(p):
    q=p.sort_values(["event_date","event_name"])
    equity=np.r_[0,q.profit100.to_numpy()/100]
    curve=np.cumsum(equity)
    peak=np.maximum.accumulate(curve)
    dd=peak-curve
    return float(dd.max())

def wilson(w,nobs,z=1.96):
    if not nobs:return (np.nan,np.nan)
    ph=w/nobs;den=1+z*z/nobs
    cen=(ph+z*z/(2*nobs))/den
    half=z*math.sqrt(ph*(1-ph)/nobs+z*z/(4*nobs*nobs))/den
    return cen-half,cen+half

def boot_roi(p,iters=20000,seed=42):
    if len(p)<2:return (np.nan,np.nan)
    x=p.profit100.to_numpy()/100
    rng=np.random.default_rng(seed)
    vals=np.empty(iters)
    for i in range(iters):
        vals[i]=rng.choice(x,size=len(x),replace=True).mean()
    return tuple(np.quantile(vals,[.025,.975]))

def summary(p):
    if p.empty:return {}
    wr=float(p.won.mean());roi=float(p.profit100.sum()/(100*len(p)))
    wl,wh=wilson(int(p.won.sum()),len(p));rl,rh=boot_roi(p)
    by=[]
    for s,g in p.groupby("season"):
        by.append((int(s),len(g),int(g.won.sum()),float(g.won.mean()),float(g.profit100.sum()/(100*len(g)))))
    return {
      "n":len(p),"wins":int(p.won.sum()),"win_rate":wr,"roi":roi,
      "wilson_win_low":wl,"wilson_win_high":wh,"bootstrap_roi_low":rl,"bootstrap_roi_high":rh,
      "max_losing_streak":max_losing_streak(p),"max_drawdown_units":max_drawdown_units(p),
      "profitable_seasons":sum(x[4]>0 for x in by),"seasons":len(by),
      "season_detail":"|".join(f"{s}:{nn}:{w}:{wr:.3f}:{rr:.3f}" for s,nn,w,wr,rr in by)
    }

def neighborhood_rule(r,kind,a,b=None):
    if kind=="age":return younger(r,a)
    if kind=="dog":
        s=younger(r,a)
        if not s:return None
        o=n(r[f"{s}_close_odds"])
        return s if pd.notna(o) and 100<=o<=b else None
    if kind=="exp":
        s=younger(r,a)
        if not s:return None
        oth="b" if s=="a" else "a"
        x=n(r.get(f"{s}_prior_fights"));y=n(r.get(f"{oth}_prior_fights"))
        return s if pd.notna(x) and pd.notna(y) and x-y>=b else None

def eval_custom(df,kind,a,b=None):
    rows=[]
    for _,r in df.iterrows():
        s=neighborhood_rule(r,kind,a,b)
        if not s:continue
        winner=r.get("winner","")
        if pd.isna(winner) or not str(winner).strip():continue
        o=n(r[f"{s}_close_odds"])
        if pd.isna(o):continue
        fighter=r[f"fighter_{s}"];won=str(fighter).strip()==str(winner).strip()
        rows.append({"season":int(r.season),"event_date":r.event_date,"event_name":r.event_name,
                     "pick":fighter,"won":won,"profit100":p100(o,won)})
    return pd.DataFrame(rows)

def main():
    d=pd.read_csv(MASTER,low_memory=False)
    disc=d[d.research_split.eq("discovery")].copy()
    val=d[d.research_split.eq("locked_validation")].copy()

    allp=[]; summaries=[]
    for key in METHODS:
        for split,df in [("discovery",disc),("locked_validation",val),("full",d)]:
            p=picks(df,key,split)
            if len(p):allp.append(p)
            z=summary(p)
            summaries.append({"method":key,"family":METHODS[key]["family"],"rule":METHODS[key]["rule"],"split":split,**z})
    pd.concat(allp,ignore_index=True).to_csv(OUT/"robustness_picks.csv",index=False)
    pd.DataFrame(summaries).to_csv(OUT/"robustness_summary.csv",index=False)

    # Validation overlap / mechanism redundancy.
    vp={k:picks(val,k,"locked_validation") for k in METHODS}
    overlap=[]
    keys=list(METHODS)
    for i,a in enumerate(keys):
        aset=set((x.event_date,x.pick) for _,x in vp[a].iterrows())
        for b in keys[i:]:
            bset=set((x.event_date,x.pick) for _,x in vp[b].iterrows())
            inter=len(aset&bset);uni=len(aset|bset)
            overlap.append({"method_a":a,"method_b":b,"a_n":len(aset),"b_n":len(bset),
                            "overlap_n":inter,"jaccard":inter/uni if uni else np.nan,
                            "share_of_smaller":inter/min(len(aset),len(bset)) if min(len(aset),len(bset)) else np.nan})
    pd.DataFrame(overlap).to_csv(OUT/"validation_overlap_matrix.csv",index=False)

    # Neighborhoods are diagnostics only. Exact candidate thresholds remain frozen.
    neighborhoods=[]
    specs=[]
    for g in [4,5,6,7,8]:specs.append(("age",g,None,f"age>={g}"))
    for g in [2,3,4,5]:
        for cap in [200,250,300]:specs.append(("dog",g,cap,f"younger>={g} & dog<=+{cap}"))
    for g in [4,5,6]:
        for eg in [0,2,4]:specs.append(("exp",g,eg,f"younger>={g} & exp_gap>={eg}"))
    for kind,a,b,label in specs:
        for split,df in [("discovery",disc),("locked_validation",val)]:
            p=eval_custom(df,kind,a,b)
            z=summary(p)
            neighborhoods.append({"family":kind,"diagnostic_rule":label,"split":split,**z})
    pd.DataFrame(neighborhoods).to_csv(OUT/"threshold_neighborhood_diagnostics.csv",index=False)

    # Unique-bet portfolio for passed exact candidates.
    passed=["AGE6","AGE5","YOUNG_DOG","YOUTH_EXP"]
    stack={}
    for key in passed:
        for _,x in vp[key].iterrows():
            k=(x.event_date,x.pick,x.opponent)
            if k not in stack:stack[k]={"event_date":x.event_date,"pick":x.pick,"opponent":x.opponent,"close_odds":x.close_odds,"won":x.won,"methods":[]}
            stack[k]["methods"].append(key)
    port=[]
    for z in stack.values():
        units=len(z["methods"])
        pr=p100(z["close_odds"],z["won"])/100
        port.append({**z,"method_count":units,"methods":"|".join(sorted(z["methods"])),"profit_flat1_each":pr*units})
    pdf=pd.DataFrame(port).sort_values(["event_date","pick"]) if port else pd.DataFrame()
    pdf.to_csv(OUT/"validation_overlap_portfolio.csv",index=False)
    portfolio={
      "unique_bets":len(pdf),
      "method_signals":int(pdf.method_count.sum()) if len(pdf) else 0,
      "winning_unique_bets":int(pdf.won.sum()) if len(pdf) else 0,
      "unique_bet_win_rate":float(pdf.won.mean()) if len(pdf) else np.nan,
      "roi_per_method_signal":float(pdf.profit_flat1_each.sum()/pdf.method_count.sum()) if len(pdf) and pdf.method_count.sum() else np.nan,
      "net_units_at_1u_per_method_signal":float(pdf.profit_flat1_each.sum()) if len(pdf) else 0.0,
      "max_method_overlap":int(pdf.method_count.max()) if len(pdf) else 0
    }
    (OUT/"validation_portfolio_summary.json").write_text(json.dumps(portfolio,indent=2)+"\n")

    sm=pd.DataFrame(summaries)
    lines=["DWCS PASSED-CANDIDATE ROBUSTNESS","="*110]
    for key in passed:
        lines+=["",f"{key} — {METHODS[key]['rule']}","-"*110]
        for split in ["discovery","locked_validation","full"]:
            x=sm[(sm.method==key)&(sm.split==split)].iloc[0]
            lines.append(f"{split:<18} n={int(x.n):3d} win={x.win_rate*100:5.1f}% ROI={x.roi*100:+6.1f}% "
                         f"95%win=[{x.wilson_win_low*100:4.1f},{x.wilson_win_high*100:4.1f}] "
                         f"bootROI=[{x.bootstrap_roi_low*100:+5.1f},{x.bootstrap_roi_high*100:+5.1f}] "
                         f"maxLS={int(x.max_losing_streak)} maxDD={x.max_drawdown_units:.2f}u")
    lines+=["","VALIDATION OVERLAP PORTFOLIO","-"*110,json.dumps(portfolio)]
    (OUT/"report.txt").write_text("\n".join(lines)+"\n")
    print("\n".join(lines))

if __name__=="__main__":main()
