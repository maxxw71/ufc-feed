#!/usr/bin/env python3
"""
Novel crypto method discovery: Hyperliquid-first, context-aware, and explicitly
orthogonal to the current live first-flush feature families.

This lane searches for genuinely different mechanisms rather than more
close-location/RSI-acceleration variants. It enriches each first-flush event
with contemporaneous BTC regime and market-flush-density features, then
combines those with non-live-core trigger features.

Selection:
- thresholds fit on earliest 60% Hyperliquid primary-perp events only
- latest 40% Hyperliquid holdout untouched
- +5 / -7.5 / 5d ROI contract with fees + 0.10% modeled slippage per side
- minimum independent market episodes and chronological fold stability
- minimum incremental coverage versus C1/C3/C4 (new signals, not renamed ones)
- frozen rule copied to independent Binance latest-40% holdout without retuning

Research only. Never auto-promotes a live method.
"""
from __future__ import annotations

from pathlib import Path
from math import sqrt
import itertools
import json
import time
import numpy as np
import pandas as pd
import requests

ROOT=Path("crypto/research")
HL=ROOT/"results_hyperliquid_all_pairs/events.csv"
BIN=ROOT/"results_rsi_dynamics/events.csv"
OUT=ROOT/"novel_context_discovery"
OUT.mkdir(parents=True,exist_ok=True)

HL_INFO="https://api.hyperliquid.xyz/info"
BIN_KLINES="https://data-api.binance.vision/api/v3/klines"

TP=.05
STOP=.075
HL_COST=2*(.00045+.0010)
BIN_COST=2*(.00070+.0010)

# Intentionally exclude the most important core features of current live
# first-flush methods C1/C3/C4: rsi_pct1, rsi_accel, rsi_vs_sma3,
# close_location, volume_ratio20. This lane must find a different explanation.
FEATURES=[
    ("btc_ret4","band","MARKET"),("btc_ret12","band","MARKET"),
    ("btc_ret24","band","MARKET"),("btc_ret72","band","MARKET"),
    ("btc_rsi14","band","MARKET"),("btc_dist_ema20","band","MARKET"),
    ("btc_atr14_pct","high","MARKET"),
    ("market_flush_count_8h","band","BREADTH"),
    ("market_flush_count_24h","band","BREADTH"),
    ("relative_ret4","band","RELATIVE"),
    ("relative_ret12","band","RELATIVE"),
    ("trigger_rsi","band","RSI"),
    ("rsi_drop_pct","band","RSI"),
    ("rsi_pct3","band","RSI"),
    ("rsi_price_shock_ratio","band","RSI"),
    ("daily_4h_rsi_gap","band","RSI"),
    ("rsi_to_daily_ratio","band","RSI"),
    ("rsi_vs_sma5","band","RSI"),("rsi_vs_sma9","band","RSI"),
    ("rsi_vs_ema5","band","RSI"),
    ("rsi_sma5_slope1","band","RSI"),("rsi_sma5_slope3","band","RSI"),
    ("volume_ratio5","high","FLOW"),("volume_z20","high","FLOW"),
    ("trades_ratio20","high","FLOW"),("dist_vwap20","band","FLOW"),
    ("obv_delta3_norm","band","FLOW"),("cmf20","band","FLOW"),
    ("atr14_pct","high","VOL"),("range_ratio20","band","VOL"),
    ("lower_wick_pct_range","band","VOL"),
    ("dist_ema20","band","TREND"),("dist_sma20","band","TREND"),
    ("dist_sma50","band","TREND"),("sma9_slope3","band","TREND"),
    ("sma20_slope3","band","TREND"),
    ("dual_stretch_9","band","TREND"),
    ("daily_ret5","band","DAILY"),("daily_rv20","band","DAILY"),
    ("daily_rsi","band","DAILY"),("price_pct3","band","PRICE"),
    ("price_dd","band","PRICE"),
]
FAMILY={f:fam for f,_,fam in FEATURES}

def rsi(close,p=14):
    d=close.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep_btc(x):
    x=x.sort_values("time").drop_duplicates("time").reset_index(drop=True).copy()
    x["btc_ret4"]=x["close"].pct_change(1)
    x["btc_ret12"]=x["close"].pct_change(3)
    x["btc_ret24"]=x["close"].pct_change(6)
    x["btc_ret72"]=x["close"].pct_change(18)
    x["btc_rsi14"]=rsi(x["close"])
    x["ema20"]=x["close"].ewm(span=20,adjust=False).mean()
    x["btc_dist_ema20"]=x["close"]/x["ema20"]-1
    tr=(x["high"]-x["low"]).rolling(14,min_periods=8).mean()
    x["btc_atr14_pct"]=tr/x["close"].replace(0,np.nan)
    return x[["time","btc_ret4","btc_ret12","btc_ret24","btc_ret72","btc_rsi14","btc_dist_ema20","btc_atr14_pct"]]

