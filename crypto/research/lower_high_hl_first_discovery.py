#!/usr/bin/env python3
"""
Hyperliquid-first discovery of NEW lower-high / second-dump rebound methods.

This intentionally reverses the old workflow that originally discovered the
lower-high family on Binance. Thresholds are now selected on Hyperliquid only,
evaluated on an untouched Hyperliquid later-40% holdout, then frozen and copied
to Binance for independent replication.

Two broad Hyperliquid structures are searched:
- base28: RSI<=28, pump>=8%, second dump>=8%
- base30_10: RSI<=30, pump>=10%, second dump>=10%

The search uses only trigger-time structure/trend features common to both
venues. It also requires incremental signals versus current C2/C2D so we do not
rename an existing method.

Research only. No live promotion.
"""
from pathlib import Path
from math import sqrt
import itertools
import json
import numpy as np
import pandas as pd

ROOT=Path("crypto/research")
HL=ROOT/"results_lower_high_hyperliquid_replication/events.csv"
BIN=ROOT/"results_lower_high_rsi30_binance/events.csv"
OUT=ROOT/"lower_high_hl_first_discovery"
OUT.mkdir(parents=True,exist_ok=True)

TP=.05
STOP=.075
HL_COST=2*(.00045+.0010)
BIN_COST=2*(.00070+.0010)

BASES={
    "base28":{"hl_col":"base28","bin_variant":"rsi28_pump8_dump8_any"},
    "base30_10":{"hl_col":"base30_10","bin_variant":"rsi30_pump10_dump10_any"},
}

FEATURES=[
    ("trigger_rsi","low","RSI"),
    ("dist_sma50","low","TREND"),
    ("dist_ema9","low","TREND"),
    ("sma50_slope6","low","TREND"),
    ("lower_wick","band","CANDLE"),
    ("pump_pct","band","STRUCTURE"),
    ("lower_high_pct","low","STRUCTURE"),
    ("second_dump_pct","low","STRUCTURE"),
]

def bval(x):
    if isinstance(x,bool):return x
    return str(x).strip().lower() in {"true","1","yes"}

def rate(s):
    q=s.dropna()
    return float(q.astype(bool).mean()) if len(q) else np.nan

def wilson(w,n,z=1.96):
    if n<=0:return np.nan
    p=w/n;den=1+z*z/n
    ctr=(p+z*z/(2*n))/den
    half=z*sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return ctr-half

def roi(g,cost,prefix):
    if g.empty:return np.nan
    win=g[prefix+"risk"].map(bval)
    mae=pd.to_numeric(g[prefix+"mae"],errors="coerce")
    stopped=(~win)&(mae<=-STOP)
    # No historical 5d timeout-close field exists in this older structural
    # table, so timeout trades are marked flat gross (conservative).
    gross=pd.Series(np.where(win,TP,np.where(stopped,-STOP,0.0)),index=g.index)
    return float((gross-cost).mean())

def episode_stats(g):
    if g.empty:return 0,np.nan
    z=g.sort_values("trigger_time").copy();ids=[];eid=0;last=None
    for t in pd.to_datetime(z["trigger_time"],utc=True):
        if last is None or t-last>pd.Timedelta(hours=18):eid+=1
        ids.append(eid);last=t
    z["episode"]=ids
    q=z.groupby("episode")["hit5"].mean()
    return int(len(q)),float(q.mean())

def spec_name(s):
    f,op,a,b,fam=s
    if op=="band":return f"{a:.6g} <= {f} <= {b:.6g}"
    return f"{f} {op} {a:.6g}"

def mask(df,s):
    f,op,a,b,_=s
    x=pd.to_numeric(df[f],errors="coerce")
    if op=="<=":return x<=a
    if op==">=":return x>=a
    return x.between(a,b,inclusive="both")

def make_specs(tr):
    out=[]
    for f,d,fam in FEATURES:
        s=pd.to_numeric(tr[f],errors="coerce").replace([np.inf,-np.inf],np.nan).dropna()
        if len(s)<70:continue
        qs=s.quantile([.10,.15,.20,.25,.30,.35,.40,.50,.60,.65,.70,.75,.80,.85,.90])
        if d=="band":
            for lo,hi in [(.10,.40),(.15,.50),(.20,.60),(.25,.65),(.30,.70),(.35,.75),(.40,.80),(.50,.85),(.60,.90)]:
                out.append((f,"band",float(qs.loc[lo]),float(qs.loc[hi]),fam))
        else:
            for q in (.10,.15,.20,.25,.30,.35,.40,.50,.60,.65,.70,.75,.80,.85,.90):
                out.append((f,"<=",float(qs.loc[q]),None,fam))
    return out

def apply(df,combo):
    m=pd.Series(True,index=df.index)
    for s in combo:m &= mask(df,s)
    return m

