#!/usr/bin/env python3
"""
Second-pass MA support research: allow price to sit around MA111 / MA350 after
the first retest, then require RSI confirmation before entry.

Motivation: the CHIP Aug-2026 example shows that an excellent support trade can
have a first MA touch candle that closes slightly below the MA and has a falling
1-bar RSI. The useful information may arrive over the next several 4H bars:
price stabilizes around the MA while RSI forms a higher low / turns upward.

Frozen event logic:
1) 75% or 90% of prior 24 closes below SMA111/SMA350.
2) breakout close above SMA by 0% or 1%.
3) extend >=5% or >=8% above SMA before first exact touch.
4) after first touch, allow 1-6 4H bars of basing around support.
5) no close may be >3% below the MA before confirmation.
6) entry only after a predeclared RSI confirmation while price closes no more
   than +4% above the MA; enter next 4H open.

RSI confirmations:
- two consecutive rising RSI bars,
- RSI > touch RSI by 3 or 5 points,
- RSI above its SMA3,
- RSI above its SMA5,
- RSI bottom-turn,
- higher RSI low vs the pre-touch 6-bar floor,
- bullish price/RSI divergence.

Hyperliquid train 60% selects. Latest 40% and Binance remain untouched.
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
OUT=Path("crypto/research/results_ma_support_rsi_base")
MAS=(111,350)
TARGETS=(.05,.075,.10,.15)
STOP=.075
HL_RT=2*(.00045+.0010)
BN_RT=2*(.00070+.0010)

BELOW=(.75,.90)
BUFFERS=(0.0,.01)
EXTS=(.05,.08)
SLOPES=("any","nonnegative")
CONFIRM_WINDOWS=(2,4,6)

def rsi(c,p=14):
    d=c.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep(x):
    x=x.copy().sort_values("time").drop_duplicates("time").reset_index(drop=True)
    for L in MAS:
        x[f"sma{L}"]=x["c"].rolling(L,min_periods=L).mean()
        x[f"sma{L}_slope6"]=x[f"sma{L}"]/x[f"sma{L}"].shift(6)-1
    x["rsi"]=rsi(x["c"])
    x["rsi_d1"]=x["rsi"].diff()
    x["rsi_sma3"]=x["rsi"].rolling(3,min_periods=2).mean()
    x["rsi_sma5"]=x["rsi"].rolling(5,min_periods=3).mean()
    x["rsi_up2"]=(x["rsi_d1"]>0)&(x["rsi_d1"].shift(1)>0)
    floor6=x["rsi"].shift(1).rolling(6,min_periods=3).min()
    x["rsi_above_floor6"]=x["rsi"]-floor6
    prior_floor=x["rsi"].shift(2).rolling(6,min_periods=3).min()
    x["rsi_bottom_turn"]=(x["rsi"].shift(1)<=prior_floor+1)&(x["rsi"]>=x["rsi"].shift(1)+2)
    prev_price=x["c"].shift(1).rolling(6,min_periods=3).min()
    x["bull_div"]=(x["c"]<=prev_price*1.01)&(x["rsi_above_floor6"]>=3)
    return x

def outcome(x,entry_idx,entry):
    f5=x.iloc[entry_idx:min(len(x),entry_idx+30)]
    f10=x.iloc[entry_idx:min(len(x),entry_idx+60)]
    if len(f5)<24:return None
    out={"entry":float(entry),"entry_time":x.iloc[entry_idx]["time"]}
    for label,f in (("5d",f5),("10d",f10)):
        mfe=float(f["h"].max()/entry-1);mae=float(f["l"].min()/entry-1)
        out[f"mfe_{label}"]=mfe;out[f"mae_{label}"]=mae
        out[f"close_ret_{label}"]=float(f.iloc[-1]["c"]/entry-1)
        for t in TARGETS:
            k=str(t*100).replace(".","p");out[f"hit{k}_{label}"]=mfe>=t
    for t in TARGETS:
        k=str(t*100).replace(".","p");tk=sk=None
        for j,(_,r) in enumerate(f5.iterrows()):
            if tk is None and float(r["h"])>=entry*(1+t):tk=j
            if sk is None and float(r["l"])<=entry*(1-STOP):sk=j
        out[f"t{k}_before_s7p5_5d"]=tk is not None and (sk is None or tk<sk)
    return out

def confirm_defs():
    return [
        ("rsi_up2",lambda x,j,touch:bool(x.loc[j,"rsi_up2"])),
        ("rsi_plus3",lambda x,j,touch:float(x.loc[j,"rsi"])>=touch+3),
        ("rsi_plus5",lambda x,j,touch:float(x.loc[j,"rsi"])>=touch+5),
        ("rsi_above_sma3",lambda x,j,touch:float(x.loc[j,"rsi"])>=float(x.loc[j,"rsi_sma3"])),
        ("rsi_above_sma5",lambda x,j,touch:float(x.loc[j,"rsi"])>=float(x.loc[j,"rsi_sma5"])),
        ("rsi_bottom_turn",lambda x,j,touch:bool(x.loc[j,"rsi_bottom_turn"])),
        ("rsi_higher_low3",lambda x,j,touch:float(x.loc[j,"rsi_above_floor6"])>=3),
        ("bull_div",lambda x,j,touch:bool(x.loc[j,"bull_div"])),
    ]

def scan_events(x,coin,venue,L):
    ma=f"sma{L}";slope=f"sma{L}_slope6";rows=[];n=len(x)
    i=max(L+30,140);last=None
    while i<n-40:
        if pd.isna(x.loc[i,ma]) or pd.isna(x.loc[i-1,ma]):i+=1;continue
        if not (float(x.loc[i-1,"c"])<=float(x.loc[i-1,ma]) and float(x.loc[i,"c"])>float(x.loc[i,ma])):
            i+=1;continue
        if last is not None and x.loc[i,"time"]-last<pd.Timedelta(days=5):
            i+=1;continue
        prev=x.loc[i-24:i-1]
        if len(prev)<24:i+=1;continue
        below=float((prev["c"]<prev[ma]).mean())
        buffer=float(x.loc[i,"c"])/float(x.loc[i,ma])-1
        maxext=buffer;touch=None
        for j in range(i+1,min(n-38,i+49)):
            mav=float(x.loc[j,ma]);maxext=max(maxext,float(x.loc[j,"h"])/mav-1)
            if float(x.loc[j,"l"])<=mav<=float(x.loc[j,"h"]):
                touch=j;break
        if touch is None:i+=1;continue
        trsi=float(x.loc[touch,"rsi"]) if pd.notna(x.loc[touch,"rsi"]) else np.nan
        if not np.isfinite(trsi):i=touch+1;continue
        for win in CONFIRM_WINDOWS:
            for cname,fn in confirm_defs():
                cj=None
                for j in range(touch+1,min(n-31,touch+1+win)):
                    mav=float(x.loc[j,ma])
                    if float(x.loc[j,"c"])<mav*.97:
                        break
                    if float(x.loc[j,"c"])>mav*1.04:
                        continue
                    try:ok=fn(x,j,trsi)
                    except Exception:ok=False
                    if ok:
                        cj=j;break
                if cj is None or cj+1>=n:continue
                o=outcome(x,cj+1,float(x.loc[cj+1,"o"]))
                if o is None:continue
                rows.append({
                    "venue":venue,"coin":coin,"ma_len":L,
                    "breakout_time":x.loc[i,"time"],"touch_time":x.loc[touch,"time"],
                    "confirm_time":x.loc[cj,"time"],"confirm_window":win,
                    "rsi_confirm":cname,"below_frac_24":below,
                    "breakout_buffer":buffer,"extension_before_touch":maxext,
                    "ma_slope6":float(x.loc[touch,slope]) if pd.notna(x.loc[touch,slope]) else np.nan,
                    "touch_close_vs_ma":float(x.loc[touch,"c"])/float(x.loc[touch,ma])-1,
                    "touch_rsi":trsi,"confirm_rsi":float(x.loc[cj,"rsi"]),
                    "confirm_rsi_change":float(x.loc[cj,"rsi"])-trsi,
                    "confirm_close_vs_ma":float(x.loc[cj,"c"])/float(x.loc[cj,ma])-1,
                    **o
                })
        last=x.loc[i,"time"];i=touch+1
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
    allx=[]
    for x in info.get("symbols",[]):
        if x.get("status")!="TRADING" or x.get("quoteAsset")!="USDT":continue
        b=str(x.get("baseAsset") or "")
        if b in stable or b.startswith("USD"):continue
        allx.append(x["symbol"])
    allx=sorted(set(allx),key=lambda z:qv.get(z,0),reverse=True)
    out=allx[:n]
    if "CHIPUSDT" in allx and "CHIPUSDT" not in out:out.append("CHIPUSDT")
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
    s=requests.Session();s.headers["User-Agent"]="appwiza-ma-support-rsi-base/1.0"
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0);start=end-timedelta(days=args.days)
    if args.venue=="hl":coins=hl_universe(s);fetch=hl_fetch;venue="Hyperliquid primary perps"
    else:coins=bn_universe(s,args.binance_symbols);fetch=bn_fetch;venue="Binance USDT spot"
    selected=coins[args.shard_index::args.shard_count];rows=[];cov=[]
    for p,coin in enumerate(selected,1):
        try:
            x=prep(fetch(s,coin,start,end))
            if len(x)<500:cov.append((coin,len(x),0,"short"));continue
            rr=[]
            for L in MAS:rr.extend(scan_events(x,coin,venue,L))
            rows.extend(rr);cov.append((coin,len(x),len(rr),"ok"));print(p,len(selected),coin,len(rr))
        except Exception as e:cov.append((coin,0,0,"error:"+str(e)[:120]))
        time.sleep(.04)
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
            for c in ["breakout_time","touch_time","confirm_time","entry_time"]:z[c]=pd.to_datetime(z[c],utc=True,errors="coerce")
    hl.to_csv(out/"hyperliquid_events.csv",index=False);bn.to_csv(out/"binance_events.csv",index=False)

    rows=[]
    for L,bf,buf,ext,slope,win,conf in itertools.product(MAS,BELOW,BUFFERS,EXTS,SLOPES,CONFIRM_WINDOWS,[x[0] for x in confirm_defs()]):
        def filt(z):
            m=(pd.to_numeric(z["ma_len"],errors="coerce")==L)&(z["rsi_confirm"]==conf)&(pd.to_numeric(z["confirm_window"],errors="coerce")==win)
            m&=(pd.to_numeric(z["below_frac_24"],errors="coerce")>=bf)
            m&=(pd.to_numeric(z["breakout_buffer"],errors="coerce")>=buf)
            m&=(pd.to_numeric(z["extension_before_touch"],errors="coerce")>=ext)
            if slope=="nonnegative":m&=(pd.to_numeric(z["ma_slope6"],errors="coerce")>=0)
            return z[m].sort_values("entry_time").drop_duplicates(["coin","entry_time"]).reset_index(drop=True)
        h=filt(hl);b=filt(bn)
        if len(h)<35:continue
        hc0=max(1,int(len(h)*.60));bc0=max(1,int(len(b)*.60)) if len(b) else 0
        ht,hh=h.iloc[:hc0],h.iloc[hc0:];bh=b.iloc[bc0:] if len(b) else b
        st,sh,sb=stats(ht,HL_RT),stats(hh,HL_RT),stats(bh,BN_RT)
        rows.append({
            "ma_len":L,"below_frac":bf,"breakout_buffer":buf,"min_extension":ext,"slope_req":slope,
            "confirm_window":win,"rsi_confirm":conf,
            "train_n":len(ht),"train_hit5":st["hit5p0"],"train_risk":st["risk5p0"],"train_roi":st["roi5p0"],
            "hl_hold_n":len(hh),"hl_hit5":sh["hit5p0"],"hl_risk":sh["risk5p0"],"hl_roi":sh["roi5p0"],
            "hl_hit7p5":sh["hit7p5"],"hl_hit10":sh["hit10p0"],"hl_hit15":sh["hit15p0"],
            "hl_roi7p5":sh["roi7p5"],"hl_roi10":sh["roi10p0"],"hl_roi15":sh["roi15p0"],
            "bin_hold_n":len(bh),"bin_hit5":sb.get("hit5p0",np.nan),"bin_risk":sb.get("risk5p0",np.nan),"bin_roi":sb.get("roi5p0",np.nan),
            "bin_hit10":sb.get("hit10p0",np.nan)
        })
    z=pd.DataFrame(rows)
    if len(z):
        z["eligible_train"]=(z["train_n"]>=25)&(z["train_hit5"]>=.72)&(z["train_risk"]>=.65)&(z["train_roi"]>=.008)
        z["crossvenue_pass"]=z["eligible_train"]&(z["hl_hold_n"]>=20)&(z["hl_hit5"]>=.78)&(z["hl_risk"]>=.70)&(z["hl_roi"]>=.0125)&(z["bin_hold_n"]>=15)&(z["bin_hit5"]>=.75)&(z["bin_risk"]>=.67)&(z["bin_roi"]>=.010)
        z["score"]=.30*z["train_hit5"]+.25*z["train_risk"]+.20*np.clip(z["train_roi"]/.04,-1,1)+.15*np.clip(np.log1p(z["train_n"])/np.log(200),0,1)+.10*(z["ma_len"]==111)
        z=z.sort_values(["crossvenue_pass","eligible_train","score","train_n"],ascending=[False,False,False,False])
    z.to_csv(out/"rsi_base_variants.csv",index=False)

    chip=bn[(bn["coin"]=="CHIPUSDT")&(bn["touch_time"]>=pd.Timestamp("2026-08-10",tz="UTC"))&(bn["touch_time"]<=pd.Timestamp("2026-08-25",tz="UTC"))].copy() if len(bn) else pd.DataFrame()
    chip.to_csv(out/"chip_aug2026_rsi_base.csv",index=False)
    winners=z[z["crossvenue_pass"]==True].head(10) if len(z) else pd.DataFrame()
    (out/"candidate_registry.json").write_text(json.dumps({"generated_at":pd.Timestamp.utcnow().isoformat(),"candidates":winners.to_dict("records") if len(winners) else []},indent=2,default=str))
    lines=[
        "MA111 / MA350 SUPPORT-BASE + RSI CONFIRMATION","",
        "This pass explicitly allows the first touch to dip/close modestly below the MA, then waits for RSI confirmation while price bases around support.",
        "It is designed to capture the CHIP-like behavior that strict same-candle support confirmation can miss.","",
        f"HL rows: {len(hl)} | coins: {int((hc['status']=='ok').sum()) if len(hc) else 0}",
        f"Binance rows: {len(bn)} | symbols: {int((bc['status']=='ok').sum()) if len(bc) else 0}","",
        "TOP VARIANTS",z.head(25).to_string(index=False) if len(z) else "none","",
        "CHIP AUG 2026",chip.head(30).to_string(index=False) if len(chip) else "none","",
        "CROSS-VENUE SURVIVORS",winners.to_string(index=False) if len(winners) else "none","",
        "No live promotion."
    ]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n");print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--mode",choices=["scan","aggregate"],default="scan");ap.add_argument("--venue",choices=["hl","bn"],default="hl")
    ap.add_argument("--days",type=int,default=600);ap.add_argument("--binance-symbols",type=int,default=160);ap.add_argument("--shard-index",type=int,default=0);ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--out",default="ma_base_out");ap.add_argument("--input-dir",default="ma_base_shards");args=ap.parse_args()
    scan(args) if args.mode=="scan" else aggregate(args)
if __name__=="__main__":main()
