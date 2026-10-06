#!/usr/bin/env python3
"""
Repeated MA support / "support maturation" research.

Inspired by the user's NIGHT 4H example:
price breaks above SMA350, returns to the orange SMA350 repeatedly, holds/reclaims
it multiple times, then trends sharply higher.

This is a distinct mechanism from "first retest":
- 1st support touch establishes the line.
- 2nd/3rd touches may confirm demand / accumulation.
- Repeated touches can also weaken support, so success is measured by ordinal
  touch count, spacing, bounce strength, volume/RSI behavior, and MA confluence.

Point-in-time mechanics:
- live SMA350 exact touch price = mean(previous 349 completed 4H closes)
- live MA111 exact touch price = mean(previous 110 completed 4H closes)
- touch-entry filters use only data known before that touch candle starts
- confirmed/reclaim entries may use completed support-candle information

Research dimensions:
- 1st / 2nd / 3rd / 4th support touches
- exact touch vs confirmed close vs 1-2 bar reclaim
- minimum bounce between touches
- spacing between touches
- rising/flat/falling SMA350
- MA111/SMA350 confluence and MA111 position
- RSI level, higher RSI lows across touches, RSI trend
- touch volume contraction, breakout volume, OBV/CMF proxy
- candle range/wicks/close location
- price compression between touches
- BTC regime / relative return
- probability of +5/+7.5/+10/+15 and reaching SMA350x2

Discovery: Hyperliquid primary perps.
External validation: Binance USD-M perpetuals, including NIGHTUSDT when listed.
No live promotion.
"""
from __future__ import annotations

import argparse,itertools,json,time,math
from pathlib import Path
from datetime import datetime,timedelta,timezone
from math import sqrt
import numpy as np
import pandas as pd
import requests

HL_API="https://api.hyperliquid.xyz/info"
BF_API="https://fapi.binance.com"
OUT=Path("crypto/research/results_ma_repeated_support")
TARGETS=(.05,.075,.10,.15)
STOP=.075
HL_RT=2*(.00045+.0010)
BF_RT=2*(.00050+.0010)

def rsi(c,p=14):
    d=c.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep(x):
    x=x.copy().sort_values("time").drop_duplicates("time").reset_index(drop=True)
    c=x["c"];v=x["v"].fillna(0)
    x["sma111"]=c.rolling(111,min_periods=111).mean()
    x["sma350"]=c.rolling(350,min_periods=350).mean()
    x["ma111_touch"]=c.shift(1).rolling(110,min_periods=110).mean()
    x["ma350_touch"]=c.shift(1).rolling(349,min_periods=349).mean()
    x["sma350_slope6"]=x["sma350"]/x["sma350"].shift(6)-1
    x["sma350_slope18"]=x["sma350"]/x["sma350"].shift(18)-1
    x["sma111_slope6"]=x["sma111"]/x["sma111"].shift(6)-1
    x["ma_spread"]=x["sma111"]/x["sma350"]-1
    x["ma_confluence_abs"]=x["ma_spread"].abs()
    x["ret1"]=c.pct_change();x["ret3"]=c/c.shift(3)-1;x["ret6"]=c/c.shift(6)-1
    x["rsi"]=rsi(c);x["rsi_d1"]=x["rsi"].diff();x["rsi_d3"]=x["rsi"].diff(3)
    x["rsi_up2"]=(x["rsi_d1"]>0)&(x["rsi_d1"].shift(1)>0)
    floor6=x["rsi"].shift(1).rolling(6,min_periods=3).min()
    x["rsi_above_floor6"]=x["rsi"]-floor6
    x["vol_med20"]=v.rolling(20,min_periods=10).median()
    x["vol_ratio20"]=v/x["vol_med20"].replace(0,np.nan)
    direction=np.sign(c.diff()).fillna(0)
    x["obv"]=(direction*v).cumsum()
    x["obv_d3_norm"]=x["obv"].diff(3)/v.rolling(20,min_periods=10).sum().replace(0,np.nan)
    rng=(x["h"]-x["l"]).replace(0,np.nan)
    x["range_pct"]=rng/c.replace(0,np.nan)
    x["range_med20"]=x["range_pct"].rolling(20,min_periods=10).median()
    x["range_ratio20"]=x["range_pct"]/x["range_med20"].replace(0,np.nan)
    x["lower_wick"]=(np.minimum(x["o"],x["c"])-x["l"])/rng
    x["close_location"]=(x["c"]-x["l"])/rng
    x["rail2"]=x["sma350"]*2
    return x