def hl_btc(start,end):
    s=requests.Session();s.headers["User-Agent"]="appwiza-novel-context/1.0"
    p={"type":"candleSnapshot","req":{"coin":"BTC","interval":"4h","startTime":int(start.timestamp()*1000),"endTime":int(end.timestamp()*1000)}}
    r=s.post(HL_INFO,json=p,timeout=45);r.raise_for_status()
    a=[]
    for z in r.json() or []:
        a.append({"time":pd.to_datetime(int(z["t"]),unit="ms",utc=True),"open":float(z["o"]),"high":float(z["h"]),"low":float(z["l"]),"close":float(z["c"])})
    return prep_btc(pd.DataFrame(a))

def bin_btc(start,end):
    s=requests.Session();s.headers["User-Agent"]="appwiza-novel-context/1.0"
    cur=int(start.timestamp()*1000);end_ms=int(end.timestamp()*1000);a=[]
    step=1000*4*3600*1000
    while cur<end_ms:
        r=s.get(BIN_KLINES,params={"symbol":"BTCUSDT","interval":"4h","startTime":cur,"endTime":end_ms,"limit":1000},timeout=45)
        r.raise_for_status();rows=r.json() or []
        if not rows:break
        for z in rows:
            a.append({"time":pd.to_datetime(int(z[0]),unit="ms",utc=True),"open":float(z[1]),"high":float(z[2]),"low":float(z[3]),"close":float(z[4])})
        nxt=int(rows[-1][0])+4*3600*1000
        if nxt<=cur:break
        cur=nxt
        time.sleep(.08)
    return prep_btc(pd.DataFrame(a))

def enrich(e,btc,trigger_col="trigger_time"):
    x=e.copy().sort_values(trigger_col).reset_index(drop=True)
    # asof backward: only BTC bars already open/known at event time. Event
    # features themselves are based on a completed 4H trigger candle, so the
    # same timestamp BTC candle is also completed in both source tables.
    b=btc.sort_values("time").copy()
    # Normalize timestamp units explicitly; pandas 3 can preserve different
    # datetime resolutions from CSV vs API construction (us vs ms).
    x[trigger_col]=pd.to_datetime(x[trigger_col],utc=True).astype("datetime64[ns, UTC]")
    b["time"]=pd.to_datetime(b["time"],utc=True).astype("datetime64[ns, UTC]")
    x=pd.merge_asof(x.sort_values(trigger_col),b,left_on=trigger_col,right_on="time",direction="backward")
    x=x.drop(columns=["time"],errors="ignore")

    times=pd.to_datetime(x[trigger_col],utc=True)
    vals=times.view("int64")
    for hrs in (8,24):
        w=pd.Timedelta(hours=hrs).value
        counts=[]
        left=0
        for i,v in enumerate(vals):
            while left<i and vals[left] < v-w:left+=1
            # prior events in the lookback + contemporaneous events already at
            # the exact timestamp; subtract self.
            j=i
            while j+1<len(vals) and vals[j+1]<=v:j+=1
            counts.append(max(0,j-left))
        x[f"market_flush_count_{hrs}h"]=counts

    x["relative_ret4"]=pd.to_numeric(x.get("price_pct1"),errors="coerce")-pd.to_numeric(x["btc_ret4"],errors="coerce")
    x["relative_ret12"]=pd.to_numeric(x.get("price_pct3"),errors="coerce")-pd.to_numeric(x["btc_ret12"],errors="coerce")
    return x

def rate(s):
    q=s.dropna()
    return float(q.astype(bool).mean()) if len(q) else np.nan

def wilson(w,n,z=1.96):
    if n<=0:return np.nan
    p=w/n;den=1+z*z/n
    ctr=(p+z*z/(2*n))/den
    half=z*sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return ctr-half

def roi(g,cost):
    if g.empty:return np.nan
    win=g["t50_before_s75_5d"].fillna(False).astype(bool)
    mae=pd.to_numeric(g["mae_5d"],errors="coerce")
    timeout=pd.to_numeric(g.get("close_ret_5d",pd.Series(0,index=g.index)),errors="coerce").fillna(0)
    stopped=(~win)&(mae<=-STOP)
    gross=pd.Series(np.where(win,TP,np.where(stopped,-STOP,timeout)),index=g.index).clip(-STOP,TP)
    return float((gross-cost).mean())

