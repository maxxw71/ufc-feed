#!/usr/bin/env python3
"""
Broad point-in-time moving-average method discovery for crypto.

Goal: explore many distinct MA mechanisms without loosening the existing
promotion standards or using future 4H information at an intrabar entry.

Venues:
- Hyperliquid primary perps = discovery venue.
- Binance USDT spot = frozen external validation venue.

Core MA structures:
A) SMA350 breakout -> first MA111 touch
B) SMA350 breakout -> first SMA350 retest
C) MA111 breakout -> first MA111 retest
D) MA111 crosses above SMA350 -> first MA111 retest
E) Established uptrend -> pullback to MA111
F) Established uptrend -> deep pullback to SMA350
G) MA111/SMA350 compression -> breakout -> first MA111 retest
H) MA111 failed breakdown -> 1-3 bar reclaim
I) SMA350 failed breakdown -> 1-3 bar reclaim

Mechanics/features explored:
- price path / momentum / extension / drawdown
- MA slopes, spread, compression, spread expansion
- RSI level, trend, bottom-turn, higher floor, divergence
- volume expansion/contraction, OBV, CMF
- candle structure, ATR, range, wick, close location
- BTC regime and asset-vs-BTC relative return
- breakout volume vs retest volume
- time to retest, time below MA, recent touch count
- Golden Ratio rail proximity (SMA350 x2/x3/x5/x8)

Touch levels are point-in-time:
- live MA111 touch level = mean(previous 110 completed closes)
- live SMA350 touch level = mean(previous 349 completed closes)
This avoids using the final close of the touch candle.

Outcomes:
- +5/+7.5/+10/+15% within 5d and 10d
- +target before -7.5% stop
- 5d timeout economics
- touch-candle stop counts conservatively; touch-candle target is never credited

Selection discipline:
- earliest 60% Hyperliquid events = training
- latest 40% Hyperliquid events = untouched holdout
- filters learned from training only
- exact frozen rule then evaluated on Binance
- episode clustering to reduce market-wide correlation
- no live promotion by this script
"""
from __future__ import annotations
import argparse,itertools,json,math,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
from math import sqrt
import numpy as np
import pandas as pd
import requests

HL_API="https://api.hyperliquid.xyz/info"
BN_API="https://data-api.binance.vision"
OUT=Path("crypto/research/results_ma_multimechanic")
TARGETS=(.05,.075,.10,.15)
STOP=.075
HL_RT=2*(.00045+.0010)
BN_RT=2*(.00070+.0010)

