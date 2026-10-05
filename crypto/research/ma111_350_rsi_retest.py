#!/usr/bin/env python3
"""
4H MA111 / MA350 breakout -> first support retest research, with RSI context.

Primary interpretation from the user:
- token trades below the 4H SMA,
- closes above it,
- later returns to the SMA for the FIRST support test,
- the candle holds/reclaims the SMA,
- enter after support is confirmed and hold for the rebound.

We compare SMA111 and SMA350 under identical rules and also test RSI behavior:
- absolute RSI level,
- RSI bottom/turn at support,
- higher RSI over consecutive 4H bars,
- RSI recovery from its pullback low,
- RSI vs its short moving averages,
- bullish RSI/price divergence at the MA,
- RSI trend while price is sitting on support.

Entry modes:
- confirmed: first MA touch closes >= MA; enter next 4H open.
- reclaim: first touch closes slightly below MA (<=1.5%) and the next 4H
  candle closes back above MA; enter following 4H open.
- touch: buy exactly at the MA on first touch regardless of candle close.
  Same-touch-candle stops count; same-candle targets do not count because their
  timing relative to the touch cannot be proven from 4H OHLC.

Discovery:
- Hyperliquid primary perps first.
- Earliest 60% training / latest 40% untouched holdout.
- Structural rules selected on Hyperliquid train only.
- RSI filters selected on the same Hyperliquid train only.
- Exact frozen variants then evaluated on Binance USDT spot.
- No live promotion.

Targets:
+5%, +7.5%, +10%, +15%, measured over 5d and 10d.
Economics:
target / -7.5% stop / 5d timeout.
HL cost assumption = 0.045% taker + 0.10% slippage per side.
Binance = 0.070% fee + 0.10% slippage per side.
Funding is excluded here and becomes mandatory only if a candidate survives.
"""
from __future__ import annotations

import argparse, itertools, json, math, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from math import sqrt

import numpy as np
import pandas as pd
import requests

HL_API="https://api.hyperliquid.xyz/info"
BN_API="https://data-api.binance.vision"
OUT=Path("crypto/research/results_ma111_350_rsi_retest")

MA_LENGTHS=(111,350)
TARGETS=(.05,.075,.10,.15)
STOP=.075
HL_RT=2*(.00045+.0010)
BN_RT=2*(.00070+.0010)

# Small predeclared structural grid.
BELOW_FRACS=(.75,.90)
BREAK_BUFFERS=(0.0,.01)
EXTENSIONS=(0.0,.03,.05,.08)
MAX_DELAYS=(12,24,48)
SLOPE_REQS=("any","nonnegative")
MODES=("confirmed","reclaim","touch")

def rsi(c,p=14):
    d=c.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep(x):
    x=x.copy().sort_values("time").drop_duplicates("time").reset_index(drop=True)
    for L in MA_LENGTHS:
        x[f"sma{L}"]=x["c"].rolling(L,min_periods=L).mean()
        # TradingView's optional SMA-5 smoothing of the SMA is recorded as
        # context because it is visible in the user's indicator settings.
        x[f"sma{L}_sm5"]=x[f"sma{L}"].rolling(5,min_periods=3).mean()
        x[f"sma{L}_slope6"]=x[f"sma{L}"]/x[f"sma{L}"].shift(6)-1
        x[f"sma{L}_slope18"]=x[f"sma{L}"]/x[f"sma{L}"].shift(18)-1
        x[f"dist_sma{L}"]=x["c"]/x[f"sma{L}"]-1

    x["rsi14"]=rsi(x["c"])
    x["rsi_delta1"]=x["rsi14"].diff()
    x["rsi_delta2"]=x["rsi14"].diff(2)
    x["rsi_delta3"]=x["rsi14"].diff(3)
    x["rsi_sma3"]=x["rsi14"].rolling(3,min_periods=2).mean()
    x["rsi_sma5"]=x["rsi14"].rolling(5,min_periods=3).mean()
    x["rsi_vs_sma3"]=x["rsi14"]/x["rsi_sma3"]-1
    x["rsi_vs_sma5"]=x["rsi14"]/x["rsi_sma5"]-1
    x["rsi_sma3_slope1"]=x["rsi_sma3"].diff()

    prev6=x["rsi14"].shift(1).rolling(6,min_periods=3).min()
    prev12=x["rsi14"].shift(1).rolling(12,min_periods=6).min()
    x["rsi_above_prev6_low"]=x["rsi14"]-prev6
    x["rsi_above_prev12_low"]=x["rsi14"]-prev12
    x["rsi_up1"]=x["rsi_delta1"]>0
    x["rsi_up2"]=(x["rsi_delta1"]>0)&(x["rsi_delta1"].shift(1)>0)
    # A usable "bottom formed" definition known at the support close:
    # the previous bar was near the prior 6-bar RSI floor and current RSI
    # has turned at least 2 points higher.
    prior_floor=x["rsi14"].shift(2).rolling(6,min_periods=3).min()
    x["rsi_bottom_turn6"]=(
        (x["rsi14"].shift(1)<=prior_floor+1.0)
        &(x["rsi14"]>=x["rsi14"].shift(1)+2.0)
    )
    # Bullish divergence context: price revisits/undercuts its recent close low
    # while RSI holds at least 3 points above its prior 6-bar RSI floor.
    prev_price6=x["c"].shift(1).rolling(6,min_periods=3).min()
    x["rsi_bull_div6"]=(x["c"]<=prev_price6*1.01)&(x["rsi_above_prev6_low"]>=3.0)

    x["ret1"]=x["c"].pct_change()
    x["ret3"]=x["c"]/x["c"].shift(3)-1
    x["vol_med20"]=x["v"].rolling(20,min_periods=10).median()
    x["vol_ratio20"]=x["v"]/x["vol_med20"].replace(0,np.nan)
    rng=(x["h"]-x["l"]).replace(0,np.nan)
    x["range_pct"]=rng/x["c"].replace(0,np.nan)
    x["lower_wick"]=(np.minimum(x["o"],x["c"])-x["l"])/rng
    x["close_location"]=(x["c"]-x["l"])/rng
    return x

