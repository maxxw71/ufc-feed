#!/usr/bin/env python3
"""
Point-in-time validation of the proposed C6 MA method.

This corrects a subtle issue in ordinary candle-based moving-average backtests:
an intrabar touch of SMA111 must use the SMA value known AT THE TOUCH, not the
SMA111 calculated later from the final 4H close.

For a 111-period SMA and current price P:
    SMA111(P) = (sum(previous 110 closes) + P) / 111
The exact equality P = SMA111(P) occurs at:
    P = mean(previous 110 completed closes)
So the historical touch price is fully point-in-time and does not depend on the
future 4H close.

Frozen rule (no tuning here):
- 4H close crosses from below to above SMA350
- >=75% of previous 24 closes were below SMA350
- before the first MA111 touch, price extends >=5% above live SMA350
- first MA111 touch occurs within 24 bars
- no completed pre-touch bar closes >3% below SMA350
- prior completed 4H RSI has risen for two consecutive bars
- buy exact point-in-time MA111 touch
- +5% target / -7.5% stop / 5d; also report higher targets

Hyperliquid train/holdout and frozen Binance replication.
No live promotion is performed by this script.
"""
from __future__ import annotations
import argparse,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
import numpy as np
import pandas as pd
import requests

HL_API="https://api.hyperliquid.xyz/info"
BN_API="https://data-api.binance.vision"
OUT=Path("crypto/research/results_ma111_sma350_pit")
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
    x["sma350_close"]=x["c"].rolling(350,min_periods=350).mean()
    # Point-in-time fixed price where current price equals live MA111.
    x["ma111_touch_level"]=x["c"].shift(1).rolling(110,min_periods=110).mean()
    x["sum_prev349"]=x["c"].shift(1).rolling(349,min_periods=349).sum()
    x["rsi"]=rsi(x["c"])
    x["rsi_d1"]=x["rsi"].diff()
    x["rsi_up2"]=(x["rsi_d1"]>0)&(x["rsi_d1"].shift(1)>0)
    return x

def live_sma350_at_price(sum_prev349,p):
    return (sum_prev349+p)/350.0

def extension_ratio(row):
    s=float(row["sum_prev349"]);p=float(row["h"])
    ma=live_sma350_at_price(s,p)
    return p/ma-1 if ma>0 else np.nan

def outcome(x,j,entry):
    # Touch is intrabar in j. Same touch-candle target cannot be credited;
    # a same-candle stop is counted conservatively.
    immediate_stop=float(x.loc[j,"l"])<=entry*(1-STOP)
    f5=x.iloc[j+1:min(len(x),j+31)]
    f10=x.iloc[j+1:min(len(x),j+61)]
    if len(f5)<24:return None
    out={"entry":float(entry),"entry_time":x.loc[j,"time"],"touch_candle_immediate_stop":immediate_stop}
    for lab,f in (("5d",f5),("10d",f10)):
        mfe=float(f["h"].max()/entry-1)
        mae=float(f["l"].min()/entry-1)
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
        for kbar,(_,r) in enumerate(f5.iterrows()):
            if tk is None and float(r["h"])>=entry*(1+t):tk=kbar
            if sk is None and float(r["l"])<=entry*(1-STOP):sk=kbar
        out[f"t{k}_before_s7p5_5d"]=tk is not None and (sk is None or tk<sk)
    return out

def scan_events(x,coin,venue):
    rows=[];n=len(x);last=None
    for i in range(380,n-35):
        if pd.isna(x.loc[i,"sma350_close"]) or pd.isna(x.loc[i-1,"sma350_close"]):continue
        if not (float(x.loc[i-1,"c"])<=float(x.loc[i-1,"sma350_close"]) and float(x.loc[i,"c"])>float(x.loc[i,"sma350_close"])):
            continue
        if last is not None and x.loc[i,"time"]-last<pd.Timedelta(days=7):continue
        prev=x.loc[i-24:i-1]
        if len(prev)<24:continue
        below=float((prev["c"]<prev["sma350_close"]).mean())
        if below<.75:continue

        maxext=extension_ratio(x.loc[i])
        touch=None
        for j in range(i+1,min(n-31,i+25)):
            if pd.isna(x.loc[j,"ma111_touch_level"]) or pd.isna(x.loc[j,"sum_prev349"]):continue
            # completed bar failure of the SMA350 breakout before support test
            if float(x.loc[j-1,"c"])<float(x.loc[j-1,"sma350_close"])*.97:
                break
            maxext=max(maxext,extension_ratio(x.loc[j]))
            level=float(x.loc[j,"ma111_touch_level"])
            if float(x.loc[j,"l"])<=level<=float(x.loc[j,"h"]):
                touch=j;break
        if touch is None or not np.isfinite(maxext) or maxext<.05:continue
        # Signal must be known before the touch candle starts.
        ctx=touch-1
        if ctx<1 or not bool(x.loc[ctx,"rsi_up2"]):continue
        entry=float(x.loc[touch,"ma111_touch_level"])
        o=outcome(x,touch,entry)
        if o is None:continue
        rows.append({
            "venue":venue,"coin":coin,"breakout_time":x.loc[i,"time"],
            "touch_time":x.loc[touch,"time"],"below_frac_24":below,
            "extension_before_touch":maxext,"delay_bars":touch-i,
            "ma111_point_in_time_touch":entry,
            "rsi_context_time":x.loc[ctx,"time"],"rsi_context":float(x.loc[ctx,"rsi"]),
            **o
        })
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
    a=[]
    for z in rows:
        try:a.append({"time":pd.to_datetime(int(z[0]),unit="ms",utc=True),"o":float(z[1]),"h":float(z[2]),"l":float(z[3]),"c":float(z[4]),"v":float(z[5])})
        except Exception:pass
    return pd.DataFrame(a) if a else pd.DataFrame()

