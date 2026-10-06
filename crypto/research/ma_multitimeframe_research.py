#!/usr/bin/env python3
"""
Multi-timeframe MA research for Appwiza crypto.

Goal: discover high-probability/high-ROI entry+exit structures using SMA111/SMA350
across 1h, 4h and 1d without look-ahead.

Mechanisms tested:
- lower-TF touch/reclaim/sweep of SMA111 or SMA350
- higher-TF trend alignment (price > MA111 > MA350; MA slopes)
- 1h entry inside 4h/1d bullish regime
- 4h entry inside daily bullish regime
- multi-TF confluence when MAs cluster
- repeated support touch count
- RSI reversal/higher-low behavior
- volume/range capitulation
- extension from MA before pullback
- dynamic exits: fixed +5/+7.5/+10, fast-MA loss, RSI rollover, trailing MA

Discovery venue: Binance USD-M perpetuals.
This is research only; no live promotion.
"""
from __future__ import annotations
import argparse,itertools,json,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
from math import sqrt
import numpy as np
import pandas as pd
import requests

API="https://fapi.binance.com"
STOP=.075
RT=2*(.00050+.0010)
TARGETS=(.05,.075,.10)
TFS=("1h","4h","1d")
OUT=Path("crypto/research/results_ma_multitimeframe")

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
    a=[]
    for x in info.get("symbols",[]):
        if x.get("status")!="TRADING" or x.get("quoteAsset")!="USDT" or x.get("contractType")!="PERPETUAL":continue
        b=str(x.get("baseAsset") or "")
        if b.startswith("USD") or b in {"USDC","FDUSD","TUSD","DAI","BUSD"}:continue
        a.append(x["symbol"])
    return sorted(set(a),key=lambda z:qv.get(z,0),reverse=True)[:n]

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
    a=[]
    for z in rows:
        try:a.append({"time":pd.to_datetime(int(z[0]),unit="ms",utc=True),"o":float(z[1]),"h":float(z[2]),"l":float(z[3]),"c":float(z[4]),"v":float(z[5])})
        except:pass
    return pd.DataFrame(a).drop_duplicates("time").sort_values("time").reset_index(drop=True) if a else pd.DataFrame()

def rsi(c,p=14):
    d=c.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep(x):
    x=x.copy(); c=x["c"]; v=x["v"].fillna(0)
    for n in (20,50,111,350):
        x[f"sma{n}"]=c.rolling(n,min_periods=n).mean()
        x[f"sma{n}_live"]=c.shift(1).rolling(n-1,min_periods=n-1).mean()
    x["rsi"]=rsi(c); x["rsi_d1"]=x["rsi"].diff(); x["rsi_d3"]=x["rsi"].diff(3)
    x["ret3"]=c/c.shift(3)-1; x["ret6"]=c/c.shift(6)-1; x["ret12"]=c/c.shift(12)-1
    x["s111_6"]=x["sma111"]/x["sma111"].shift(6)-1
    x["s350_6"]=x["sma350"]/x["sma350"].shift(6)-1
    x["dist111"]=c/x["sma111"]-1; x["dist350"]=c/x["sma350"]-1
    x["spread"]=x["sma111"]/x["sma350"]-1
    x["vol_ratio"]=v/v.rolling(20,min_periods=10).median().replace(0,np.nan)
    rng=(x["h"]-x["l"]).replace(0,np.nan)
    x["range_pct"]=rng/c.replace(0,np.nan)
    x["range_ratio"]=x["range_pct"]/x["range_pct"].rolling(20,min_periods=10).median().replace(0,np.nan)
    x["lower_wick"]=(np.minimum(x["o"],x["c"])-x["l"])/rng
    x["close_loc"]=(x["c"]-x["l"])/rng
    return x

def enrich_lower(low, high, prefix):
    cols=["time","c","sma111","sma350","s111_6","s350_6","rsi","dist111","dist350","spread"]
    h=high[cols].copy()
    h.columns=["time"]+[prefix+c for c in cols[1:]]
    return pd.merge_asof(low.sort_values("time"),h.sort_values("time"),on="time",direction="backward")

