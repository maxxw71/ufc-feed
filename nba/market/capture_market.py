#!/usr/bin/env python3
import csv,gzip,json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"market"
RAW=OUT/"raw"
URL="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
CORE_ODDS="https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{event_id}/competitions/{competition_id}/odds"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*"}

def now(): return datetime.now(timezone.utc)

def read(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def write(path,rows):
    rows=list(rows)
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields: fields=["_empty"]
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader(); w.writerows(rows)

def num(v):
    if v in (None,""): return None
    try: return float(v)
    except: return v

def main():
    t=now()
    captured=t.isoformat()
    all_events=[]
    raw_bundle={"captured_at_utc":captured,"days":[]}
    for offset in range(0,8):
        d=(t+timedelta(days=offset)).strftime("%Y%m%d")
        r=requests.get(URL,params={"dates":d,"limit":100},headers=HEADERS,timeout=30)
        r.raise_for_status()
        payload=r.json()
        raw_bundle["days"].append({"date":d,"payload":payload})
        all_events.extend(payload.get("events") or [])

    rows=[]
    for ev in all_events:
        comps=ev.get("competitions") or []
        if not comps: continue
        comp=comps[0]
        competitors={}
        for c in comp.get("competitors") or []:
            if c.get("homeAway") in ("home","away"):
                competitors[c["homeAway"]]=c
        home=competitors.get("home") or {}
        away=competitors.get("away") or {}
        ht=home.get("team") or {}; at=away.get("team") or {}
        odds=comp.get("odds") or []
        if isinstance(odds,dict): odds=[odds]
        if not odds:
            event_id=str(ev.get("id") or "")
            competition_id=str(comp.get("id") or event_id)
            try:
                ou=CORE_ODDS.format(event_id=event_id,competition_id=competition_id)
                rr=requests.get(ou,headers=HEADERS,timeout=20)
                if rr.ok:
                    core=rr.json()
                    odds=core.get("items") or []
            except Exception:
                odds=[]
        for o in odds:
            provider=o.get("provider") or {}
            home_odds=o.get("homeTeamOdds") or {}
            away_odds=o.get("awayTeamOdds") or {}
            rows.append({
                "captured_at_utc":captured,
                "game_id":str(ev.get("id") or ""),
                "game_date":ev.get("date"),
                "home_team_id":str(ht.get("id") or ""),"home_team":ht.get("displayName"),"home_tricode":ht.get("abbreviation"),
                "away_team_id":str(at.get("id") or ""),"away_team":at.get("displayName"),"away_tricode":at.get("abbreviation"),
                "provider_id":str(provider.get("id") or ""),"provider":provider.get("name"),
                "details":o.get("details"),"spread":num(o.get("spread")),"over_under":num(o.get("overUnder")),
                "home_moneyline":num(home_odds.get("moneyLine")),"away_moneyline":num(away_odds.get("moneyLine")),
                "home_spread_odds":num(home_odds.get("spreadOdds")),"away_spread_odds":num(away_odds.get("spreadOdds")),
                "over_odds":num(o.get("overOdds") if o.get("overOdds") is not None else (o.get("over") or {}).get("odds")),
                "under_odds":num(o.get("underOdds") if o.get("underOdds") is not None else (o.get("under") or {}).get("odds")),
                "open_over":num((((o.get("open") or {}).get("over") or {}).get("value"))),
                "open_under":num((((o.get("open") or {}).get("under") or {}).get("value"))),
                "open_home_spread":num((((((o.get("open") or {}).get("spread") or {}).get("home") or {}).get("line")))),
                "open_away_spread":num((((((o.get("open") or {}).get("spread") or {}).get("away") or {}).get("line")))),
                "home_favorite":home_odds.get("favorite"),"away_favorite":away_odds.get("favorite"),
                "source":"espn_scoreboard_odds","pregame_snapshot":True
            })

    raw_dir=RAW/t.strftime("%Y-%m-%d")
    raw_dir.mkdir(parents=True,exist_ok=True)
    raw_path=raw_dir/(t.strftime("%H%M%SZ")+".json.gz")
    with gzip.open(raw_path,"wt",encoding="utf-8") as f:
        json.dump(raw_bundle,f,separators=(",",":"))

    hist=OUT/"market_snapshots.csv.gz"
    existing=read(hist)
    # exact capture-time rows are unique by construction; retain full time series
    write(hist,existing+rows)

    latest={
      "captured_at_utc":captured,"events_scanned":len(all_events),"odds_rows":len(rows),
      "games_with_odds":len(set(r["game_id"] for r in rows)),
      "providers":sorted(set(r["provider"] for r in rows if r.get("provider"))),
      "raw_snapshot":str(raw_path.relative_to(ROOT)),
      "source":"espn_scoreboard_odds",
      "notes":"Prospective market snapshots only. Historical market backfill remains a separate lane."
    }
    (OUT/"latest.json").write_text(json.dumps(latest,indent=2)+"\n")
    print(json.dumps(latest,indent=2))

if __name__=="__main__":
    main()
