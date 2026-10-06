#!/usr/bin/env python3
"""
4H Golden-Ratio MA rail research.

Rails visible in the user's TradingView setup:
- MA111
- SMA350
- SMA350 * 2
- SMA350 * 3
- SMA350 * 5
- SMA350 * 8

The *2/*3/*5/*8 lines are price multiples of SMA350, NOT SMA lengths.

Research families:
1) Rail breakout -> first support retest.
2) MA111 first support after a rail breakout.
3) MA111/SMA350 crossover -> first MA111 support.
4) Rail-to-next-rail continuation ("ladder") after a successful support test.
5) RSI higher-floor / bottom-turn context at support.

Discovery is Hyperliquid-primary-perp first:
- chronological earliest 60% train
- latest 40% untouched holdout
- frozen Binance USDT-spot replication
- no live promotion.

Fixed trade outcomes:
+5%, +7.5%, +10%, +15%
-7.5% stop
5d and 10d horizons
HL costs: 0.045% taker + 0.10% slippage / side
Binance: 0.070% fee + 0.10% slippage / side
Funding is excluded in discovery and required later for any survivor.
"""
from __future__ import annotations

import argparse,itertools,json,math,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from math import sqrt
import numpy as np
import pandas as pd
import requests

HL_API="https://api.hyperliquid.xyz/info"
BN_API="https://data-api.binance.vision"
OUT=Path("crypto/research/results_golden_ratio_ma_rails")

TARGETS=(.05,.075,.10,.15)
STOP=.075
HL_RT=2*(.00045+.0010)
BN_RT=2*(.00070+.0010)
MULTS=(1,2,3,5,8)
NEXT={1:2,2:3,3:5,5:8}
BELOW_FRACS=(.75,.90)
EXTS=(.03,.05,.08)
MAX_DELAYS=(12,24,48)
MODES=("touch","confirmed","reclaim")

def rsi(c,p=14):
    d=c.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep(x):
    x=x.copy().sort_values("time").drop_duplicates("time").reset_index(drop=True)
    x["sma111"]=x["c"].rolling(111,min_periods=111).mean()
    x["sma350"]=x["c"].rolling(350,min_periods=350).mean()
    x["sma350_sm5"]=x["sma350"].rolling(5,min_periods=3).mean()
    x["sma111_slope6"]=x["sma111"]/x["sma111"].shift(6)-1
    x["sma350_slope6"]=x["sma350"]/x["sma350"].shift(6)-1
    x["sma350_slope18"]=x["sma350"]/x["sma350"].shift(18)-1
    x["ma111_vs_350"]=x["sma111"]/x["sma350"]-1
    for m in MULTS:
        x[f"rail_{m}"]=x["sma350"]*m
        x[f"dist_rail_{m}"]=x["c"]/x[f"rail_{m}"]-1
    x["rsi"]=rsi(x["c"])
    x["rsi_d1"]=x["rsi"].diff()
    x["rsi_d3"]=x["rsi"].diff(3)
    x["rsi_sma3"]=x["rsi"].rolling(3,min_periods=2).mean()
    x["rsi_sma5"]=x["rsi"].rolling(5,min_periods=3).mean()
    floor6=x["rsi"].shift(1).rolling(6,min_periods=3).min()
    floor12=x["rsi"].shift(1).rolling(12,min_periods=6).min()
    x["rsi_above_floor6"]=x["rsi"]-floor6
    x["rsi_above_floor12"]=x["rsi"]-floor12
    prior_floor=x["rsi"].shift(2).rolling(6,min_periods=3).min()
    x["rsi_bottom_turn"]=(x["rsi"].shift(1)<=prior_floor+1)&(x["rsi"]>=x["rsi"].shift(1)+2)
    x["rsi_up2"]=(x["rsi_d1"]>0)&(x["rsi_d1"].shift(1)>0)
    prev_price=x["c"].shift(1).rolling(6,min_periods=3).min()
    x["rsi_bull_div6"]=(x["c"]<=prev_price*1.01)&(x["rsi_above_floor6"]>=3)
    x["vol_med20"]=x["v"].rolling(20,min_periods=10).median()
    x["vol_ratio20"]=x["v"]/x["vol_med20"].replace(0,np.nan)
    rng=(x["h"]-x["l"]).replace(0,np.nan)
    x["lower_wick"]=(np.minimum(x["o"],x["c"])-x["l"])/rng
    x["close_location"]=(x["c"]-x["l"])/rng
    return x

