#!/usr/bin/env python3
import argparse,csv,gzip,json,re,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"market"/"historical"
SCORE="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
CORE="https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{event}/competitions/{competition}/odds"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*"}

SEASON_DATES={
 "2025-26":(date(2025,10,21),date(2026,4,12)),
 "2024-25":(date(2024,10,22),date(2025,4,13)),
 "2023-24":(date(2023,10,24),date(2024,4,14)),
 "2022-23":(date(2022,10,18),date(2023,4,9)),
 "2021-22":(date(2021,10,19),date(2022,4,10)),
 "2020-21":(date(2020,12,22),date(2021,5,16)),
 "2019-20":(date(2019,10,22),date(2020,8,14)),
 "2018-19":(date(2018,10,16),date(2019,4,10)),
 "2017-18":(date(2017,10,17),date(2018,4,11)),
 "2016-17":(date(2016,10,25),date(2017,4,12)),
 "2015-16":(date(2015,10,27),date(2016,4,13)),
}
NBA_TRICODES={"ATL","BOS","BKN","CHA","CHI","CLE","DAL","DEN","DET","GS","GSW","HOU","IND","LAC","LAL","MEM","MIA","MIL","MIN","NO","NOP","NY","NYK","OKC","ORL","PHI","PHX","POR","SAC","SA","SAS","TOR","UTAH","UTA","WSH","WAS"}

def daterange(a,b):
    d=a
    while d<=b:
        yield d
        d+=timedelta(days=1)

def num(v):
    if v in (None,""): return None
    try:return float(v)
    except:return v

def open_line(open_obj,kind,side=None):
    if not isinstance(open_obj,dict): return None
    if kind=="total":
        obj=open_obj.get("total") or {}
        return obj.get("alternateDisplayValue") or obj.get("american") or obj.get("value")
    if kind=="spread":
        obj=(open_obj.get("spread") or {}).get(side or "") or {}
        return obj.get("line") or obj.get("alternateDisplayValue") or obj.get("american")
    return None

def discover(season):
    start,end=SEASON_DATES[season]
    s=requests.Session()
    events={}
    for i,d in enumerate(daterange(start,end),1):
        ds=d.strftime("%Y%m%d")
        r=s.get(SCORE,params={"dates":ds,"limit":100},headers=HEADERS,timeout=25)
        r.raise_for_status()
        for ev in r.json().get("events") or []:
            if int((ev.get("season") or {}).get("type") or 0)!=2: continue
            comps=ev.get("competitions") or []
            if not comps: continue
            comp=comps[0]
            tris=[((c.get("team") or {}).get("abbreviation")) for c in comp.get("competitors") or []]
            tris=[x for x in tris if x]
            if len(tris)!=2 or any(x not in NBA_TRICODES for x in tris): continue
            st=((ev.get("status") or {}).get("type") or {})
            # For completed historical seasons, reject canceled/postponed shells.
            if not bool(st.get("completed")) and st.get("state")!="post": continue
            eid=str(ev.get("id") or "")
            cid=str(comp.get("id") or eid)
            if eid: events[eid]={"event":ev,"competition_id":cid}
        if i%50==0: print(f"{season}: scanned {i} days, {len(events)} completed events",flush=True)
        time.sleep(0.02)
    return events

def fetch_odds(item):
    eid,itemd=item
    ev=itemd["event"]; cid=itemd["competition_id"]
    url=CORE.format(event=eid,competition=cid)
    try:
        r=requests.get(url,params={"limit":100},headers=HEADERS,timeout=25)
        status=r.status_code
        r.raise_for_status()
        data=r.json()
        return eid,status,data,None
    except Exception as e:
        return eid,None,None,str(e)

def flatten(season,eid,ev,data,captured):
    comps=ev.get("competitions") or [{}]
    comp=comps[0]
    sides={}
    for c in comp.get("competitors") or []:
        if c.get("homeAway") in ("home","away"): sides[c["homeAway"]]=c
    h=(sides.get("home") or {}).get("team") or {}
    a=(sides.get("away") or {}).get("team") or {}
    rows=[]
    for o in (data or {}).get("items") or []:
        p=o.get("provider") or {}; ho=o.get("homeTeamOdds") or {}; ao=o.get("awayTeamOdds") or {}
        op=o.get("open") or {}
        rows.append({
          "season":season,"game_id":eid,"game_date":ev.get("date"),
          "home_team":h.get("displayName"),"home_tricode":h.get("abbreviation"),
          "away_team":a.get("displayName"),"away_tricode":a.get("abbreviation"),
          "provider_id":str(p.get("id") or ""),"provider":p.get("name"),
          "details":o.get("details"),"spread":num(o.get("spread")),"over_under":num(o.get("overUnder")),
          "home_moneyline":num(ho.get("moneyLine")),"away_moneyline":num(ao.get("moneyLine")),
          "home_spread_odds":num(ho.get("spreadOdds")),"away_spread_odds":num(ao.get("spreadOdds")),
          "over_odds":num(o.get("overOdds")),"under_odds":num(o.get("underOdds")),
          "open_total":open_line(op,"total"),"open_home_spread":open_line(op,"spread","home"),
          "open_away_spread":open_line(op,"spread","away"),
          "fetched_at_utc":captured,"source":"espn_core_historical_odds"
        })
    return rows

def write_gz(path,rows):
    rows=list(rows); fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields:fields=["_empty"]
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--season",required=True,choices=sorted(SEASON_DATES))
    ap.add_argument("--workers",type=int,default=8)
    args=ap.parse_args()
    season=args.season
    captured=datetime.now(timezone.utc).isoformat()
    events=discover(season)
    rows=[]; failures=[]; events_with_odds=0
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,12))) as ex:
        futs={ex.submit(fetch_odds,item):item[0] for item in events.items()}
        for i,fut in enumerate(as_completed(futs),1):
            eid,status,data,err=fut.result()
            if err:
                failures.append({"game_id":eid,"error":err,"http_status":status}); continue
            rr=flatten(season,eid,events[eid]["event"],data,captured)
            if rr: events_with_odds+=1
            rows.extend(rr)
            if i%200==0: print(f"{season}: odds {i}/{len(events)}",flush=True)
    out=OUT/season.replace("-","_")
    write_gz(out/"odds.csv.gz",rows)
    summary={
      "season":season,"completed_events":len(events),"events_with_odds":events_with_odds,
      "coverage":events_with_odds/len(events) if events else 0,
      "odds_rows":len(rows),"providers":sorted(set(r["provider"] for r in rows if r.get("provider"))),
      "failures":failures[:50],"failure_count":len(failures),"generated_at_utc":captured
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(json.dumps(summary,indent=2))

if __name__=="__main__":
    main()
