#!/usr/bin/env python3
"""
4H lower-high / second-dump / RSI<=30 rebound research on Binance.

Pattern hypothesis:
1) Established downtrend.
2) A first relief pump off a local low.
3) That pump makes a LOWER HIGH versus the preceding swing high.
4) Price dumps again.
5) 4H RSI reaches <=30 on the second dump.
6) Enter next 4H open and test +5%/+10%, target-before-stop, and RSI/MA refinements.

Research-only. No live orders.
"""
from __future__ import annotations
import argparse, math, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests

API="https://data-api.binance.vision"
STABLE={"USDC","FDUSD","TUSD","USDP","DAI","BUSD","USD1","EUR","TRY","BRL","GBP","AUD","JPY"}

def gj(s,path,params=None):
    last=None
    for k in range(6):
        try:
            r=s.get(API+path,params=params,timeout=25)
            if r.ok:return r.json()
            last=RuntimeError("HTTP %s %s"%(r.status_code,r.text[:180]))
        except Exception as e:last=e
        time.sleep(min(8,1.5**k))
    raise RuntimeError(str(last))

def universe(s,n):
    info=gj(s,"/api/v3/exchangeInfo");tick=gj(s,"/api/v3/ticker/24hr")
    qv={x["symbol"]:float(x.get("quoteVolume") or 0) for x in tick if isinstance(x,dict) and "symbol" in x}
    syms=[]
    for x in info.get("symbols",[]):
        if x.get("status")!="TRADING" or x.get("quoteAsset")!="USDT":continue
        b=x.get("baseAsset","")
        if b in STABLE or b.startswith("USD") or b.startswith("1000") or any(b.endswith(z) for z in ("UP","DOWN","BULL","BEAR")):continue
        syms.append(x["symbol"])
    syms.sort(key=lambda z:qv.get(z,0),reverse=True)
    # Force SKYUSDT for the user's diagnostic example.
    if "SKYUSDT" in [x.get("symbol") for x in info.get("symbols",[])] and "SKYUSDT" not in syms[:n]:
        return ["SKYUSDT"]+syms[:max(0,n-1)]
    return syms[:n]

def fetch(s,sym,start,end):
    cur=int(start.timestamp()*1000);stop=int(end.timestamp()*1000);rows=[];step=4*3600*1000
    while cur<stop:
        b=gj(s,"/api/v3/klines",{"symbol":sym,"interval":"4h","startTime":cur,"endTime":stop,"limit":1000})
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
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep(x):
    x=x.copy()
    x["rsi"]=rsi(x["c"])
    x["rsi_pct1"]=x["rsi"].pct_change()
    x["rsi_pct3"]=x["rsi"]/x["rsi"].shift(3)-1
    x["rsi_delta"]=x["rsi"].diff()
    x["rsi_accel"]=x["rsi_delta"].diff()
    x["rsi_sma5"]=x["rsi"].rolling(5,min_periods=3).mean()
    x["rsi_vs_sma5"]=x["rsi"]/x["rsi_sma5"]-1
    x["sma20"]=x["c"].rolling(20,min_periods=10).mean()
    x["sma50"]=x["c"].rolling(50,min_periods=25).mean()
    x["ema9"]=x["c"].ewm(span=9,adjust=False).mean()
    x["sma50_slope6"]=x["sma50"]/x["sma50"].shift(6)-1
    x["dist_sma20"]=x["c"]/x["sma20"]-1
    x["dist_sma50"]=x["c"]/x["sma50"]-1
    x["dist_ema9"]=x["c"]/x["ema9"]-1
    x["range"]=(x["h"]-x["l"]).replace(0,np.nan)
    x["close_location"]=(x["c"]-x["l"])/x["range"]
    x["lower_wick"]=(np.minimum(x["o"],x["c"])-x["l"])/x["range"]
    x["vol_med20"]=x["v"].rolling(20,min_periods=10).median()
    x["vol_ratio20"]=x["v"]/x["vol_med20"].replace(0,np.nan)
    return x

def local_low_idx(x,start,end):
    if end<start:return None
    s=x.loc[start:end,"l"]
    return int(s.idxmin()) if len(s) else None

def local_peak_idx(x,start,end):
    if end<start:return None
    s=x.loc[start:end,"h"]
    return int(s.idxmax()) if len(s) else None