def outcome(x,entry_idx,entry,mode,touch_idx=None,next_mult=None):
    if entry_idx>=len(x) or not np.isfinite(entry) or entry<=0:return None
    immediate_stop=False
    if mode=="touch" and touch_idx is not None:
        immediate_stop=float(x.loc[touch_idx,"l"])<=entry*(1-STOP)
    f5=x.iloc[entry_idx:min(len(x),entry_idx+30)]
    f10=x.iloc[entry_idx:min(len(x),entry_idx+60)]
    if len(f5)<24:return None
    out={"entry":float(entry),"entry_time":x.iloc[entry_idx]["time"],
         "touch_candle_immediate_stop":immediate_stop}
    for lab,f in (("5d",f5),("10d",f10)):
        mfe=float(f["h"].max()/entry-1);mae=float(f["l"].min()/entry-1)
        if immediate_stop:mae=min(mae,-STOP)
        out[f"mfe_{lab}"]=mfe;out[f"mae_{lab}"]=mae
        out[f"close_ret_{lab}"]=float(f.iloc[-1]["c"]/entry-1)
        for t in TARGETS:
            k=str(t*100).replace(".","p")
            out[f"hit{k}_{lab}"]=mfe>=t and not immediate_stop
    for t in TARGETS:
        k=str(t*100).replace(".","p")
        if immediate_stop:
            out[f"t{k}_before_s7p5_5d"]=False;continue
        tk=sk=None
        for j,(_,r) in enumerate(f5.iterrows()):
            if tk is None and float(r["h"])>=entry*(1+t):tk=j
            if sk is None and float(r["l"])<=entry*(1-STOP):sk=j
        out[f"t{k}_before_s7p5_5d"]=tk is not None and (sk is None or tk<sk)

    if next_mult is not None:
        # Dynamic next rail: each future bar is compared with that bar's
        # contemporaneous next Golden Ratio rail.
        for lab,f in (("5d",f5),("10d",f10)):
            hit=False;bars=None
            for j,(idx,r) in enumerate(f.iterrows()):
                rail=float(x.loc[idx,f"rail_{next_mult}"])
                if float(r["h"])>=rail:
                    hit=True;bars=j;break
            out[f"hit_next_rail_{lab}"]=hit
            out[f"bars_to_next_rail_{lab}"]=bars
        out["next_rail_gap_at_entry"]=float(x.loc[entry_idx,f"rail_{next_mult}"])/entry-1
    return out

def support_context(x,idx,break_idx):
    pull=x.loc[break_idx+1:idx]
    pmin=float(pull["rsi"].min()) if len(pull) and pull["rsi"].notna().any() else np.nan
    r=x.loc[idx]
    return {
        "rsi":float(r["rsi"]) if pd.notna(r["rsi"]) else np.nan,
        "rsi_d1":float(r["rsi_d1"]) if pd.notna(r["rsi_d1"]) else np.nan,
        "rsi_d3":float(r["rsi_d3"]) if pd.notna(r["rsi_d3"]) else np.nan,
        "rsi_above_sma3":bool(pd.notna(r["rsi_sma3"]) and r["rsi"]>=r["rsi_sma3"]),
        "rsi_above_sma5":bool(pd.notna(r["rsi_sma5"]) and r["rsi"]>=r["rsi_sma5"]),
        "rsi_above_floor6":float(r["rsi_above_floor6"]) if pd.notna(r["rsi_above_floor6"]) else np.nan,
        "rsi_above_floor12":float(r["rsi_above_floor12"]) if pd.notna(r["rsi_above_floor12"]) else np.nan,
        "rsi_recovery_from_pullback_min":float(r["rsi"])-pmin if np.isfinite(pmin) and pd.notna(r["rsi"]) else np.nan,
        "rsi_bottom_turn":bool(r["rsi_bottom_turn"]),
        "rsi_up2":bool(r["rsi_up2"]),
        "rsi_bull_div6":bool(r["rsi_bull_div6"]),
        "vol_ratio20":float(r["vol_ratio20"]) if pd.notna(r["vol_ratio20"]) else np.nan,
        "lower_wick":float(r["lower_wick"]) if pd.notna(r["lower_wick"]) else np.nan,
        "close_location":float(r["close_location"]) if pd.notna(r["close_location"]) else np.nan,
        "ma111_vs_350":float(r["ma111_vs_350"]) if pd.notna(r["ma111_vs_350"]) else np.nan,
        "sma111_slope6":float(r["sma111_slope6"]) if pd.notna(r["sma111_slope6"]) else np.nan,
        "sma350_slope6":float(r["sma350_slope6"]) if pd.notna(r["sma350_slope6"]) else np.nan,
        "sma350_slope18":float(r["sma350_slope18"]) if pd.notna(r["sma350_slope18"]) else np.nan,
    }

