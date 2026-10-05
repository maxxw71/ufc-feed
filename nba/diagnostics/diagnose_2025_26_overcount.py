#!/usr/bin/env python3
import json
from collections import Counter,defaultdict
from datetime import date,timedelta,datetime,timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"diagnostics"
OUT.mkdir(parents=True,exist_ok=True)
SCORE="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*"}
NBA={"ATL","BOS","BKN","CHA","CHI","CLE","DAL","DEN","DET","GS","GSW","HOU","IND","LAC","LAL","MEM","MIA","MIL","MIN","NO","NOP","NY","NYK","OKC","ORL","PHI","PHX","POR","SAC","SA","SAS","TOR","UTAH","UTA","WSH","WAS"}

def dates(a,b):
    d=a
    while d<=b:
        yield d
        d+=timedelta(days=1)

events=[]
for d in dates(date(2025,10,21),date(2026,4,12)):
    r=requests.get(SCORE,params={"dates":d.strftime("%Y%m%d"),"limit":100},headers=HEADERS,timeout=20)
    r.raise_for_status()
    for ev in r.json().get("events") or []:
        if int((ev.get("season") or {}).get("type") or 0)!=2: continue
        comps=ev.get("competitions") or []
        if not comps: continue
        c=comps[0]
        teams={}
        for x in c.get("competitors") or []:
            t=x.get("team") or {}
            if x.get("homeAway") in ("home","away"):
                teams[x["homeAway"]]=t
        if set(teams)!= {"home","away"}: continue
        ht=teams["home"].get("abbreviation"); at=teams["away"].get("abbreviation")
        if ht not in NBA or at not in NBA: continue
        st=((ev.get("status") or {}).get("type") or {})
        if not bool(st.get("completed")) and st.get("state")!="post": continue
        sides={}
        for x in c.get("competitors") or []:
            if x.get("homeAway") in ("home","away"): sides[x["homeAway"]]=x
        events.append({
          "id":str(ev.get("id") or ""),"date":ev.get("date"),"name":ev.get("name"),
          "shortName":ev.get("shortName"),"home":ht,"away":at,
          "home_score":(sides.get("home") or {}).get("score"),"away_score":(sides.get("away") or {}).get("score"),
          "neutralSite":c.get("neutralSite"),"conferenceCompetition":c.get("conferenceCompetition"),
          "notes":c.get("notes"),"tournamentId":c.get("tournamentId"),"groups":c.get("groups"),
          "type":c.get("type"),"season":ev.get("season"),"status":ev.get("status")
        })

team_counts=Counter()
for e in events:
    team_counts[e["home"]]+=1; team_counts[e["away"]]+=1
over={t:n for t,n in team_counts.items() if n>82}

identity=defaultdict(list)
for e in events:
    key=(str(e["date"])[:10],tuple(sorted([e["home"],e["away"]])),str(e["home_score"]),str(e["away_score"]))
    identity[key].append(e)
dups=[{"key":k,"events":v} for k,v in identity.items() if len(v)>1]

# surface candidate special games: duplicates, neutral games, games involving every >82 team,
# or games whose competition metadata carries notes/tournament markers.
special=[]
for e in events:
    if e["home"] in over or e["away"] in over:
        if e.get("neutralSite") or e.get("notes") or e.get("tournamentId") or e.get("type"):
            special.append(e)

report={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "event_count":len(events),"team_counts":dict(sorted(team_counts.items())),
 "over_82":over,"duplicate_identity_groups":dups,
 "candidate_special_games":special
}
(OUT/"2025_26_regular_overcount.json").write_text(json.dumps(report,indent=2,default=str)+"\n")
print(json.dumps({"event_count":len(events),"over_82":over,"duplicate_groups":len(dups),"special_candidates":len(special)},indent=2))