def rsi(c,p=14):
    d=c.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep(x):
    x=x.copy().sort_values("time").drop_duplicates("time").reset_index(drop=True)
    c=x["c"]
    x["sma111"]=c.rolling(111,min_periods=111).mean()
    x["sma350"]=c.rolling(350,min_periods=350).mean()
    x["ma111_touch"]=c.shift(1).rolling(110,min_periods=110).mean()
    x["ma350_touch"]=c.shift(1).rolling(349,min_periods=349).mean()
    x["sum_prev349"]=c.shift(1).rolling(349,min_periods=349).sum()
    x["sma350_sm5"]=x["sma350"].rolling(5,min_periods=3).mean()

    for name,L in (("ma111",111),("ma350",350)):
        col="sma111" if L==111 else "sma350"
        x[f"{name}_slope6"]=x[col]/x[col].shift(6)-1
        x[f"{name}_slope18"]=x[col]/x[col].shift(18)-1
        x[f"{name}_slope30"]=x[col]/x[col].shift(30)-1
    x["ma_spread"]=x["sma111"]/x["sma350"]-1
    x["ma_spread_d3"]=x["ma_spread"].diff(3)
    x["ma_spread_d6"]=x["ma_spread"].diff(6)
    x["ma_compression_abs"]=x["ma_spread"].abs()
    x["dist111"]=c/x["sma111"]-1
    x["dist350"]=c/x["sma350"]-1
    x["dist350_sm5"]=c/x["sma350_sm5"]-1

    x["ret1"]=c.pct_change()
    x["ret3"]=c/c.shift(3)-1
    x["ret6"]=c/c.shift(6)-1
    x["ret12"]=c/c.shift(12)-1
    x["ret30"]=c/c.shift(30)-1
    x["dd12"]=c/x["h"].rolling(12,min_periods=6).max()-1
    x["dd30"]=c/x["h"].rolling(30,min_periods=15).max()-1
    x["above_low12"]=c/x["l"].rolling(12,min_periods=6).min()-1

    x["rsi"]=rsi(c)
    x["rsi_d1"]=x["rsi"].diff()
    x["rsi_d3"]=x["rsi"].diff(3)
    x["rsi_sma3"]=x["rsi"].rolling(3,min_periods=2).mean()
    x["rsi_sma5"]=x["rsi"].rolling(5,min_periods=3).mean()
    x["rsi_vs_sma3"]=x["rsi"]/x["rsi_sma3"]-1
    x["rsi_vs_sma5"]=x["rsi"]/x["rsi_sma5"]-1
    floor6=x["rsi"].shift(1).rolling(6,min_periods=3).min()
    floor12=x["rsi"].shift(1).rolling(12,min_periods=6).min()
    x["rsi_above_floor6"]=x["rsi"]-floor6
    x["rsi_above_floor12"]=x["rsi"]-floor12
    x["rsi_up1"]=x["rsi_d1"]>0
    x["rsi_up2"]=(x["rsi_d1"]>0)&(x["rsi_d1"].shift(1)>0)
    prior_floor=x["rsi"].shift(2).rolling(6,min_periods=3).min()
    x["rsi_bottom_turn"]=(x["rsi"].shift(1)<=prior_floor+1)&(x["rsi"]>=x["rsi"].shift(1)+2)
    prev_price=x["c"].shift(1).rolling(6,min_periods=3).min()
    x["rsi_bull_div6"]=(x["c"]<=prev_price*1.01)&(x["rsi_above_floor6"]>=3)

    v=x["v"].fillna(0)
    x["vol_med5"]=v.rolling(5,min_periods=3).median()
    x["vol_med20"]=v.rolling(20,min_periods=10).median()
    x["vol_mean20"]=v.rolling(20,min_periods=10).mean()
    x["vol_std20"]=v.rolling(20,min_periods=10).std()
    x["vol_ratio5"]=v/x["vol_med5"].replace(0,np.nan)
    x["vol_ratio20"]=v/x["vol_med20"].replace(0,np.nan)
    x["vol_z20"]=(v-x["vol_mean20"])/x["vol_std20"].replace(0,np.nan)
    direction=np.sign(c.diff()).fillna(0)
    x["obv"]=(direction*v).cumsum()
    x["obv_delta3_norm"]=x["obv"].diff(3)/v.rolling(20,min_periods=10).sum().replace(0,np.nan)

    tp=(x["h"]+x["l"]+x["c"])/3
    mf=tp*v
    pos=np.where(tp.diff()>0,mf,0.0);neg=np.where(tp.diff()<0,mf,0.0)
    ps=pd.Series(pos,index=x.index).rolling(20,min_periods=10).sum()
    ns=pd.Series(neg,index=x.index).rolling(20,min_periods=10).sum()
    x["cmf_proxy20"]=(ps-ns)/(ps+ns).replace(0,np.nan)

    rng=(x["h"]-x["l"]).replace(0,np.nan)
    x["range_pct"]=rng/c.replace(0,np.nan)
    x["range_med20"]=x["range_pct"].rolling(20,min_periods=10).median()
    x["range_ratio20"]=x["range_pct"]/x["range_med20"].replace(0,np.nan)
    x["atr14_pct"]=rng.rolling(14,min_periods=8).mean()/c.replace(0,np.nan)
    x["lower_wick"]=(np.minimum(x["o"],x["c"])-x["l"])/rng
    x["upper_wick"]=(x["h"]-np.maximum(x["o"],x["c"]))/rng
    x["close_location"]=(x["c"]-x["l"])/rng

    for m in (2,3,5,8):
        x[f"dist_rail{m}"]=c/(x["sma350"]*m)-1
    return x

def merge_btc(x,btc):
    if btc.empty:return x
    b=btc[["time","ret1","ret3","ret6","rsi","atr14_pct"]].copy()
    b.columns=["time","btc_ret1","btc_ret3","btc_ret6","btc_rsi","btc_atr14_pct"]
    y=pd.merge_asof(x.sort_values("time"),b.sort_values("time"),on="time",direction="backward")
    y["rel_ret1"]=y["ret1"]-y["btc_ret1"]
    y["rel_ret3"]=y["ret3"]-y["btc_ret3"]
    return y

def live_sma350_at_price(row,p):
    s=float(row["sum_prev349"])
    return (s+p)/350.0