def merge_btc(x,btc):
    if btc.empty:return x
    b=btc[["time","ret1","ret3","rsi"]].copy()
    b.columns=["time","btc_ret1","btc_ret3","btc_rsi"]
    y=pd.merge_asof(x.sort_values("time"),b.sort_values("time"),on="time",direction="backward")
    y["rel_ret1"]=y["ret1"]-y["btc_ret1"];y["rel_ret3"]=y["ret3"]-y["btc_ret3"]
    return y

def outcome(x,idx,entry,mode="confirmed"):
    # confirmed/reclaim entries are next open after support proof
    start=idx+1
    if start>=len(x):return None
    if mode=="touch":
        start=idx+1
        immediate_stop=float(x.loc[idx,"l"])<=entry*(1-STOP)
    else:
        immediate_stop=False
    f5=x.iloc[start:min(len(x),start+30)]
    f10=x.iloc[start:min(len(x),start+60)]
    if len(f5)<24:return None
    out={"entry":float(entry),"entry_time":x.loc[start,"time"],"touch_candle_immediate_stop":immediate_stop}
    for lab,f in (("5d",f5),("10d",f10)):
        mfe=float(f["h"].max()/entry-1);mae=float(f["l"].min()/entry-1)
        if immediate_stop:mae=min(mae,-STOP)
        out[f"mfe_{lab}"]=mfe;out[f"mae_{lab}"]=mae;out[f"close_ret_{lab}"]=float(f.iloc[-1]["c"]/entry-1)
        for t in TARGETS:
            k=str(t*100).replace(".","p");out[f"hit{k}_{lab}"]=mfe>=t and not immediate_stop
    for t in TARGETS:
        k=str(t*100).replace(".","p")
        if immediate_stop:out[f"t{k}_before_s7p5_5d"]=False;continue
        tk=sk=None
        for j,(_,r) in enumerate(f5.iterrows()):
            if tk is None and float(r["h"])>=entry*(1+t):tk=j
            if sk is None and float(r["l"])<=entry*(1-STOP):sk=j
        out[f"t{k}_before_s7p5_5d"]=tk is not None and (sk is None or tk<sk)
    # dynamic x2 rail
    hit2=False
    for ridx,r in f10.iterrows():
        if pd.notna(x.loc[ridx,"rail2"]) and float(r["h"])>=float(x.loc[ridx,"rail2"]):
            hit2=True;break
    out["hit_sma350x2_10d"]=hit2
    return out

def touch_candidates(x,break_idx,maxbars=72):
    """Return successful/attempted SMA350 support touches after breakout."""
    touches=[];last_touch=-999
    for j in range(break_idx+1,min(len(x)-31,break_idx+1+maxbars)):
        if pd.isna(x.loc[j,"ma350_touch"]):continue
        lv=float(x.loc[j,"ma350_touch"])
        if j-last_touch<2:continue
        exact=float(x.loc[j,"l"])<=lv<=float(x.loc[j,"h"])
        if not exact:continue
        close=float(x.loc[j,"c"])
        status="confirmed" if close>=float(x.loc[j,"sma350"]) else "below"
        reclaim=None
        if status=="below" and close>=float(x.loc[j,"sma350"])*.97:
            for k in range(j+1,min(len(x)-31,j+3)):
                if float(x.loc[k,"c"])>=float(x.loc[k,"sma350"]):
                    reclaim=k;status="reclaim";break
        touches.append({"touch":j,"level":lv,"status":status,"reclaim":reclaim})
        last_touch=j
        if len(touches)>=5:break
    return touches

