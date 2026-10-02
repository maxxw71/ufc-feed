#!/usr/bin/env python3
"""
External replication of frozen Hyperliquid primary-perp rules on Binance USDT spot.

No rule tuning. Thresholds are copied exactly from Hyperliquid TRAIN-selected
primary-perp rules and evaluated on a separate venue/universe.
"""
from __future__ import annotations
import argparse,time,math
from datetime import datetime,timedelta,timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests

API="https://data-api.binance.vision"
STABLE={"USDC","FDUSD","TUSD","USDP","DAI","BUSD","USD1","EUR","TRY","BRL","GBP","AUD","JPY"}

FROZEN={
    "ema9_m3p649_rsiacc_m7p63088": lambda r:(r["dist_ema9"]<=-0.03649) and (r["rsi_accel"]<=-7.63088),
    "rsi1_m21p182_sma9slope_lte2p604": lambda r:(r["rsi_pct1"]<=-0.21182) and (r["sma9_slope3"]<=0.02604),
    "rsi1_m21p182_sma9_m3p517": lambda r:(r["rsi_pct1"]<=-0.21182) and (r["dist_sma9"]<=-0.03517),
    "rsi_sma5_slope_m5p915_rsi1_m17p635": lambda r:(r["rsi_sma5_slope1"]<=-0.05915) and (r["rsi_pct1"]<=-0.17635),
    "ema9_m3p649_close_bottom16p592": lambda r:(r["dist_ema9"]<=-0.03649) and (r["close_location"]<=0.16592),
}

def gj(s,path,params=None):
    last=None
    for k in range(6):
        try:
            r=s.get(API+path,params=params,timeout=25)
            if r.ok:return r.json()
            last=RuntimeError("HTTP %s %s"%(r.status_code,r.text[:160]))
        except Exception as e:last=e
        time.sleep(min(8,1.5**k))
    raise RuntimeError(str(last))

def universe(s,n):
    info=gj(s,"/api/v3/exchangeInfo");tick=gj(s,"/api/v3/ticker/24hr")
    qv={x["symbol"]:float(x.get("quoteVolume") or 0) for x in tick if isinstance(x,dict) and "symbol" in x}
    a=[]
    for x in info.get("symbols",[]):
        if x.get("status")!="TRADING" or x.get("quoteAsset")!="USDT":continue
        b=x.get("baseAsset","")
        if b in STABLE or b.startswith("USD") or b.startswith("1000") or any(b.endswith(z) for z in ("UP","DOWN","BULL","BEAR")):continue
        a.append(x["symbol"])
    a.sort(key=lambda z:qv.get(z,0),reverse=True)
    return a[:n]

def fetch(s,sym,interval,start,end):
    step={"1d":86400000,"4h":14400000}[interval]
    cur=int(start.timestamp()*1000);stop=int(end.timestamp()*1000);rows=[]
    while cur<stop:
        b=gj(s,"/api/v3/klines",{"symbol":sym,"interval":interval,"startTime":cur,"endTime":stop,"limit":1000})
        if not b:break
        rows.extend(b);nxt=int(b[-1][0])+step
        if nxt<=cur:break
        cur=nxt
        if len(b)<1000:break
        time.sleep(.02)
    cols=["t","o","h","l","c","v","ct","qv","n","tb","tq","x"]
    d=pd.DataFrame(rows,columns=cols)
    if d.empty:return d
    for c in ["o","h","l","c","v"]:d[c]=pd.to_numeric(d[c],errors="coerce")
    d["time"]=pd.to_datetime(d["t"],unit="ms",utc=True)
    return d.dropna(subset=["o","h","l","c"]).drop_duplicates("time").sort_values("time").reset_index(drop=True)

def rsi(c,p=14):
    d=c.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean();al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan);return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep4(h):
    x=h.copy();x["rsi"]=rsi(x["c"]);x["rsi_pct1"]=x["rsi"].pct_change()
    x["rsi_delta"]=x["rsi"].diff();x["rsi_accel"]=x["rsi_delta"].diff()
    x["sma9"]=x["c"].rolling(9,min_periods=5).mean();x["ema9"]=x["c"].ewm(span=9,adjust=False).mean()
    x["dist_sma9"]=x["c"]/x["sma9"]-1;x["dist_ema9"]=x["c"]/x["ema9"]-1
    x["sma9_slope3"]=x["sma9"]/x["sma9"].shift(3)-1
    x["rsi_sma5"]=x["rsi"].rolling(5,min_periods=3).mean()
    x["rsi_sma5_slope1"]=x["rsi_sma5"].pct_change()
    x["range"]=x["h"]-x["l"];x["close_location"]=(x["c"]-x["l"])/x["range"].replace(0,np.nan)
    return x

