#!/usr/bin/env python3
"""
Research the user's 4H SMA350 breakout -> first support retest method.

Interpretation frozen before evaluation:
1. Price has spent most of the prior 24 4H closes BELOW SMA350.
2. A 4H candle closes above SMA350 (optionally with a breakout buffer).
3. Price extends above SMA350 by a minimum amount before the retest.
4. The FIRST qualifying pullback to/near SMA350 must close at/above SMA350,
   so the long MA is acting as support.
5. Confirmed mode enters next 4H open after that support candle.
   Touch mode is also tested separately as a pre-placed limit at SMA350 on the
   first exact touch, without requiring the candle close. Touch mode therefore
   represents the user's more aggressive "buy the first touch" interpretation.

Search discipline:
- Hyperliquid primary perps are the discovery venue.
- Earliest 60% of events are training; latest 40% is untouched holdout.
- A small predeclared structural grid is searched on training only.
- Frozen variants are then independently evaluated on Binance USDT spot.
- No live promotion.

Outcomes:
+5%, +7.5%, +10%, +15% within 5d and 10d.
Risk economics use +target / -7.5% stop / 5d timeout.
Costs: Hyperliquid 0.045% taker/side + 0.10% slippage/side;
Binance 0.070% fee/side + 0.10% slippage/side.
Funding is excluded from this discovery stage and becomes a mandatory replay
if a candidate survives.
"""
from __future__ import annotations
import argparse, itertools, json, math, time
from pathlib import Path
from datetime import datetime,timedelta,timezone
from math import sqrt
import numpy as np
import pandas as pd
import requests

HL_API="https://api.hyperliquid.xyz/info"
BN_API="https://data-api.binance.vision"
ROOT=Path("crypto/research")
OUT=ROOT/"results_sma350_retest"

SMA_LEN=350
STOP=.075
HL_RT=2*(.00045+.0010)
BN_RT=2*(.00070+.0010)
TARGETS=(.05,.075,.10,.15)

# Small predeclared grid. This is intentionally structural, not an unrestricted
# threshold optimizer.
BELOW_FRACS=(.75,.90)
BREAK_BUFFERS=(0.0,.01)
EXTENSIONS=(.03,.05,.08)
RETEST_TOLS=(0.0,.005,.01)
MAX_DELAYS=(12,24)
MODES=("confirmed","touch")

def rsi(c,p=14):
    d=c.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep(x):
    x=x.copy().sort_values("time").drop_duplicates("time").reset_index(drop=True)
    x["sma350"]=x["c"].rolling(SMA_LEN,min_periods=SMA_LEN).mean()
    x["sma350_slope6"]=x["sma350"]/x["sma350"].shift(6)-1
    x["sma350_slope18"]=x["sma350"]/x["sma350"].shift(18)-1
    x["dist_sma350"]=x["c"]/x["sma350"]-1
    x["ret1"]=x["c"].pct_change()
    x["ret3"]=x["c"]/x["c"].shift(3)-1
    x["rsi14"]=rsi(x["c"])
    x["vol_med20"]=x["v"].rolling(20,min_periods=10).median()
    x["vol_ratio20"]=x["v"]/x["vol_med20"].replace(0,np.nan)
    rng=(x["h"]-x["l"]).replace(0,np.nan)
    x["range_pct"]=rng/x["c"].replace(0,np.nan)
    x["lower_wick"]=(np.minimum(x["o"],x["c"])-x["l"])/rng
    x["close_location"]=(x["c"]-x["l"])/rng
    return x

