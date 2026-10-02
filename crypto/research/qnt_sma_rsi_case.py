#!/usr/bin/env python3
"""
QNT 4H case study: recent blow-off -> crash -> rebound.
Researches price SMA/EMA and RSI's own SMA/EMA structure around the move.
Retrospective case study only; no live order logic.
"""
from __future__ import annotations
import math, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests

API="https://data-api.binance.vision"

def get_json(s,path,params=None):
    last=None
    for k in range(6):
        try:
            r=s.get(API+path,params=params,timeout=25)
            if r.ok:return r.json()
            last=RuntimeError("HTTP %s %s"%(r.status_code,r.text[:180]))
        except Exception as e:last=e
        time.sleep(min(8,1.5**k))
    raise RuntimeError(str(last))

def rsi(close,p=14):
    d=close.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def fetch(s,symbol,start,end):
    cur=int(start.timestamp()*1000); stop=int(end.timestamp()*1000); rows=[]
    step=4*3600*1000
    while cur<stop:
        b=get_json(s,"/api/v3/klines",{"symbol":symbol,"interval":"4h","startTime":cur,"endTime":stop,"limit":1000})
        if not b:break
        rows.extend(b); nxt=int(b[-1][0])+step
        if nxt<=cur:break
        cur=nxt
        if len(b)<1000:break
    cols=["t","o","h","l","c","v","ct","qv","n","tb","tq","x"]
    x=pd.DataFrame(rows,columns=cols)
    for c in ["o","h","l","c","v"]:x[c]=pd.to_numeric(x[c],errors="coerce")
    x["time"]=pd.to_datetime(x["t"],unit="ms",utc=True)
    x=x.dropna(subset=["o","h","l","c"]).drop_duplicates("time").sort_values("time").reset_index(drop=True)
    return x

def prep(x):
    x=x.copy()
    x["rsi"]=rsi(x["c"])
    x["rsi_pct1"]=x["rsi"].pct_change()
    x["rsi_pct3"]=x["rsi"]/x["rsi"].shift(3)-1
    x["rsi_delta"]=x["rsi"].diff()
    x["rsi_accel"]=x["rsi_delta"].diff()
    x["sma9"]=x["c"].rolling(9,min_periods=5).mean()
    x["sma20"]=x["c"].rolling(20,min_periods=10).mean()
    x["sma50"]=x["c"].rolling(50,min_periods=25).mean()
    x["ema9"]=x["c"].ewm(span=9,adjust=False).mean()
    x["ema20"]=x["c"].ewm(span=20,adjust=False).mean()
    x["dist_sma9"]=x["c"]/x["sma9"]-1
    x["dist_sma20"]=x["c"]/x["sma20"]-1
    x["dist_ema9"]=x["c"]/x["ema9"]-1
    x["rsi_sma3"]=x["rsi"].rolling(3,min_periods=2).mean()
    x["rsi_sma5"]=x["rsi"].rolling(5,min_periods=3).mean()
    x["rsi_sma9"]=x["rsi"].rolling(9,min_periods=5).mean()
    x["rsi_ema5"]=x["rsi"].ewm(span=5,adjust=False).mean()
    x["rsi_vs_sma5"]=x["rsi"]/x["rsi_sma5"]-1
    x["rsi_vs_sma9"]=x["rsi"]/x["rsi_sma9"]-1
    x["rsi_sma5_slope1"]=x["rsi_sma5"].pct_change()
    x["rsi_cross_up_sma5"]=(x["rsi"]>x["rsi_sma5"])&(x["rsi"].shift(1)<=x["rsi_sma5"].shift(1))
    x["rsi_cross_dn_sma5"]=(x["rsi"]<x["rsi_sma5"])&(x["rsi"].shift(1)>=x["rsi_sma5"].shift(1))
    x["px_cross_up_ema9"]=(x["c"]>x["ema9"])&(x["c"].shift(1)<=x["ema9"].shift(1))
    x["px_cross_dn_ema9"]=(x["c"]<x["ema9"])&(x["c"].shift(1)>=x["ema9"].shift(1))
    return x