def find_event(x,t,
               rsi_max=30,
               min_pump=0.08,
               min_second_dump=0.08,
               lower_high_margin=0.00,
               require_undercut=False):
    """
    At current index t (all information available by this bar close):
      - downtrend established before relief pump
      - find first local low in prior 36 bars
      - relief peak after low but before current bar
      - peak is below prior 30-bar high = lower high
      - current bar is a second dump with RSI <= rsi_max
    """
    if t<90 or pd.isna(x.loc[t,"rsi"]) or float(x.loc[t,"rsi"])>rsi_max:return None

    # Search candidate first lows 6-30 bars behind current bar.
    lo_start=max(55,t-36);lo_end=t-6
    if lo_end<=lo_start:return None
    l1=local_low_idx(x,lo_start,lo_end)
    if l1 is None or l1+2>=t:return None

    # Downtrend must be visible AT the first low, not inferred later.
    if pd.isna(x.loc[l1,"sma50"]) or pd.isna(x.loc[l1,"sma50_slope6"]):return None
    downtrend=(float(x.loc[l1,"c"])<float(x.loc[l1,"sma50"])
               and float(x.loc[l1,"sma20"])<float(x.loc[l1,"sma50"])
               and float(x.loc[l1,"sma50_slope6"])<0)
    if not downtrend:return None

    # Relief pump peak after the low and at least 2 bars before current trigger.
    p1=local_peak_idx(x,l1+1,t-2)
    if p1 is None:return None
    low1=float(x.loc[l1,"l"]);peak=float(x.loc[p1,"h"])
    pump=peak/low1-1
    if pump<min_pump:return None

    # Preceding swing high known before the first low.
    ph_start=max(0,l1-36);ph_end=l1-2
    if ph_end<=ph_start:return None
    prior_high=float(x.loc[ph_start:ph_end,"h"].max())
    if not np.isfinite(prior_high) or prior_high<=0:return None
    lower_high=peak/prior_high-1
    if peak>prior_high*(1-lower_high_margin):return None

    # Current trigger must occur after the relief peak and represent a renewed dump.
    if t<=p1:return None
    second_dump=float(x.loc[t,"c"])/peak-1
    if second_dump>-min_second_dump:return None
    if require_undercut and float(x.loc[t,"l"])>low1:return None

    # Avoid repeated triggers during same oversold sequence; caller de-dupes.
    return {
        "first_low_idx":l1,"pump_peak_idx":p1,
        "first_low":low1,"pump_peak":peak,"prior_high":prior_high,
        "pump_pct":pump,"lower_high_pct":lower_high,
        "second_dump_pct":second_dump,
        "undercut_first_low":float(x.loc[t,"l"])<=low1,
    }

def outcome(x,t):
    if t+1>=len(x):return None
    entry=float(x.loc[t+1,"o"])
    if entry<=0:return None
    f=x.iloc[t+1:min(len(x),t+1+30)]
    if f.empty:return None
    out={"entry":entry,"entry_time":x.loc[t+1,"time"],
         "mfe5d":float(f["h"].max()/entry-1),"mae5d":float(f["l"].min()/entry-1)}
    out["hit5"]=out["mfe5d"]>=.05;out["hit10"]=out["mfe5d"]>=.10
    for stop in (.05,.075,.10):
        tk=sk=None
        for k,(_,r) in enumerate(f.iterrows()):
            if tk is None and float(r["h"])>=entry*1.05:tk=k
            if sk is None and float(r["l"])<=entry*(1-stop):sk=k
        out[f"t5_s{stop}"]=tk is not None and (sk is None or tk<sk)
    return out