def outcome(x,entry_idx,entry,mode,touch_idx=None):
    if entry_idx>=len(x) or not np.isfinite(entry) or entry<=0:return None
    # For touch entry, a stop in the touch candle is definitely after the MA
    # fill on the way down. A target in that same candle is not provably after
    # the fill, so it is intentionally ignored.
    immediate_stop=False
    if mode=="touch" and touch_idx is not None:
        immediate_stop=float(x.loc[touch_idx,"l"])<=entry*(1-STOP)

    f5=x.iloc[entry_idx:min(len(x),entry_idx+30)]
    f10=x.iloc[entry_idx:min(len(x),entry_idx+60)]
    if len(f5)<24:return None
    out={"entry":float(entry),"entry_time":x.iloc[entry_idx]["time"],"entry_mode":mode}
    for label,f in (("5d",f5),("10d",f10)):
        if f.empty:continue
        mfe=float(f["h"].max()/entry-1)
        mae_future=float(f["l"].min()/entry-1)
        mae=min(mae_future,-STOP if immediate_stop else mae_future)
        out[f"mfe_{label}"]=mfe
        out[f"mae_{label}"]=mae
        out[f"close_ret_{label}"]=float(f.iloc[-1]["c"]/entry-1)
        for t in TARGETS:
            key=str(t*100).replace(".","p")
            out[f"hit{key}_{label}"]=mfe>=t and not immediate_stop

    for t in TARGETS:
        key=str(t*100).replace(".","p")
        if immediate_stop:
            out[f"t{key}_before_s7p5_5d"]=False
            continue
        tk=sk=None
        for k,(_,r) in enumerate(f5.iterrows()):
            if tk is None and float(r["h"])>=entry*(1+t):tk=k
            if sk is None and float(r["l"])<=entry*(1-STOP):sk=k
        out[f"t{key}_before_s7p5_5d"]=tk is not None and (sk is None or tk<sk)
    out["touch_candle_immediate_stop"]=immediate_stop
    return out

def support_features(x,idx,breakout_idx,ma_len):
    r=x.loc[idx];ma=f"sma{ma_len}"
    pull=x.loc[breakout_idx+1:idx]
    pull_rsi_min=float(pull["rsi14"].min()) if len(pull) and pull["rsi14"].notna().any() else np.nan
    return {
        "signal_idx":int(idx),
        "signal_time":r["time"],
        "signal_close":float(r["c"]),
        "signal_ma":float(r[ma]),
        "signal_close_vs_ma":float(r["c"])/float(r[ma])-1,
        "rsi14":float(r["rsi14"]) if pd.notna(r["rsi14"]) else np.nan,
        "rsi_delta1":float(r["rsi_delta1"]) if pd.notna(r["rsi_delta1"]) else np.nan,
        "rsi_delta2":float(r["rsi_delta2"]) if pd.notna(r["rsi_delta2"]) else np.nan,
        "rsi_delta3":float(r["rsi_delta3"]) if pd.notna(r["rsi_delta3"]) else np.nan,
        "rsi_vs_sma3":float(r["rsi_vs_sma3"]) if pd.notna(r["rsi_vs_sma3"]) else np.nan,
        "rsi_vs_sma5":float(r["rsi_vs_sma5"]) if pd.notna(r["rsi_vs_sma5"]) else np.nan,
        "rsi_sma3_slope1":float(r["rsi_sma3_slope1"]) if pd.notna(r["rsi_sma3_slope1"]) else np.nan,
        "rsi_above_prev6_low":float(r["rsi_above_prev6_low"]) if pd.notna(r["rsi_above_prev6_low"]) else np.nan,
        "rsi_above_prev12_low":float(r["rsi_above_prev12_low"]) if pd.notna(r["rsi_above_prev12_low"]) else np.nan,
        "rsi_up1":bool(r["rsi_up1"]),
        "rsi_up2":bool(r["rsi_up2"]),
        "rsi_bottom_turn6":bool(r["rsi_bottom_turn6"]),
        "rsi_bull_div6":bool(r["rsi_bull_div6"]),
        "rsi_recovery_from_pullback_min":float(r["rsi14"])-pull_rsi_min if np.isfinite(pull_rsi_min) and pd.notna(r["rsi14"]) else np.nan,
        "vol_ratio20":float(r["vol_ratio20"]) if pd.notna(r["vol_ratio20"]) else np.nan,
        "lower_wick":float(r["lower_wick"]) if pd.notna(r["lower_wick"]) else np.nan,
        "close_location":float(r["close_location"]) if pd.notna(r["close_location"]) else np.nan,
    }