def find_recent_sequence(x):
    # Search last 90d for a large local peak, subsequent trough within 12d,
    # then rebound high within next 12d. Score emphasizes both crash and rebound.
    y=x[x["time"]>=x["time"].max()-pd.Timedelta(days=90)].copy()
    best=None
    for pi in y.index:
        p=float(x.loc[pi,"h"])
        if pi<12:continue
        # require local peak and preceding 5d acceleration
        if p < float(x.loc[max(0,pi-6):pi,"h"].max()):continue
        pre0=float(x.loc[max(0,pi-30),"c"])
        pre_gain=p/pre0-1 if pre0>0 else 0
        if pre_gain<0.20:continue
        tw=x.loc[pi+1:min(len(x)-1,pi+72)]
        if tw.empty:continue
        ti=int(tw["l"].idxmin()); trough=float(x.loc[ti,"l"])
        dd=trough/p-1
        if dd>-0.20:continue
        rw=x.loc[ti+1:min(len(x)-1,ti+72)]
        if rw.empty:continue
        ri=int(rw["h"].idxmax()); rebound=float(x.loc[ri,"h"])
        rb=rebound/trough-1
        score=abs(dd)+rb+0.25*pre_gain
        if best is None or score>best["score"]:
            best={"pi":pi,"ti":ti,"ri":ri,"peak":p,"trough":trough,"rebound":rebound,
                  "dd":dd,"rb":rb,"pre_gain":pre_gain,"score":score}
    return best

def first_after(x,start_idx,end_idx,col):
    for j in range(start_idx+1,min(len(x),end_idx+1)):
        if bool(x.loc[j,col]):return j
    return None

def fmtrow(x,i):
    r=x.loc[i]
    return {
        "time":str(r["time"]),
        "close":float(r["c"]),
        "high":float(r["h"]),
        "low":float(r["l"]),
        "rsi":float(r["rsi"]) if pd.notna(r["rsi"]) else np.nan,
        "rsi_pct1":float(r["rsi_pct1"]) if pd.notna(r["rsi_pct1"]) else np.nan,
        "rsi_pct3":float(r["rsi_pct3"]) if pd.notna(r["rsi_pct3"]) else np.nan,
        "rsi_accel":float(r["rsi_accel"]) if pd.notna(r["rsi_accel"]) else np.nan,
        "rsi_vs_sma5":float(r["rsi_vs_sma5"]) if pd.notna(r["rsi_vs_sma5"]) else np.nan,
        "rsi_vs_sma9":float(r["rsi_vs_sma9"]) if pd.notna(r["rsi_vs_sma9"]) else np.nan,
        "dist_sma9":float(r["dist_sma9"]) if pd.notna(r["dist_sma9"]) else np.nan,
        "dist_sma20":float(r["dist_sma20"]) if pd.notna(r["dist_sma20"]) else np.nan,
        "dist_ema9":float(r["dist_ema9"]) if pd.notna(r["dist_ema9"]) else np.nan,
    }

