#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, re, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
RAW=ROOT/'data/raw/espn_mls'
CACHE=RAW/'parsed'
PROC=ROOT/'data/processed'
REP=ROOT/'reports'
for p in [RAW,CACHE,PROC,REP]:p.mkdir(parents=True,exist_ok=True)

BASE=PROC/'mls_match_features_advanced.parquet'
LINEUPS=PROC/'espn_confirmed_lineups.parquet'
CARDS=PROC/'espn_match_cards.parquet'
MATCHMAP=PROC/'espn_match_map.parquet'
META=REP/'espn_lineup_card_meta.json'

JINA='https://r.jina.ai/https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/'
UA='Mozilla/5.0 AppwizaMLSLineups/1.0'

ESPN_TEAM_ALIASES={
    'Red Bull New York':'New York Red Bulls',
    'New York Red Bulls':'New York Red Bulls',
    'Atlanta United':'Atlanta United FC',
    'Atlanta United FC':'Atlanta United FC',
    'CF Montréal':'CF Montréal',
    'CF Montreal':'CF Montréal',
    'LAFC':'Los Angeles FC',
    'Los Angeles FC':'Los Angeles FC',
    'Inter Miami CF':'Inter Miami CF',
    'St. Louis CITY SC':'St. Louis City SC',
    'St. Louis City SC':'St. Louis City SC',
}

def now():return datetime.now(timezone.utc).isoformat()

def canon_espn_team(x):
    raw=str(x or '').strip()
    return canon_team(ESPN_TEAM_ALIASES.get(raw,raw))

def jina_json(path,timeout=60,retries=4):
    url=JINA+path
    last=None
    for attempt in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/plain'})
            body=urllib.request.urlopen(req,timeout=timeout).read().decode('utf-8','replace')
            payload=body.split('Markdown Content:',1)[-1].strip()
            return json.loads(payload),body
        except Exception as e:
            last=e
            time.sleep(1.0*(attempt+1))
    raise last

def scoreboard(day):
    cache=CACHE/f'scoreboard_{day}.json'
    if cache.exists():
        return json.loads(cache.read_text())
    data,_=jina_json(f'scoreboard?dates={day}&limit=100')
    events=[]
    for ev in data.get('events') or []:
        comp=(ev.get('competitions') or [{}])[0]
        if str((comp.get('type') or {}).get('abbreviation','')).lower()=='friendly':
            continue
        teams={}
        for c in comp.get('competitors') or []:
            teams[c.get('homeAway')]=canon_espn_team((c.get('team') or {}).get('displayName'))
        if not teams.get('home') or not teams.get('away'):continue
        events.append({
            'espn_event_id':str(ev.get('id')),
            'date':ev.get('date'),
            'home_team':teams['home'],
            'away_team':teams['away'],
            'status':(((comp.get('status') or {}).get('type') or {}).get('name')),
        })
    cache.write_text(json.dumps(events,ensure_ascii=False,separators=(',',':')))
    return events

def parse_summary(event_id):
    cache=CACHE/f'summary_{event_id}.json'
    if cache.exists():
        return json.loads(cache.read_text())
    d,body=jina_json(f'summary?event={event_id}',timeout=75)

    roster_rows=[]
    for tr in d.get('rosters') or []:
        team=canon_espn_team((tr.get('team') or {}).get('displayName'))
        home_away=tr.get('homeAway')
        for r in tr.get('roster') or []:
            a=r.get('athlete') or {}
            pos=a.get('position') or {}
            stats={str(x.get('name')):x.get('value') for x in (r.get('stats') or []) if x.get('name')}
            roster_rows.append({
                'espn_event_id':str(event_id),
                'team':team,
                'home_away':home_away,
                'espn_player_id':str(a.get('id')) if a.get('id') is not None else None,
                'player_name':a.get('displayName') or a.get('fullName'),
                'starter':bool(r.get('starter')),
                'active':r.get('active'),
                'subbed_in':r.get('subbedIn'),
                'subbed_out':r.get('subbedOut'),
                'formation_place':r.get('formationPlace'),
                'position':pos.get('abbreviation') if isinstance(pos,dict) else pos,
                'yellow_cards':stats.get('yellowCards'),
                'red_cards':stats.get('redCards'),
            })

    card_rows=[]
    header_comp=((d.get('header') or {}).get('competitions') or [{}])[0]
    for detail in header_comp.get('details') or []:
        typ=(detail.get('type') or {}).get('text') or ''
        low=typ.lower()
        if 'yellow card' not in low and 'red card' not in low:
            continue
        athlete=(detail.get('athletesInvolved') or [{}])[0]
        team_id=str((detail.get('team') or {}).get('id')) if (detail.get('team') or {}).get('id') is not None else None
        card_rows.append({
            'espn_event_id':str(event_id),
            'card_type':'RED' if 'red' in low else 'YELLOW',
            'minute':((detail.get('clock') or {}).get('displayValue')),
            'espn_player_id':str(athlete.get('id')) if athlete.get('id') is not None else None,
            'player_name':athlete.get('displayName') or athlete.get('fullName'),
            'espn_team_id':team_id,
        })

    parsed={
        'espn_event_id':str(event_id),
        'source_url':f'https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/summary?event={event_id}',
        'source_sha256':hashlib.sha256(body.encode()).hexdigest(),
        'rosters':roster_rows,
        'cards':card_rows,
    }
    cache.write_text(json.dumps(parsed,ensure_ascii=False,separators=(',',':')))
    return parsed

