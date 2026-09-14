from __future__ import annotations

from pathlib import Path
import json
import os
import re
import time

import numpy as np
import pandas as pd
import requests

SOURCE_ROOT=Path(os.environ.get('NFL_SOURCE_ROOT','/home/anestishkurti92/nfl-predictor-v1'))
CTX=Path(os.environ.get('NFL_CONTEXT_ROOT','/home/appwiza-runner/nfl-context-data'))
OUT=CTX/'raw'/'coaching_staff_2006_2026.csv'
STATUS=CTX/'NFL_COACHING_STAFF_STATUS.json'
OUT.parent.mkdir(parents=True,exist_ok=True)
SCHEDULES=SOURCE_ROOT/'data'/'raw'/'schedules_2006_2026.parquet'
API='https://en.wikipedia.org/w/api.php'
UA={'User-Agent':'AppWizaNFLResearch/1.0 (+https://appwiza.com)'}
ALIASES={'LAR':'LA','STL':'LA','SD':'LAC','OAK':'LV','WSH':'WAS','ARZ':'ARI','BLT':'BAL'}
BASE_NAMES={
 'ARI':'Arizona Cardinals','ATL':'Atlanta Falcons','BAL':'Baltimore Ravens','BUF':'Buffalo Bills','CAR':'Carolina Panthers','CHI':'Chicago Bears','CIN':'Cincinnati Bengals','CLE':'Cleveland Browns',
 'DAL':'Dallas Cowboys','DEN':'Denver Broncos','DET':'Detroit Lions','GB':'Green Bay Packers','HOU':'Houston Texans','IND':'Indianapolis Colts','JAX':'Jacksonville Jaguars','KC':'Kansas City Chiefs',
 'LV':'Las Vegas Raiders','LAC':'Los Angeles Chargers','LA':'Los Angeles Rams','MIA':'Miami Dolphins','MIN':'Minnesota Vikings','NE':'New England Patriots','NO':'New Orleans Saints','NYG':'New York Giants',
 'NYJ':'New York Jets','PHI':'Philadelphia Eagles','PIT':'Pittsburgh Steelers','SF':'San Francisco 49ers','SEA':'Seattle Seahawks','TB':'Tampa Bay Buccaneers','TEN':'Tennessee Titans','WAS':'Washington Commanders'
}

def canon(x):
    if pd.isna(x): return None
    s=str(x).strip().upper()
    return ALIASES.get(s,s)

def team_name(code,season):
    if code=='LV' and season<=2019:return 'Oakland Raiders'
    if code=='LAC' and season<=2016:return 'San Diego Chargers'
    if code=='LA' and season<=2015:return 'St. Louis Rams'
    if code=='WAS':
        if season<=2019:return 'Washington Redskins'
        if season<=2021:return 'Washington Football Team'
    return BASE_NAMES[code]

def strip_markup(x):
    if not x:return None
    s=x
    s=re.sub(r'<ref[^>/]*?>.*?</ref>',' ',s,flags=re.I|re.S)
    s=re.sub(r'<ref[^>]*/>',' ',s,flags=re.I)
    s=re.sub(r'<!--.*?-->',' ',s,flags=re.S)
    # Convert common wikilinks to display text.
    s=re.sub(r'\[\[(?:[^\]|]*\|)?([^\]]+)\]\]',r'\1',s)
    s=re.sub(r'\{\{(?:nowrap|small|plainlist|ubl|unbulleted list)\|([^{}]+)\}\}',r'\1',s,flags=re.I)
    s=re.sub(r'\{\{[^{}]*\}\}',' ',s)
    s=re.sub(r"''+",'',s)
    s=re.sub(r'<[^>]+>',' ',s)
    s=s.replace('&nbsp;',' ')
    s=re.sub(r'\s+',' ',s).strip(' ,;|-')
    return s or None

def extract_param(text,names):
    # Infobox values are normally one line. Accept a small continuation window,
    # stopping at the next parameter line.
    for name in names:
        m=re.search(r'^\|\s*'+re.escape(name)+r'\s*=\s*(.*?)(?=\n\|\s*[A-Za-z0-9_ -]+\s*=|\n\}\})',text,flags=re.I|re.M|re.S)
        if m:
            v=strip_markup(m.group(1))
            if v:return v
    return None

# Authoritative HC labels come from nflverse schedules, including midseason realities.
s=pd.read_parquet(SCHEDULES)
s=s[s.game_type.eq('REG')].copy()
parts=[]
for side in ['home','away']:
    z=pd.DataFrame({'season':pd.to_numeric(s.season,errors='coerce').astype(int),'team':s[f'{side}_team'],'head_coach':s[f'{side}_coach']})
    z['team']=z.team.map(canon)
    parts.append(z)