def outcome(x,i,entry,maxbars):
    start=i+1
    if start>=len(x):return None
    f=x.iloc[start:min(len(x),start+maxbars)]
    if len(f)<max(8,maxbars//3):return None
    out={}
    for t in TARGETS:
        tk=sk=None
        for j,(_,r) in enumerate(f.iterrows()):
            if tk is None and float(r["h"])>=entry*(1+t):tk=j
            if sk is None and float(r["l"])<=entry*(1-STOP):sk=j
        key=str(t*100).replace(".","p")
        out[f"t{key}_before_stop"]=tk is not None and (sk is None or tk<sk)
        out[f"hit{key}"]=float(f["h"].max())>=entry*(1+t)
    # dynamic exits
    peak=entry; trail_exit=None; ma_exit=None; rsi_exit=None
    for j,(idx,r) in enumerate(f.iterrows()):
        peak=max(peak,float(r["h"]))
        if trail_exit is None and float(r["l"])<=peak*.95: trail_exit=(float(r["c"])/entry-1,j)
        if ma_exit is None and pd.notna(r["sma20"]) and float(r["c"])<float(r["sma20"]): ma_exit=(float(r["c"])/entry-1,j)
        if rsi_exit is None and pd.notna(r["rsi"]) and float(r["rsi"])<50 and j>=2: rsi_exit=(float(r["c"])/entry-1,j)
    last=float(f.iloc[-1]["c"])/entry-1
    out["exit_trail5"]=trail_exit[0] if trail_exit else last
    out["exit_sma20"]=ma_exit[0] if ma_exit else last
    out["exit_rsi50"]=rsi_exit[0] if rsi_exit else last
    out["mfe"]=float(f["h"].max()/entry-1); out["mae"]=float(f["l"].min()/entry-1); out["timeout"]=last
    return out

def scan_tf(x,coin,tf):
    rows=[]; min_i=370; maxbars={"1h":120,"4h":30,"1d":12}[tf]
    touch_gap={"1h":6,"4h":3,"1d":2}[tf]
    prior_touches={111:[],350:[]}
    for i in range(min_i,len(x)-maxbars-1):
        r=x.loc[i]; p=x.loc[i-1]
        for ma in (111,350):
            lv=r[f"sma{ma}_live"]
            if pd.isna(lv):continue
            touched=float(r["l"])<=float(lv)<=float(r["h"])
            if not touched:continue
            prior_touches[ma]=[j for j in prior_touches[ma] if i-j<=80]
            ord_touch=1+len(prior_touches[ma]); prior_touches[ma].append(i)
            ctx=i-1
            entry=float(lv)
            o=outcome(x,i,entry,maxbars)
            if not o:continue
            close_above=float(r["c"])>=float(r[f"sma{ma}"])
            sweep=float(r["l"])<float(lv) and close_above
            reclaim=close_above and float(p["c"])<float(p[f"sma{ma}"]) if pd.notna(p[f"sma{ma}"]) else False
            base={
              "coin":coin,"tf":tf,"time":r["time"],"ma":ma,"entry":entry,
              "ordinal_touch":ord_touch,"close_above":close_above,"sweep":sweep,"reclaim":reclaim,
              "rsi_prev":p["rsi"],"rsi_d1_prev":p["rsi_d1"],"rsi_d3_prev":p["rsi_d3"],
              "vol_ratio_prev":p["vol_ratio"],"range_ratio_prev":p["range_ratio"],
              "lower_wick":r["lower_wick"],"close_loc":r["close_loc"],
              "s111_6_prev":p["s111_6"],"s350_6_prev":p["s350_6"],
              "dist111_prev":p["dist111"],"dist350_prev":p["dist350"],"spread_prev":p["spread"],
              "mom3_prev":p["ret3"],"mom6_prev":p["ret6"],"mom12_prev":p["ret12"],
            }
            for c in x.columns:
                if c.startswith("h4_") or c.startswith("d1_"): base[c]=p.get(c)
            rows.append({**base,**o})
    return rows

def scan(args):
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    s=requests.Session(); s.headers["User-Agent"]="appwiza-ma-mtf/1.0"
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    syms=universe(s,args.symbols)[args.shard_index::args.shard_count]
    rows=[]; cov=[]
    for pos,sym in enumerate(syms,1):
        try:
            d1=prep(fetch(s,sym,"1d",end-timedelta(days=1500),end))
            h4=prep(fetch(s,sym,"4h",end-timedelta(days=900),end))
            h1=prep(fetch(s,sym,"1h",end-timedelta(days=420),end))
            if len(d1)>=380 and len(h4)>=450:
                h4e=enrich_lower(h4,d1,"d1_")
                rows.extend(scan_tf(h4e,sym,"4h"))
            if len(h1)>=500 and len(h4)>=380 and len(d1)>=380:
                h1e=enrich_lower(h1,h4,"h4_"); h1e=enrich_lower(h1e,d1,"d1_")
                rows.extend(scan_tf(h1e,sym,"1h"))
            if len(d1)>=400: rows.extend(scan_tf(d1,sym,"1d"))
            cov.append((sym,len(h1),len(h4),len(d1),"ok"))
            print(pos,len(syms),sym,len(rows))
        except Exception as e:
            cov.append((sym,0,0,0,"error:"+str(e)[:120])); print("ERR",sym,e)
    pd.DataFrame(rows).to_csv(out/f"events_{args.shard_index:02d}.csv",index=False)
    pd.DataFrame(cov,columns=["coin","h1","h4","d1","status"]).to_csv(out/f"coverage_{args.shard_index:02d}.csv",index=False)

def rate(s):
    return float(s.fillna(False).astype(bool).mean()) if len(s) else np.nan

def roi5(g):
    if g.empty:return np.nan
    win=g["t5p0_before_stop"].fillna(False).astype(bool)
    stopped=(~win)&(pd.to_numeric(g["mae"],errors="coerce")<=-STOP)
    timeout=pd.to_numeric(g["timeout"],errors="coerce").fillna(0).clip(-STOP,.05)
    gross=np.where(win,.05,np.where(stopped,-STOP,timeout))
    return float(np.mean(gross)-RT)

def aggregate(args):
    root=Path(args.input_dir); out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    fs=[]
    for p in root.rglob("events_*.csv"):
        try:
            z=pd.read_csv(p)
            if len(z):fs.append(z)
        except:pass
    e=pd.concat(fs,ignore_index=True) if fs else pd.DataFrame()
    if e.empty:raise SystemExit("no events")
    e["time"]=pd.to_datetime(e["time"],utc=True,errors="coerce")
    e=e.sort_values("time").drop_duplicates(["coin","tf","time","ma"]).reset_index(drop=True)
    e.to_csv(out/"events.csv",index=False)
    rows=[]
    # predeclared interpretable filters, not arbitrary tree fitting
    for tf,ma,style,trend4,trend1,rsi_lo,rsi_hi,volmin,ordmin in itertools.product(
      TFS,(111,350),("touch","close","sweep","reclaim"),(False,True),(False,True),
      (None,30,35,40),(None,55,60,65),(None,1.25,1.75),(1,2,3)):
        q=e[(e["tf"]==tf)&(pd.to_numeric(e["ma"],errors="coerce")==ma)&(pd.to_numeric(e["ordinal_touch"],errors="coerce")>=ordmin)].copy()
        if style=="close": q=q[q["close_above"].astype(str).str.lower().isin(["true","1"])]
        elif style=="sweep": q=q[q["sweep"].astype(str).str.lower().isin(["true","1"])]
        elif style=="reclaim": q=q[q["reclaim"].astype(str).str.lower().isin(["true","1"])]
        if rsi_lo is not None:q=q[pd.to_numeric(q["rsi_prev"],errors="coerce")>=rsi_lo]
        if rsi_hi is not None:q=q[pd.to_numeric(q["rsi_prev"],errors="coerce")<=rsi_hi]
        if volmin is not None:q=q[pd.to_numeric(q["vol_ratio_prev"],errors="coerce")>=volmin]
        if trend4 and tf=="1h":
            q=q[(pd.to_numeric(q.get("h4_c"),errors="coerce")>pd.to_numeric(q.get("h4_sma111"),errors="coerce"))&
                (pd.to_numeric(q.get("h4_sma111"),errors="coerce")>pd.to_numeric(q.get("h4_sma350"),errors="coerce"))&
                (pd.to_numeric(q.get("h4_s111_6"),errors="coerce")>=0)]
        if trend1 and tf in ("1h","4h"):
            q=q[(pd.to_numeric(q.get("d1_c"),errors="coerce")>pd.to_numeric(q.get("d1_sma111"),errors="coerce"))&
                (pd.to_numeric(q.get("d1_sma111"),errors="coerce")>pd.to_numeric(q.get("d1_sma350"),errors="coerce"))]
        q=q.sort_values("time").drop_duplicates(["coin","time"])
        if len(q)<60:continue
        cut=int(len(q)*.60); tr=q.iloc[:cut]; ho=q.iloc[cut:]
        rec={"tf":tf,"ma":ma,"style":style,"trend4":trend4,"trend1":trend1,"rsi_lo":rsi_lo,"rsi_hi":rsi_hi,"volmin":volmin,"ordmin":ordmin,
             "train_n":len(tr),"train_hit5":rate(tr["hit5p0"]),"train_risk5":rate(tr["t5p0_before_stop"]),"train_roi5":roi5(tr),
             "hold_n":len(ho),"hold_hit5":rate(ho["hit5p0"]),"hold_risk5":rate(ho["t5p0_before_stop"]),"hold_hit10":rate(ho["hit10p0"]),"hold_roi5":roi5(ho),
             "trail5_roi":float(pd.to_numeric(ho["exit_trail5"],errors="coerce").mean()-RT),
             "sma20_exit_roi":float(pd.to_numeric(ho["exit_sma20"],errors="coerce").mean()-RT),
             "rsi50_exit_roi":float(pd.to_numeric(ho["exit_rsi50"],errors="coerce").mean()-RT)}
        rec["eligible"]=bool(rec["train_n"]>=35 and rec["hold_n"]>=25 and rec["train_hit5"]>=.75 and rec["hold_hit5"]>=.80 and rec["hold_risk5"]>=.72 and rec["hold_roi5"]>=.015)
        rows.append(rec)
    z=pd.DataFrame(rows)
    if len(z):
        z=z.sort_values(["eligible","hold_hit5","hold_roi5","hold_n"],ascending=[False,False,False,False])
    z.to_csv(out/"variants.csv",index=False)
    winners=z[z["eligible"]==True].head(20) if len(z) else pd.DataFrame()
    reg={"generated_at":pd.Timestamp.utcnow().isoformat(),"research_only":True,"candidates":winners.to_dict("records") if len(winners) else []}
    (out/"candidate_registry.json").write_text(json.dumps(reg,indent=2,default=str))
    lines=["MULTI-TIMEFRAME MA RESEARCH","",f"events: {len(e)}",f"variants tested: {len(z)}",f"eligible: {len(winners)}","",
           "TOP VARIANTS",z.head(40).to_string(index=False) if len(z) else "none","",
           "RESEARCH ONLY — no live promotion without independent replay/deep audit."]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--mode",choices=["scan","aggregate"],default="scan")
    ap.add_argument("--symbols",type=int,default=180); ap.add_argument("--shard-index",type=int,default=0); ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--out",default="ma_mtf_out"); ap.add_argument("--input-dir",default="ma_mtf_shards")
    a=ap.parse_args(); scan(a) if a.mode=="scan" else aggregate(a)
if __name__=="__main__":main()