def scan_coin(x,coin,venue):
    rows=[];n=len(x);last_break=None
    for i in range(380,n-40):
        if pd.isna(x.loc[i,"sma350"]) or pd.isna(x.loc[i-1,"sma350"]):continue
        cross=float(x.loc[i-1,"c"])<=float(x.loc[i-1,"sma350"]) and float(x.loc[i,"c"])>float(x.loc[i,"sma350"])
        if not cross:continue
        if last_break is not None and x.loc[i,"time"]-last_break<pd.Timedelta(days=10):continue
        prev=x.loc[i-24:i-1]
        below=float((prev["c"]<prev["sma350"]).mean()) if len(prev)==24 else 0
        if below<.75:continue
        ts=touch_candidates(x,i,72)
        if not ts:continue

        breakout_vol=float(x.loc[i,"vol_ratio20"]) if pd.notna(x.loc[i,"vol_ratio20"]) else np.nan
        successful=[t for t in ts if t["status"] in ("confirmed","reclaim")]
        for ordinal,t in enumerate(ts,1):
            j=t["touch"];ctx=j-1
            if ctx<=i:continue
            # Bounce since prior support touch, or since breakout for first.
            p0=i if ordinal==1 else ts[ordinal-2]["touch"]
            seg=x.loc[p0:j-1]
            bounce=float(seg["h"].max()/float(x.loc[p0,"ma350_touch" if ordinal>1 else "sma350"])-1) if len(seg) else np.nan
            spacing=j-p0
            prior_rsis=[float(x.loc[q["touch"]-1,"rsi"]) for q in ts[:ordinal-1] if q["touch"]-1>=0 and pd.notna(x.loc[q["touch"]-1,"rsi"])]
            prior_vols=[float(x.loc[q["touch"]-1,"vol_ratio20"]) for q in ts[:ordinal-1] if q["touch"]-1>=0 and pd.notna(x.loc[q["touch"]-1,"vol_ratio20"])]
            this_rsi=float(x.loc[ctx,"rsi"]) if pd.notna(x.loc[ctx,"rsi"]) else np.nan
            this_vol=float(x.loc[ctx,"vol_ratio20"]) if pd.notna(x.loc[ctx,"vol_ratio20"]) else np.nan
            rsi_higher_all=bool(prior_rsis and np.isfinite(this_rsi) and this_rsi>max(prior_rsis[-2:]))
            vol_contract=bool(prior_vols and np.isfinite(this_vol) and this_vol<prior_vols[-1])
            base={
                "venue":venue,"coin":coin,"breakout_time":x.loc[i,"time"],"touch_time":x.loc[j,"time"],
                "ordinal_touch":ordinal,"touch_status":t["status"],"below_frac_24":below,
                "bars_since_breakout":j-i,"spacing_from_prev_touch":spacing,
                "bounce_since_prev_touch":bounce,"breakout_vol_ratio20":breakout_vol,
                "rsi":this_rsi,"rsi_up2":bool(x.loc[ctx,"rsi_up2"]),"rsi_above_floor6":x.loc[ctx,"rsi_above_floor6"],
                "rsi_higher_than_prior_touches":rsi_higher_all,
                "touch_vol_ratio20":this_vol,"touch_volume_contracting":vol_contract,
                "obv_d3_norm":x.loc[ctx,"obv_d3_norm"],"range_ratio20":x.loc[ctx,"range_ratio20"],
                "lower_wick":x.loc[ctx,"lower_wick"],"close_location":x.loc[ctx,"close_location"],
                "sma350_slope6":x.loc[ctx,"sma350_slope6"],"sma350_slope18":x.loc[ctx,"sma350_slope18"],
                "sma111_slope6":x.loc[ctx,"sma111_slope6"],"ma_spread":x.loc[ctx,"ma_spread"],
                "ma_confluence_abs":x.loc[ctx,"ma_confluence_abs"],
                "ma111_above_350":bool(x.loc[ctx,"sma111"]>=x.loc[ctx,"sma350"]),
                "btc_rsi":x.loc[ctx].get("btc_rsi"),"btc_ret3":x.loc[ctx].get("btc_ret3"),
                "rel_ret3":x.loc[ctx].get("rel_ret3"),
                "prior_successful_touch_count":sum(1 for q in ts[:ordinal-1] if q["status"] in ("confirmed","reclaim")),
            }
            # confirmed/reclaim entry
            if t["status"]=="confirmed":
                o=outcome(x,j,float(x.loc[j+1,"o"]),"confirmed")
                if o:rows.append({**base,"entry_style":"confirmed",**o})
            elif t["status"]=="reclaim" and t["reclaim"] is not None:
                k=t["reclaim"]
                if k+1<len(x):
                    o=outcome(x,k,float(x.loc[k+1,"o"]),"confirmed")
                    if o:rows.append({**base,"entry_style":"reclaim","confirm_time":x.loc[k,"time"],**o})
            # exact touch entry using only pre-touch context
            o=outcome(x,j,float(t["level"]),"touch")
            if o:rows.append({**base,"entry_style":"touch",**o})
        last_break=x.loc[i,"time"]
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