def scan_rail_retests(x,coin,venue,m):
    rail=f"rail_{m}";rows=[];i=380;n=len(x);last=None
    while i<n-35:
        if pd.isna(x.loc[i,rail]) or pd.isna(x.loc[i-1,rail]):i+=1;continue
        cross=float(x.loc[i-1,"c"])<=float(x.loc[i-1,rail]) and float(x.loc[i,"c"])>float(x.loc[i,rail])
        if not cross:i+=1;continue
        if last is not None and x.loc[i,"time"]-last<pd.Timedelta(days=7):i+=1;continue
        prev=x.loc[i-24:i-1]
        below=float((prev["c"]<prev[rail]).mean()) if len(prev)==24 else 0
        maxext=float(x.loc[i,"c"])/float(x.loc[i,rail])-1
        touch=None
        for j in range(i+1,min(n-32,i+49)):
            rv=float(x.loc[j,rail]);maxext=max(maxext,float(x.loc[j,"h"])/rv-1)
            if float(x.loc[j,"l"])<=rv<=float(x.loc[j,"h"]):
                touch=j;break
        if touch is None:i+=1;continue
        j=touch;rv=float(x.loc[j,rail]);close=float(x.loc[j,"c"])
        common={
            "family":"rail_retest","venue":venue,"coin":coin,"rail_mult":m,
            "breakout_time":x.loc[i,"time"],"touch_time":x.loc[j,"time"],
            "below_frac_24":below,"extension_before_touch":maxext,
            "delay_bars":j-i,"touch_close_vs_rail":close/rv-1,
            "base_sma350":float(x.loc[j,"sma350"]),
            "rail_value":rv
        }
        nxt=NEXT.get(m)
        if j+1<n:
            o=outcome(x,j+1,rv,"touch",j,nxt)
            # Touch entry occurs intrabar, so only information from the PRIOR
            # completed 4H bar may be used as an RSI filter. This prevents
            # using the touch candle close to justify an entry that happened
            # earlier in that same candle.
            ctx_idx=max(i,j-1)
            if o:rows.append({**common,"mode":"touch","rsi_context_time":x.loc[ctx_idx,"time"],**support_context(x,ctx_idx,i),**o})
        if close>=rv and j+1<n:
            o=outcome(x,j+1,float(x.loc[j+1,"o"]),"confirmed",None,nxt)
            if o:rows.append({**common,"mode":"confirmed","rsi_context_time":x.loc[j,"time"],**support_context(x,j,i),**o})
        if -0.03<=close/rv-1<0 and j+2<n:
            if float(x.loc[j+1,"c"])>=float(x.loc[j+1,rail]):
                o=outcome(x,j+2,float(x.loc[j+2,"o"]),"reclaim",None,nxt)
                if o:rows.append({**common,"mode":"reclaim","reclaim_time":x.loc[j+1,"time"],"rsi_context_time":x.loc[j+1,"time"],**support_context(x,j+1,i),**o})
        last=x.loc[i,"time"];i=j+1
    return rows