def episode_stats(g):
    if g.empty:return 0,np.nan
    z=g.sort_values("trigger_time").copy();ids=[];eid=0;last=None
    for t in pd.to_datetime(z["trigger_time"],utc=True):
        if last is None or t-last>pd.Timedelta(hours=18):eid+=1
        ids.append(eid);last=t
    z["episode"]=ids
    q=z.groupby("episode")["hit5_5d"].mean()
    return int(len(q)),float(q.mean())

def spec_name(s):
    f,op,a,b,fam=s
    if op=="band":return f"{a:.6g} <= {f} <= {b:.6g}"
    return f"{f} {op} {a:.6g}"

def mask(df,s):
    f,op,a,b,_=s;x=pd.to_numeric(df[f],errors="coerce")
    if op=="<=":return x<=a
    if op==">=":return x>=a
    return x.between(a,b,inclusive="both")

def specs(train):
    out=[]
    for f,direction,fam in FEATURES:
        if f not in train.columns:continue
        s=pd.to_numeric(train[f],errors="coerce").replace([np.inf,-np.inf],np.nan).dropna()
        if len(s)<100:continue
        q=s.quantile([.08,.12,.16,.20,.25,.30,.35,.40,.50,.60,.65,.70,.75,.80,.84,.88,.92])
        if direction=="high":
            for qq in (.60,.65,.70,.75,.80,.84,.88,.92):
                out.append((f,">=",float(q.loc[qq]),None,fam))
        else:
            # Broad bands allow non-monotonic market/BTC regime effects.
            for lo,hi in [(.08,.30),(.12,.35),(.16,.40),(.20,.50),(.25,.60),(.30,.65),(.35,.70),(.40,.75),(.50,.80),(.60,.88),(.70,.92)]:
                out.append((f,"band",float(q.loc[lo]),float(q.loc[hi]),fam))
    return out

def apply(df,combo):
    m=pd.Series(True,index=df.index)
    for s in combo:m &= mask(df,s)
    return m

def live_mask(df):
    a=(pd.to_numeric(df["rsi_pct1"],errors="coerce")<=-0.18174)&(pd.to_numeric(df["rsi_accel"],errors="coerce")<=-11.7202)
    c=(pd.to_numeric(df["close_location"],errors="coerce")<=0.1527)&(pd.to_numeric(df["volume_ratio20"],errors="coerce")>=1.8296)
    d=(pd.to_numeric(df["rsi_accel"],errors="coerce")<=-9.01822)&(pd.to_numeric(df["rsi_vs_sma3"],errors="coerce")<=-0.120961)
    return a|c|d

def folds(train,combo):
    vals=[];ns=[]
    for ii in np.array_split(np.arange(len(train)),4):
        g=train.iloc[ii];q=g[apply(g,combo)]
        if len(q)>=8:vals.append(rate(q["hit5_5d"]));ns.append(len(q))
    return len(vals),(float(np.mean(vals)) if vals else np.nan),(float(np.min(vals)) if vals else np.nan)