def scan_ma_events(x,coin,venue,ma_len):
    ma=f"sma{ma_len}";sm=f"sma{ma_len}_sm5"
    s6=f"sma{ma_len}_slope6";s18=f"sma{ma_len}_slope18"
    rows=[];i=max(ma_len+30,140);last_break=None
    n=len(x)
    while i<n-32:
        if pd.isna(x.loc[i,ma]) or pd.isna(x.loc[i-1,ma]):
            i+=1;continue
        crossed=float(x.loc[i-1,"c"])<=float(x.loc[i-1,ma]) and float(x.loc[i,"c"])>float(x.loc[i,ma])
        if not crossed:
            i+=1;continue
        # Avoid recounting repeated chatter around the line.
        if last_break is not None and x.loc[i,"time"]-last_break<pd.Timedelta(days=5):
            i+=1;continue
        prev24=x.loc[i-24:i-1]
        if len(prev24)<24:
            i+=1;continue
        below_frac=float((prev24["c"]<prev24[ma]).mean())
        breakout_buffer=float(x.loc[i,"c"])/float(x.loc[i,ma])-1
        max_ext=breakout_buffer
        first_touch=None
        # We generate the raw first exact touch within 48 bars, then structural
        # variants filter delay/extension later. The FIRST touch is sacred.
        for j in range(i+1,min(n-31,i+49)):
            if pd.isna(x.loc[j,ma]):continue
            mav=float(x.loc[j,ma])
            max_ext=max(max_ext,float(x.loc[j,"h"])/mav-1)
            exact_touch=float(x.loc[j,"l"])<=mav<=float(x.loc[j,"h"])
            if exact_touch:
                first_touch=j;break
        if first_touch is None:
            i+=1;continue

        j=first_touch
        touch_close=float(x.loc[j,"c"])
        touch_ma=float(x.loc[j,ma])
        touch_close_vs=touch_close/touch_ma-1
        common={
            "venue":venue,"coin":coin,"ma_len":ma_len,
            "breakout_idx":int(i),"breakout_time":x.loc[i,"time"],
            "breakout_close":float(x.loc[i,"c"]),"breakout_ma":float(x.loc[i,ma]),
            "below_frac_24":below_frac,"breakout_buffer":breakout_buffer,
            "retest_idx":int(j),"retest_time":x.loc[j,"time"],
            "retest_delay_bars":int(j-i),
            "extension_before_retest":max_ext,
            "retest_close":touch_close,"retest_ma":touch_ma,
            "retest_close_vs_ma":touch_close_vs,
            "ma_slope6":float(x.loc[j,s6]) if pd.notna(x.loc[j,s6]) else np.nan,
            "ma_slope18":float(x.loc[j,s18]) if pd.notna(x.loc[j,s18]) else np.nan,
            "ma_vs_smoothed5":float(x.loc[j,ma])/float(x.loc[j,sm])-1 if pd.notna(x.loc[j,sm]) and float(x.loc[j,sm])>0 else np.nan,
        }

        # Aggressive exact-touch entry: always included, including failed support.
        if j+1<n:
            o=outcome(x,j+1,touch_ma,"touch",touch_idx=j)
            if o:
                rows.append({**common,"mode":"touch","support_confirmed":touch_close>=touch_ma,
                             **support_features(x,j,i,ma_len),**o})

        # Confirmed support: first touch closes at/above the MA.
        if touch_close>=touch_ma and j+1<n:
            o=outcome(x,j+1,float(x.loc[j+1,"o"]),"confirmed")
            if o:
                rows.append({**common,"mode":"confirmed","support_confirmed":True,
                             **support_features(x,j,i,ma_len),**o})

        # Reclaim: first touch closes slightly below the MA, then next closed
        # 4H candle recovers above the contemporaneous MA. Enter one bar later.
        if -0.015<=touch_close_vs<0 and j+2<n and pd.notna(x.loc[j+1,ma]):
            if float(x.loc[j+1,"c"])>=float(x.loc[j+1,ma]):
                o=outcome(x,j+2,float(x.loc[j+2,"o"]),"reclaim")
                if o:
                    rec={**common,"mode":"reclaim","support_confirmed":True,
                         "reclaim_time":x.loc[j+1,"time"],
                         "reclaim_close_vs_ma":float(x.loc[j+1,"c"])/float(x.loc[j+1,ma])-1,
                         **support_features(x,j+1,i,ma_len),**o}
                    rows.append(rec)
        last_break=x.loc[i,"time"]
        i=j+1
    return rows

