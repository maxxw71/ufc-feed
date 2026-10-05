#!/usr/bin/env python3
import csv,gzip,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"market"
API="https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

def write_append(path,new_rows):
    existing=[]
    if path.exists():
        with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
            existing=list(csv.DictReader(f))
    rows=existing+new_rows
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields: fields=["_empty"]
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

def main():
    key=os.getenv("THE_ODDS_API_KEY","").strip()
    if not key:
        print("THE_ODDS_API_KEY not configured; skipping external bookmaker snapshot.")
        return 0
    captured=datetime.now(timezone.utc).isoformat()
    params={"apiKey":key,"regions":"us","markets":"h2h,spreads,totals","oddsFormat":"american","dateFormat":"iso"}
    r=requests.get(API,params=params,timeout=45)
    r.raise_for_status()
    payload=r.json()
    rows=[]
    for ev in payload:
        for book in ev.get("bookmakers") or []:
            for market in book.get("markets") or []:
                mkey=market.get("key")
                outcomes=market.get("outcomes") or []
                for o in outcomes:
                    rows.append({
                      "captured_at_utc":captured,"source":"the_odds_api","source_event_id":ev.get("id"),
                      "commence_time":ev.get("commence_time"),"home_team":ev.get("home_team"),"away_team":ev.get("away_team"),
                      "bookmaker_key":book.get("key"),"bookmaker":book.get("title"),"bookmaker_last_update":book.get("last_update"),
                      "market":mkey,"outcome":o.get("name"),"price":o.get("price"),"point":o.get("point")
                    })
    path=OUT/"external_bookmaker_snapshots.csv.gz"
    write_append(path,rows)
    meta={
      "captured_at_utc":captured,"events":len(payload),"rows":len(rows),
      "bookmakers":len(set(r["bookmaker_key"] for r in rows if r.get("bookmaker_key"))),
      "remaining_requests":r.headers.get("x-requests-remaining"),"used_requests":r.headers.get("x-requests-used"),
      "source":"the_odds_api"
    }
    (OUT/"external_latest.json").write_text(json.dumps(meta,indent=2)+"\n")
    print(json.dumps(meta,indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
