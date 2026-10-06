#!/usr/bin/env python3
"""
Multi-timeframe repeated-support / support-maturation research.

Origin hypothesis: NIGHT 4H showed 1st SMA350 defense -> 2nd defense -> 3rd
defense -> expansion. This script generalizes that structure across 1h, 4h, 1d.

Research questions:
- Does success improve after 2nd/3rd/4th defense?
- Does higher-TF trend alignment improve lower-TF repeated-support entries?
- Are higher RSI lows / lower sell volume / tighter penetration across touches useful?
- Is MA111/MA350 confluence stronger than standalone SMA350?
- Which exits maximize realized ROI: +5/+7.5/+10, trailing, or MA-loss exit?

Research only. No live promotion.
"""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
import requests

API="https://fapi.binance.com"
STOP=.075
RT=2*(.00050+.0010)
TARGETS=(.05,.075,.10)
TFS=("1h","4h","1d")

def get(s,path,params=None,tries=8):
    last=None
    for k in range(tries):
        try:
            r=s.get(API+path,params=params,timeout=35)
            if r.ok:return r.json()
            last=RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
        except Exception as e:last=e
        time.sleep(min(10,1.3*(k+1)))
    raise RuntimeError(str(last))

def universe(s,n=180):
    info=get(s,"/fapi/v1/exchangeInfo"); tick=get(s,"/fapi/v1/ticker/24hr")
    qv={x["symbol"]:float(x.get("quoteVolume") or 0) for x in tick if isinstance(x,dict) and x.get("symbol")}
    arr=[]
    for x in info.get("symbols",[]):
        if x.get("status")!="TRADING" or x.get("quoteAsset")!="USDT" or x.get("contractType")!="PERPETUAL":continue
        b=str(x.get("baseAsset") or "")
        if b.startswith("USD") or b in {"USDC","FDUSD","TUSD","DAI","BUSD"}:continue
        arr.append(x["symbol"])
    arr=sorted(set(arr),key=lambda z:qv.get(z,0),reverse=True)[:n]
    if "NIGHTUSDT" not in arr:
        allsyms=[x["symbol"] for x in info.get("symbols",[]) if x.get("symbol")=="NIGHTUSDT" and x.get("status")=="TRADING"]
        if allsyms: arr.append("NIGHTUSDT")
    return arr

def fetch(s,sym,tf,start,end):
    step={"1h":3600_000,"4h":4*3600_000,"1d":24*3600_000}[tf]
    cur=int(start.timestamp()*1000); stop=int(end.timestamp()*1000); rows=[]
    while cur<stop:
        b=get(s,"/fapi/v1/klines",{"symbol":sym,"interval":tf,"startTime":cur,"endTime":stop,"limit":1500})
        if not b:break
        rows.extend(b); nxt=int(b[-1][0])+step
        if nxt<=cur:break
        cur=nxt
        if len(b)<1500:break
        time.sleep(.02)
    out=[]
    for z in rows:
        try: out.append({"time":pd.to_datetime(int(z[0]),unit="ms",utc=True),"o":float(z[1]),"h":float(z[2]),"l":float(z[3]),"c":float(z[4]),"v":float(z[5])})
        except: pass
    return pd.DataFrame(out).drop_duplicates("time").sort_values("time").reset_index(drop=True) if out else pd.DataFrame()

def rsi(c,p=14):
    d=c.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep(x):
    x=x.copy(); c=x["c"]; v=x["v"].fillna(0)
    x["sma111"]=c.rolling(111,min_periods=111).mean()
    x["sma350"]=c.rolling(350,min_periods=350).mean()
    x["ma111_live"]=c.shift(1).rolling(110,min_periods=110).mean()
    x["ma350_live"]=c.shift(1).rolling(349,min_periods=349).mean()
    x["s111_slope"]=x["sma111"]/x["sma111"].shift(6)-1
    x["s350_slope"]=x["sma350"]/x["sma350"].shift(6)-1
    x["spread"]=x["sma111"]/x["sma350"]-1
    x["rsi"]=rsi(c)
    x["vol_ratio"]=v/v.rolling(20,min_periods=10).median().replace(0,np.nan)
    rng=(x["h"]-x["l"]).replace(0,np.nan)
    x["lower_wick"]=(np.minimum(x["o"],x["c"])-x["l"])/rng
    x["close_loc"]=(x["c"]-x["l"])/rng
    x["range_ratio"]=(rng/c)/(rng/c).rolling(20,min_periods=10).median().replace(0,np.nan)
    return x