def prep_daily(d):
    x=d.copy();x["ret1"]=x["c"].pct_change();x["ret5"]=x["c"]/x["c"].shift(5)-1
    x["prior60"]=x["h"].shift(1).rolling(60,min_periods=60).max();x["rv20"]=x["ret1"].rolling(20,min_periods=20).std()
    x["setup"]=(x["ret5"]>=.15)&(x["h"]>=x["prior60"])&(x["rv20"]>=.025)
    ev=[];last=None
    for _,r in x[x["setup"]].iterrows():
        t=r["time"]
        if last is None or t-last>=pd.Timedelta(days=10):ev.append(t);last=t
    return x,ev

def first_flush(h,arm,dd=.08):
    w=h[(h["time"]>=arm)&(h["time"]<arm+pd.Timedelta(days=5))]
    peak=-math.inf;pi=None
    for i,r in w.iterrows():
        if float(r["h"])>=peak:peak=float(r["h"]);pi=i
        if pi is not None and i>pi and float(r["c"])/peak-1<=-dd:return i
    return None

def outcome(h,idx):
    loc=h.index.get_loc(idx)
    if loc+1>=len(h):return None
    entry=float(h.iloc[loc+1]["o"]);f=h.iloc[loc+1:min(len(h),loc+31)]
    if entry<=0 or f.empty:return None
    out={"entry":entry,"entry_time":h.iloc[loc+1]["time"],
         "mfe5d":float(f["h"].max()/entry-1),"mae5d":float(f["l"].min()/entry-1)}
    out["hit5"]=out["mfe5d"]>=.05;out["hit10"]=out["mfe5d"]>=.10
    for stop in (.05,.075,.10):
        tk=sk=None
        for k,(_,r) in enumerate(f.iterrows()):
            if tk is None and float(r["h"])>=entry*1.05:tk=k
            if sk is None and float(r["l"])<=entry*(1-stop):sk=k
        out[f"t5_s{stop}"]=tk is not None and (sk is None or tk<sk)
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--symbols",type=int,default=120);ap.add_argument("--days",type=int,default=900)
    args=ap.parse_args();out=Path("crypto/research/results_binance_external_replication");out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-external-replication/1.0"
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0);start=end-timedelta(days=args.days)
    rows=[];cov=[]
    for pos,sym in enumerate(universe(s,args.symbols),1):
        try:
            d=fetch(s,sym,"1d",start-timedelta(days=100),end)
            if len(d)<120:continue
            _,arms=prep_daily(d);arms=[a for a in arms if a>=pd.Timestamp(start)]
            if not arms:cov.append((sym,0,0));continue
            h=prep4(fetch(s,sym,"4h",min(arms).to_pydatetime()-timedelta(days=2),end))
            ne=0
            for arm in arms:
                idx=first_flush(h,arm,.08)
                if idx is None:continue
                r=h.loc[idx]
                feat={"dist_ema9":float(r["dist_ema9"]),"dist_sma9":float(r["dist_sma9"]),"sma9_slope3":float(r["sma9_slope3"]),
                      "rsi_pct1":float(r["rsi_pct1"]),"rsi_accel":float(r["rsi_accel"]),"rsi_sma5_slope1":float(r["rsi_sma5_slope1"]),
                      "close_location":float(r["close_location"])}
                o=outcome(h,idx)
                if o is None:continue
                rec={"symbol":sym,"arm_time":arm,"trigger_time":r["time"],**feat,**o}
                for nm,fn in FROZEN.items():rec[nm]=bool(fn(rec))
                rows.append(rec);ne+=1
            cov.append((sym,len(arms),ne));print(pos,sym,ne)
        except Exception as ex:
            print("ERR",sym,ex)
    e=pd.DataFrame(rows);e.to_csv(out/"events.csv",index=False)
    pd.DataFrame(cov,columns=["symbol","arms","events"]).to_csv(out/"coverage.csv",index=False)
    if e.empty:raise SystemExit(2)
    e=e.sort_values("entry_time").reset_index(drop=True);cut=max(1,int(len(e)*.60));tr=e.iloc[:cut];ho=e.iloc[cut:]
    summ=[]
    for nm in FROZEN:
        for label,g in [("all",e),("train",tr),("holdout",ho)]:
            q=g[g[nm]]
            if not len(q):continue
            summ.append({"rule":nm,"split":label,"n":len(q),"hit5":q["hit5"].mean(),"hit10":q["hit10"].mean(),
                         "t5_s5":q["t5_s0.05"].mean(),"t5_s7p5":q["t5_s0.075"].mean(),"t5_s10":q["t5_s0.1"].mean(),
                         "median_mfe":q["mfe5d"].median(),"median_mae":q["mae5d"].median()})
    z=pd.DataFrame(summ);z.to_csv(out/"frozen_rule_results.csv",index=False)
    lines=["BINANCE EXTERNAL REPLICATION OF FROZEN HYPERLIQUID PRIMARY-PERP RULES","",
           f"Events={len(e)} train={len(tr)} holdout={len(ho)}","",z.to_string(index=False),
           "","No threshold in this file was selected using Binance results."]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n");print((out/"REPORT.txt").read_text())
if __name__=="__main__":main()