h=pd.concat(parts,ignore_index=True)
h=h.groupby(['season','team'],as_index=False).agg(head_coach=('head_coach',lambda x:x.dropna().astype(str).mode().iloc[0] if len(x.dropna()) else None))

rows=[]; year_status={}
for season in range(2010,2027):
    teams=sorted(h.loc[h.season.eq(season),'team'].dropna().unique())
    title_to_team={f'{season} {team_name(code,season)} season':code for code in teams}
    try:
        params={
          'action':'query','prop':'revisions','rvprop':'content','rvslots':'main',
          'format':'json','formatversion':'2','redirects':'1','titles':'|'.join(title_to_team)
        }
        r=requests.get(API,params=params,headers=UA,timeout=60); r.raise_for_status()
        data=r.json()
        found={}
        for p in data.get('query',{}).get('pages',[]):
            title=p.get('title','')
            # redirects can canonicalize title; recover team by matching suffix/name.
            code=title_to_team.get(title)
            if not code:
                for c in teams:
                    if team_name(c,season) in title:
                        code=c;break
            revs=p.get('revisions') or []
            text=''
            if revs:
                text=(revs[0].get('slots',{}).get('main',{}) or {}).get('content','')
            oc=extract_param(text,['off_coach','offensive_coach','offensive coordinator','offensive_coordinator'])
            dc=extract_param(text,['def_coach','defensive_coach','defensive coordinator','defensive_coordinator'])
            if code:
                found[code]=(oc,dc,title)
        for code in teams:
            oc,dc,title=found.get(code,(None,None,f'{season} {team_name(code,season)} season'))
            rows.append({'season':season,'team':code,'offensive_coordinator':oc,'defensive_coordinator':dc,
                         'staff_source':'wikipedia_team_season_infobox','source_page':title})
        year_status[str(season)]={
          'teams':len(teams),'pages_found':len(found),
          'oc':sum(bool(found.get(c,(None,None,None))[0]) for c in teams),
          'dc':sum(bool(found.get(c,(None,None,None))[1]) for c in teams)
        }
        time.sleep(.35)
    except Exception as e:
        year_status[str(season)]={'teams':len(teams),'error':repr(e),'oc':0,'dc':0}
        for code in teams:
            rows.append({'season':season,'team':code,'offensive_coordinator':None,'defensive_coordinator':None,
                         'staff_source':'wikipedia_team_season_infobox_failed','source_page':f'{season} {team_name(code,season)} season'})

src=pd.DataFrame(rows)
staff=h.merge(src,on=['season','team'],how='left').sort_values(['team','season']).reset_index(drop=True)
for role in ['head_coach','offensive_coordinator','defensive_coordinator']:
    prev=staff.groupby('team')[role].shift(1)
    known=staff[role].notna() & prev.notna()
    staff[f'{role}_changed']=np.nan
    staff.loc[known,f'{role}_changed']=staff.loc[known,role].astype(str).ne(prev.loc[known].astype(str)).astype(float)
staff['coordinator_changes']=staff[['offensive_coordinator_changed','defensive_coordinator_changed']].sum(axis=1,min_count=2)
staff['major_staff_changes']=staff[['head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed']].sum(axis=1,min_count=3)
staff.to_csv(OUT,index=False)

# Do not silently accept a parser/source failure. Require broad historical coverage.
by_season={str(int(y)):{'teams':len(z),'oc':int(z.offensive_coordinator.notna().sum()),'dc':int(z.defensive_coordinator.notna().sum())} for y,z in staff.groupby('season')}
weak={y:v for y,v in by_season.items() if 2010<=int(y)<=2026 and (v['oc']<24 or v['dc']<24)}
status={
 'team_seasons':len(staff),'head_coach_filled':int(staff.head_coach.notna().sum()),
 'oc_filled':int(staff.offensive_coordinator.notna().sum()),'dc_filled':int(staff.defensive_coordinator.notna().sum()),
 'both_coordinators_filled':int((staff.offensive_coordinator.notna()&staff.defensive_coordinator.notna()).sum()),
 'by_season':by_season,'wikipedia_status':year_status,'weak_coverage_seasons':weak,'output':str(OUT),
 'limitations':['Coordinator coverage intentionally starts in 2010; 2006-2009 remain unknown.','Wikipedia team-season infobox coordinator labels are public/auditable but can omit a coordinator when a club did not formally use one.','Unknown change values remain null, never zero.']
}
STATUS.write_text(json.dumps(status,indent=2,default=str))
print(json.dumps(status,indent=2,default=str))
print('=== 2026 LAC ===')
print(staff[(staff.season==2026)&(staff.team=='LAC')].to_string(index=False))
if weak:
    raise RuntimeError(f'Coordinator parser/source weak coverage: {weak}')