def outcome(x,i,entry,bars):
    f=x.iloc[i+1:min(len(x),i+1+bars)]
    if len(f)<max(8,bars//3):return None
    out={"mfe":float(f["h"].max()/entry-1),"mae":float(f["l"].min()/entry-1)}
    for t in TARGETS:
        tk=sk=None
        for j,(_,r) in enumerate(f.iterrows()):
            if tk is None and float(r["h"])>=entry*(1+t):tk=j
            if sk is None and float(r["l"])<=entry*(1-STOP):sk=j
        k=str(t*100).replace(".","p")
        out[f"hit{k}"]=float(f["h"].max())>=entry*(1+t)
        out[f"t{k}_before_stop"]=tk is not None and (sk is None or tk<sk)
    peak=entry; trail=None; maexit=None
    for _,r in f.iterrows():
        peak=max(peak,float(r["h"]))
        if trail is None and float(r["l"])<=peak*.95: trail=float(r["c"])/entry-1
        if maexit is None and pd.notna(r["sma111"]) and float(r["c"])<float(r["sma111"]): maexit=float(r["c"])/entry-1
    last=float(f.iloc[-1]["c"])/entry-1
    out["trail5_exit"]=trail if trail is not None else last
    out["ma111_loss_exit"]=maexit if maexit is not None else last
    out["timeout"]=last
    return out

def scan_series(x,coin,tf,ma):
    live=f"ma{ma}_live"; sma=f"sma{ma}"
    if len(x)<420:return []
    maxbars={"1h":120,"4h":36,"1d":15}[tf]
    rows=[]; cluster=[]; last=-999
    for i in range(370,len(x)-maxbars-1):
        lv=x.loc[i,live]
        if pd.isna(lv):continue
        if not (float(x.loc[i,"l"])<=float(lv)<=float(x.loc[i,"h"])):continue
        if i-last<2:continue
        # retain only touches in same broad support episode
        cluster=[j for j in cluster if i-j<={"1h":168,"4h":60,"1d":45}[tf]]
        ordinal=len(cluster)+1
        prev=cluster[-1] if cluster else None
        cluster.append(i); last=i
        entry=float(lv)
        o=outcome(x,i,entry,maxbars)
        if not o:continue
        prior_rsi=float(x.loc[prev-1,"rsi"]) if prev is not None and prev>0 and pd.notna(x.loc[prev-1,"rsi"]) else np.nan
        prior_vol=float(x.loc[prev-1,"vol_ratio"]) if prev is not None and prev>0 and pd.notna(x.loc[prev-1,"vol_ratio"]) else np.nan
        pre=i-1
        between=x.loc[prev:i-1] if prev is not None else x.loc[max(0,i-12):i-1]
        bounce=float(between["h"].max()/entry-1) if len(between) else np.nan
        penetration=float(x.loc[i,"l"]/entry-1)
        rows.append({
          "coin":coin,"tf":tf,"ma":ma,"time":x.loc[i,"time"],"ordinal":ordinal,
          "close_above":float(x.loc[i,"c"])>=float(x.loc[i,sma]),
          "sweep":float(x.loc[i,"l"])<entry and float(x.loc[i,"c"])>=float(x.loc[i,sma]),
          "rsi":x.loc[pre,"rsi"],"rsi_higher_than_prior":bool(pd.notna(prior_rsi) and pd.notna(x.loc[pre,"rsi"]) and float(x.loc[pre,"rsi"])>prior_rsi),
          "vol_ratio":x.loc[pre,"vol_ratio"],"volume_contracting":bool(pd.notna(prior_vol) and pd.notna(x.loc[pre,"vol_ratio"]) and float(x.loc[pre,"vol_ratio"])<prior_vol),
          "lower_wick":x.loc[i,"lower_wick"],"close_loc":x.loc[i,"close_loc"],"range_ratio":x.loc[i,"range_ratio"],
          "s111_slope":x.loc[pre,"s111_slope"],"s350_slope":x.loc[pre,"s350_slope"],"spread":x.loc[pre,"spread"],
          "ma111_above_350":bool(pd.notna(x.loc[pre,"sma111"]) and pd.notna(x.loc[pre,"sma350"]) and float(x.loc[pre,"sma111"])>float(x.loc[pre,"sma350"])),
          "bounce_since_prior":bounce,"penetration":penetration,
          **o
        })
    return rows

def scan(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-ma-support-maturation-mtf/1.0"
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    syms=universe(s,args.symbols)[args.shard_index::args.shard_count]
    rows=[];cov=[]
    for p,sym in enumerate(syms,1):
        try:
            for tf,days in (("1h",420),("4h",900),("1d",1500)):
                x=prep(fetch(s,sym,tf,end-timedelta(days=days),end))
                for ma in (111,350): rows.extend(scan_series(x,sym,tf,ma))
            cov.append((sym,"ok"));print(p,len(syms),sym)
        except Exception as e:
            cov.append((sym,"error:"+str(e)[:120]));print("ERR",sym,e)
    pd.DataFrame(rows).to_csv(out/f"events_{args.shard_index:02d}.csv",index=False)
    pd.DataFrame(cov,columns=["coin","status"]).to_csv(out/f"coverage_{args.shard_index:02d}.csv",index=False)

def rate(s): return float(s.fillna(False).astype(bool).mean()) if len(s) else np.nan
def roi5(g):
    if g.empty:return np.nan
    w=g["t5p0_before_stop"].fillna(False).astype(bool)
    stopped=(~w)&(pd.to_numeric(g["mae"],errors="coerce")<=-STOP)
    timeout=pd.to_numeric(g["timeout"],errors="coerce").fillna(0).clip(-STOP,.05)
    return float(np.mean(np.where(w,.05,np.where(stopped,-STOP,timeout)))-RT)

def aggregate(args):
    root=Path(args.input_dir);out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    fs=[]
    for p in root.rglob("events_*.csv"):
        try:
            z=pd.read_csv(p)
            if len(z):fs.append(z)
        except:pass
    e=pd.concat(fs,ignore_index=True) if fs else pd.DataFrame()
    if e.empty:raise SystemExit("no events")
    e["time"]=pd.to_datetime(e["time"],utc=True,errors="coerce")
    e=e.sort_values("time").drop_duplicates(["coin","tf","ma","time"]).reset_index(drop=True)
    e.to_csv(out/"events.csv",index=False)
    rows=[]
    for tf in TFS:
      for ma in (111,350):
       for ordinal in (1,2,3,4):
        for close in (False,True):
         for rising in (False,True):
          for rh in (False,True):
           for vc in (False,True):
            q=e[(e["tf"]==tf)&(pd.to_numeric(e["ma"],errors="coerce")==ma)&(pd.to_numeric(e["ordinal"],errors="coerce")==ordinal)].copy()
            if close:q=q[q["close_above"].astype(str).str.lower().isin(["true","1"])]
            if rising:q=q[pd.to_numeric(q["s350_slope"],errors="coerce")>=0]
            if rh:q=q[q["rsi_higher_than_prior"].astype(str).str.lower().isin(["true","1"])]
            if vc:q=q[q["volume_contracting"].astype(str).str.lower().isin(["true","1"])]
            if len(q)<50:continue
            q=q.sort_values("time").drop_duplicates(["coin","time"])
            cut=int(len(q)*.60);tr=q.iloc[:cut];ho=q.iloc[cut:]
            rows.append({
              "tf":tf,"ma":ma,"ordinal":ordinal,"close_above":close,"rising_sma350":rising,"rsi_higher":rh,"volume_contracting":vc,
              "train_n":len(tr),"train_hit5":rate(tr["hit5p0"]),"train_risk5":rate(tr["t5p0_before_stop"]),"train_roi5":roi5(tr),
              "hold_n":len(ho),"hold_hit5":rate(ho["hit5p0"]),"hold_risk5":rate(ho["t5p0_before_stop"]),"hold_hit10":rate(ho["hit10p0"]),"hold_roi5":roi5(ho),
              "trail5_roi":float(pd.to_numeric(ho["trail5_exit"],errors="coerce").mean()-RT),
              "ma111_loss_roi":float(pd.to_numeric(ho["ma111_loss_exit"],errors="coerce").mean()-RT)
            })
    z=pd.DataFrame(rows)
    if len(z):
        z["eligible"]=(z["train_n"]>=30)&(z["hold_n"]>=20)&(z["train_hit5"]>=.75)&(z["hold_hit5"]>=.80)&(z["hold_risk5"]>=.72)&(z["hold_roi5"]>=.015)
        z=z.sort_values(["eligible","hold_hit5","hold_roi5","hold_n"],ascending=[False,False,False,False])
    z.to_csv(out/"variants.csv",index=False)
    night=e[e["coin"].astype(str).eq("NIGHTUSDT")].copy()
    night.to_csv(out/"night_all_timeframes.csv",index=False)
    winners=z[z.get("eligible",False)==True].head(20) if len(z) and "eligible" in z else pd.DataFrame()
    (out/"candidate_registry.json").write_text(json.dumps({"research_only":True,"night_origin":True,"candidates":winners.to_dict("records") if len(winners) else []},indent=2,default=str))
    lines=["MULTI-TIMEFRAME SUPPORT MATURATION","",
           "Origin: NIGHT 1st support -> 2nd support -> 3rd support -> expansion.",
           f"events: {len(e)} | NIGHT events: {len(night)} | eligible variants: {len(winners)}","",
           "TOP VARIANTS",z.head(40).to_string(index=False) if len(z) else "none","",
           "NIGHT DIAGNOSTIC",night.head(80).to_string(index=False) if len(night) else "No NIGHT events available.","",
           "Research only. No live promotion."]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--mode",choices=["scan","aggregate"],default="scan")
    ap.add_argument("--symbols",type=int,default=180);ap.add_argument("--shard-index",type=int,default=0);ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--out",default="ma_support_mtf_out");ap.add_argument("--input-dir",default="ma_support_mtf_shards")
    a=ap.parse_args();scan(a) if a.mode=="scan" else aggregate(a)
if __name__=="__main__":main()