def main():
    out=Path("crypto/research/results_qnt_sma_rsi_case");out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-qnt-research/1.0"
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    x=prep(fetch(s,"QNTUSDT",end-timedelta(days=180),end))
    if x.empty:raise SystemExit("No QNT data")
    seq=find_recent_sequence(x)
    if not seq:raise SystemExit("No qualifying recent QNT sequence")

    pi,ti,ri=seq["pi"],seq["ti"],seq["ri"]
    # true candle close nearest the trough: useful for executable signal analysis.
    trough_close_i=int(x.loc[pi+1:ti,"c"].idxmin())
    rsi_peak=float(x.loc[pi:trough_close_i,"rsi"].max())
    tr=float(x.loc[trough_close_i,"rsi"])
    rsi_drop=tr/rsi_peak-1 if rsi_peak>0 else np.nan

    rsi_up=first_after(x,trough_close_i,ri+12,"rsi_cross_up_sma5")
    px_up=first_after(x,trough_close_i,ri+12,"px_cross_up_ema9")
    dual=None
    for j in range(trough_close_i+1,min(len(x),ri+13)):
        if float(x.loc[j,"rsi"])>float(x.loc[j,"rsi_sma5"]) and float(x.loc[j,"c"])>float(x.loc[j,"ema9"]):
            dual=j;break

    # Exhaustion exits: after rebound begins, first RSI-SMA5 turn after RSI has
    # recovered half its peak-to-trough loss; and first price EMA9 loss.
    loss=max(rsi_peak-tr,1e-9); recovered=False; px_reclaimed=False
    rsi_exit=px_exit=None
    for j in range(trough_close_i+1,min(len(x),trough_close_i+90)):
        if float(x.loc[j,"rsi"])>=tr+0.5*loss:recovered=True
        if float(x.loc[j,"c"])>float(x.loc[j,"ema9"]):px_reclaimed=True
        if recovered and rsi_exit is None and bool(x.loc[j,"rsi_cross_dn_sma5"]):rsi_exit=j
        if px_reclaimed and px_exit is None and bool(x.loc[j,"px_cross_dn_ema9"]):px_exit=j

    entry_i=trough_close_i+1
    entry=float(x.loc[entry_i,"o"])
    def perf(i):
        if i is None:return None
        return {"time":str(x.loc[i,"time"]),"close":float(x.loc[i,"c"]),
                "return_from_next_open":float(x.loc[i,"c"])/entry-1,
                "bars_after_trough_close":int(i-trough_close_i)}

    rows=[]
    for name,i in [("peak",pi),("trough_low",ti),("trough_close",trough_close_i),
                   ("rsi_cross_up_sma5",rsi_up),("price_cross_up_ema9",px_up),
                   ("dual_reclaim",dual),("rebound_high",ri),("rsi_exit",rsi_exit),("price_exit",px_exit)]:
        if i is not None:
            q=fmtrow(x,i);q["marker"]=name;rows.append(q)
    pd.DataFrame(rows).to_csv(out/"qnt_turning_points.csv",index=False)
    x.to_csv(out/"qnt_4h_features.csv",index=False)

    lines=[
        "QNT 4H SMA + RSI-SMA CASE STUDY",
        "",
        "Peak: %s high=%.4f"%(x.loc[pi,"time"],seq["peak"]),
        "Trough: %s low=%.4f"%(x.loc[ti,"time"],seq["trough"]),
        "Peak-to-trough: %.2f%%"%(seq["dd"]*100),
        "Subsequent rebound high: %s high=%.4f"%(x.loc[ri,"time"],seq["rebound"]),
        "Trough-to-rebound: %.2f%%"%(seq["rb"]*100),
        "Pre-peak acceleration: %.2f%%"%(seq["pre_gain"]*100),
        "",
        "TRIGGER / MOMENTUM RESET",
        "Trough-close candle: "+str(fmtrow(x,trough_close_i)),
        "RSI peak in collapse window: %.4f"%rsi_peak,
        "RSI at trough-close: %.4f"%tr,
        "RSI percentage collapse: %.2f%%"%(rsi_drop*100),
        "",
        "ENTRY TIMING CANDIDATES",
        "RSI crosses above RSI-SMA5: "+str(perf(rsi_up)),
        "Price crosses above EMA9: "+str(perf(px_up)),
        "Both RSI>RSI-SMA5 and price>EMA9: "+str(perf(dual)),
        "",
        "EXIT TIMING CANDIDATES",
        "RSI crosses below RSI-SMA5 after >=50% RSI recovery: "+str(perf(rsi_exit)),
        "Price loses EMA9 after reclaim: "+str(perf(px_exit)),
        "",
        "Research implication: compare immediate next-open entry against RSI-leading-price, price-EMA9 reclaim, and dual-reclaim entries. For exits, compare RSI-SMA5 rollover versus price EMA9 loss so the strategy can let the rebound run without waiting for a fixed RSI 70/80 threshold."
    ]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print("\n".join(lines))

if __name__=="__main__":
    main()