def scan(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-pit-ma111-sma350/1.0"
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0);start=end-timedelta(days=args.days)
    if args.venue=="hl":coins=hl_universe(s);fetch=hl_fetch;venue="Hyperliquid primary perps"
    else:coins=bn_universe(s,args.binance_symbols);fetch=bn_fetch;venue="Binance USDT spot"
    rows=[];cov=[]
    for p,coin in enumerate(coins[args.shard_index::args.shard_count],1):
        try:
            x=prep(fetch(s,coin,start,end))
            if len(x)<500:cov.append((coin,len(x),0,"short"));continue
            rr=scan_events(x,coin,venue);rows.extend(rr);cov.append((coin,len(x),len(rr),"ok"))
            print(p,coin,len(rr))
        except Exception as e:
            cov.append((coin,0,0,"error:"+str(e)[:120]))
    pd.DataFrame(rows).to_csv(out/f"{args.venue}_events_{args.shard_index:02d}.csv",index=False)
    pd.DataFrame(cov,columns=["coin","bars","events","status"]).to_csv(out/f"{args.venue}_coverage_{args.shard_index:02d}.csv",index=False)

def rate(s):
    q=s.dropna();return float(q.astype(bool).mean()) if len(q) else np.nan
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
    return r

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
            for c in ["breakout_time","touch_time","rsi_context_time","entry_time"]:z[c]=pd.to_datetime(z[c],utc=True,errors="coerce")
            z.sort_values("entry_time",inplace=True);z.reset_index(drop=True,inplace=True)
    hl.to_csv(out/"hyperliquid_events.csv",index=False);bn.to_csv(out/"binance_events.csv",index=False)
    hc.to_csv(out/"hyperliquid_coverage.csv",index=False);bc.to_csv(out/"binance_coverage.csv",index=False)
    hcut=max(1,int(len(hl)*.60));bcut=max(1,int(len(bn)*.60)) if len(bn) else 0
    ht,hh=hl.iloc[:hcut],hl.iloc[hcut:];bt,bh=(bn.iloc[:bcut],bn.iloc[bcut:]) if len(bn) else (bn,bn)
    a,b,c,d=stats(ht,HL_RT),stats(hh,HL_RT),stats(bt,BN_RT),stats(bh,BN_RT)
    passed=bool(
        a["n"]>=25 and a["hit5p0"]>=.72 and a["risk5p0"]>=.65 and a["roi5p0"]>=.008
        and b["n"]>=20 and b["hit5p0"]>=.78 and b["risk5p0"]>=.70 and b["roi5p0"]>=.0125
        and d["n"]>=15 and d["hit5p0"]>=.75 and d["risk5p0"]>=.67 and d["roi5p0"]>=.010
    )
    result={"rule":"point-in-time SMA350 breakout -> >=5% extension -> first live-MA111 touch <=24 bars -> prior RSI up2",
            "hyperliquid_train":a,"hyperliquid_holdout":b,"binance_train":c,"binance_holdout":d,"crossvenue_pass":passed}
    (out/"summary.json").write_text(__import__("json").dumps(result,indent=2))
    pd.DataFrame([
        {"venue":"HL","split":"train",**a},{"venue":"HL","split":"holdout",**b},
        {"venue":"Binance","split":"train",**c},{"venue":"Binance","split":"holdout",**d}
    ]).to_csv(out/"summary.csv",index=False)
    chip=bn[(bn["coin"]=="CHIPUSDT")&(bn["touch_time"]>=pd.Timestamp("2026-08-10",tz="UTC"))&(bn["touch_time"]<=pd.Timestamp("2026-08-25",tz="UTC"))]
    chip.to_csv(out/"chip_aug2026.csv",index=False)
    lines=["POINT-IN-TIME MA111 / SMA350 VALIDATION","",result["rule"],"",
           "HL TRAIN "+str(a),"HL HOLDOUT "+str(b),"BINANCE TRAIN "+str(c),"BINANCE HOLDOUT "+str(d),"",
           f"CROSSVENUE_PASS={passed}","",
           "CHIP AUG 2026",chip.to_string(index=False) if len(chip) else "none"]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n");print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--mode",choices=["scan","aggregate"],default="scan");ap.add_argument("--venue",choices=["hl","bn"],default="hl")
    ap.add_argument("--days",type=int,default=600);ap.add_argument("--binance-symbols",type=int,default=160);ap.add_argument("--shard-index",type=int,default=0);ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--out",default="pit_out");ap.add_argument("--input-dir",default="pit_shards")
    args=ap.parse_args();scan(args) if args.mode=="scan" else aggregate(args)
if __name__=="__main__":main()
