#!/usr/bin/env python3
"""
Hyperliquid-only discovery of a NEW generic capitulation-rebound family.

Unlike C1/C3/C4, this does NOT require a prior daily blow-off breakout.
Unlike C2/C2D, this does NOT require a lower-high / second-dump structure.

Frozen broad event definition before any rule search:
- primary Hyperliquid perp
- completed 4H candle return <= -3.5%
- 12H return <= -6%
- 4H range >= 1.25x its 20-bar median
- trigger RSI <= 50
- 5-day per-coin cooldown
- enter next 4H open

The broad event set is then searched using only trigger-time features with a
60/40 chronological split. The latest 40% is untouched holdout.

Primary economics:
+5% target / -7.5% stop / 5-day timeout
0.045% taker fee per side + 0.10% modeled slippage per side
(actual funding is a mandatory later replay if a candidate survives)

Research only. No live promotion and no orders.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from pathlib import Path
from math import sqrt
from datetime import datetime,timedelta,timezone

import numpy as np
import pandas as pd
import requests

API="https://api.hyperliquid.xyz/info"
OUT=Path("crypto/research/results_generic_hl_capitulation")

TP=.05
STOP=.075
RT_COST=2*(.00045+.0010)

def post(s,p,tries=8):
    last=None
    for k in range(tries):
        try:
            r=s.post(API,json=p,timeout=45)
            if r.status_code==200:return r.json()
            last=RuntimeError(f"HTTP {r.status_code}: {r.text[:180]}")
        except Exception as e:last=e
        time.sleep(min(12,1.25*(k+1)))
    raise RuntimeError(str(last))

def universe(s):
    meta,ctx=post(s,{"type":"metaAndAssetCtxs"})
    out=[]
    for i,u in enumerate(meta.get("universe",[])):
        if u.get("isDelisted") or not u.get("name"):continue
        out.append(str(u["name"]))
    return out

def fetch(s,coin,start,end):
    rows=post(s,{"type":"candleSnapshot","req":{"coin":coin,"interval":"4h",
        "startTime":int(start.timestamp()*1000),"endTime":int(end.timestamp()*1000)}})
    a=[]
    for r in rows or []:
        try:
            a.append({"time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),
                      "open":float(r["o"]),"high":float(r["h"]),"low":float(r["l"]),
                      "close":float(r["c"]),"volume":float(r.get("v",0)),
                      "trades":float(r.get("n",np.nan))})
        except Exception:pass
    return pd.DataFrame(a).drop_duplicates("time").sort_values("time").reset_index(drop=True) if a else pd.DataFrame()

def rsi(c,p=14):
    d=c.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep(x):
    x=x.copy()
    x["ret1"]=x["close"].pct_change()
    x["ret3"]=x["close"]/x["close"].shift(3)-1
    x["ret6"]=x["close"]/x["close"].shift(6)-1
    x["rsi14"]=rsi(x["close"])
    x["rsi_pct1"]=x["rsi14"].pct_change()
    x["rsi_pct3"]=x["rsi14"]/x["rsi14"].shift(3)-1
    x["rsi_delta"]=x["rsi14"].diff()
    x["rsi_accel"]=x["rsi_delta"].diff()
    x["rsi_sma3"]=x["rsi14"].rolling(3,min_periods=2).mean()
    x["rsi_sma5"]=x["rsi14"].rolling(5,min_periods=3).mean()
    x["rsi_sma9"]=x["rsi14"].rolling(9,min_periods=5).mean()
    x["rsi_vs_sma3"]=x["rsi14"]/x["rsi_sma3"]-1
    x["rsi_vs_sma5"]=x["rsi14"]/x["rsi_sma5"]-1
    x["rsi_vs_sma9"]=x["rsi14"]/x["rsi_sma9"]-1

    x["ema9"]=x["close"].ewm(span=9,adjust=False).mean()
    x["ema20"]=x["close"].ewm(span=20,adjust=False).mean()
    x["sma20"]=x["close"].rolling(20,min_periods=10).mean()
    x["sma50"]=x["close"].rolling(50,min_periods=25).mean()
    for n in ("ema9","ema20","sma20","sma50"):
        x["dist_"+n]=x["close"]/x[n]-1
    x["sma20_slope3"]=x["sma20"]/x["sma20"].shift(3)-1
    x["sma50_slope6"]=x["sma50"]/x["sma50"].shift(6)-1

    rng=(x["high"]-x["low"]).replace(0,np.nan)
    x["range_pct"]=rng/x["close"].replace(0,np.nan)
    x["range_med20"]=x["range_pct"].rolling(20,min_periods=10).median()
    x["range_ratio20"]=x["range_pct"]/x["range_med20"].replace(0,np.nan)
    x["close_location"]=(x["close"]-x["low"])/rng
    x["lower_wick"]=(np.minimum(x["open"],x["close"])-x["low"])/rng

    x["volume_med20"]=x["volume"].rolling(20,min_periods=10).median()
    x["volume_mean20"]=x["volume"].rolling(20,min_periods=10).mean()
    x["volume_std20"]=x["volume"].rolling(20,min_periods=10).std()
    x["volume_ratio20"]=x["volume"]/x["volume_med20"].replace(0,np.nan)
    x["volume_z20"]=(x["volume"]-x["volume_mean20"])/x["volume_std20"].replace(0,np.nan)
    x["trades_med20"]=x["trades"].rolling(20,min_periods=10).median()
    x["trades_ratio20"]=x["trades"]/x["trades_med20"].replace(0,np.nan)

    typical=(x["high"]+x["low"]+x["close"])/3
    x["vwap20"]=(typical*x["volume"]).rolling(20,min_periods=10).sum()/x["volume"].rolling(20,min_periods=10).sum().replace(0,np.nan)
    x["dist_vwap20"]=x["close"]/x["vwap20"]-1

    # Pre-trigger path features.
    x["dd_24h"]=x["close"]/x["high"].rolling(6,min_periods=3).max()-1
    x["dd_72h"]=x["close"]/x["high"].rolling(18,min_periods=8).max()-1
    x["volatility_24h"]=x["ret1"].rolling(6,min_periods=4).std()
    return x

def outcome(x,i):
    if i+1>=len(x):return None
    entry=float(x.loc[i+1,"open"])
    if not np.isfinite(entry) or entry<=0:return None
    f=x.iloc[i+1:min(len(x),i+31)]
    if f.empty:return None
    mfe=float(f["high"].max()/entry-1);mae=float(f["low"].min()/entry-1)
    rec={"entry":entry,"entry_time":x.loc[i+1,"time"],"mfe_5d":mfe,"mae_5d":mae,
         "close_ret_5d":float(f.iloc[-1]["close"]/entry-1),
         "hit5_5d":mfe>=.05,"hit10_5d":mfe>=.10}
    tk=sk=None
    for k,(_,r) in enumerate(f.iterrows()):
        if tk is None and float(r["high"])>=entry*(1+TP):tk=k
        if sk is None and float(r["low"])<=entry*(1-STOP):sk=k
    rec["t50_before_s75_5d"]=tk is not None and (sk is None or tk<sk)
    return rec

FEATURE_COLS=[
    "ret1","ret3","ret6","rsi14","rsi_pct1","rsi_pct3","rsi_accel",
    "rsi_vs_sma3","rsi_vs_sma5","rsi_vs_sma9",
    "dist_ema9","dist_ema20","dist_sma20","dist_sma50",
    "sma20_slope3","sma50_slope6","range_ratio20","close_location","lower_wick",
    "volume_ratio20","volume_z20","trades_ratio20","dist_vwap20",
    "dd_24h","dd_72h","volatility_24h"
]

def events_for_coin(x,coin):
    rows=[];last=None
    for i in range(60,len(x)-31):
        r=x.loc[i]
        if not (
            float(r["ret1"])<=-.035
            and float(r["ret3"])<=-.06
            and float(r["range_ratio20"])>=1.25
            and float(r["rsi14"])<=50
        ):continue
        tt=r["time"]
        if last is not None and tt-last<pd.Timedelta(days=5):continue
        o=outcome(x,i)
        if o is None:continue
        rec={"coin":coin,"trigger_time":tt,"trigger_close":float(r["close"])}
        for c in FEATURE_COLS:rec[c]=float(r[c]) if pd.notna(r[c]) else np.nan
        rec.update(o);rows.append(rec);last=tt
    return rows

def scan(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-generic-capitulation/1.0"
    coins=universe(s)[args.shard_index::args.shard_count]
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    start=end-timedelta(days=args.days)
    rows=[];cov=[]
    for pos,coin in enumerate(coins,1):
        try:
            x=prep(fetch(s,coin,start,end))
            rr=events_for_coin(x,coin) if len(x)>=150 else []
            rows.extend(rr);cov.append((coin,len(x),len(rr),"ok"))
            print(pos,coin,len(rr))
        except Exception as ex:
            cov.append((coin,0,0,"error:"+str(ex)[:100]));print("ERR",coin,ex)
        time.sleep(args.sleep)
    pd.DataFrame(rows).to_csv(out/f"events_{args.shard_index:02d}.csv",index=False)
    pd.DataFrame(cov,columns=["coin","bars","events","status"]).to_csv(out/f"coverage_{args.shard_index:02d}.csv",index=False)

def rate(s):
    q=s.dropna();return float(q.astype(bool).mean()) if len(q) else np.nan

def roi(g):
    if g.empty:return np.nan
    win=g["t50_before_s75_5d"].fillna(False).astype(bool)
    mae=pd.to_numeric(g["mae_5d"],errors="coerce")
    timeout=pd.to_numeric(g["close_ret_5d"],errors="coerce").fillna(0)
    stopped=(~win)&(mae<=-STOP)
    gross=pd.Series(np.where(win,TP,np.where(stopped,-STOP,timeout)),index=g.index).clip(-STOP,TP)
    return float((gross-RT_COST).mean())

def wilson(w,n,z=1.96):
    if n<=0:return np.nan
    p=w/n;den=1+z*z/n;ctr=(p+z*z/(2*n))/den
    half=z*sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return ctr-half

def eps(g):
    if g.empty:return 0,np.nan
    z=g.sort_values("trigger_time").copy();ids=[];eid=0;last=None
    for t in pd.to_datetime(z["trigger_time"],utc=True):
        if last is None or t-last>pd.Timedelta(hours=18):eid+=1
        ids.append(eid);last=t
    z["ep"]=ids;q=z.groupby("ep")["hit5_5d"].mean()
    return int(len(q)),float(q.mean())

FEATURES=[
    ("rsi14","band","RSI"),("rsi_pct1","band","RSI"),("rsi_pct3","band","RSI"),
    ("rsi_accel","band","RSI"),("rsi_vs_sma3","band","RSI"),("rsi_vs_sma5","band","RSI"),("rsi_vs_sma9","band","RSI"),
    ("ret1","band","PRICE"),("ret3","band","PRICE"),("ret6","band","PRICE"),("dd_24h","band","PRICE"),("dd_72h","band","PRICE"),
    ("dist_ema9","band","TREND"),("dist_ema20","band","TREND"),("dist_sma20","band","TREND"),("dist_sma50","band","TREND"),
    ("sma20_slope3","band","TREND"),("sma50_slope6","band","TREND"),
    ("range_ratio20","band","CANDLE"),("close_location","band","CANDLE"),("lower_wick","band","CANDLE"),
    ("volume_ratio20","high","FLOW"),("volume_z20","high","FLOW"),("trades_ratio20","high","FLOW"),("dist_vwap20","band","FLOW"),
    ("volatility_24h","band","VOL")
]

def spec_name(s):
    f,op,a,b,fam=s
    return f"{a:.6g} <= {f} <= {b:.6g}" if op=="band" else f"{f} >= {a:.6g}"

def mask(df,s):
    f,op,a,b,_=s;x=pd.to_numeric(df[f],errors="coerce")
    return x>=a if op==">=" else x.between(a,b,inclusive="both")

def make_specs(tr):
    out=[]
    for f,d,fam in FEATURES:
        s=pd.to_numeric(tr[f],errors="coerce").replace([np.inf,-np.inf],np.nan).dropna()
        if len(s)<120:continue
        q=s.quantile([.08,.12,.16,.20,.25,.30,.35,.40,.50,.60,.65,.70,.75,.80,.84,.88,.92])
        if d=="high":
            for qq in (.60,.65,.70,.75,.80,.84,.88,.92):out.append((f,">=",float(q.loc[qq]),None,fam))
        else:
            for lo,hi in [(.08,.30),(.12,.35),(.16,.40),(.20,.50),(.25,.60),(.30,.65),(.35,.70),(.40,.75),(.50,.80),(.60,.88),(.70,.92)]:
                out.append((f,"band",float(q.loc[lo]),float(q.loc[hi]),fam))
    return out

def apply(df,combo):
    m=pd.Series(True,index=df.index)
    for s in combo:m &= mask(df,s)
    return m

def folds(tr,combo):
    vals=[]
    for ii in np.array_split(np.arange(len(tr)),4):
        g=tr.iloc[ii];q=g[apply(g,combo)]
        if len(q)>=8:vals.append(rate(q["hit5_5d"]))
    return len(vals),(float(np.mean(vals)) if vals else np.nan),(float(np.min(vals)) if vals else np.nan)

def discover(e):
    x=e.sort_values("entry_time").reset_index(drop=True)
    cut=max(1,int(len(x)*.60));tr=x.iloc[:cut].copy();ho=x.iloc[cut:].copy()
    ss=make_specs(tr)
    singles=[]
    for s in ss:
        g=tr[mask(tr,s)]
        if len(g)<40:continue
        sc=.40*rate(g["hit5_5d"])+.30*rate(g["t50_before_s75_5d"])+.30*max(0,roi(g)/.035)
        singles.append((sc,len(g),s))
    singles.sort(key=lambda z:(z[0],z[1]),reverse=True)
    fam={}
    for item in singles:
        k=item[2][4];fam.setdefault(k,[])
        if len(fam[k])<10:fam[k].append(item[2])
    top=[s for a in fam.values() for s in a]

    combos=[(s,) for s in top]
    for a,b in itertools.combinations(top,2):
        if a[4]!=b[4]:combos.append((a,b))
    t3=[]
    for a in fam.values():t3.extend(a[:2])
    for c in itertools.combinations(t3,3):
        if len({s[4] for s in c})==3:combos.append(c)

    rows=[];seen=set()
    for combo in combos:
        nm=" AND ".join(spec_name(s) for s in combo)
        if nm in seen:continue
        seen.add(nm)
        gt=tr[apply(tr,combo)];gh=ho[apply(ho,combo)]
        if len(gt)<40 or len(gh)<25:continue
        hit=rate(gh["hit5_5d"]);risk=rate(gh["t50_before_s75_5d"]);hor=roi(gh);trr=roi(gt)
        lo=wilson(int(gh["hit5_5d"].fillna(False).astype(bool).sum()),len(gh))
        epn,epr=eps(gh);nf,fmean,fmin=folds(tr,combo)
        eligible=(hit>=.80 and risk>=.72 and hor>=.015 and trr>=.0125 and epn>=15 and epr>=.76 and lo>=.62 and nf>=3 and fmean>=.75)
        rows.append({"rule":nm,"features":"+".join(s[0] for s in combo),
                     "families":"+".join(sorted({s[4] for s in combo})),
                     "conditions":json.dumps([{"feature":s[0],"op":s[1],"a":s[2],"b":s[3]} for s in combo]),
                     "train_n":len(gt),"train_hit5":rate(gt["hit5_5d"]),"train_risk":rate(gt["t50_before_s75_5d"]),"train_net_roi":trr,
                     "hold_n":len(gh),"hold_hit5":hit,"hold_hit10":rate(gh["hit10_5d"]),"hold_risk":risk,"hold_net_roi":hor,
                     "wilson_lower":lo,"episodes":epn,"episode_hit5":epr,
                     "train_folds":nf,"fold_mean_hit5":fmean,"fold_min_hit5":fmin,
                     "eligible":eligible,
                     "score":.28*hit+.22*risk+.15*epr+.10*lo+.25*min(max(hor/.035,0),1)})
    z=pd.DataFrame(rows)
    return z.sort_values(["eligible","score","hold_n"],ascending=[False,False,False]) if len(z) else z

def aggregate(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    fs=[];cov=[]
    for p in sorted(Path(args.input_dir).rglob("events_*.csv")):
        try:
            d=pd.read_csv(p)
            if len(d):fs.append(d)
        except Exception:pass
    for p in sorted(Path(args.input_dir).rglob("coverage_*.csv")):
        try:cov.append(pd.read_csv(p))
        except Exception:pass
    e=pd.concat(fs,ignore_index=True) if fs else pd.DataFrame()
    c=pd.concat(cov,ignore_index=True) if cov else pd.DataFrame()
    e.to_csv(out/"events.csv",index=False);c.to_csv(out/"coverage.csv",index=False)
    if e.empty:
        (out/"REPORT.txt").write_text("GENERIC HL CAPITULATION\nNo events generated.\n");return
    for k in ["trigger_time","entry_time"]:e[k]=pd.to_datetime(e[k],utc=True,errors="coerce")
    z=discover(e);z.to_csv(out/"all_rules.csv",index=False)
    cand=z[z["eligible"]==True].copy() if len(z) else pd.DataFrame()
    if len(cand):
        cand["family_key"]=cand["families"]+"|"+cand["features"]
        cand=cand.sort_values(["score","hold_n"],ascending=[False,False]).drop_duplicates("family_key").head(20)
    cand.to_csv(out/"candidate_shortlist.csv",index=False)
    reg=[]
    for i,(_,r) in enumerate(cand.iterrows(),1):
        reg.append({"candidate_id":f"CAP-{pd.Timestamp.utcnow().strftime('%Y%m%d')}-{i:02d}",
                    "status":"HYPERLIQUID_SHADOW_PENDING_CROSSVENUE",
                    "rule":r["rule"],"features":r["features"].split("+"),"families":r["families"].split("+"),
                    "conditions":json.loads(r["conditions"]),
                    "validation":{"train_n":int(r["train_n"]),"train_hit5":float(r["train_hit5"]),"train_net_roi":float(r["train_net_roi"]),
                                  "hold_n":int(r["hold_n"]),"hold_hit5":float(r["hold_hit5"]),"hold_hit10":float(r["hold_hit10"]),
                                  "hold_risk":float(r["hold_risk"]),"hold_net_roi":float(r["hold_net_roi"]),
                                  "episodes":int(r["episodes"]),"episode_hit5":float(r["episode_hit5"])},
                    "next_stage":["freeze exact rule","replicate on Binance or another independent venue","actual-funding economic replay","forward shadow"]})
    (out/"candidate_registry.json").write_text(json.dumps({"generated_at":pd.Timestamp.utcnow().isoformat(),
        "family":"generic 4H capitulation rebound; no daily blowoff/lower-high prerequisite","candidates":reg},indent=2))
    lines=["GENERIC HYPERLIQUID CAPITULATION METHOD DISCOVERY","",
           f"Events: {len(e)} | coins: {e['coin'].nunique()}",
           f"Eligible Hyperliquid candidates: {len(cand)}","",
           "Broad setup is fixed before search: 4H <= -3.5%, 12H <= -6%, range >=1.25x median, RSI<=50, 5d cooldown.",
           "No Binance work is done unless a Hyperliquid candidate first clears the full ROI/holdout gates.","",
           "TOP HYPERLIQUID CANDIDATES",
           cand.to_string(index=False) if len(cand) else "none","",
           "No live promotion."]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mode",choices=["scan","aggregate"],default="scan")
    ap.add_argument("--days",type=int,default=600)
    ap.add_argument("--shard-index",type=int,default=0)
    ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--sleep",type=float,default=.08)
    ap.add_argument("--out",default="generic_cap_out")
    ap.add_argument("--input-dir",default="generic_cap_shards")
    args=ap.parse_args()
    scan(args) if args.mode=="scan" else aggregate(args)

if __name__=="__main__":
    main()