def scan_ma111_after_rail_break(x,coin,venue,m):
    rail=f"rail_{m}";rows=[];i=380;n=len(x);last=None
    while i<n-35:
        if pd.isna(x.loc[i,rail]) or pd.isna(x.loc[i-1,rail]) or pd.isna(x.loc[i,"sma111"]):
            i+=1;continue
        cross=float(x.loc[i-1,"c"])<=float(x.loc[i-1,rail]) and float(x.loc[i,"c"])>float(x.loc[i,rail])
        if not cross:i+=1;continue
        if last is not None and x.loc[i,"time"]-last<pd.Timedelta(days=7):i+=1;continue
        prev=x.loc[i-24:i-1];below=float((prev["c"]<prev[rail]).mean()) if len(prev)==24 else 0
        maxext=float(x.loc[i,"c"])/float(x.loc[i,rail])-1
        touch=None
        for j in range(i+1,min(n-32,i+49)):
            rv=float(x.loc[j,rail]);ma111=float(x.loc[j,"sma111"])
            maxext=max(maxext,float(x.loc[j,"h"])/rv-1)
            # Broken rail must not have catastrophically failed before the MA111 support attempt.
            if float(x.loc[j,"c"])<rv*.97:break
            if float(x.loc[j,"l"])<=ma111<=float(x.loc[j,"h"]):
                touch=j;break
        if touch is None:i+=1;continue
        j=touch;ma111=float(x.loc[j,"sma111"]);close=float(x.loc[j,"c"])
        common={
            "family":"ma111_after_rail","venue":venue,"coin":coin,"rail_mult":m,
            "breakout_time":x.loc[i,"time"],"touch_time":x.loc[j,"time"],
            "below_frac_24":below,"extension_before_touch":maxext,"delay_bars":j-i,
            "touch_close_vs_rail":close/float(x.loc[j,rail])-1,
            "touch_close_vs_ma111":close/ma111-1,
            "base_sma350":float(x.loc[j,"sma350"]),"rail_value":float(x.loc[j,rail])
        }
        nxt=NEXT.get(m)
        if j+1<n:
            o=outcome(x,j+1,ma111,"touch",j,nxt)
            ctx_idx=max(i,j-1)
            if o:rows.append({**common,"mode":"touch","rsi_context_time":x.loc[ctx_idx,"time"],**support_context(x,ctx_idx,i),**o})
        if close>=ma111 and j+1<n:
            o=outcome(x,j+1,float(x.loc[j+1,"o"]),"confirmed",None,nxt)
            if o:rows.append({**common,"mode":"confirmed","rsi_context_time":x.loc[j,"time"],**support_context(x,j,i),**o})
        if -0.03<=close/ma111-1<0 and j+2<n and float(x.loc[j+1,"c"])>=float(x.loc[j+1,"sma111"]):
            o=outcome(x,j+2,float(x.loc[j+2,"o"]),"reclaim",None,nxt)
            if o:rows.append({**common,"mode":"reclaim","reclaim_time":x.loc[j+1,"time"],"rsi_context_time":x.loc[j+1,"time"],**support_context(x,j+1,i),**o})
        last=x.loc[i,"time"];i=j+1
    return rows