def bf_get(s,path,params=None,tries=8):
    last=None
    for k in range(tries):
        try:
            r=s.get(BF_API+path,params=params,timeout=35)
            if r.ok:return r.json()
            last=RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
        except Exception as e:last=e
        time.sleep(min(12,1.5*(k+1)))
    raise RuntimeError(str(last))
def bf_universe(s,n=160):
    info=bf_get(s,"/fapi/v1/exchangeInfo");tick=bf_get(s,"/fapi/v1/ticker/24hr")
    qv={x["symbol"]:float(x.get("quoteVolume") or 0) for x in tick if isinstance(x,dict) and x.get("symbol")}
    arr=[]
    for x in info.get("symbols",[]):
        if x.get("status")!="TRADING" or x.get("quoteAsset")!="USDT" or x.get("contractType")!="PERPETUAL":continue
        b=str(x.get("baseAsset") or "")
        if b.startswith("USD") or b in {"USDC","FDUSD","TUSD","DAI","BUSD"}:continue
        arr.append(x["symbol"])
    arr=sorted(set(arr),key=lambda z:qv.get(z,0),reverse=True)
    out=arr[:n]
    if "NIGHTUSDT" in arr and "NIGHTUSDT" not in out:out.append("NIGHTUSDT")
    return out
def bf_fetch(s,symbol,start,end):
    cur=int(start.timestamp()*1000);stop=int(end.timestamp()*1000);step=4*3600*1000;rows=[]
    while cur<stop:
        b=bf_get(s,"/fapi/v1/klines",{"symbol":symbol,"interval":"4h","startTime":cur,"endTime":stop,"limit":1500})
        if not b:break
        rows.extend(b);nxt=int(b[-1][0])+step
        if nxt<=cur:break
        cur=nxt
        if len(b)<1500:break
    a=[]
    for z in rows:
        try:a.append({"time":pd.to_datetime(int(z[0]),unit="ms",utc=True),"o":float(z[1]),"h":float(z[2]),"l":float(z[3]),"c":float(z[4]),"v":float(z[5])})
        except Exception:pass
    return pd.DataFrame(a) if a else pd.DataFrame()