def point_features(x,idx,anchor_idx=None,breakout_idx=None):
    r=x.loc[idx]
    d={
        "ret1":r["ret1"],"ret3":r["ret3"],"ret6":r["ret6"],"ret12":r["ret12"],"ret30":r["ret30"],
        "dd12":r["dd12"],"dd30":r["dd30"],"above_low12":r["above_low12"],
        "ma111_slope6":r["ma111_slope6"],"ma111_slope18":r["ma111_slope18"],"ma111_slope30":r["ma111_slope30"],
        "ma350_slope6":r["ma350_slope6"],"ma350_slope18":r["ma350_slope18"],"ma350_slope30":r["ma350_slope30"],
        "ma_spread":r["ma_spread"],"ma_spread_d3":r["ma_spread_d3"],"ma_spread_d6":r["ma_spread_d6"],
        "ma_compression_abs":r["ma_compression_abs"],"dist111":r["dist111"],"dist350":r["dist350"],"dist350_sm5":r["dist350_sm5"],
        "rsi":r["rsi"],"rsi_d1":r["rsi_d1"],"rsi_d3":r["rsi_d3"],"rsi_vs_sma3":r["rsi_vs_sma3"],"rsi_vs_sma5":r["rsi_vs_sma5"],
        "rsi_above_floor6":r["rsi_above_floor6"],"rsi_above_floor12":r["rsi_above_floor12"],
        "rsi_up1":r["rsi_up1"],"rsi_up2":r["rsi_up2"],"rsi_bottom_turn":r["rsi_bottom_turn"],"rsi_bull_div6":r["rsi_bull_div6"],
        "vol_ratio5":r["vol_ratio5"],"vol_ratio20":r["vol_ratio20"],"vol_z20":r["vol_z20"],"obv_delta3_norm":r["obv_delta3_norm"],"cmf_proxy20":r["cmf_proxy20"],
        "atr14_pct":r["atr14_pct"],"range_ratio20":r["range_ratio20"],"lower_wick":r["lower_wick"],"upper_wick":r["upper_wick"],"close_location":r["close_location"],
        "btc_ret1":r.get("btc_ret1"),"btc_ret3":r.get("btc_ret3"),"btc_ret6":r.get("btc_ret6"),"btc_rsi":r.get("btc_rsi"),"btc_atr14_pct":r.get("btc_atr14_pct"),
        "rel_ret1":r.get("rel_ret1"),"rel_ret3":r.get("rel_ret3"),
        "dist_rail2":r["dist_rail2"],"dist_rail3":r["dist_rail3"],"dist_rail5":r["dist_rail5"],"dist_rail8":r["dist_rail8"],
    }
    if anchor_idx is not None and anchor_idx<=idx:
        p=x.loc[anchor_idx:idx]
        d["bars_since_anchor"]=idx-anchor_idx
        d["max_runup_from_anchor"]=float(p["h"].max()/float(x.loc[anchor_idx,"c"])-1)
        d["max_dd_from_anchor"]=float(p["l"].min()/float(x.loc[anchor_idx,"c"])-1)
    if breakout_idx is not None and breakout_idx<=idx:
        d["breakout_vol_ratio20"]=x.loc[breakout_idx,"vol_ratio20"]
        d["retest_vs_breakout_volume"]=float(r["v"])/float(x.loc[breakout_idx,"v"]) if float(x.loc[breakout_idx,"v"])>0 else np.nan
        d["bars_since_breakout"]=idx-breakout_idx
    return {k:(float(v) if isinstance(v,(int,float,np.integer,np.floating)) and pd.notna(v) else bool(v) if isinstance(v,(bool,np.bool_)) else np.nan if pd.isna(v) else v) for k,v in d.items()}

def outcome(x,touch_idx,entry,mode="touch"):
    immediate_stop=False
    if mode=="touch":
        immediate_stop=float(x.loc[touch_idx,"l"])<=entry*(1-STOP)
    f5=x.iloc[touch_idx+1:min(len(x),touch_idx+31)]
    f10=x.iloc[touch_idx+1:min(len(x),touch_idx+61)]
    if len(f5)<24:return None
    out={"entry":float(entry),"entry_time":x.loc[touch_idx,"time"],"touch_candle_immediate_stop":immediate_stop}
    for lab,f in (("5d",f5),("10d",f10)):
        mfe=float(f["h"].max()/entry-1);mae=float(f["l"].min()/entry-1)
        if immediate_stop:mae=min(mae,-STOP)
        out[f"mfe_{lab}"]=mfe;out[f"mae_{lab}"]=mae;out[f"close_ret_{lab}"]=float(f.iloc[-1]["c"]/entry-1)
        for t in TARGETS:
            key=str(t*100).replace(".","p");out[f"hit{key}_{lab}"]=mfe>=t and not immediate_stop
    for t in TARGETS:
        key=str(t*100).replace(".","p")
        if immediate_stop:out[f"t{key}_before_s7p5_5d"]=False;continue
        tk=sk=None
        for k,(_,r) in enumerate(f5.iterrows()):
            if tk is None and float(r["h"])>=entry*(1+t):tk=k
            if sk is None and float(r["l"])<=entry*(1-STOP):sk=k
        out[f"t{key}_before_s7p5_5d"]=tk is not None and (sk is None or tk<sk)
    return out

def count_below(x,i,col,look=24):
    p=x.loc[max(0,i-look):i-1]
    return float((p["c"]<p[col]).mean()) if len(p) else np.nan

def recent_touch_count(x,i,level_col,look=24,tol=.01):
    n=0
    for j in range(max(1,i-look),i):
        lv=float(x.loc[j,level_col]) if pd.notna(x.loc[j,level_col]) else np.nan
        if np.isfinite(lv) and float(x.loc[j,"l"])<=lv*(1+tol) and float(x.loc[j,"h"])>=lv*(1-tol):n+=1
    return n

def add_event(rows,x,coin,venue,family,anchor,touch,entry,level_name,breakout=None,mode="touch",extra=None):
    if touch<=0:return
    # All signal filters use prior completed bar for touch entries.
    ctx=touch-1 if mode=="touch" else touch
    o=outcome(x,touch,entry,mode)
    if o is None:return
    base={
        "venue":venue,"coin":coin,"family":family,"mode":mode,
        "anchor_time":x.loc[anchor,"time"],"touch_time":x.loc[touch,"time"],
        "context_time":x.loc[ctx,"time"],"level_name":level_name,
        "touch_level":float(entry),"delay_bars":touch-anchor,
        "recent_touch_count":recent_touch_count(x,touch,"ma111_touch" if level_name=="MA111" else "ma350_touch"),
        **point_features(x,ctx,anchor,breakout),
        **o
    }
    if extra:base.update(extra)
    rows.append(base)