def merge_existing(path,new,key_cols):
    if path.exists():
        old=pd.read_parquet(path)
        z=pd.concat([old,new],ignore_index=True,sort=False)
    else:z=new.copy()
    if len(z):
        z=z.drop_duplicates(key_cols,keep='last')
    z.to_parquet(path,index=False)
    return z

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--years',nargs='+',type=int,default=[2023])
    ap.add_argument('--workers',type=int,default=6)
    args=ap.parse_args()
    years=sorted(set(args.years))

    base=pd.read_parquet(BASE).copy()
    base['date']=pd.to_datetime(base.date,errors='coerce')
    base=base[base.season.isin(years)&base.asa_game_available.eq(True)&base.asa_knockout_game.eq(False)].copy()
    base['home_team']=base.home_team.map(canon_team);base['away_team']=base.away_team.map(canon_team)
    base=base[base.date.notna()].copy()

    dates=sorted(base.date.dt.strftime('%Y%m%d').unique())
    events=[]
    score_fail=[]
    for i,day in enumerate(dates,1):
        try:events.extend(scoreboard(day))
        except Exception as e:score_fail.append({'day':day,'error':type(e).__name__+':'+str(e)[:180]})
        if i%25==0:print('scoreboards',i,'/',len(dates),'events',len(events),flush=True)

    ev=pd.DataFrame(events)
    if ev.empty:raise RuntimeError('ESPN scoreboard mapping returned zero events')
    ev=ev.drop_duplicates(['espn_event_id']).copy()

    # Exact same-day/team mapping. ESPN may use a different display name but canonical aliases handle it.
    maps=[]
    for _,g in base.iterrows():
        day=g.date.strftime('%Y-%m-%d')
        z=ev[(ev.home_team.eq(g.home_team))&(ev.away_team.eq(g.away_team))]
        if len(z)>1:
            z=z[pd.to_datetime(z.date,errors='coerce',utc=True).dt.strftime('%Y-%m-%d').eq(day)]
        if len(z)==1:
            r=z.iloc[0]
            maps.append({'match_id':g.match_id,'asa_game_id':str(g.asa_game_id),'season':int(g.season),
                         'date':g.date,'home_team':g.home_team,'away_team':g.away_team,
                         'espn_event_id':str(r.espn_event_id),'espn_event_date':r.date})
    mmap=pd.DataFrame(maps)
    if mmap.empty:raise RuntimeError('No ESPN events matched MLS warehouse')

    event_ids=sorted(mmap.espn_event_id.unique())
    summaries=[];sum_fail=[]
    def work(eid):
        try:return eid,parse_summary(eid),None
        except Exception as e:return eid,None,type(e).__name__+':'+str(e)[:180]
    with ThreadPoolExecutor(max_workers=max(1,args.workers)) as ex:
        futs=[ex.submit(work,eid) for eid in event_ids]
        for n_,fut in enumerate(as_completed(futs),1):
            eid,data,err=fut.result()
            if err:sum_fail.append({'espn_event_id':eid,'error':err})
            else:summaries.append(data)
            if n_%50==0:print('summaries',n_,'/',len(event_ids),'ok',len(summaries),'fail',len(sum_fail),flush=True)

    map_by_event=mmap.set_index('espn_event_id').to_dict('index')
    lineup_rows=[];card_rows=[];complete=0
    for s in summaries:
        meta=map_by_event.get(str(s['espn_event_id']))
        if not meta:continue
        rr=[]
        for x in s['rosters']:
            row={**meta,**x}
            lineup_rows.append(row);rr.append(row)
        for x in s['cards']:card_rows.append({**meta,**x})
        if rr:
            home_st=sum(bool(x['starter']) for x in rr if x['home_away']=='home')
            away_st=sum(bool(x['starter']) for x in rr if x['home_away']=='away')
            complete+=int(home_st==11 and away_st==11)

    ldf=pd.DataFrame(lineup_rows);cdf=pd.DataFrame(card_rows)
    if ldf.empty:raise RuntimeError('ESPN summaries produced zero lineup rows')
    all_lineups=merge_existing(LINEUPS,ldf,['espn_event_id','team','espn_player_id'])
    if len(cdf):
        all_cards=merge_existing(CARDS,cdf,['espn_event_id','card_type','minute','espn_player_id','player_name'])
    else:
        all_cards=pd.read_parquet(CARDS) if CARDS.exists() else pd.DataFrame()
    all_map=merge_existing(MATCHMAP,mmap,['match_id'])

    mapped_matches=len(mmap);requested=len(base)
    line_events=ldf.espn_event_id.nunique()
    starter_counts=ldf[ldf.starter.eq(True)].groupby(['espn_event_id','home_away']).size().unstack(fill_value=0)
    complete_from_df=int(((starter_counts.get('home',0)==11)&(starter_counts.get('away',0)==11)).sum()) if len(starter_counts) else 0
    meta={
        'built_at':now(),'years_requested':years,'warehouse_matches_requested':requested,
        'scoreboard_dates':len(dates),'scoreboard_failures':score_fail,
        'matched_events':mapped_matches,'match_rate':mapped_matches/max(1,requested),
        'summary_ok':len(summaries),'summary_failures':sum_fail,
        'lineup_events':int(line_events),'complete_11v11_lineup_events':complete_from_df,
        'lineup_rows_added':len(ldf),'card_rows_added':len(cdf),
        'total_lineup_rows':len(all_lineups),'total_card_rows':len(all_cards),'total_match_map_rows':len(all_map),
        'leakage_note':'Confirmed starters are historical post-match facts. They may be used only to construct state for subsequent matches unless a prospective lineup was captured before the target kickoff.',
    }
    META.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':main()
