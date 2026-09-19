#!/usr/bin/env python3
from __future__ import annotations

import hashlib,json,urllib.request
from datetime import datetime,timezone,timedelta
from pathlib import Path

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'live/lineups';OUT.mkdir(parents=True,exist_ok=True)
JINA='https://r.jina.ai/https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/'
UA='Mozilla/5.0 AppwizaMLSProspectiveLineups/1.0'

def now():return datetime.now(timezone.utc)

def fetch_json(path):
    url=JINA+path
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/plain'})
    body=urllib.request.urlopen(req,timeout=60).read().decode('utf-8','replace')
    payload=body.split('Markdown Content:',1)[-1].strip()
    return json.loads(payload),hashlib.sha256(body.encode()).hexdigest()

def event_time(ev):
    x=ev.get('date')
    if not x:return None
    try:return datetime.fromisoformat(x.replace('Z','+00:00')).astimezone(timezone.utc)
    except Exception:return None

def parse_lineups(d):
    teams=[]
    for tr in d.get('rosters') or []:
        team=tr.get('team') or {}
        starters=[]
        bench=[]
        for r in tr.get('roster') or []:
            a=r.get('athlete') or {}
            pos=r.get('position') or a.get('position') or {}
            row={
                'espn_player_id':str(a.get('id')) if a.get('id') is not None else None,
                'player_name':a.get('displayName') or a.get('fullName'),
                'position':pos.get('abbreviation') if isinstance(pos,dict) else pos,
                'formation_place':r.get('formationPlace'),
                'starter':bool(r.get('starter')),
                'active':r.get('active'),
            }
            (starters if row['starter'] else bench).append(row)
        teams.append({
            'home_away':tr.get('homeAway'),
            'espn_team_id':str(team.get('id')) if team.get('id') is not None else None,
            'team_name':team.get('displayName') or team.get('name'),
            'starters':starters,'bench':bench,
        })
    by={x['home_away']:x for x in teams}
    complete=(
        len(by.get('home',{}).get('starters',[]))==11 and
        len(by.get('away',{}).get('starters',[]))==11
    )
    return teams,complete

def main():
    captured=now()
    days=[captured.date(),(captured+timedelta(days=1)).date()]
    events={}
    for day in days:
        try:
            d,_=fetch_json('scoreboard?dates='+day.strftime('%Y%m%d')+'&limit=100')
        except Exception:
            continue
        for ev in d.get('events') or []:
            eid=str(ev.get('id'))
            if eid:events[eid]=ev

    snapshots=[]
    for eid,ev in events.items():
        ko=event_time(ev)
        if ko is None:continue
        mins=(ko-captured).total_seconds()/60
        # First priority is pre-kickoff capture. A short post-kickoff grace
        # period is retained but explicitly labelled so it is never treated as
        # pregame-known information.
        if mins>150 or mins<-30:continue
        try:d,sha=fetch_json('summary?event='+eid)
        except Exception:continue
        teams,complete=parse_lineups(d)
        if not complete:continue
        pregame=captured<=ko
        snapshots.append({
            'captured_at':captured.isoformat(),
            'espn_event_id':eid,
            'kickoff_utc':ko.isoformat(),
            'minutes_before_kickoff':(ko-captured).total_seconds()/60,
            'pregame_known':pregame,
            'source_url':'https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/summary?event='+eid,
            'source_sha256':sha,
            'teams':teams,
        })

    if not snapshots:
        print(json.dumps({'captured_at':captured.isoformat(),'complete_lineups':0},indent=2))
        return

    # Immutable per-event first-pregame-known file. Later snapshots may update
    # history but never overwrite the earliest valid pregame capture.
    history=OUT/'history.jsonl'
    known_first={}
    if history.exists():
        for line in history.read_text().splitlines():
            try:
                x=json.loads(line)
                if x.get('pregame_known'):
                    known_first.setdefault(str(x.get('espn_event_id')),x)
            except Exception:pass

    appended=[]
    with history.open('a') as h:
        for x in snapshots:
            h.write(json.dumps(x,ensure_ascii=False,separators=(',',':'))+'\n')
            appended.append(x)
            if x['pregame_known'] and x['espn_event_id'] not in known_first:
                known_first[x['espn_event_id']]=x
                (OUT/f"first_pregame_{x['espn_event_id']}.json").write_text(json.dumps(x,indent=2,ensure_ascii=False))

    latest={'captured_at':captured.isoformat(),'complete_lineups':len(snapshots),'snapshots':snapshots}
    (OUT/'latest.json').write_text(json.dumps(latest,indent=2,ensure_ascii=False))
    print(json.dumps({
        'captured_at':captured.isoformat(),
        'complete_lineups':len(snapshots),
        'pregame_complete':sum(x['pregame_known'] for x in snapshots),
        'first_pregame_total':len(known_first),
    },indent=2))

if __name__=='__main__':
    main()