def outcome(x,entry_idx,entry,mode):
    if entry_idx>=len(x) or not np.isfinite(entry) or entry<=0:return None
    f5=x.iloc[entry_idx:min(len(x),entry_idx+30)]
    f10=x.iloc[entry_idx:min(len(x),entry_idx+60)]
    if len(f5)<24:return None
    out={"entry":float(entry),"entry_time":x.iloc[entry_idx]["time"],"entry_mode":mode}
    for label,f in (("5d",f5),("10d",f10)):
        if f.empty:continue
        mfe=float(f["h"].max()/entry-1);mae=float(f["l"].min()/entry-1)
        out[f"mfe_{label}"]=mfe;out[f"mae_{label}"]=mae
        out[f"close_ret_{label}"]=float(f.iloc[-1]["c"]/entry-1)
        for t in TARGETS:
            key=str(t*100).replace(".","p")
            out[f"hit{key}_{label}"]=mfe>=t

    # order-aware outcomes from 4H candles, same-bar target+stop conservative.
    for t in TARGETS:
        tk=sk=None
        for k,(_,r) in enumerate(f5.iterrows()):
            if tk is None and float(r["h"])>=entry*(1+t):tk=k
            if sk is None and float(r["l"])<=entry*(1-STOP):sk=k
        key=str(t*100).replace(".","p")
        out[f"t{key}_before_s7p5_5d"]=tk is not None and (sk is None or tk<sk)
    return out

def variant_id(below,buf,ext,tol,delay,mode):
    return f"{mode}|below{below:.2f}|buf{buf:.3f}|ext{ext:.3f}|tol{tol:.3f}|d{delay}"

def scan_variant(x,coin,venue,below_frac,buffer,extension,tol,max_delay,mode):
    rec=[];i=max(SMA_LEN+30,380);last_entry=None
    n=len(x)
    while i<n-30:
        if pd.isna(x.loc[i,"sma350"]) or pd.isna(x.loc[i-1,"sma350"]):
            i+=1;continue
        # Cross from below. A majority-below prior regime prevents choppy
        # crossovers from being treated as fresh breakouts.
        prev24=x.loc[i-24:i-1]
        if len(prev24)<24:
            i+=1;continue
        below_ratio=float((prev24["c"]<prev24["sma350"]).mean())
        breakout=(
            float(x.loc[i-1,"c"])<=float(x.loc[i-1,"sma350"])
            and float(x.loc[i,"c"])>=float(x.loc[i,"sma350"])*(1+buffer)
            and below_ratio>=below_frac
        )
        if not breakout:
            i+=1;continue

        breakout_time=x.loc[i,"time"]
        breakout_close=float(x.loc[i,"c"])
        breakout_sma=float(x.loc[i,"sma350"])
        max_ext=breakout_close/breakout_sma-1
        retest=None
        failed=False
        # Search only for the FIRST retest attempt after price has achieved
        # the required extension. If a candle closes below SMA350 before a
        # successful confirmed retest, the breakout is considered failed.
        for j in range(i+1,min(n-30,i+1+max_delay)):
            if pd.isna(x.loc[j,"sma350"]):continue
            sma=float(x.loc[j,"sma350"])
            max_ext=max(max_ext,float(x.loc[j,"h"])/sma-1)
            touched=float(x.loc[j,"l"])<=sma*(1+tol)
            exact_touch=float(x.loc[j,"l"])<=sma<=float(x.loc[j,"h"])
            if mode=="touch":
                if max_ext>=extension and exact_touch:
                    retest=(j,sma);break
                if float(x.loc[j,"c"])<sma and max_ext>=extension:
                    failed=True;break
            else:
                if max_ext>=extension and touched:
                    if float(x.loc[j,"c"])>=sma:
                        retest=(j,None);break
                    failed=True;break
        if retest is None:
            i+=1;continue

        j,limit_px=retest
        if last_entry is not None and x.loc[j,"time"]-last_entry<pd.Timedelta(days=7):
            i=j+1;continue

        if mode=="touch":
            # The exact touch occurs somewhere inside the retest 4H candle.
            # Without 1m data we cannot know whether that candle's later high
            # or low occurred before/after the fill. Start outcome accounting
            # from the NEXT 4H candle to avoid intrabar lookahead. This is
            # deliberately conservative for touch entries.
            entry_idx=j+1
            if entry_idx>=n:break
            entry=float(limit_px)
        else:
            entry_idx=j+1
            if entry_idx>=n:break
            entry=float(x.loc[entry_idx,"o"])
        o=outcome(x,entry_idx,entry,mode)
        if o is None:
            i=j+1;continue

        row=x.loc[j]
        rec.append({
            "venue":venue,"coin":coin,
            "variant_id":variant_id(below_frac,buffer,extension,tol,max_delay,mode),
            "below_frac":below_frac,"breakout_buffer":buffer,
            "min_extension":extension,"retest_tolerance":tol,
            "max_delay_bars":max_delay,"entry_mode":mode,
            "breakout_time":breakout_time,"retest_time":row["time"],
            "breakout_close":breakout_close,
            "breakout_sma350":breakout_sma,
            "realized_extension":max_ext,
            "retest_close":float(row["c"]),
            "retest_sma350":float(row["sma350"]),
            "retest_close_vs_sma":float(row["c"])/float(row["sma350"])-1,
            "sma350_slope6":float(row["sma350_slope6"]) if pd.notna(row["sma350_slope6"]) else np.nan,
            "sma350_slope18":float(row["sma350_slope18"]) if pd.notna(row["sma350_slope18"]) else np.nan,
            "retest_rsi14":float(row["rsi14"]) if pd.notna(row["rsi14"]) else np.nan,
            "retest_vol_ratio20":float(row["vol_ratio20"]) if pd.notna(row["vol_ratio20"]) else np.nan,
            "retest_lower_wick":float(row["lower_wick"]) if pd.notna(row["lower_wick"]) else np.nan,
            "retest_close_location":float(row["close_location"]) if pd.notna(row["close_location"]) else np.nan,
            **o
        })
        last_entry=x.loc[entry_idx,"time"]
        i=j+1
    return rec