def discover(hl):
    x=hl.sort_values("entry_time").reset_index(drop=True)
    cut=max(1,int(len(x)*.60));tr=x.iloc[:cut].copy();ho=x.iloc[cut:].copy()
    ss=specs(tr)

    singles=[]
    for s in ss:
        g=tr[mask(tr,s)]
        if len(g)<32:continue
        score=.45*rate(g["hit5_5d"])+.30*rate(g["t50_before_s75_5d"])+.25*max(0,roi(g,HL_COST)/.03)
        singles.append((score,len(g),s))
    singles.sort(key=lambda z:(z[0],z[1]),reverse=True)

    # Keep strongest ingredients per mechanism family, so one family cannot
    # crowd all others out before combinations are formed.
    by={}
    for item in singles:
        fam=item[2][4]
        by.setdefault(fam,[])
        if len(by[fam])<10:by[fam].append(item[2])
    top=[s for fam in by.values() for s in fam]
    top=top[:90]

    combos=[]
    combos.extend((s,) for s in top)
    for a,b in itertools.combinations(top,2):
        if a[4]!=b[4]:combos.append((a,b))
    # triples only from the strongest 2 specs per family, and require three
    # different mechanism families.
    t3=[]
    for fam,lst in by.items():t3.extend(lst[:2])
    for c in itertools.combinations(t3,3):
        if len({z[4] for z in c})==3:combos.append(c)

    live_ho=live_mask(ho)
    rows=[];seen=set()
    for combo in combos:
        nm=" AND ".join(spec_name(s) for s in combo)
        if nm in seen:continue
        seen.add(nm)
        gt=tr[apply(tr,combo)];gh=ho[apply(ho,combo)]
        if len(gt)<32 or len(gh)<20:continue
        hit=rate(gh["hit5_5d"]);risk=rate(gh["t50_before_s75_5d"])
        trr=roi(gt,HL_COST);hor=roi(gh,HL_COST)
        epn,epr=episode_stats(gh)
        lo=wilson(int(gh["hit5_5d"].fillna(False).astype(bool).sum()),len(gh))
        nf,fmean,fmin=folds(tr,combo)
        incremental=float((~live_ho.loc[gh.index]).mean()) if len(gh) else 0
        fams="+".join(sorted({s[4] for s in combo}))
        eligible=(
            hit>=.80 and risk>=.72 and hor>=.015 and trr>=.0125
            and len(gh)>=20 and epn>=12 and epr>=.76 and lo>=.62
            and nf>=3 and fmean>=.75
            and incremental>=.25
        )
        rows.append({
            "rule":nm,"features":"+".join(s[0] for s in combo),"families":fams,
            "conditions":json.dumps([{"feature":s[0],"op":s[1],"a":s[2],"b":s[3]} for s in combo]),
            "train_n":len(gt),"train_hit5":rate(gt["hit5_5d"]),"train_risk":rate(gt["t50_before_s75_5d"]),"train_net_roi":trr,
            "hold_n":len(gh),"hold_hit5":hit,"hold_hit10":rate(gh["hit10_5d"]),"hold_risk":risk,"hold_net_roi":hor,
            "wilson_lower":lo,"episodes":epn,"episode_hit5":epr,
            "train_folds":nf,"fold_mean_hit5":fmean,"fold_min_hit5":fmin,
            "incremental_vs_C1_C3_C4":incremental,
            "eligible":eligible,
            "score":.25*hit+.20*risk+.15*epr+.10*lo+.20*min(max(hor/.03,0),1)+.10*incremental,
        })
    z=pd.DataFrame(rows)
    return z.sort_values(["eligible","score","hold_n"],ascending=[False,False,False]) if len(z) else z

def validate(cands,bn):
    x=bn.sort_values("entry_time").reset_index(drop=True)
    cut=max(1,int(len(x)*.60));ho=x.iloc[cut:].copy()
    rows=[];registry=[]
    for i,(_,r) in enumerate(cands.iterrows(),1):
        conds=json.loads(r["conditions"]);m=pd.Series(True,index=ho.index);unsupported=[]
        for c in conds:
            if c["feature"] not in ho.columns:
                unsupported.append(c["feature"]);m &= False;continue
            s=(c["feature"],c["op"],c["a"],c.get("b"),"")
            m &= mask(ho,s)
        g=ho[m];n=len(g)
        hit=rate(g["hit5_5d"]) if n else np.nan
        risk=rate(g["t50_before_s75_5d"]) if n else np.nan
        rr=roi(g,BIN_COST) if n else np.nan
        lo=wilson(int(g["hit5_5d"].fillna(False).astype(bool).sum()),n) if n else np.nan
        passed=(not unsupported and n>=15 and hit>=.78 and risk>=.68 and rr>=.010 and lo>=.58)
        cid=f"NOVEL-{pd.Timestamp.utcnow().strftime('%Y%m%d')}-{i:02d}"
        status="CROSS_VALIDATED_SHADOW" if passed else "SHADOW_ONLY"
        rows.append({"candidate_id":cid,"status":status,"rule":r["rule"],"families":r["families"],"binance_n":n,
                     "binance_hit5":hit,"binance_hit10":rate(g["hit10_5d"]) if n else np.nan,
                     "binance_risk":risk,"binance_net_roi":rr,"binance_wilson_lower":lo,
                     "unsupported_features":"+".join(unsupported)})
        registry.append({
            "candidate_id":cid,"status":status,"rule":r["rule"],
            "features":r["features"].split("+"),"families":r["families"].split("+"),
            "conditions":conds,
            "hyperliquid":{
                "train_n":int(r["train_n"]),"train_hit5":float(r["train_hit5"]),"train_net_roi":float(r["train_net_roi"]),
                "hold_n":int(r["hold_n"]),"hold_hit5":float(r["hold_hit5"]),"hold_hit10":float(r["hold_hit10"]),
                "hold_risk":float(r["hold_risk"]),"hold_net_roi":float(r["hold_net_roi"]),
                "episodes":int(r["episodes"]),"episode_hit5":float(r["episode_hit5"]),
                "incremental_vs_C1_C3_C4":float(r["incremental_vs_C1_C3_C4"]),
            },
            "binance":{
                "n":int(n),"hit5":None if pd.isna(hit) else float(hit),
                "hit10":None if n==0 else float(rate(g["hit10_5d"])),
                "risk":None if pd.isna(risk) else float(risk),
                "net_roi":None if pd.isna(rr) else float(rr),
                "wilson_lower":None if pd.isna(lo) else float(lo),
                "passed":bool(passed),
            },
            "promotion_requirements":["actual-funding Hyperliquid economic replay","forward shadow tracking","manual review"],
        })
    return pd.DataFrame(rows),registry

