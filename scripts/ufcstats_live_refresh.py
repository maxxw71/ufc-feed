#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT=Path.home()/"ufc-predictor-v1"
AR=ROOT/"auto_research"
CURRENT=AR/"raw"/"ufcstats_competitions_current.csv"
LIVE=AR/"live_ufcstats"
IND=ROOT/"raw"/"individuals.csv"
VENDOR=ROOT/"vendor_ufcstats"
LIVE.mkdir(parents=True,exist_ok=True)

def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def csv_stats(path,date_col=None):
    if not path.exists() or path.stat().st_size==0:return {"rows":0,"max_date":None}
    try:d=pd.read_csv(path,low_memory=False)
    except Exception:return {"rows":0,"max_date":None}
    out={"rows":int(len(d)),"max_date":None}
    if date_col and date_col in d.columns:
        x=pd.to_datetime(d[date_col],errors="coerce")
        if x.notna().any():out["max_date"]=str(x.max().date())
    return out

def seed_if_newer(src,dst,date_col=None):
    if not src.exists():return
    a=csv_stats(src,date_col);b=csv_stats(dst,date_col)
    ad=pd.to_datetime(a.get("max_date"),errors="coerce");bd=pd.to_datetime(b.get("max_date"),errors="coerce")
    if not dst.exists() or a["rows"]>b["rows"] or (pd.notna(ad) and (pd.isna(bd) or ad>bd)):
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)

def main():
    if not VENDOR.exists():raise RuntimeError("vendor_ufcstats missing; redeploy UFC prospective pipeline")
    sys.path.insert(0,str(VENDOR))
    from libs.scraping.ufcstats import scrape_ufcstats

    live_comp=LIVE/"competitions.csv";live_ind=LIVE/"individuals.csv"
    seed_if_newer(CURRENT,live_comp,"event_date")
    seed_if_newer(IND,live_ind,None)
    before=csv_stats(live_comp,"event_date")

    counts=scrape_ufcstats(LIVE,fighters=True,fights=True,force_full=False,log_level="WARNING")
    after=csv_stats(live_comp,"event_date")
    if after["rows"]<before["rows"]:
        raise RuntimeError(f"UFCStats live scrape shrank dataset: {before} -> {after}")
    a=pd.to_datetime(after.get("max_date"),errors="coerce");b=pd.to_datetime(before.get("max_date"),errors="coerce")
    if pd.notna(a) and pd.notna(b) and a<b:
        raise RuntimeError(f"UFCStats live scrape regressed max date: {before} -> {after}")

    CURRENT.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(live_comp,CURRENT)
    if live_ind.exists() and csv_stats(live_ind)["rows"]>=csv_stats(IND)["rows"]:
        IND.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(live_ind,IND)

    print(json.dumps({"live_ufcstats_refresh":"ok","at":stamp(),"before":before,"after":after,"counts":counts,
                      "current":str(CURRENT),"individuals":csv_stats(IND)},indent=2))

if __name__=="__main__":main()