def all_variants(x,coin,venue):
    rows=[]
    for args in itertools.product(BELOW_FRACS,BREAK_BUFFERS,EXTENSIONS,RETEST_TOLS,MAX_DELAYS,MODES):
        rows.extend(scan_variant(x,coin,venue,*args))
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

def bn_universe(s,n=140):
    info=bn_get(s,"/api/v3/exchangeInfo")
    tick=bn_get(s,"/api/v3/ticker/24hr")
    qv={x["symbol"]:float(x.get("quoteVolume") or 0) for x in tick if isinstance(x,dict) and x.get("symbol")}
    stable={"USDC","FDUSD","TUSD","USDP","DAI","BUSD","USD1"}
    out=[]
    for x in info.get("symbols",[]):
        if x.get("status")!="TRADING" or x.get("quoteAsset")!="USDT":continue
        b=str(x.get("baseAsset") or "")
        if b in stable or b.startswith("USD") or b.startswith("1000"):continue
        if any(b.endswith(z) for z in ("UP","DOWN","BULL","BEAR")):continue
        out.append(x["symbol"])
    out.sort(key=lambda z:qv.get(z,0),reverse=True)
    return out[:n]

def bn_fetch(s,symbol,start,end):
    cur=int(start.timestamp()*1000);stop=int(end.timestamp()*1000);rows=[]
    step=4*3600*1000
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
    s=requests.Session();s.headers["User-Agent"]="appwiza-sma350-retest-research/1.0"
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
            rr=all_variants(x,coin,venue)
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

