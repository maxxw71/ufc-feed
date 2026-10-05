#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"market"
SCORE="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
CORE="https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{event}/competitions/{competition}/odds"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*"}

DATES=["20251021","20260115","20260412"]

def main():
    report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"dates":[]}
    for ds in DATES:
        sr=requests.get(SCORE,params={"dates":ds,"limit":100},headers=HEADERS,timeout=30)
        sr.raise_for_status()
        payload=sr.json()
        day={"date":ds,"events":[]}
        for ev in payload.get("events") or []:
            comps=ev.get("competitions") or []
            if not comps: continue
            comp=comps[0]
            eid=str(ev.get("id") or "")
            cid=str(comp.get("id") or eid)
            ou=CORE.format(event=eid,competition=cid)
            rr=requests.get(ou,params={"limit":100},headers=HEADERS,timeout=20)
            item={"event_id":eid,"competition_id":cid,"name":ev.get("name"),"date":ev.get("date"),
                  "http_status":rr.status_code,"odds_count":None,"providers":[]}
            if rr.ok:
                data=rr.json()
                items=data.get("items") or []
                item["odds_count"]=len(items)
                item["providers"]=[(x.get("provider") or {}).get("name") for x in items]
                if items:
                    sample=items[0]
                    item["sample"]={
                      "details":sample.get("details"),"overUnder":sample.get("overUnder"),"spread":sample.get("spread"),
                      "homeMoneyline":(sample.get("homeTeamOdds") or {}).get("moneyLine"),
                      "awayMoneyline":(sample.get("awayTeamOdds") or {}).get("moneyLine"),
                      "open":sample.get("open")
                    }
            day["events"].append(item)
        day["events_with_odds"]=sum(1 for x in day["events"] if (x.get("odds_count") or 0)>0)
        day["events_total"]=len(day["events"])
        report["dates"].append(day)
    report["events_total"]=sum(x["events_total"] for x in report["dates"])
    report["events_with_odds"]=sum(x["events_with_odds"] for x in report["dates"])
    (OUT/"historical_espn_odds_probe.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))

if __name__=="__main__":
    main()