def scan_symbol(x,sym):
    rows=[];last_trigger=None
    variants=[
        (30,.08,.08,0.00,False),
        (30,.10,.10,0.00,False),
        (30,.08,.10,0.00,True),
        (32,.08,.08,0.00,False),
        (28,.08,.08,0.00,False),
    ]
    for params in variants:
        rmax,mp,md,lm,und=params
        last=None
        for t in range(90,len(x)-31):
            ev=find_event(x,t,rmax,mp,md,lm,und)
            if ev is None:continue
            # one event per variant per 5-day oversold sequence
            if last is not None and x.loc[t,"time"]-last<pd.Timedelta(days=5):continue
            o=outcome(x,t)
            if o is None:continue
            rec={
                "symbol":sym,
                "variant":f"rsi{rmax}_pump{int(mp*100)}_dump{int(md*100)}_"+("undercut" if und else "any"),
                "trigger_time":x.loc[t,"time"],
                "trigger_close":float(x.loc[t,"c"]),
                "trigger_low":float(x.loc[t,"l"]),
                "trigger_rsi":float(x.loc[t,"rsi"]),
                "rsi_pct1":float(x.loc[t,"rsi_pct1"]) if pd.notna(x.loc[t,"rsi_pct1"]) else np.nan,
                "rsi_pct3":float(x.loc[t,"rsi_pct3"]) if pd.notna(x.loc[t,"rsi_pct3"]) else np.nan,
                "rsi_accel":float(x.loc[t,"rsi_accel"]) if pd.notna(x.loc[t,"rsi_accel"]) else np.nan,
                "rsi_vs_sma5":float(x.loc[t,"rsi_vs_sma5"]) if pd.notna(x.loc[t,"rsi_vs_sma5"]) else np.nan,
                "dist_sma20":float(x.loc[t,"dist_sma20"]) if pd.notna(x.loc[t,"dist_sma20"]) else np.nan,
                "dist_sma50":float(x.loc[t,"dist_sma50"]) if pd.notna(x.loc[t,"dist_sma50"]) else np.nan,
                "dist_ema9":float(x.loc[t,"dist_ema9"]) if pd.notna(x.loc[t,"dist_ema9"]) else np.nan,
                "sma50_slope6":float(x.loc[t,"sma50_slope6"]) if pd.notna(x.loc[t,"sma50_slope6"]) else np.nan,
                "close_location":float(x.loc[t,"close_location"]) if pd.notna(x.loc[t,"close_location"]) else np.nan,
                "lower_wick":float(x.loc[t,"lower_wick"]) if pd.notna(x.loc[t,"lower_wick"]) else np.nan,
                "vol_ratio20":float(x.loc[t,"vol_ratio20"]) if pd.notna(x.loc[t,"vol_ratio20"]) else np.nan,
                **ev,**o
            }
            rows.append(rec);last=x.loc[t,"time"]
    return rows

def summarize(e):
    rows=[]
    for v,g in e.groupby("variant"):
        g=g.sort_values("entry_time");cut=max(1,int(len(g)*.60));tr=g.iloc[:cut];ho=g.iloc[cut:]
        rows.append({
            "variant":v,"n":len(g),"symbols":g["symbol"].nunique(),
            "hold_n":len(ho),"hold_hit5":float(ho["hit5"].mean()) if len(ho) else np.nan,
            "hold_hit10":float(ho["hit10"].mean()) if len(ho) else np.nan,
            "hold_t5_s5":float(ho["t5_s0.05"].mean()) if len(ho) else np.nan,
            "hold_t5_s7p5":float(ho["t5_s0.075"].mean()) if len(ho) else np.nan,
            "hold_t5_s10":float(ho["t5_s0.1"].mean()) if len(ho) else np.nan,
            "hold_median_mfe":float(ho["mfe5d"].median()) if len(ho) else np.nan,
            "hold_median_mae":float(ho["mae5d"].median()) if len(ho) else np.nan,
        })
    return pd.DataFrame(rows).sort_values(["hold_hit5","hold_n"],ascending=[False,False])

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--symbols",type=int,default=120);ap.add_argument("--days",type=int,default=900);ap.add_argument("--out",default="crypto/research/results_lower_high_rsi30_binance")
    args=ap.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-lower-high-rsi30/1.0"
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0);start=end-timedelta(days=args.days)
    rows=[];cov=[]
    for i,sym in enumerate(universe(s,args.symbols),1):
        try:
            x=prep(fetch(s,sym,start-timedelta(days=60),end))
            rr=scan_symbol(x,sym);rows.extend(rr);cov.append((sym,len(x),len(rr)))
            print(i,sym,len(rr))
        except Exception as ex:
            cov.append((sym,0,0));print("ERR",sym,ex)
    e=pd.DataFrame(rows);e.to_csv(out/"events.csv",index=False)
    pd.DataFrame(cov,columns=["symbol","bars","events"]).to_csv(out/"coverage.csv",index=False)
    if e.empty:
        (out/"REPORT.txt").write_text("No events\n");raise SystemExit(2)
    z=summarize(e);z.to_csv(out/"summary.csv",index=False)
    sky=e[e["symbol"]=="SKYUSDT"].sort_values("trigger_time")
    sky.to_csv(out/"skyusdt_events.csv",index=False)
    lines=[
        "LOWER-HIGH / SECOND-DUMP / RSI<=30 RESEARCH — BINANCE 4H",
        "",
        z.to_string(index=False),
        "",
        "SKYUSDT DIAGNOSTIC EVENTS",
        sky.to_string(index=False) if len(sky) else "No SKYUSDT event under current definitions.",
        "",
        "Pattern is defined entirely from information available by the trigger close; entry is next 4H open.",
    ]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n");print((out/"REPORT.txt").read_text())

if __name__=="__main__":main()