def folds(tr,combo):
    vals=[]
    for ii in np.array_split(np.arange(len(tr)),4):
        g=tr.iloc[ii];q=g[apply(g,combo)]
        if len(q)>=7:vals.append(rate(q["hit5"]))
    return len(vals),(float(np.mean(vals)) if vals else np.nan),(float(np.min(vals)) if vals else np.nan)

def live_c2_mask(g):
    return (
        g["base30_10"].map(bval)
        &(pd.to_numeric(g["lower_high_pct"],errors="coerce")<=-0.19915)
        &(pd.to_numeric(g["pump_pct"],errors="coerce")>=0.21337)
    )

def discover_base(e,base_name,hl_col):
    g=e[e[hl_col].map(bval)].sort_values("entry_time").reset_index(drop=True)
    if len(g)<120:return pd.DataFrame()
    cut=max(1,int(len(g)*.60));tr=g.iloc[:cut].copy();ho=g.iloc[cut:].copy()
    ss=make_specs(tr)

    singles=[]
    for s in ss:
        q=tr[mask(tr,s)]
        if len(q)<28:continue
        rr=roi(q,HL_COST,"")
        score=.45*rate(q["hit5"])+.30*rate(q["risk"])+.25*max(0,rr/.035)
        singles.append((score,len(q),s))
    singles.sort(key=lambda z:(z[0],z[1]),reverse=True)

    # strongest 8 specifications from each mechanism family
    fam={}
    for item in singles:
        k=item[2][4];fam.setdefault(k,[])
        if len(fam[k])<8:fam[k].append(item[2])
    top=[s for a in fam.values() for s in a]

    combos=[(s,) for s in top]
    for a,b in itertools.combinations(top,2):
        if a[4]!=b[4]:combos.append((a,b))
    t3=[]
    for a in fam.values():t3.extend(a[:2])
    for c in itertools.combinations(t3,3):
        if len({s[4] for s in c})==3:combos.append(c)

    live=live_c2_mask(ho)
    rows=[];seen=set()
    for combo in combos:
        name=" AND ".join(spec_name(s) for s in combo)
        if name in seen:continue
        seen.add(name)
        gt=tr[apply(tr,combo)];gh=ho[apply(ho,combo)]
        if len(gt)<28 or len(gh)<18:continue
        hit=rate(gh["hit5"]);risk=rate(gh["risk"])
        trr=roi(gt,HL_COST,"");hor=roi(gh,HL_COST,"")
        lo=wilson(int(gh["hit5"].map(bval).sum()),len(gh))
        epn,epr=episode_stats(gh)
        nf,fmean,fmin=folds(tr,combo)
        incremental=float((~live.loc[gh.index]).mean()) if len(gh) else 0
        eligible=(
            hit>=.82 and risk>=.72 and hor>=.015 and trr>=.0125
            and len(gh)>=18 and epn>=10 and epr>=.76 and lo>=.60
            and nf>=3 and fmean>=.76 and incremental>=.25
        )
        rows.append({
            "base":base_name,"rule":name,"features":"+".join(s[0] for s in combo),
            "families":"+".join(sorted({s[4] for s in combo})),
            "conditions":json.dumps([{"feature":s[0],"op":s[1],"a":s[2],"b":s[3]} for s in combo]),
            "train_n":len(gt),"train_hit5":rate(gt["hit5"]),"train_risk":rate(gt["risk"]),"train_net_roi":trr,
            "hold_n":len(gh),"hold_hit5":hit,"hold_hit10":rate(gh["hit10"]),"hold_risk":risk,"hold_net_roi":hor,
            "wilson_lower":lo,"episodes":epn,"episode_hit5":epr,
            "train_folds":nf,"fold_mean_hit5":fmean,"fold_min_hit5":fmin,
            "incremental_vs_C2":incremental,"eligible":eligible,
            "score":.25*hit+.20*risk+.15*epr+.10*lo+.20*min(max(hor/.035,0),1)+.10*incremental,
        })
    z=pd.DataFrame(rows)
    return z.sort_values(["eligible","score","hold_n"],ascending=[False,False,False]) if len(z) else z

def normalize_hl(e):
    x=e.copy()
    x["hit5"]=x["hit5"].map(bval)
    x["hit10"]=x["hit10"].map(bval)
    x["risk"]=x["t5_s0.075"].map(bval)
    x["mae"]=pd.to_numeric(x["mae5d"],errors="coerce")
    return x

def normalize_bin(e):
    x=e.copy()
    x["hit5"]=x["hit5"].map(bval)
    x["hit10"]=x["hit10"].map(bval)
    x["risk"]=x["t5_s0.075"].map(bval)
    x["mae"]=pd.to_numeric(x["mae5d"],errors="coerce")
    return x