def main():
    hl=pd.read_csv(HL)
    for c in ["trigger_time","entry_time","arm_time"]:hl[c]=pd.to_datetime(hl[c],utc=True,errors="coerce")
    hl=hl[(hl["kind"]=="perp")&(hl["dex"]=="primary")&np.isclose(pd.to_numeric(hl["flush_threshold"],errors="coerce"),.08)].copy()

    bn=pd.read_csv(BIN)
    for c in ["trigger_time","entry_time","arm_time"]:
        if c in bn.columns:bn[c]=pd.to_datetime(bn[c],utc=True,errors="coerce")
    if "flush_dd_threshold" in bn.columns:
        bn=bn[np.isclose(pd.to_numeric(bn["flush_dd_threshold"],errors="coerce"),.08)].copy()

    start=min(hl["trigger_time"].min(),bn["trigger_time"].min())-pd.Timedelta(days=10)
    end=max(hl["trigger_time"].max(),bn["trigger_time"].max())+pd.Timedelta(hours=8)

    hbtc=hl_btc(pd.Timestamp(hl["trigger_time"].min())-pd.Timedelta(days=10),pd.Timestamp(hl["trigger_time"].max())+pd.Timedelta(hours=8))
    bbtc=bin_btc(pd.Timestamp(bn["trigger_time"].min())-pd.Timedelta(days=10),pd.Timestamp(bn["trigger_time"].max())+pd.Timedelta(hours=8))

    he=enrich(hl,hbtc)
    be=enrich(bn,bbtc)
    he.to_csv(OUT/"hyperliquid_context_events.csv",index=False)
    be.to_csv(OUT/"binance_context_events.csv",index=False)

    allr=discover(he)
    allr.to_csv(OUT/"all_rules.csv",index=False)
    cand=allr[allr["eligible"]==True].copy() if len(allr) else pd.DataFrame()
    if len(cand):
        cand["family_key"]=cand["families"]+"|"+cand["features"]
        cand=cand.sort_values(["score","hold_n"],ascending=[False,False]).drop_duplicates("family_key").head(20)
    cand.to_csv(OUT/"hyperliquid_shortlist.csv",index=False)

    cross,reg=validate(cand,be) if len(cand) else (pd.DataFrame(),[])
    cross.to_csv(OUT/"binance_validation.csv",index=False)
    (OUT/"candidate_registry.json").write_text(json.dumps({
        "generated_at":pd.Timestamp.utcnow().isoformat(),
        "design":"Hyperliquid-first orthogonal/context discovery; frozen Binance replication",
        "excluded_live_core_features":["rsi_pct1","rsi_accel","rsi_vs_sma3","close_location","volume_ratio20"],
        "candidates":reg,
    },indent=2))

    passed=cross[cross["status"]=="CROSS_VALIDATED_SHADOW"] if len(cross) else pd.DataFrame()
    lines=[
        "NOVEL CONTEXT / ORTHOGONAL CRYPTO METHOD DISCOVERY","",
        f"Hyperliquid primary-perp 8% first-flush events: {len(he)}",
        f"Eligible Hyperliquid candidates: {len(cand)}",
        f"Binance cross-validated shadows: {len(passed)}","",
        "This lane explicitly excludes the core live features rsi_pct1, rsi_accel, rsi_vs_sma3, close_location and volume_ratio20.",
        "It adds BTC regime, market flush density, and asset-vs-BTC relative shock features, and requires >=25% incremental holdout signals versus C1/C3/C4.",
        "",
        "HYPERLIQUID SHORTLIST",
        cand.to_string(index=False) if len(cand) else "none",
        "",
        "BINANCE FROZEN-RULE VALIDATION",
        cross.to_string(index=False) if len(cross) else "none",
        "",
        "No live promotion. Any survivor still requires actual-funding economic replay and forward shadow validation.",
    ]
    (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":
    main()