def scan_coin(x,coin,venue):
    rows=[];n=len(x)
    if n<430:return rows

    # A/B/C: breakout -> first retest families.
    for family,breakcol,touchcol,levelname,minbelow in [
        ("sma350_break_ma111_touch","sma350","ma111_touch","MA111",.75),
        ("sma350_break_sma350_retest","sma350","ma350_touch","SMA350",.75),
        ("ma111_break_ma111_retest","sma111","ma111_touch","MA111",.75),
    ]:
        last=None
        for i in range(380,n-35):
            if pd.isna(x.loc[i,breakcol]) or pd.isna(x.loc[i-1,breakcol]):continue
            if not (float(x.loc[i-1,"c"])<=float(x.loc[i-1,breakcol]) and float(x.loc[i,"c"])>float(x.loc[i,breakcol])):continue
            if last is not None and x.loc[i,"time"]-last<pd.Timedelta(days=5):continue
            below=count_below(x,i,breakcol,24)
            if below<minbelow:continue
            touch=None;maxext=float(x.loc[i,"c"])/float(x.loc[i,breakcol])-1
            for j in range(i+1,min(n-31,i+49)):
                if pd.isna(x.loc[j,touchcol]):continue
                lv=float(x.loc[j,touchcol])
                maxext=max(maxext,float(x.loc[j,"h"])/float(x.loc[j,breakcol])-1 if pd.notna(x.loc[j,breakcol]) else maxext)
                if float(x.loc[j,"l"])<=lv<=float(x.loc[j,"h"]):
                    touch=j;break
            if touch is None:continue
            add_event(rows,x,coin,venue,family,i,touch,float(x.loc[touch,touchcol]),levelname,i,
                      extra={"below_frac_24":below,"extension_before_touch":maxext})
            last=x.loc[i,"time"]

    # D: MA111 crosses above SMA350 -> first MA111 retest.
    last=None
    for i in range(380,n-35):
        if any(pd.isna(x.loc[i,c]) for c in ("sma111","sma350")):continue
        cross=float(x.loc[i-1,"sma111"])<=float(x.loc[i-1,"sma350"]) and float(x.loc[i,"sma111"])>float(x.loc[i,"sma350"])
        if not cross or float(x.loc[i,"c"])<float(x.loc[i,"sma350"]):continue
        if last is not None and x.loc[i,"time"]-last<pd.Timedelta(days=10):continue
        for j in range(i+1,min(n-31,i+49)):
            lv=float(x.loc[j,"ma111_touch"])
            if float(x.loc[j,"l"])<=lv<=float(x.loc[j,"h"]):
                add_event(rows,x,coin,venue,"ma111_cross350_retest",i,j,lv,"MA111",i)
                last=x.loc[i,"time"];break

    # E/F established uptrend pullbacks. Require both MAs rising and MA111>350.
    # Event anchor is last local 12-bar high before touch.
    last_e=last_f=None
    for j in range(380,n-31):
        if pd.isna(x.loc[j,"ma111_touch"]) or pd.isna(x.loc[j,"ma350_touch"]):continue
        ctx=j-1
        up=(float(x.loc[ctx,"sma111"])>float(x.loc[ctx,"sma350"]) and float(x.loc[ctx,"ma111_slope18"])>0 and float(x.loc[ctx,"ma350_slope18"])>=0)
        if not up:continue
        recent=x.loc[max(350,j-12):j-1]
        if recent.empty:continue
        anchor=int(recent["h"].idxmax())
        # MA111 trend pullback
        lv=float(x.loc[j,"ma111_touch"])
        if float(x.loc[j,"l"])<=lv<=float(x.loc[j,"h"]):
            if last_e is None or x.loc[j,"time"]-last_e>=pd.Timedelta(days=5):
                add_event(rows,x,coin,venue,"trend_pullback_ma111",anchor,j,lv,"MA111",None,
                          extra={"pre_pullback_runup":float(x.loc[anchor,"h"])/float(x.loc[max(350,anchor-12),"c"])-1})
                last_e=x.loc[j,"time"]
        # SMA350 deep pullback
        lv2=float(x.loc[j,"ma350_touch"])
        if float(x.loc[j,"l"])<=lv2<=float(x.loc[j,"h"]):
            if last_f is None or x.loc[j,"time"]-last_f>=pd.Timedelta(days=7):
                add_event(rows,x,coin,venue,"trend_pullback_sma350",anchor,j,lv2,"SMA350",None)
                last_f=x.loc[j,"time"]

    # G compression breakout -> first MA111 retest.
    last=None
    for i in range(380,n-35):
        if pd.isna(x.loc[i,"ma_compression_abs"]) or pd.isna(x.loc[i,"sma111"]) or pd.isna(x.loc[i,"sma350"]):continue
        compressed=float(x.loc[i-1,"ma_compression_abs"])<=.03
        cross=float(x.loc[i-1,"c"])<=max(float(x.loc[i-1,"sma111"]),float(x.loc[i-1,"sma350"])) and float(x.loc[i,"c"])>max(float(x.loc[i,"sma111"]),float(x.loc[i,"sma350"]))
        if not (compressed and cross):continue
        if last is not None and x.loc[i,"time"]-last<pd.Timedelta(days=7):continue
        for j in range(i+1,min(n-31,i+49)):
            lv=float(x.loc[j,"ma111_touch"])
            if float(x.loc[j,"l"])<=lv<=float(x.loc[j,"h"]):
                add_event(rows,x,coin,venue,"compression_break_ma111",i,j,lv,"MA111",i,
                          extra={"compression_at_break":float(x.loc[i-1,"ma_compression_abs"])})
                last=x.loc[i,"time"];break

    # H/I failed breakdown -> reclaim. Entry next open, so touch candle context is known.
    for family,col,levelname in [("ma111_failed_break_reclaim","sma111","MA111"),("sma350_failed_break_reclaim","sma350","SMA350")]:
        last=None
        for i in range(380,n-35):
            if pd.isna(x.loc[i,col]) or pd.isna(x.loc[i-1,col]):continue
            broke=float(x.loc[i-1,"c"])>=float(x.loc[i-1,col]) and float(x.loc[i,"c"])<float(x.loc[i,col])
            if not broke:continue
            if last is not None and x.loc[i,"time"]-last<pd.Timedelta(days=5):continue
            for j in range(i+1,min(n-31,i+4)):
                if float(x.loc[j,"c"])>=float(x.loc[j,col]):
                    entry=float(x.loc[j+1,"o"])
                    # use pseudo touch index j but mode confirmed: outcome starts j+1, no touch-candle immediate stop
                    o=outcome(x,j,entry,"confirmed")
                    if o is None:break
                    ctx=j
                    rows.append({
                        "venue":venue,"coin":coin,"family":family,"mode":"reclaim",
                        "anchor_time":x.loc[i,"time"],"touch_time":x.loc[j,"time"],"context_time":x.loc[ctx,"time"],
                        "level_name":levelname,"touch_level":float(x.loc[j,col]),"delay_bars":j-i,
                        "recent_touch_count":recent_touch_count(x,j,"ma111_touch" if levelname=="MA111" else "ma350_touch"),
                        **point_features(x,ctx,i,None),**o
                    })
                    last=x.loc[i,"time"];break
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
    rows=hl_post(s,{"type":"candleSnapshot","req":{"coin":coin,"interval":"4h","startTime":int(start.timestamp()*1000),"endTime":int(end.timestamp()*1000)}})
    a=[]
    for r in rows or []:
        try:a.append({"time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),"o":float(r["o"]),"h":float(r["h"]),"l":float(r["l"]),"c":float(r["c"]),"v":float(r.get("v",0))})
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
    info=bn_get(s,"/api/v3/exchangeInfo");tick=bn_get(s,"/api/v3/ticker/24hr")
    qv={x["symbol"]:float(x.get("quoteVolume") or 0) for x in tick if isinstance(x,dict) and x.get("symbol")}
    stable={"USDC","FDUSD","TUSD","USDP","DAI","BUSD","USD1"}
    arr=[]
    for x in info.get("symbols",[]):
        if x.get("status")!="TRADING" or x.get("quoteAsset")!="USDT":continue
        b=str(x.get("baseAsset") or "")
        if b in stable or b.startswith("USD"):continue
        arr.append(x["symbol"])
    arr=sorted(set(arr),key=lambda z:qv.get(z,0),reverse=True)
    out=arr[:n]
    for force in ("CHIPUSDT","STRKUSDT"):
        if force in arr and force not in out:out.append(force)
    return out
def bn_fetch(s,symbol,start,end):
    cur=int(start.timestamp()*1000);stop=int(end.timestamp()*1000);step=4*3600*1000;rows=[]
    while cur<stop:
        b=bn_get(s,"/api/v3/klines",{"symbol":symbol,"interval":"4h","startTime":cur,"endTime":stop,"limit":1000})
        if not b:break
        rows.extend(b);nxt=int(b[-1][0])+step
        if nxt<=cur:break
        cur=nxt
        if len(b)<1000:break
    a=[]
    for z in rows:
        try:a.append({"time":pd.to_datetime(int(z[0]),unit="ms",utc=True),"o":float(z[1]),"h":float(z[2]),"l":float(z[3]),"c":float(z[4]),"v":float(z[5])})
        except Exception:pass
    return pd.DataFrame(a) if a else pd.DataFrame()

def scan(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-ma-multimechanic/1.0"
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0);start=end-timedelta(days=args.days)
    if args.venue=="hl":coins=hl_universe(s);fetch=hl_fetch;venue="Hyperliquid primary perps";btc_coin="BTC"
    else:coins=bn_universe(s,args.binance_symbols);fetch=bn_fetch;venue="Binance USDT spot";btc_coin="BTCUSDT"
    btc=prep(fetch(s,btc_coin,start,end))
    rows=[];cov=[]
    for p,coin in enumerate(coins[args.shard_index::args.shard_count],1):
        try:
            x=prep(fetch(s,coin,start,end))
            if len(x)<500:cov.append((coin,len(x),0,"short"));continue
            x=merge_btc(x,btc)
            rr=scan_coin(x,coin,venue);rows.extend(rr);cov.append((coin,len(x),len(rr),"ok"))
            print(p,coin,len(rr))
        except Exception as e:
            cov.append((coin,0,0,"error:"+str(e)[:120]));print("ERR",coin,e)
        time.sleep(.03)
    pd.DataFrame(rows).to_csv(out/f"{args.venue}_events_{args.shard_index:02d}.csv",index=False)
    pd.DataFrame(cov,columns=["coin","bars","events","status"]).to_csv(out/f"{args.venue}_coverage_{args.shard_index:02d}.csv",index=False)

def rate(s):
    q=s.dropna();return float(q.astype(bool).mean()) if len(q) else np.nan
def wilson(w,n,z=1.96):
    if n<=0:return np.nan
    p=w/n;den=1+z*z/n;ctr=(p+z*z/(2*n))/den
    half=z*sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return ctr-half
def econ(g,t,cost):
    if g.empty:return np.nan
    k=str(t*100).replace(".","p");win=g[f"t{k}_before_s7p5_5d"].fillna(False).astype(bool)
    mae=pd.to_numeric(g["mae_5d"],errors="coerce");timeout=pd.to_numeric(g["close_ret_5d"],errors="coerce").fillna(0)
    stopped=(~win)&(mae<=-STOP);gross=pd.Series(np.where(win,t,np.where(stopped,-STOP,timeout)),index=g.index).clip(-STOP,t)
    return float((gross-cost).mean())

def episodes(g):
    if g.empty:return 0,np.nan
    z=g.sort_values("entry_time").copy();ids=[];eid=0;last=None
    for t in pd.to_datetime(z["entry_time"],utc=True):
        if last is None or t-last>pd.Timedelta(hours=18):eid+=1
        ids.append(eid);last=t
    z["ep"]=ids
    q=z.groupby("ep")["hit5p0_5d"].mean()
    return int(len(q)),float(q.mean())

NUM_FEATURES=[
 "ret1","ret3","ret6","ret12","ret30","dd12","dd30","above_low12",
 "ma111_slope6","ma111_slope18","ma111_slope30","ma350_slope6","ma350_slope18","ma350_slope30",
 "ma_spread","ma_spread_d3","ma_spread_d6","ma_compression_abs","dist111","dist350","dist350_sm5",
 "rsi","rsi_d1","rsi_d3","rsi_vs_sma3","rsi_vs_sma5","rsi_above_floor6","rsi_above_floor12",
 "vol_ratio5","vol_ratio20","vol_z20","obv_delta3_norm","cmf_proxy20","atr14_pct","range_ratio20","lower_wick","upper_wick","close_location",
 "btc_ret1","btc_ret3","btc_ret6","btc_rsi","btc_atr14_pct","rel_ret1","rel_ret3",
 "dist_rail2","dist_rail3","dist_rail5","dist_rail8","bars_since_anchor","max_runup_from_anchor","max_dd_from_anchor",
 "breakout_vol_ratio20","retest_vs_breakout_volume","bars_since_breakout","recent_touch_count",
 "below_frac_24","extension_before_touch","compression_at_break","pre_pullback_runup"
]
BOOL_FEATURES=["rsi_up1","rsi_up2","rsi_bottom_turn","rsi_bull_div6"]

def stats(g,cost):
    if g.empty:return {"n":0}
    hit=g["hit5p0_5d"].astype(bool);risk=g["t5p0_before_s7p5_5d"].astype(bool)
    epn,epr=episodes(g)
    return {"n":len(g),"hit5":rate(hit),"risk":rate(risk),"roi":econ(g,.05,cost),
            "hit10":rate(g["hit10p0_5d"]),"hit15":rate(g["hit15p0_5d"]),
            "wilson":wilson(int(hit.sum()),len(g)),"episodes":epn,"episode_hit5":epr}

def filter_specs(train):
    out=[]
    for f in NUM_FEATURES:
        if f not in train.columns:continue
        s=pd.to_numeric(train[f],errors="coerce").replace([np.inf,-np.inf],np.nan).dropna()
        if len(s)<50:continue
        q=s.quantile([.15,.20,.25,.30,.35,.40,.50,.60,.65,.70,.75,.80,.85])
        for qq in (.20,.25,.30,.35,.40):
            out.append((f,"<=",float(q.loc[qq]),"NUM"))
        for qq in (.60,.65,.70,.75,.80):
            out.append((f,">=",float(q.loc[qq]),"NUM"))
        for lo,hi in [(.20,.60),(.25,.65),(.30,.70),(.35,.75),(.40,.80)]:
            out.append((f,"band",(float(q.loc[lo]),float(q.loc[hi])),"NUM"))
    for f in BOOL_FEATURES:
        if f in train.columns:
            out.append((f,"bool",True,"BOOL"))
    return out

def apply_spec(df,s):
    f,op,v,_=s
    if f not in df.columns:return pd.Series(False,index=df.index)
    if op=="bool":return df[f].astype(str).str.lower().isin(["true","1"])
    x=pd.to_numeric(df[f],errors="coerce")
    if op=="<=":return x<=v
    if op==">=":return x>=v
    a,b=v;return x.between(a,b,inclusive="both")

def spec_name(s):
    f,op,v,_=s
    if op=="band":return f"{v[0]:.6g}<={f}<={v[1]:.6g}"
    if op=="bool":return f
    return f"{f}{op}{v:.6g}"

def discover_family(h,b,fam):
    h=h[h["family"]==fam].sort_values("entry_time").drop_duplicates(["coin","entry_time"]).reset_index(drop=True)
    b=b[b["family"]==fam].sort_values("entry_time").drop_duplicates(["coin","entry_time"]).reset_index(drop=True)
    if len(h)<80:return pd.DataFrame(),{}
    hc=max(1,int(len(h)*.60));bc=max(1,int(len(b)*.60)) if len(b) else 0
    tr,ho=h.iloc[:hc],h.iloc[hc:];bh=b.iloc[bc:] if len(b) else b
    base_tr=stats(tr,HL_RT);base_ho=stats(ho,HL_RT);base_bin=stats(bh,BN_RT)

    specs=filter_specs(tr)
    singles=[]
    for s in specs:
        gt=tr[apply_spec(tr,s)]
        if len(gt)<max(30,int(len(tr)*.25)):continue
        st=stats(gt,HL_RT)
        score=.40*st["hit5"]+.25*st["risk"]+.25*max(min(st["roi"]/.03,1),-1)+.10*min(st["n"]/100,1)
        singles.append((score,s,st))
    singles.sort(key=lambda x:x[0],reverse=True)
    top=[x[1] for x in singles[:35]]

    combos=[(s,) for s in top]
    for a,c in itertools.combinations(top[:22],2):
        if a[0]!=c[0]:combos.append((a,c))

    rows=[];seen=set()
    for combo in combos:
        nm=" AND ".join(spec_name(s) for s in combo)
        if nm in seen:continue
        seen.add(nm)
        mt=pd.Series(True,index=tr.index);mh=pd.Series(True,index=ho.index);mb=pd.Series(True,index=bh.index)
        for s in combo:
            mt&=apply_spec(tr,s);mh&=apply_spec(ho,s);mb&=apply_spec(bh,s)
        gt,gh,gb=tr[mt],ho[mh],bh[mb]
        if len(gt)<30 or len(gh)<20:continue
        st,sh,sb=stats(gt,HL_RT),stats(gh,HL_RT),stats(gb,BN_RT)
        eligible=(
            st["hit5"]>=.72 and st["risk"]>=.65 and st["roi"]>=.008
            and sh["hit5"]>=.78 and sh["risk"]>=.70 and sh["roi"]>=.0125
            and sh["episodes"]>=12 and sh["episode_hit5"]>=.76 and sh["wilson"]>=.60
            and sb.get("n",0)>=15 and sb.get("hit5",0)>=.75 and sb.get("risk",0)>=.67 and sb.get("roi",-1)>=.010
        )
        rows.append({
            "family":fam,"rule":nm,"features":"+".join(s[0] for s in combo),
            "train_n":st["n"],"train_hit5":st["hit5"],"train_risk":st["risk"],"train_roi":st["roi"],
            "hl_hold_n":sh["n"],"hl_hit5":sh["hit5"],"hl_risk":sh["risk"],"hl_roi":sh["roi"],"hl_hit10":sh["hit10"],"hl_hit15":sh["hit15"],
            "hl_episodes":sh["episodes"],"hl_episode_hit5":sh["episode_hit5"],"hl_wilson":sh["wilson"],
            "bin_hold_n":sb.get("n",0),"bin_hit5":sb.get("hit5",np.nan),"bin_risk":sb.get("risk",np.nan),"bin_roi":sb.get("roi",np.nan),
            "eligible":eligible,
            "score":.25*sh["hit5"]+.20*sh["risk"]+.20*min(max(sh["roi"]/.03,-1),1)+.15*sb.get("hit5",0)+.10*sb.get("risk",0)+.10*min(max(sb.get("roi",0)/.03,-1),1)
        })
    z=pd.DataFrame(rows)
    if len(z):z=z.sort_values(["eligible","score","hl_hold_n"],ascending=[False,False,False])
    return z,{"family":fam,"hl_total":len(h),"bin_total":len(b),"train":base_tr,"holdout":base_ho,"binance_holdout":base_bin}

def aggregate(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True);root=Path(args.input_dir)
    def cat(pat):
        fs=[]
        for p in root.rglob(pat):
            try:
                z=pd.read_csv(p)
                if len(z):fs.append(z)
            except Exception:pass
        return pd.concat(fs,ignore_index=True) if fs else pd.DataFrame()
    hl=cat("hl_events_*.csv");bn=cat("bn_events_*.csv");hc=cat("hl_coverage_*.csv");bc=cat("bn_coverage_*.csv")
    for z in (hl,bn):
        if len(z):
            for c in ["anchor_time","touch_time","context_time","entry_time"]:
                if c in z:z[c]=pd.to_datetime(z[c],utc=True,errors="coerce")
    hl.to_csv(out/"hyperliquid_events.csv",index=False);bn.to_csv(out/"binance_events.csv",index=False)
    hc.to_csv(out/"hyperliquid_coverage.csv",index=False);bc.to_csv(out/"binance_coverage.csv",index=False)

    allrules=[];baselines=[]
    for fam in sorted(set(hl["family"])) if len(hl) else []:
        z,base=discover_family(hl,bn,fam)
        baselines.append(base)
        if len(z):allrules.append(z)
    rr=pd.concat(allrules,ignore_index=True) if allrules else pd.DataFrame()
    if len(rr):rr=rr.sort_values(["eligible","score","hl_hold_n"],ascending=[False,False,False])
    rr.to_csv(out/"all_rules.csv",index=False)
    pd.DataFrame([{
        "family":x["family"],"hl_total":x["hl_total"],"bin_total":x["bin_total"],
        **{"train_"+k:v for k,v in x["train"].items()},
        **{"hold_"+k:v for k,v in x["holdout"].items()},
        **{"bin_"+k:v for k,v in x["binance_holdout"].items()},
    } for x in baselines]).to_csv(out/"family_baselines.csv",index=False)

    # Keep only one top candidate per family+feature signature to avoid threshold clones.
    cand=rr[rr["eligible"]==True].copy() if len(rr) else pd.DataFrame()
    if len(cand):
        cand["key"]=cand["family"]+"|"+cand["features"]
        cand=cand.drop_duplicates("key").head(25)
    cand.to_csv(out/"candidate_shortlist.csv",index=False)
    registry=[]
    for i,(_,r) in enumerate(cand.iterrows(),1):
        registry.append({
            "candidate_id":f"MA-MECH-{pd.Timestamp.utcnow().strftime('%Y%m%d')}-{i:02d}",
            "status":"CROSS_VALIDATED_RESEARCH",
            "family":r["family"],"rule":r["rule"],"features":r["features"].split("+"),
            "hyperliquid":{"train_n":int(r["train_n"]),"train_hit5":float(r["train_hit5"]),"train_risk":float(r["train_risk"]),"train_roi":float(r["train_roi"]),
                           "hold_n":int(r["hl_hold_n"]),"hold_hit5":float(r["hl_hit5"]),"hold_risk":float(r["hl_risk"]),"hold_roi":float(r["hl_roi"]),
                           "episodes":int(r["hl_episodes"]),"episode_hit5":float(r["hl_episode_hit5"])},
            "binance":{"n":int(r["bin_hold_n"]),"hit5":float(r["bin_hit5"]),"risk":float(r["bin_risk"]),"roi":float(r["bin_roi"])},
            "next_stage":["actual Hyperliquid funding replay","1m fill/exit audit where intrabar","$10k L2 capacity","forward shadow"]
        })
    (out/"candidate_registry.json").write_text(json.dumps({"generated_at":pd.Timestamp.utcnow().isoformat(),"candidates":registry},indent=2))

    lines=[
        "BROAD POINT-IN-TIME MA METHOD DISCOVERY","",
        "Families: SMA350->MA111, SMA350 retest, MA111 retest, MA111/350 crossover retest, trend pullbacks, compression breakout, failed-breakdown reclaims.",
        "Mechanics: price, MA geometry, RSI, volume/OBV/CMF, ATR/candles, BTC regime, relative return, breakout/retest volume, touch count, rail proximity.",
        "All intrabar MA touches use point-in-time levels; touch entries use only prior completed-bar features.","",
        f"HL rows: {len(hl)} | coins ok: {int((hc['status']=='ok').sum()) if len(hc) else 0}",
        f"Binance rows: {len(bn)} | symbols ok: {int((bc['status']=='ok').sum()) if len(bc) else 0}","",
        "FAMILY BASELINES",pd.DataFrame([{
            "family":x["family"],"hl_total":x["hl_total"],"bin_total":x["bin_total"],
            "train":x["train"],"holdout":x["holdout"],"binance":x["binance_holdout"]
        } for x in baselines]).to_string(index=False) if baselines else "none","",
        "TOP CROSS-VALIDATED CANDIDATES",cand.to_string(index=False) if len(cand) else "none","",
        "TOP 30 RULES",rr.head(30).to_string(index=False) if len(rr) else "none","",
        "No live promotion."
    ]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n");print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--mode",choices=["scan","aggregate"],default="scan");ap.add_argument("--venue",choices=["hl","bn"],default="hl")
    ap.add_argument("--days",type=int,default=600);ap.add_argument("--binance-symbols",type=int,default=160)
    ap.add_argument("--shard-index",type=int,default=0);ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--out",default="ma_multi_out");ap.add_argument("--input-dir",default="ma_multi_shards")
    args=ap.parse_args();scan(args) if args.mode=="scan" else aggregate(args)
if __name__=="__main__":main()