def scan_cross111_350(x,coin,venue):
    rows=[];n=len(x);last=None
    for i in range(380,n-35):
        if any(pd.isna(x.loc[i,c]) for c in ["sma111","sma350"]):continue
        cross=float(x.loc[i-1,"sma111"])<=float(x.loc[i-1,"sma350"]) and float(x.loc[i,"sma111"])>float(x.loc[i,"sma350"])
        if not cross or float(x.loc[i,"c"])<float(x.loc[i,"sma350"]):continue
        if last is not None and x.loc[i,"time"]-last<pd.Timedelta(days=10):continue
        touch=None
        for j in range(i+1,min(n-32,i+49)):
            ma=float(x.loc[j,"sma111"])
            if float(x.loc[j,"l"])<=ma<=float(x.loc[j,"h"]):
                touch=j;break
        if touch is None:continue
        j=touch;ma=float(x.loc[j,"sma111"]);close=float(x.loc[j,"c"])
        common={
            "family":"ma111_cross350_retest","venue":venue,"coin":coin,"rail_mult":1,
            "breakout_time":x.loc[i,"time"],"touch_time":x.loc[j,"time"],
            "below_frac_24":np.nan,"extension_before_touch":float(x.loc[i:j,"h"].max()/ma-1),
            "delay_bars":j-i,"touch_close_vs_ma111":close/ma-1,
            "base_sma350":float(x.loc[j,"sma350"]),"rail_value":float(x.loc[j,"sma350"])
        }
        if j+1<n:
            o=outcome(x,j+1,ma,"touch",j,2)
            ctx_idx=max(i,j-1)
            if o:rows.append({**common,"mode":"touch","rsi_context_time":x.loc[ctx_idx,"time"],**support_context(x,ctx_idx,i),**o})
        if close>=ma and j+1<n:
            o=outcome(x,j+1,float(x.loc[j+1,"o"]),"confirmed",None,2)
            if o:rows.append({**common,"mode":"confirmed","rsi_context_time":x.loc[j,"time"],**support_context(x,j,i),**o})
        last=x.loc[i,"time"]
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
        time.sleep(.02)
    a=[]
    for z in rows:
        try:a.append({"time":pd.to_datetime(int(z[0]),unit="ms",utc=True),"o":float(z[1]),"h":float(z[2]),"l":float(z[3]),"c":float(z[4]),"v":float(z[5])})
        except Exception:pass
    return pd.DataFrame(a) if a else pd.DataFrame()

def scan(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-golden-ma-rails/1.0"
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0);start=end-timedelta(days=args.days)
    if args.venue=="hl":coins=hl_universe(s);fetch=hl_fetch;venue="Hyperliquid primary perps"
    else:coins=bn_universe(s,args.binance_symbols);fetch=bn_fetch;venue="Binance USDT spot"
    selected=coins[args.shard_index::args.shard_count];rows=[];cov=[]
    for p,coin in enumerate(selected,1):
        try:
            x=prep(fetch(s,coin,start,end))
            if len(x)<500:cov.append((coin,len(x),0,"short"));continue
            rr=[]
            for m in MULTS:
                rr.extend(scan_rail_retests(x,coin,venue,m))
                rr.extend(scan_ma111_after_rail_break(x,coin,venue,m))
            rr.extend(scan_cross111_350(x,coin,venue))
            rows.extend(rr);cov.append((coin,len(x),len(rr),"ok"));print(p,len(selected),coin,len(rr))
        except Exception as e:
            cov.append((coin,0,0,"error:"+str(e)[:120]));print("ERR",coin,e)
        time.sleep(.04)
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
def stats(g,cost):
    r={"n":len(g)}
    for t in TARGETS:
        k=str(t*100).replace(".","p")
        r[f"hit{k}"]=rate(g[f"hit{k}_5d"]) if len(g) else np.nan
        r[f"risk{k}"]=rate(g[f"t{k}_before_s7p5_5d"]) if len(g) else np.nan
        r[f"roi{k}"]=econ(g,t,cost) if len(g) else np.nan
    if len(g) and "hit_next_rail_10d" in g:
        r["next_rail_5d"]=rate(g["hit_next_rail_5d"])
        r["next_rail_10d"]=rate(g["hit_next_rail_10d"])
        r["median_next_gap"]=float(pd.to_numeric(g["next_rail_gap_at_entry"],errors="coerce").median())
    return r