def hl_post(s,p,tries=8):
    last=None
    for k in range(tries):
        try:
            r=s.post(HL_API,json=p,timeout=40)
            if r.status_code==200:return r.json()
            last=RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
        except Exception as e:last=e
        time.sleep(min(10,1.2*(k+1)))
    raise RuntimeError(str(last))

def hl_universe(s):
    meta,ctx=hl_post(s,{"type":"metaAndAssetCtxs"})
    return [str(u["name"]) for u in meta.get("universe",[]) if u.get("name") and not u.get("isDelisted")]

def hl_fetch(s,coin,start,end):
    rows=hl_post(s,{"type":"candleSnapshot","req":{"coin":coin,"interval":"4h",
        "startTime":int(start.timestamp()*1000),"endTime":int(end.timestamp()*1000)}})
    a=[]
    for r in rows or []:
        try:a.append({"time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),
                      "o":float(r["o"]),"h":float(r["h"]),"l":float(r["l"]),
                      "c":float(r["c"]),"v":float(r.get("v",0))})
        except Exception:pass
    return pd.DataFrame(a) if a else pd.DataFrame()

def bn_get(s,path,params=None,tries=8):
    last=None
    for k in range(tries):
        try:
            r=s.get(BN_API+path,params=params,timeout=35)
            if r.ok:return r.json()
            last=RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
        except Exception as e:last=e
        time.sleep(min(10,1.2*(k+1)))
    raise RuntimeError(str(last))

def bn_universe(s,n=160):
    info=bn_get(s,"/api/v3/exchangeInfo")
    tick=bn_get(s,"/api/v3/ticker/24hr")
    qv={x["symbol"]:float(x.get("quoteVolume") or 0) for x in tick if isinstance(x,dict) and x.get("symbol")}
    stable={"USDC","FDUSD","TUSD","USDP","DAI","BUSD","USD1"}
    all_usdt=[]
    for x in info.get("symbols",[]):
        if x.get("status")!="TRADING" or x.get("quoteAsset")!="USDT":continue
        b=str(x.get("baseAsset") or "")
        if b in stable or b.startswith("USD"):continue
        if any(b.endswith(z) for z in ("UP","DOWN","BULL","BEAR")):continue
        all_usdt.append(x["symbol"])
    all_usdt=sorted(set(all_usdt),key=lambda z:qv.get(z,0),reverse=True)
    out=all_usdt[:n]
    # Force the user's CHIP example into the external panel when Binance lists it.
    if "CHIPUSDT" in all_usdt and "CHIPUSDT" not in out:
        out.append("CHIPUSDT")
    return out

def bn_fetch(s,symbol,start,end):
    cur=int(start.timestamp()*1000);stop=int(end.timestamp()*1000);rows=[];step=4*3600*1000
    while cur<stop:
        batch=bn_get(s,"/api/v3/klines",{"symbol":symbol,"interval":"4h","startTime":cur,"endTime":stop,"limit":1000})
        if not batch:break
        rows.extend(batch)
        nxt=int(batch[-1][0])+step
        if nxt<=cur:break
        cur=nxt
        if len(batch)<1000:break
        time.sleep(.02)
    a=[]
    for z in rows:
        try:a.append({"time":pd.to_datetime(int(z[0]),unit="ms",utc=True),
                      "o":float(z[1]),"h":float(z[2]),"l":float(z[3]),"c":float(z[4]),"v":float(z[5])})
        except Exception:pass
    return pd.DataFrame(a) if a else pd.DataFrame()

