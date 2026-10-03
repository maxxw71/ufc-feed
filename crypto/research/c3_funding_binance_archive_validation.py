#!/usr/bin/env python3
"""
Apply the frozen Hyperliquid C3 funding gate to the already frozen Binance C3
spot-event panel, using Binance USD-M perpetual funding archives from
data.binance.vision. No Binance threshold tuning.
"""
from __future__ import annotations

import io,json,zipfile,time
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

SRC=Path("crypto/research/results_c3_binance_validation/events.csv")
OUT=Path("crypto/research/results_c3_funding_binance_archive")
OUT.mkdir(parents=True,exist_ok=True)
FUNDING_MIN=0.000013
BASE="https://data.binance.vision/data/futures/um/monthly/fundingRate"

def month_key(ts):
    t=pd.Timestamp(ts)
    return t.strftime("%Y-%m")

def prev_month(key):
    t=pd.Timestamp(key+"-01")-pd.offsets.MonthBegin(1)
    return t.strftime("%Y-%m")

def rate(s):
    x=s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan

def _to_dt(series):
    x=pd.to_numeric(series,errors="coerce")
    med=x.dropna().median() if x.notna().any() else np.nan
    if not np.isfinite(med):
        return pd.to_datetime(series,utc=True,errors="coerce")
    if med>1e14:
        return pd.to_datetime(x,unit="us",utc=True,errors="coerce")
    if med>1e11:
        return pd.to_datetime(x,unit="ms",utc=True,errors="coerce")
    if med>1e9:
        return pd.to_datetime(x,unit="s",utc=True,errors="coerce")
    return pd.to_datetime(series,utc=True,errors="coerce")

def parse_zip(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names=[n for n in z.namelist() if not n.endswith("/")]
        if not names:return pd.DataFrame(columns=["time","funding_rate"])
        raw=z.read(names[0])
    # First try normal header.
    d=pd.read_csv(io.BytesIO(raw))
    cols=[str(c).strip().lower() for c in d.columns]
    d.columns=cols
    time_col=next((c for c in cols if "time" in c or "calc" in c),None)
    rate_col=next((c for c in cols if "funding" in c and "rate" in c),None)
    if time_col and rate_col:
        out=pd.DataFrame({"time":_to_dt(d[time_col]),"funding_rate":pd.to_numeric(d[rate_col],errors="coerce")})
        return out.dropna().sort_values("time")
    # Fallback for headerless or variant archives.
    d=pd.read_csv(io.BytesIO(raw),header=None)
    best_t=None
    for c in d.columns:
        x=pd.to_numeric(d[c],errors="coerce")
        med=x.dropna().median() if x.notna().any() else np.nan
        if np.isfinite(med) and med>1e9:
            best_t=c;break
    best_r=None
    candidates=[]
    for c in d.columns:
        if c==best_t:continue
        x=pd.to_numeric(d[c],errors="coerce")
        clean=x.dropna()
        if len(clean):
            medabs=float(clean.abs().median())
            if medabs<.05:
                candidates.append((medabs,c))
    if candidates:
        # funding is usually the small-magnitude decimal field.
        best_r=sorted(candidates,key=lambda x:x[0])[0][1]
    if best_t is None or best_r is None:
        return pd.DataFrame(columns=["time","funding_rate"])
    return pd.DataFrame({
        "time":_to_dt(d[best_t]),
        "funding_rate":pd.to_numeric(d[best_r],errors="coerce")
    }).dropna().sort_values("time")

def fetch_month(s,symbol,key):
    url=f"{BASE}/{quote(symbol)}/{quote(symbol)}-fundingRate-{key}.zip"
    r=s.get(url,timeout=30)
    if r.status_code==404:return pd.DataFrame(columns=["time","funding_rate"])
    r.raise_for_status()
    return parse_zip(r.content)

def summary(g):
    if g is None or len(g)==0:
        return {"n":0,"hit5":None,"hit10":None,"t5_s7p5":None,"median_mfe":None,"median_mae":None}
    return {
        "n":int(len(g)),
        "hit5":rate(g["hit5"]),
        "hit10":rate(g["hit10"]),
        "t5_s7p5":rate(g["t5_s7p5"]),
        "median_mfe":float(g["mfe5d"].median()),
        "median_mae":float(g["mae5d"].median()),
    }

def main():
    e=pd.read_csv(SRC)
    for c in ["trigger_time","entry_time"]:
        e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    e=e.sort_values("entry_time").reset_index(drop=True)

    s=requests.Session()
    s.headers["User-Agent"]="appwiza-c3-funding-archive-validation/1.0"
    cache={}
    funding=[]
    ages=[]
    missing=[]

    for i,r in e.iterrows():
        sym=str(r["symbol"])
        t=pd.Timestamp(r["trigger_time"])
        frames=[]
        for key in [prev_month(month_key(t)),month_key(t)]:
            ck=(sym,key)
            if ck not in cache:
                try:
                    cache[ck]=fetch_month(s,sym,key)
                except Exception as ex:
                    print("FETCH_ERR",sym,key,str(ex)[:120])
                    cache[ck]=pd.DataFrame(columns=["time","funding_rate"])
                time.sleep(.03)
            if len(cache[ck]):frames.append(cache[ck])
        if frames:
            q=pd.concat(frames,ignore_index=True).drop_duplicates("time").sort_values("time")
            q=q[q["time"]<=t]
            if len(q):
                x=q.iloc[-1]
                funding.append(float(x["funding_rate"]))
                ages.append(float((t-pd.Timestamp(x["time"])).total_seconds()/3600))
                continue
        funding.append(np.nan);ages.append(np.nan);missing.append(sym)

    e["funding_rate_at_trigger"]=funding
    e["funding_age_hours"]=ages
    e.to_csv(OUT/"events_with_funding.csv",index=False)

    cut=max(1,int(len(e)*.60))
    tr=e.iloc[:cut].copy();ho=e.iloc[cut:].copy()
    allf=e[e["funding_rate_at_trigger"]>=FUNDING_MIN]
    trf=tr[tr["funding_rate_at_trigger"]>=FUNDING_MIN]
    hof=ho[ho["funding_rate_at_trigger"]>=FUNDING_MIN]

    result={
        "price_signal_source":"Binance USDT spot C3 frozen events",
        "funding_source":"Binance USD-M perpetual monthly funding archives",
        "funding_rate_min":FUNDING_MIN,
        "funding_coverage":float(e["funding_rate_at_trigger"].notna().mean()),
        "median_funding_age_hours":float(e["funding_age_hours"].dropna().median()) if e["funding_age_hours"].notna().any() else None,
        "c3_all":summary(e),
        "c3_holdout":summary(ho),
        "c3_funding_all":summary(allf),
        "c3_funding_train":summary(trf),
        "c3_funding_holdout":summary(hof),
        "missing_symbols":sorted(set(missing)),
    }
    h=result["c3_funding_holdout"]
    result["similar_to_hyperliquid"]=bool(
        h["n"]>=15 and h["hit5"] is not None and h["hit5"]>=.85
        and h["t5_s7p5"] is not None and h["t5_s7p5"]>=.78
    )
    (OUT/"REPORT.json").write_text(json.dumps(result,indent=2))
    (OUT/"REPORT.txt").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))

if __name__=="__main__":
    main()