def rsi_filter_defs():
    return [
        ("none",lambda d:pd.Series(True,index=d.index)),
        ("bottom_turn",lambda d:d["rsi_bottom_turn"].astype(str).str.lower().isin(["true","1"])),
        ("higher_floor6_3",lambda d:pd.to_numeric(d["rsi_above_floor6"],errors="coerce")>=3),
        ("higher_floor6_6",lambda d:pd.to_numeric(d["rsi_above_floor6"],errors="coerce")>=6),
        ("higher_floor12_5",lambda d:pd.to_numeric(d["rsi_above_floor12"],errors="coerce")>=5),
        ("pullback_recovery4",lambda d:pd.to_numeric(d["rsi_recovery_from_pullback_min"],errors="coerce")>=4),
        ("rsi_up2",lambda d:d["rsi_up2"].astype(str).str.lower().isin(["true","1"])),
        ("bull_div6",lambda d:d["rsi_bull_div6"].astype(str).str.lower().isin(["true","1"])),
    ]

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
            for c in ["breakout_time","touch_time","entry_time","reclaim_time"]:
                if c in z:z[c]=pd.to_datetime(z[c],utc=True,errors="coerce")
    hl.to_csv(out/"hyperliquid_events.csv",index=False);bn.to_csv(out/"binance_events.csv",index=False)
    hc.to_csv(out/"hyperliquid_coverage.csv",index=False);bc.to_csv(out/"binance_coverage.csv",index=False)

    rows=[]
    families=sorted(set(hl["family"])) if len(hl) else []
    for fam in families:
        mults=sorted(pd.to_numeric(hl.loc[hl["family"]==fam,"rail_mult"],errors="coerce").dropna().unique())
        for m,mode,bf,ext,delay,rising,rsi_name in itertools.product(mults,MODES,BELOW_FRACS,EXTS,MAX_DELAYS,(False,True),[x[0] for x in rsi_filter_defs()]):
            def filt(z):
                q=z[(z["family"]==fam)&(pd.to_numeric(z["rail_mult"],errors="coerce")==m)&(z["mode"]==mode)].copy()
                if q.empty:return q
                if fam!="ma111_cross350_retest":
                    q=q[pd.to_numeric(q["below_frac_24"],errors="coerce")>=bf]
                    q=q[pd.to_numeric(q["extension_before_touch"],errors="coerce")>=ext]
                q=q[pd.to_numeric(q["delay_bars"],errors="coerce")<=delay]
                if rising:q=q[pd.to_numeric(q["sma350_slope6"],errors="coerce")>=0]
                fn=dict((n,f) for n,f in rsi_filter_defs())[rsi_name]
                q=q[fn(q)]
                return q.sort_values("entry_time").drop_duplicates(["coin","entry_time"]).reset_index(drop=True)
            h=filt(hl);b=filt(bn)
            if len(h)<35:continue
            hc0=max(1,int(len(h)*.60));bc0=max(1,int(len(b)*.60)) if len(b) else 0
            ht,hh=h.iloc[:hc0],h.iloc[hc0:];bh=b.iloc[bc0:] if len(b) else b
            st,sh,sb=stats(ht,HL_RT),stats(hh,HL_RT),stats(bh,BN_RT)
            rows.append({
                "family":fam,"rail_mult":m,"mode":mode,"below_frac":bf,"min_extension":ext,
                "max_delay_bars":delay,"require_rising_sma350":rising,"rsi_filter":rsi_name,
                "train_n":len(ht),"train_hit5":st["hit5p0"],"train_risk":st["risk5p0"],"train_roi":st["roi5p0"],
                "hl_hold_n":len(hh),"hl_hit5":sh["hit5p0"],"hl_risk":sh["risk5p0"],"hl_roi":sh["roi5p0"],
                "hl_hit7p5":sh["hit7p5"],"hl_hit10":sh["hit10p0"],"hl_hit15":sh["hit15p0"],
                "hl_roi7p5":sh["roi7p5"],"hl_roi10":sh["roi10p0"],"hl_roi15":sh["roi15p0"],
                "hl_next_rail_5d":sh.get("next_rail_5d",np.nan),"hl_next_rail_10d":sh.get("next_rail_10d",np.nan),
                "median_next_rail_gap":sh.get("median_next_gap",np.nan),
                "bin_hold_n":len(bh),"bin_hit5":sb.get("hit5p0",np.nan),"bin_risk":sb.get("risk5p0",np.nan),"bin_roi":sb.get("roi5p0",np.nan),
                "bin_hit10":sb.get("hit10p0",np.nan),"bin_next_rail_10d":sb.get("next_rail_10d",np.nan),
            })
    z=pd.DataFrame(rows)
    if len(z):
        z["eligible_train"]=(z["train_n"]>=25)&(z["train_hit5"]>=.72)&(z["train_risk"]>=.65)&(z["train_roi"]>=.008)
        z["crossvenue_pass"]=z["eligible_train"]&(z["hl_hold_n"]>=20)&(z["hl_hit5"]>=.78)&(z["hl_risk"]>=.70)&(z["hl_roi"]>=.0125)&(z["bin_hold_n"]>=15)&(z["bin_hit5"]>=.75)&(z["bin_risk"]>=.67)&(z["bin_roi"]>=.010)
        z["score"]=.28*z["train_hit5"]+.24*z["train_risk"]+.20*np.clip(z["train_roi"]/.04,-1,1)+.12*np.clip(np.log1p(z["train_n"])/np.log(200),0,1)+.08*(z["rsi_filter"]!="none")+.08*(z["family"]=="ma111_after_rail")
        z=z.sort_values(["crossvenue_pass","eligible_train","score","train_n"],ascending=[False,False,False,False])
    z.to_csv(out/"variant_results.csv",index=False)

    # Separate ladder report: after support at rung m, probability of touching next rung.
    ladder=z[z["rail_mult"].isin([1,2,3,5])].copy() if len(z) else pd.DataFrame()
    if len(ladder):
        ladder=ladder.sort_values(["crossvenue_pass","hl_next_rail_10d","hl_hold_n"],ascending=[False,False,False])
    ladder.to_csv(out/"next_rail_ladder.csv",index=False)

    # Forced screenshot examples for visual audit.
    examples=bn[(bn["coin"].isin(["CHIPUSDT","STRKUSDT"]))].copy() if len(bn) else pd.DataFrame()
    examples.to_csv(out/"chip_strk_examples.csv",index=False)

    winners=z[z["crossvenue_pass"]==True].head(15) if len(z) else pd.DataFrame()
    (out/"candidate_registry.json").write_text(json.dumps({
        "generated_at":pd.Timestamp.utcnow().isoformat(),
        "rails":["MA111","SMA350","SMA350x2","SMA350x3","SMA350x5","SMA350x8"],
        "important_note":"x2/x3/x5/x8 multiply the SMA350 VALUE; they are not longer SMA periods",
        "candidates":winners.to_dict("records") if len(winners) else []
    },indent=2,default=str))
    lines=[
        "GOLDEN-RATIO 4H MA RAIL RESEARCH","",
        "Rails: MA111, SMA350, SMA350×2, ×3, ×5, ×8.",
        "The multiples are price rails derived from SMA350, not SMA lengths.","",
        f"HL event rows: {len(hl)} | coins: {int((hc['status']=='ok').sum()) if len(hc) else 0}",
        f"Binance event rows: {len(bn)} | symbols: {int((bc['status']=='ok').sum()) if len(bc) else 0}","",
        "TOP VARIANTS",z.head(30).to_string(index=False) if len(z) else "none","",
        "NEXT-RAIL LADDER",ladder.head(30).to_string(index=False) if len(ladder) else "none","",
        "CHIP / STRK EXAMPLES",examples.head(60).to_string(index=False) if len(examples) else "none","",
        "CROSS-VENUE SURVIVORS",winners.to_string(index=False) if len(winners) else "none","",
        "Any survivor still requires actual Hyperliquid funding, 1m same-bar replay, $10k L2 capacity, and forward shadow testing."
    ]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n");print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--mode",choices=["scan","aggregate"],default="scan");ap.add_argument("--venue",choices=["hl","bn"],default="hl")
    ap.add_argument("--days",type=int,default=600);ap.add_argument("--binance-symbols",type=int,default=160)
    ap.add_argument("--shard-index",type=int,default=0);ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--out",default="golden_ma_out");ap.add_argument("--input-dir",default="golden_ma_shards")
    args=ap.parse_args();scan(args) if args.mode=="scan" else aggregate(args)
if __name__=="__main__":main()