def scan(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-ma111-350-rsi-retest/1.0"
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    start=end-timedelta(days=args.days)
    if args.venue=="hl":
        coins=hl_universe(s);fetch=hl_fetch;venue="Hyperliquid primary perps"
    else:
        coins=bn_universe(s,args.binance_symbols);fetch=bn_fetch;venue="Binance USDT spot"
    selected=coins[args.shard_index::args.shard_count]
    rows=[];cov=[]
    for pos,coin in enumerate(selected,1):
        try:
            x=prep(fetch(s,coin,start,end))
            if len(x)<500:
                cov.append((coin,len(x),0,"short"));continue
            rr=[]
            for L in MA_LENGTHS:rr.extend(scan_ma_events(x,coin,venue,L))
            rows.extend(rr);cov.append((coin,len(x),len(rr),"ok"))
            print(pos,len(selected),coin,len(rr))
        except Exception as e:
            cov.append((coin,0,0,"error:"+str(e)[:120]));print("ERR",coin,e)
        time.sleep(args.sleep)
    pd.DataFrame(rows).to_csv(out/f"{args.venue}_events_{args.shard_index:02d}.csv",index=False)
    pd.DataFrame(cov,columns=["coin","bars","events","status"]).to_csv(out/f"{args.venue}_coverage_{args.shard_index:02d}.csv",index=False)

def rate(s):
    q=s.dropna();return float(q.astype(bool).mean()) if len(q) else np.nan

def wilson(w,n,z=1.96):
    if n<=0:return np.nan
    p=w/n;den=1+z*z/n
    ctr=(p+z*z/(2*n))/den
    half=z*sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return ctr-half

def econ(g,target,cost):
    if g.empty:return np.nan
    k=str(target*100).replace(".","p")
    win=g[f"t{k}_before_s7p5_5d"].fillna(False).astype(bool)
    mae=pd.to_numeric(g["mae_5d"],errors="coerce")
    timeout=pd.to_numeric(g["close_ret_5d"],errors="coerce").fillna(0)
    stopped=(~win)&(mae<=-STOP)
    gross=pd.Series(np.where(win,target,np.where(stopped,-STOP,timeout)),index=g.index).clip(-STOP,target)
    return float((gross-cost).mean())

def stats(g,cost):
    row={"n":len(g)}
    if not len(g):return row
    for t in TARGETS:
        k=str(t*100).replace(".","p")
        row[f"hit{k}_5d"]=rate(g[f"hit{k}_5d"])
        row[f"hit{k}_10d"]=rate(g[f"hit{k}_10d"])
        row[f"t{k}_before_s7p5"]=rate(g[f"t{k}_before_s7p5_5d"])
        row[f"net_roi_t{k}"]=econ(g,t,cost)
    row["median_mae5d"]=float(pd.to_numeric(g["mae_5d"],errors="coerce").median())
    row["median_mfe5d"]=float(pd.to_numeric(g["mfe_5d"],errors="coerce").median())
    return row

def structural_mask(df,bf,buf,ext,delay,slope):
    m=(pd.to_numeric(df["below_frac_24"],errors="coerce")>=bf)
    m&=(pd.to_numeric(df["breakout_buffer"],errors="coerce")>=buf)
    m&=(pd.to_numeric(df["extension_before_retest"],errors="coerce")>=ext)
    m&=(pd.to_numeric(df["retest_delay_bars"],errors="coerce")<=delay)
    if slope=="nonnegative":
        m&=(pd.to_numeric(df["ma_slope6"],errors="coerce")>=0)
    return m

def structure_id(L,mode,bf,buf,ext,delay,slope):
    return f"MA{L}|{mode}|below{bf:.2f}|buf{buf:.2f}|ext{ext:.2f}|d{delay}|slope={slope}"

def rsi_specs():
    out=[]
    # LEVEL family
    for v in (30,35,40,45,50,55,60):
        out.append(("LEVEL",f"RSI<={v}",lambda d,v=v:pd.to_numeric(d["rsi14"],errors="coerce")<=v))
    for v in (30,35,40,45,50):
        out.append(("LEVEL",f"RSI>={v}",lambda d,v=v:pd.to_numeric(d["rsi14"],errors="coerce")>=v))
    # TURN / trend families
    for v in (0,2,4):
        out.append(("TURN",f"RSI delta1>={v}",lambda d,v=v:pd.to_numeric(d["rsi_delta1"],errors="coerce")>=v))
    for v in (0,3,6):
        out.append(("TREND",f"RSI delta3>={v}",lambda d,v=v:pd.to_numeric(d["rsi_delta3"],errors="coerce")>=v))
    out += [
        ("TREND","RSI up 1 bar",lambda d:d["rsi_up1"].astype(str).str.lower().isin(["true","1"])),
        ("TREND","RSI up 2 consecutive bars",lambda d:d["rsi_up2"].astype(str).str.lower().isin(["true","1"])),
        ("TREND","RSI above SMA3",lambda d:pd.to_numeric(d["rsi_vs_sma3"],errors="coerce")>=0),
        ("TREND","RSI above SMA5",lambda d:pd.to_numeric(d["rsi_vs_sma5"],errors="coerce")>=0),
        ("TREND","RSI SMA3 rising",lambda d:pd.to_numeric(d["rsi_sma3_slope1"],errors="coerce")>=0),
        ("BOTTOM","RSI bottom-turn 6-bar",lambda d:d["rsi_bottom_turn6"].astype(str).str.lower().isin(["true","1"])),
        ("DIVERGENCE","RSI bullish divergence 6-bar",lambda d:d["rsi_bull_div6"].astype(str).str.lower().isin(["true","1"])),
    ]
    for v in (1,2,3):
        out.append(("BOTTOM",f"RSI within/recovered prev6 low +{v}",
                    lambda d,v=v:pd.to_numeric(d["rsi_above_prev6_low"],errors="coerce")>=v))
    for v in (2,4,6,8):
        out.append(("BOTTOM",f"RSI recovery from pullback min >={v}",
                    lambda d,v=v:pd.to_numeric(d["rsi_recovery_from_pullback_min"],errors="coerce")>=v))
    return out

def eval_rsi_filter(train,hold,btrain,bhold,cost,base_id,L,mode):
    specs=rsi_specs()
    candidates=[]
    # Single RSI conditions.
    combos=[(s,) for s in specs]
    # Pairs only across different conceptual families.
    for a,b in itertools.combinations(specs,2):
        if a[0]!=b[0]:combos.append((a,b))
    base=stats(train,cost)
    for combo in combos:
        mt=pd.Series(True,index=train.index)
        for _,_,fn in combo:mt &= fn(train)
        gt=train[mt]
        if len(gt)<max(20,int(len(train)*.50)):continue
        st=stats(gt,cost)
        # Selection is +5-first. Require real training improvement, not just N.
        if st.get("hit5p0_5d",0)<base.get("hit5p0_5d",0)+.025:continue
        if st.get("t5p0_before_s7p5",0)<base.get("t5p0_before_s7p5",0)+.035:continue
        if st.get("net_roi_t5p0",-1)<max(base.get("net_roi_t5p0",-1),.008):continue

        mh=pd.Series(True,index=hold.index)
        for _,_,fn in combo:mh &= fn(hold)
        gh=hold[mh]

        mbt=pd.Series(True,index=btrain.index)
        mbh=pd.Series(True,index=bhold.index)
        for _,_,fn in combo:
            mbt &= fn(btrain);mbh &= fn(bhold)
        gbh=bhold[mbh]

        sh=stats(gh,HL_RT);sbh=stats(gbh,BN_RT)
        names=" AND ".join(x[1] for x in combo)
        candidates.append({
            "structure_id":base_id,"ma_len":L,"mode":mode,
            "rsi_rule":names,
            "rsi_families":"+".join(sorted(set(x[0] for x in combo))),
            "train_n":len(gt),"train_keep_fraction":len(gt)/len(train),
            "train_hit5":st.get("hit5p0_5d",np.nan),
            "train_risk":st.get("t5p0_before_s7p5",np.nan),
            "train_roi":st.get("net_roi_t5p0",np.nan),
            "hl_hold_n":len(gh),
            "hl_hold_hit5":sh.get("hit5p0_5d",np.nan),
            "hl_hold_risk":sh.get("t5p0_before_s7p5",np.nan),
            "hl_hold_roi":sh.get("net_roi_t5p0",np.nan),
            "hl_hold_hit7p5":sh.get("hit7p5_5d",np.nan),
            "hl_hold_hit10":sh.get("hit10p0_5d",np.nan),
            "hl_hold_hit15":sh.get("hit15p0_5d",np.nan),
            "bin_hold_n":len(gbh),
            "bin_hold_hit5":sbh.get("hit5p0_5d",np.nan),
            "bin_hold_risk":sbh.get("t5p0_before_s7p5",np.nan),
            "bin_hold_roi":sbh.get("net_roi_t5p0",np.nan),
            "bin_hold_hit10":sbh.get("hit10p0_5d",np.nan),
        })
    z=pd.DataFrame(candidates)
    if len(z):
        z["crossvenue_pass"]=(
            (z["hl_hold_n"]>=20)&(z["hl_hold_hit5"]>=.78)&(z["hl_hold_risk"]>=.70)&(z["hl_hold_roi"]>=.0125)
            &(z["bin_hold_n"]>=15)&(z["bin_hold_hit5"]>=.75)&(z["bin_hold_risk"]>=.67)&(z["bin_hold_roi"]>=.010)
        )
        z["score"]=(
            .25*z["hl_hold_hit5"]+.20*z["hl_hold_risk"]+.20*np.clip(z["hl_hold_roi"]/.04,-1,1)
            +.15*z["bin_hold_hit5"]+.10*z["bin_hold_risk"]+.10*np.clip(z["bin_hold_roi"]/.04,-1,1)
        )
        z=z.sort_values(["crossvenue_pass","score","hl_hold_n"],ascending=[False,False,False])
    return z

def aggregate(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    root=Path(args.input_dir)
    def cat(pat):
        fs=[]
        for p in sorted(root.rglob(pat)):
            try:
                z=pd.read_csv(p)
                if len(z):fs.append(z)
            except Exception:pass
        return pd.concat(fs,ignore_index=True) if fs else pd.DataFrame()

    hl=cat("hl_events_*.csv");bn=cat("bn_events_*.csv")
    hc=cat("hl_coverage_*.csv");bc=cat("bn_coverage_*.csv")
    for z in (hl,bn):
        if len(z):
            for c in ["breakout_time","retest_time","signal_time","entry_time","reclaim_time"]:
                if c in z:z[c]=pd.to_datetime(z[c],utc=True,errors="coerce")
    hl.to_csv(out/"hyperliquid_events.csv",index=False)
    bn.to_csv(out/"binance_events.csv",index=False)
    hc.to_csv(out/"hyperliquid_coverage.csv",index=False)
    bc.to_csv(out/"binance_coverage.csv",index=False)

    struct=[]
    for L,mode in itertools.product(MA_LENGTHS,MODES):
        hg=hl[(pd.to_numeric(hl["ma_len"],errors="coerce")==L)&(hl["mode"]==mode)].sort_values("entry_time").reset_index(drop=True)
        bg=bn[(pd.to_numeric(bn["ma_len"],errors="coerce")==L)&(bn["mode"]==mode)].sort_values("entry_time").reset_index(drop=True)
        for bf,buf,ext,delay,slope in itertools.product(BELOW_FRACS,BREAK_BUFFERS,EXTENSIONS,MAX_DELAYS,SLOPE_REQS):
            h=hg[structural_mask(hg,bf,buf,ext,delay,slope)].copy()
            b=bg[structural_mask(bg,bf,buf,ext,delay,slope)].copy()
            if len(h)<40:continue
            hc0=max(1,int(len(h)*.60));bc0=max(1,int(len(b)*.60)) if len(b) else 0
            ht,hh=h.iloc[:hc0],h.iloc[hc0:]
            bt,bh=(b.iloc[:bc0],b.iloc[bc0:]) if len(b) else (b,b)
            st=stats(ht,HL_RT);sh=stats(hh,HL_RT);sb=stats(bh,BN_RT)
            sid=structure_id(L,mode,bf,buf,ext,delay,slope)
            struct.append({
                "structure_id":sid,"ma_len":L,"mode":mode,"below_frac":bf,
                "breakout_buffer":buf,"min_extension":ext,"max_delay_bars":delay,"slope_req":slope,
                "train_n":len(ht),"train_hit5":st.get("hit5p0_5d",np.nan),
                "train_risk":st.get("t5p0_before_s7p5",np.nan),"train_roi":st.get("net_roi_t5p0",np.nan),
                "hl_hold_n":len(hh),"hl_hold_hit5":sh.get("hit5p0_5d",np.nan),
                "hl_hold_risk":sh.get("t5p0_before_s7p5",np.nan),"hl_hold_roi":sh.get("net_roi_t5p0",np.nan),
                "hl_hold_hit7p5":sh.get("hit7p5_5d",np.nan),"hl_hold_hit10":sh.get("hit10p0_5d",np.nan),
                "hl_hold_hit15":sh.get("hit15p0_5d",np.nan),
                "hl_hold_hit5_10d":sh.get("hit5p0_10d",np.nan),"hl_hold_hit10_10d":sh.get("hit10p0_10d",np.nan),
                "hl_roi_t7p5":sh.get("net_roi_t7p5",np.nan),"hl_roi_t10":sh.get("net_roi_t10p0",np.nan),"hl_roi_t15":sh.get("net_roi_t15p0",np.nan),
                "bin_hold_n":len(bh),"bin_hold_hit5":sb.get("hit5p0_5d",np.nan),
                "bin_hold_risk":sb.get("t5p0_before_s7p5",np.nan),"bin_hold_roi":sb.get("net_roi_t5p0",np.nan),
                "bin_hold_hit10":sb.get("hit10p0_5d",np.nan),
            })
    s=pd.DataFrame(struct)
    if len(s):
        s["eligible_train"]=(
            (s["train_n"]>=35)&(s["train_hit5"]>=.70)&(s["train_risk"]>=.62)&(s["train_roi"]>=.006)
        )
        s["score"]=.35*s["train_hit5"]+.30*s["train_risk"]+.25*np.clip(s["train_roi"]/.03,-1,1)+.10*np.clip(np.log1p(s["train_n"])/np.log(250),0,1)
        s=s.sort_values(["eligible_train","score","train_n"],ascending=[False,False,False])
    s.to_csv(out/"structural_variants.csv",index=False)

    # RSI search only on the best structurally selected confirmed/reclaim
    # variants. Touch-mode RSI at the candle close would be lookahead.
    rsi_frames=[]
    if len(s):
        for L in MA_LENGTHS:
            q=s[(s["ma_len"]==L)&(s["mode"].isin(["confirmed","reclaim"]))&(s["eligible_train"]==True)].head(4)
            for _,r in q.iterrows():
                hf=hl[(pd.to_numeric(hl["ma_len"],errors="coerce")==L)&(hl["mode"]==r["mode"])].sort_values("entry_time").reset_index(drop=True)
                bf=bn[(pd.to_numeric(bn["ma_len"],errors="coerce")==L)&(bn["mode"]==r["mode"])].sort_values("entry_time").reset_index(drop=True)
                hm=structural_mask(hf,r["below_frac"],r["breakout_buffer"],r["min_extension"],int(r["max_delay_bars"]),r["slope_req"])
                bm=structural_mask(bf,r["below_frac"],r["breakout_buffer"],r["min_extension"],int(r["max_delay_bars"]),r["slope_req"])
                h=hf[hm].reset_index(drop=True);b=bf[bm].reset_index(drop=True)
                hc0=max(1,int(len(h)*.60));bc0=max(1,int(len(b)*.60)) if len(b) else 0
                rt=eval_rsi_filter(h.iloc[:hc0],h.iloc[hc0:],b.iloc[:bc0],b.iloc[bc0:],HL_RT,r["structure_id"],L,r["mode"])
                if len(rt):rsi_frames.append(rt)
    rz=pd.concat(rsi_frames,ignore_index=True) if rsi_frames else pd.DataFrame()
    if len(rz):
        rz=rz.sort_values(["crossvenue_pass","score","hl_hold_n"],ascending=[False,False,False])
    rz.to_csv(out/"rsi_filter_search.csv",index=False)

    # Best baseline and best RSI-enhanced configuration per MA.
    comp=[]
    for L in MA_LENGTHS:
        bs=s[(s["ma_len"]==L)&(s["eligible_train"]==True)].head(1) if len(s) else pd.DataFrame()
        rs=rz[rz["ma_len"]==L].head(1) if len(rz) else pd.DataFrame()
        if len(bs):
            r=bs.iloc[0]
            comp.append({"ma_len":L,"type":"baseline","rule":r["structure_id"],
                         "hl_n":r["hl_hold_n"],"hl_hit5":r["hl_hold_hit5"],"hl_risk":r["hl_hold_risk"],"hl_roi":r["hl_hold_roi"],
                         "hl_hit7p5":r["hl_hold_hit7p5"],"hl_hit10":r["hl_hold_hit10"],"hl_hit15":r["hl_hold_hit15"],
                         "bin_n":r["bin_hold_n"],"bin_hit5":r["bin_hold_hit5"],"bin_risk":r["bin_hold_risk"],"bin_roi":r["bin_hold_roi"]})
        if len(rs):
            r=rs.iloc[0]
            comp.append({"ma_len":L,"type":"RSI-enhanced","rule":r["structure_id"]+" | "+r["rsi_rule"],
                         "hl_n":r["hl_hold_n"],"hl_hit5":r["hl_hold_hit5"],"hl_risk":r["hl_hold_risk"],"hl_roi":r["hl_hold_roi"],
                         "hl_hit7p5":r["hl_hold_hit7p5"],"hl_hit10":r["hl_hold_hit10"],"hl_hit15":r["hl_hold_hit15"],
                         "bin_n":r["bin_hold_n"],"bin_hit5":r["bin_hold_hit5"],"bin_risk":r["bin_hold_risk"],"bin_roi":r["bin_hold_roi"],
                         "crossvenue_pass":r["crossvenue_pass"]})
    compdf=pd.DataFrame(comp)
    compdf.to_csv(out/"best_ma111_vs_ma350.csv",index=False)

    # CHIP example diagnostic around Aug 17, 2026.
    chip=bn[(bn["coin"]=="CHIPUSDT")&
            (bn["retest_time"]>=pd.Timestamp("2026-08-10",tz="UTC"))&
            (bn["retest_time"]<=pd.Timestamp("2026-08-25",tz="UTC"))].copy() if len(bn) else pd.DataFrame()
    chip.to_csv(out/"chip_aug2026_diagnostic.csv",index=False)

    winners=rz[rz["crossvenue_pass"]==True].head(8) if len(rz) else pd.DataFrame()
    (out/"candidate_registry.json").write_text(json.dumps({
        "generated_at":pd.Timestamp.utcnow().isoformat(),
        "family":"4H SMA111/SMA350 breakout -> first support retest with RSI confirmation",
        "selection":"Hyperliquid train only; latest40 HL + Binance frozen validation",
        "candidates":winners.to_dict("records") if len(winners) else []
    },indent=2,default=str))

    lines=[
        "4H MA111 / MA350 FIRST-SUPPORT RETEST + RSI RESEARCH","",
        "Tests both MA111 and MA350 as separate method families under identical rules.",
        "RSI options include level, bottom/turn, higher 4H RSI trend, recovery from pullback minimum, RSI-MA trend, and bullish divergence.",
        "Touch entry is evaluated honestly without using support-candle-close RSI as a filter; RSI-enhanced candidates use confirmed/reclaim entries only.","",
        f"Hyperliquid raw event rows: {len(hl)} | coins scanned: {int((hc['status']=='ok').sum()) if len(hc) else 0}",
        f"Binance raw event rows: {len(bn)} | symbols scanned: {int((bc['status']=='ok').sum()) if len(bc) else 0}","",
        "BEST MA111 VS MA350",
        compdf.to_string(index=False) if len(compdf) else "none","",
        "TOP RSI-ENHANCED RULES",
        rz.head(20).to_string(index=False) if len(rz) else "none","",
        "TOP STRUCTURAL BASELINES",
        s.head(20).to_string(index=False) if len(s) else "none","",
        "CHIP AUG 2026 DIAGNOSTIC",
        chip.head(30).to_string(index=False) if len(chip) else "No qualifying CHIP event in the exact frozen raw-event definition.","",
        "CROSS-VENUE RSI SURVIVORS",
        winners.to_string(index=False) if len(winners) else "none","",
        "Any survivor still requires actual Hyperliquid funding + 1m same-bar replay + forward shadow validation before promotion."
    ]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mode",choices=["scan","aggregate"],default="scan")
    ap.add_argument("--venue",choices=["hl","bn"],default="hl")
    ap.add_argument("--days",type=int,default=600)
    ap.add_argument("--binance-symbols",type=int,default=160)
    ap.add_argument("--shard-index",type=int,default=0)
    ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--sleep",type=float,default=.05)
    ap.add_argument("--out",default="ma_retest_out")
    ap.add_argument("--input-dir",default="ma_retest_shards")
    args=ap.parse_args()
    scan(args) if args.mode=="scan" else aggregate(args)

if __name__=="__main__":
    main()