def summarize_variant(g,cost):
    g=g.sort_values("entry_time").reset_index(drop=True)
    cut=max(1,int(len(g)*.60));tr=g.iloc[:cut];ho=g.iloc[cut:]
    row={"n":len(g),"train_n":len(tr),"hold_n":len(ho)}
    for lab,q in (("train",tr),("hold",ho)):
        for t in TARGETS:
            k=str(t*100).replace(".","p")
            row[f"{lab}_hit{k}_5d"]=rate(q[f"hit{k}_5d"]) if len(q) else np.nan
            row[f"{lab}_hit{k}_10d"]=rate(q[f"hit{k}_10d"]) if len(q) else np.nan
            row[f"{lab}_t{k}_before_s7p5"]=rate(q[f"t{k}_before_s7p5_5d"]) if len(q) else np.nan
            row[f"{lab}_net_roi_t{k}"]=econ(q,t,cost) if len(q) else np.nan
        row[f"{lab}_median_mae5d"]=float(pd.to_numeric(q["mae_5d"],errors="coerce").median()) if len(q) else np.nan
        row[f"{lab}_median_mfe5d"]=float(pd.to_numeric(q["mfe_5d"],errors="coerce").median()) if len(q) else np.nan
    return row

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
            for c in ["breakout_time","retest_time","entry_time"]:z[c]=pd.to_datetime(z[c],utc=True,errors="coerce")
    hl.to_csv(out/"hyperliquid_events.csv",index=False)
    bn.to_csv(out/"binance_events.csv",index=False)
    hc.to_csv(out/"hyperliquid_coverage.csv",index=False)
    bc.to_csv(out/"binance_coverage.csv",index=False)

    rows=[]
    for vid,g in hl.groupby("variant_id"):
        s=summarize_variant(g,HL_RT)
        meta=g.iloc[0]
        rows.append({"variant_id":vid,
                     "entry_mode":meta["entry_mode"],"below_frac":meta["below_frac"],
                     "breakout_buffer":meta["breakout_buffer"],"min_extension":meta["min_extension"],
                     "retest_tolerance":meta["retest_tolerance"],"max_delay_bars":meta["max_delay_bars"],
                     **s})
    z=pd.DataFrame(rows)
    if len(z):
        # Discovery score is based on +5% because user's primary question is
        # probability of a profitable support bounce. Higher targets remain
        # diagnostic, not selection variables.
        z["eligible_train"]=(
            (z["train_n"]>=45)
            &(z["train_hit5p0_5d"]>=.72)
            &(z["train_t5p0_before_s7p5"]>=.65)
            &(z["train_net_roi_t5p0"]>=.008)
        )
        z["score"]=(
            .35*z["train_hit5p0_5d"]+.30*z["train_t5p0_before_s7p5"]
            +.25*np.clip(z["train_net_roi_t5p0"]/.03,-1,1)
            +.10*np.clip(np.log1p(z["train_n"])/np.log(250),0,1)
        )
        z=z.sort_values(["eligible_train","score","train_n"],ascending=[False,False,False])
    z.to_csv(out/"hyperliquid_variant_search.csv",index=False)

    # Evaluate only the top training-selected, structurally distinct variants
    # on untouched HL holdout and on Binance. No Binance retuning.
    shortlist=[]
    if len(z):
        q=z[z["eligible_train"]==True].copy()
        if len(q):
            q["family"]=q["entry_mode"].astype(str)+"|"+q["below_frac"].astype(str)+"|"+q["min_extension"].astype(str)
            q=q.sort_values(["score","train_n"],ascending=[False,False]).drop_duplicates("family").head(12)
            shortlist=q.copy()
    result=[]
    for _,r in shortlist.iterrows() if isinstance(shortlist,pd.DataFrame) else []:
        vid=r["variant_id"]
        gh=hl[hl["variant_id"]==vid].sort_values("entry_time").reset_index(drop=True)
        ch=max(1,int(len(gh)*.60));ho=gh.iloc[ch:]
        gb=bn[bn["variant_id"]==vid].sort_values("entry_time").reset_index(drop=True)
        bsum=summarize_variant(gb,BN_RT) if len(gb) else {}
        rec={k:r[k] for k in ["variant_id","entry_mode","below_frac","breakout_buffer","min_extension","retest_tolerance","max_delay_bars"]}
        rec.update({
            "hl_hold_n":len(ho),
            "hl_hit5_5d":rate(ho["hit5p0_5d"]) if len(ho) else np.nan,
            "hl_t5_before_s7p5":rate(ho["t5p0_before_s7p5_5d"]) if len(ho) else np.nan,
            "hl_net_roi_t5":econ(ho,.05,HL_RT) if len(ho) else np.nan,
            "hl_hit7p5_5d":rate(ho["hit7p5_5d"]) if len(ho) else np.nan,
            "hl_hit10_5d":rate(ho["hit10p0_5d"]) if len(ho) else np.nan,
            "hl_hit15_5d":rate(ho["hit15p0_5d"]) if len(ho) else np.nan,
            "hl_hit5_10d":rate(ho["hit5p0_10d"]) if len(ho) else np.nan,
            "hl_hit10_10d":rate(ho["hit10p0_10d"]) if len(ho) else np.nan,
            "hl_net_roi_t7p5":econ(ho,.075,HL_RT) if len(ho) else np.nan,
            "hl_net_roi_t10":econ(ho,.10,HL_RT) if len(ho) else np.nan,
            "hl_net_roi_t15":econ(ho,.15,HL_RT) if len(ho) else np.nan,
            "hl_wilson_hit5":wilson(int(ho["hit5p0_5d"].fillna(False).astype(bool).sum()),len(ho)) if len(ho) else np.nan,
            "bin_all_n":len(gb),
            "bin_hold_n":bsum.get("hold_n",0),
            "bin_hold_hit5_5d":bsum.get("hold_hit5p0_5d",np.nan),
            "bin_hold_t5_before_s7p5":bsum.get("hold_t5p0_before_s7p5",np.nan),
            "bin_hold_net_roi_t5":bsum.get("hold_net_roi_t5p0",np.nan),
            "bin_hold_hit7p5_5d":bsum.get("hold_hit7p5_5d",np.nan),
            "bin_hold_hit10_5d":bsum.get("hold_hit10p0_5d",np.nan),
            "bin_hold_hit15_5d":bsum.get("hold_hit15p0_5d",np.nan),
        })
        rec["crossvenue_pass"]=bool(
            rec["hl_hold_n"]>=30 and rec["hl_hit5_5d"]>=.78
            and rec["hl_t5_before_s7p5"]>=.70 and rec["hl_net_roi_t5"]>=.0125
            and rec["bin_hold_n"]>=25 and rec["bin_hold_hit5_5d"]>=.75
            and rec["bin_hold_t5_before_s7p5"]>=.67 and rec["bin_hold_net_roi_t5"]>=.010
        )
        result.append(rec)
    rr=pd.DataFrame(result)
    if len(rr):rr=rr.sort_values(["crossvenue_pass","hl_net_roi_t5","hl_hold_n"],ascending=[False,False,False])
    rr.to_csv(out/"shortlist_crossvenue.csv",index=False)

    winner=rr[rr["crossvenue_pass"]==True].head(1) if len(rr) else pd.DataFrame()
    (out/"candidate_registry.json").write_text(json.dumps({
        "generated_at":pd.Timestamp.utcnow().isoformat(),
        "method_family":"4H SMA350 breakout -> first support retest",
        "entry_interpretations":["confirmed next-4H-open","preplaced exact-touch limit"],
        "selection":"Hyperliquid earliest 60% only; latest40 + Binance frozen validation",
        "candidates":winner.to_dict("records") if len(winner) else []
    },indent=2,default=str))
    lines=[
        "SMA350 BREAKOUT -> FIRST SUPPORT RETEST RESEARCH","",
        "Interpretation: prior below-SMA regime -> 4H breakout -> minimum extension -> FIRST support retest -> buy.",
        "Both confirmed-next-open and aggressive exact-touch entries are tested separately.",
        "Selection is Hyperliquid training-only. Latest 40% Hyperliquid and Binance are untouched/frozen validation.","",
        f"Hyperliquid events rows: {len(hl)} | scanned coins: {int((hc['status']=='ok').sum()) if len(hc) else 0}",
        f"Binance events rows: {len(bn)} | scanned symbols: {int((bc['status']=='ok').sum()) if len(bc) else 0}","",
        "TOP FROZEN SHORTLIST",
        rr.to_string(index=False) if len(rr) else "none","",
        "CROSS-VENUE SURVIVOR",
        winner.to_string(index=False) if len(winner) else "none","",
        "Funding and 1m same-bar replay are NOT included yet; mandatory if a candidate survives."
    ]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mode",choices=["scan","aggregate"],default="scan")
    ap.add_argument("--venue",choices=["hl","bn"],default="hl")
    ap.add_argument("--days",type=int,default=600)
    ap.add_argument("--binance-symbols",type=int,default=140)
    ap.add_argument("--shard-index",type=int,default=0)
    ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--sleep",type=float,default=.05)
    ap.add_argument("--out",default="sma350_out")
    ap.add_argument("--input-dir",default="sma350_shards")
    args=ap.parse_args()
    scan(args) if args.mode=="scan" else aggregate(args)

if __name__=="__main__":
    main()