def scan(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-ma-repeat-support/1.0"
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0);start=end-timedelta(days=args.days)
    if args.venue=="hl":
        coins=hl_universe(s);fetch=hl_fetch;venue="Hyperliquid primary perps";btc_coin="BTC"
    else:
        coins=bf_universe(s,args.binance_symbols);fetch=bf_fetch;venue="Binance USD-M perpetuals";btc_coin="BTCUSDT"
    btc=prep(fetch(s,btc_coin,start,end))
    rows=[];cov=[]
    selected=coins[args.shard_index::args.shard_count]
    for p,coin in enumerate(selected,1):
        try:
            x=prep(fetch(s,coin,start,end))
            if len(x)<500:cov.append((coin,len(x),0,"short"));continue
            x=merge_btc(x,btc)
            rr=scan_coin(x,coin,venue);rows.extend(rr);cov.append((coin,len(x),len(rr),"ok"))
            print(p,len(selected),coin,len(rr))
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
def stats(g,cost):
    if g.empty:return {"n":0}
    return {"n":len(g),"hit5":rate(g["hit5p0_5d"]),"risk":rate(g["t5p0_before_s7p5_5d"]),"roi":econ(g,.05,cost),
            "hit10":rate(g["hit10p0_5d"]),"hit15":rate(g["hit15p0_5d"]),"rail2_10d":rate(g["hit_sma350x2_10d"]),
            "wilson":wilson(int(g["hit5p0_5d"].astype(bool).sum()),len(g))}

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
    hl=cat("hl_events_*.csv");bf=cat("bf_events_*.csv");hc=cat("hl_coverage_*.csv");bc=cat("bf_coverage_*.csv")
    for z in (hl,bf):
        if len(z):
            for c in ["breakout_time","touch_time","entry_time","confirm_time"]:
                if c in z:z[c]=pd.to_datetime(z[c],utc=True,errors="coerce")
    hl.to_csv(out/"hyperliquid_events.csv",index=False);bf.to_csv(out/"binance_perp_events.csv",index=False)
    hc.to_csv(out/"hyperliquid_coverage.csv",index=False);bc.to_csv(out/"binance_perp_coverage.csv",index=False)

    rows=[]
    # Predeclared interpretable combinations.
    for ordinal,style,minbounce,minspace,rising,confluence,rsi_higher,vol_contract in itertools.product(
        (1,2,3,4),("touch","confirmed","reclaim"),(0,.02,.04,.06),(2,4,6),(False,True),(None,.02,.05),(False,True),(False,True)
    ):
        def filt(z):
            if z.empty:return z
            q=z[(pd.to_numeric(z["ordinal_touch"],errors="coerce")==ordinal)&(z["entry_style"]==style)].copy()
            q=q[pd.to_numeric(q["bounce_since_prev_touch"],errors="coerce")>=minbounce]
            q=q[pd.to_numeric(q["spacing_from_prev_touch"],errors="coerce")>=minspace]
            if rising:q=q[pd.to_numeric(q["sma350_slope6"],errors="coerce")>=0]
            if confluence is not None:q=q[pd.to_numeric(q["ma_confluence_abs"],errors="coerce")<=confluence]
            if rsi_higher:q=q[q["rsi_higher_than_prior_touches"].astype(str).str.lower().isin(["true","1"])]
            if vol_contract:q=q[q["touch_volume_contracting"].astype(str).str.lower().isin(["true","1"])]
            return q.sort_values("entry_time").drop_duplicates(["coin","entry_time"]).reset_index(drop=True)
        h=filt(hl);b=filt(bf)
        if len(h)<45:continue
        hc0=max(1,int(len(h)*.60));bc0=max(1,int(len(b)*.60)) if len(b) else 0
        ht,hh=h.iloc[:hc0],h.iloc[hc0:];bh=b.iloc[bc0:] if len(b) else b
        st,sh,sb=stats(ht,HL_RT),stats(hh,HL_RT),stats(bh,BF_RT)
        eligible=(st["n"]>=25 and st["hit5"]>=.72 and st["risk"]>=.65 and st["roi"]>=.008
                  and sh["n"]>=20 and sh["hit5"]>=.78 and sh["risk"]>=.70 and sh["roi"]>=.0125 and sh["wilson"]>=.60
                  and sb.get("n",0)>=15 and sb.get("hit5",0)>=.75 and sb.get("risk",0)>=.67 and sb.get("roi",-1)>=.01)
        rows.append({"ordinal_touch":ordinal,"entry_style":style,"min_bounce":minbounce,"min_spacing":minspace,
                     "rising_sma350":rising,"max_ma_confluence":confluence,"rsi_higher_touch_low":rsi_higher,
                     "volume_contracting":vol_contract,
                     "train_n":st["n"],"train_hit5":st["hit5"],"train_risk":st["risk"],"train_roi":st["roi"],
                     "hl_hold_n":sh["n"],"hl_hit5":sh["hit5"],"hl_risk":sh["risk"],"hl_roi":sh["roi"],"hl_hit10":sh["hit10"],"hl_hit15":sh["hit15"],"hl_rail2_10d":sh["rail2_10d"],
                     "bin_hold_n":sb.get("n",0),"bin_hit5":sb.get("hit5",np.nan),"bin_risk":sb.get("risk",np.nan),"bin_roi":sb.get("roi",np.nan),"bin_rail2_10d":sb.get("rail2_10d",np.nan),
                     "eligible":eligible})
    z=pd.DataFrame(rows)
    if len(z):
        z["score"]=.25*z["hl_hit5"]+.20*z["hl_risk"]+.20*np.clip(z["hl_roi"]/.03,-1,1)+.15*z["bin_hit5"]+.10*z["bin_risk"]+.10*np.clip(z["bin_roi"]/.03,-1,1)
        z=z.sort_values(["eligible","score","hl_hold_n"],ascending=[False,False,False])
    z.to_csv(out/"variant_results.csv",index=False)

    # Direct ordinal baseline report.
    ordrows=[]
    for ordinal in (1,2,3,4):
        for style in ("touch","confirmed","reclaim"):
            h=hl[(pd.to_numeric(hl["ordinal_touch"],errors="coerce")==ordinal)&(hl["entry_style"]==style)].sort_values("entry_time").drop_duplicates(["coin","entry_time"])
            b=bf[(pd.to_numeric(bf["ordinal_touch"],errors="coerce")==ordinal)&(bf["entry_style"]==style)].sort_values("entry_time").drop_duplicates(["coin","entry_time"])
            if not len(h):continue
            c=max(1,int(len(h)*.60));d=max(1,int(len(b)*.60)) if len(b) else 0
            ordrows.append({"ordinal_touch":ordinal,"entry_style":style,
                            **{"train_"+k:v for k,v in stats(h.iloc[:c],HL_RT).items()},
                            **{"hold_"+k:v for k,v in stats(h.iloc[c:],HL_RT).items()},
                            **{"bin_"+k:v for k,v in stats(b.iloc[d:],BF_RT).items()}})
    ords=pd.DataFrame(ordrows);ords.to_csv(out/"ordinal_baselines.csv",index=False)

    night=bf[bf["coin"].astype(str).eq("NIGHTUSDT")].copy() if len(bf) else pd.DataFrame()
    night.to_csv(out/"night_diagnostic.csv",index=False)

    winners=z[z["eligible"]==True].head(15) if len(z) else pd.DataFrame()
    (out/"candidate_registry.json").write_text(json.dumps({"generated_at":pd.Timestamp.utcnow().isoformat(),"candidates":winners.to_dict("records") if len(winners) else []},indent=2,default=str))
    lines=[
      "REPEATED SMA350 SUPPORT / SUPPORT MATURATION RESEARCH","",
      "Tests whether 2nd/3rd/4th successful SMA350 support touches become better entries than the first touch, and whether RSI higher lows, contracting touch volume, bounce strength, MA111/350 confluence, and rising SMA improve the edge.","",
      f"HL rows: {len(hl)} | coins ok: {int((hc['status']=='ok').sum()) if len(hc) else 0}",
      f"Binance perp rows: {len(bf)} | symbols ok: {int((bc['status']=='ok').sum()) if len(bc) else 0}","",
      "ORDINAL BASELINES",ords.to_string(index=False) if len(ords) else "none","",
      "TOP FILTERED VARIANTS",z.head(30).to_string(index=False) if len(z) else "none","",
      "NIGHT DIAGNOSTIC",night.head(50).to_string(index=False) if len(night) else "NIGHTUSDT not returned / insufficient history","",
      "CROSS-VENUE SURVIVORS",winners.to_string(index=False) if len(winners) else "none","",
      "No live promotion."
    ]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n");print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--mode",choices=["scan","aggregate"],default="scan");ap.add_argument("--venue",choices=["hl","bf"],default="hl")
    ap.add_argument("--days",type=int,default=600);ap.add_argument("--binance-symbols",type=int,default=160);ap.add_argument("--shard-index",type=int,default=0);ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--out",default="repeat_ma_out");ap.add_argument("--input-dir",default="repeat_ma_shards")
    args=ap.parse_args();scan(args) if args.mode=="scan" else aggregate(args)
if __name__=="__main__":main()