def validate(cands,bn):
    rows=[];reg=[]
    for i,(_,r) in enumerate(cands.iterrows(),1):
        var=BASES[r["base"]]["bin_variant"]
        x=bn[bn["variant"]==var].sort_values("entry_time").reset_index(drop=True)
        cut=max(1,int(len(x)*.60));ho=x.iloc[cut:].copy()
        m=pd.Series(True,index=ho.index);unsupported=[]
        conds=json.loads(r["conditions"])
        for c in conds:
            if c["feature"] not in ho.columns:
                unsupported.append(c["feature"]);m &= False;continue
            m &= mask(ho,(c["feature"],c["op"],c["a"],c.get("b"),""))
        g=ho[m];n=len(g)
        hit=rate(g["hit5"]) if n else np.nan
        risk=rate(g["risk"]) if n else np.nan
        rr=roi(g,BIN_COST,"") if n else np.nan
        lo=wilson(int(g["hit5"].map(bval).sum()),n) if n else np.nan
        passed=(not unsupported and n>=18 and hit>=.78 and risk>=.68 and rr>=.010 and lo>=.58)
        cid=f"LHHL-{pd.Timestamp.utcnow().strftime('%Y%m%d')}-{i:02d}"
        status="CROSS_VALIDATED_SHADOW" if passed else "SHADOW_ONLY"
        rows.append({"candidate_id":cid,"status":status,"base":r["base"],"rule":r["rule"],
                     "binance_n":n,"binance_hit5":hit,"binance_hit10":rate(g["hit10"]) if n else np.nan,
                     "binance_risk":risk,"binance_net_roi":rr,"binance_wilson_lower":lo,
                     "unsupported_features":"+".join(unsupported)})
        reg.append({
            "candidate_id":cid,"status":status,"base":r["base"],"rule":r["rule"],
            "features":r["features"].split("+"),"families":r["families"].split("+"),
            "conditions":conds,
            "hyperliquid":{
                "train_n":int(r["train_n"]),"train_hit5":float(r["train_hit5"]),"train_net_roi":float(r["train_net_roi"]),
                "hold_n":int(r["hold_n"]),"hold_hit5":float(r["hold_hit5"]),"hold_hit10":float(r["hold_hit10"]),
                "hold_risk":float(r["hold_risk"]),"hold_net_roi":float(r["hold_net_roi"]),
                "episodes":int(r["episodes"]),"episode_hit5":float(r["episode_hit5"]),
                "incremental_vs_C2":float(r["incremental_vs_C2"]),
            },
            "binance":{"variant":var,"n":int(n),"hit5":None if pd.isna(hit) else float(hit),
                       "hit10":None if n==0 else float(rate(g["hit10"])),
                       "risk":None if pd.isna(risk) else float(risk),
                       "net_roi":None if pd.isna(rr) else float(rr),
                       "wilson_lower":None if pd.isna(lo) else float(lo),"passed":bool(passed)},
            "promotion_requirements":["actual-funding Hyperliquid replay","forward shadow tracking","manual review"],
        })
    return pd.DataFrame(rows),reg

def main():
    hl=normalize_hl(pd.read_csv(HL))
    bn=normalize_bin(pd.read_csv(BIN))
    for c in ["trigger_time","entry_time"]:
        hl[c]=pd.to_datetime(hl[c],utc=True,errors="coerce")
        bn[c]=pd.to_datetime(bn[c],utc=True,errors="coerce")

    frames=[]
    for base,cfg in BASES.items():
        z=discover_base(hl,base,cfg["hl_col"])
        if len(z):frames.append(z)
    allr=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    if len(allr):allr=allr.sort_values(["eligible","score","hold_n"],ascending=[False,False,False])
    allr.to_csv(OUT/"all_rules.csv",index=False)

    cand=allr[allr["eligible"]==True].copy() if len(allr) else pd.DataFrame()
    if len(cand):
        cand["family_key"]=cand["base"]+"|"+cand["families"]+"|"+cand["features"]
        cand=cand.sort_values(["score","hold_n"],ascending=[False,False]).drop_duplicates("family_key").head(20)
    cand.to_csv(OUT/"hyperliquid_shortlist.csv",index=False)

    cross,reg=validate(cand,bn) if len(cand) else (pd.DataFrame(),[])
    cross.to_csv(OUT/"binance_validation.csv",index=False)
    (OUT/"candidate_registry.json").write_text(json.dumps({
        "generated_at":pd.Timestamp.utcnow().isoformat(),
        "design":"Hyperliquid-first lower-high discovery; frozen Binance validation",
        "candidates":reg,
    },indent=2))
    passed=cross[cross["status"]=="CROSS_VALIDATED_SHADOW"] if len(cross) else pd.DataFrame()
    lines=[
        "HYPERLIQUID-FIRST LOWER-HIGH METHOD DISCOVERY","",
        f"Eligible Hyperliquid candidates: {len(cand)}",
        f"Binance cross-validated shadows: {len(passed)}","",
        "HYPERLIQUID SHORTLIST",
        cand.to_string(index=False) if len(cand) else "none","",
        "BINANCE FROZEN VALIDATION",
        cross.to_string(index=False) if len(cross) else "none","",
        "No live promotion. This search requires incremental coverage versus current C2 and still requires actual-funding replay."
    ]
    (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":
    main()
