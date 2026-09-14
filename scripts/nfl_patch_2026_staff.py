from __future__ import annotations

from pathlib import Path
from html import unescape
import json, os, re, requests
import numpy as np
import pandas as pd

CTX=Path(os.environ.get('NFL_CONTEXT_ROOT','/home/appwiza-runner/nfl-context-data'))
P=CTX/'raw'/'coaching_staff_2006_2026.csv'
STATUS=CTX/'NFL_COACHING_STAFF_STATUS.json'
URL='https://www.chatffb.com/coaches'
TEAM_NAMES={
 'Arizona Cardinals':'ARI','Atlanta Falcons':'ATL','Baltimore Ravens':'BAL','Buffalo Bills':'BUF','Carolina Panthers':'CAR','Chicago Bears':'CHI','Cincinnati Bengals':'CIN','Cleveland Browns':'CLE',
 'Dallas Cowboys':'DAL','Denver Broncos':'DEN','Detroit Lions':'DET','Green Bay Packers':'GB','Houston Texans':'HOU','Indianapolis Colts':'IND','Jacksonville Jaguars':'JAX','Kansas City Chiefs':'KC',
 'Las Vegas Raiders':'LV','Los Angeles Chargers':'LAC','Los Angeles Rams':'LA','Miami Dolphins':'MIA','Minnesota Vikings':'MIN','New England Patriots':'NE','New Orleans Saints':'NO','New York Giants':'NYG',
 'New York Jets':'NYJ','Philadelphia Eagles':'PHI','Pittsburgh Steelers':'PIT','San Francisco 49ers':'SF','Seattle Seahawks':'SEA','Tampa Bay Buccaneers':'TB','Tennessee Titans':'TEN','Washington Commanders':'WAS'
}
OFFICIAL_2026={
 'GB':('Adam Stenavich','Jonathan Gannon','https://www.packers.com/team/coaches-roster/'),
 'JAX':('Grant Udinski','Anthony Campanile','https://www.jaguars.com/team/coaches-roster/'),
 'LA':('Nate Scheelhaase','Chris Shula','https://www.therams.com/team/coaches-roster/'),
 'NE':('Josh McDaniels','Zak Kuhr','https://www.patriots.com/team/coaches-roster/'),
 'SF':('Klay Kubiak','Raheem Morris','https://www.49ers.com/team/coaches-roster/'),
 'TB':('Zac Robinson','NO FORMAL DC - Todd Bowles HC defensive play-caller','https://www.buccaneers.com/team/coaches-roster/'),
 'LAC':('Mike McDaniel',"Chris O'Leary",'https://www.chargers.com/team/coaches-roster/'),
}

def clean(s): return re.sub(r'\s+',' ',s).strip(' ,') if s else None

def role_name(line, role):
    suffix=' '+role
    if not line.endswith(suffix): return None
    name=clean(line[:-len(suffix)])
    if not name or len(name.split())<2 or len(name.split())>6:return None
    if name in {'Offense','Defense'}:return None
    return name

r=requests.get(URL,headers={'User-Agent':'Mozilla/5.0 (compatible; AppWizaNFLResearch/1.0)'},timeout=40); r.raise_for_status()
# Preserve HTML text-node boundaries so the role label cannot absorb neighboring coaches/titles.
raw=unescape(re.sub(r'<[^>]+>','\n',r.text))
lines=[clean(x) for x in raw.splitlines()]
lines=[x for x in lines if x]
team_hits=[]
for i,line in enumerate(lines):
    for full,code in TEAM_NAMES.items():
        if line==full:
            team_hits.append((i,full,code)); break
# Some responsive layouts repeat a team name. Keep the first occurrence that is followed by staff roles.
first={}
for i,full,code in team_hits:first.setdefault(code,(i,full,code))
positions=sorted(first.values())
found={}
for idx,(start,full,code) in enumerate(positions):
    end=positions[idx+1][0] if idx+1<len(positions) else len(lines)
    block=lines[start:end]
    oc=dc=None
    for line in block:
        oc=oc or role_name(line,'Offensive Coordinator')
        dc=dc or role_name(line,'Defensive Coordinator')
    found[code]={'offensive_coordinator':oc,'defensive_coordinator':dc}
valid={k:v for k,v in found.items() if v['offensive_coordinator'] and v['defensive_coordinator']}
missing=sorted(set(TEAM_NAMES.values())-set(valid))
print('2026 clean complete rollup teams:',len(valid),sorted(valid))
print('2026 rollup missing/incomplete teams:',missing)
for code in missing: print(code, found.get(code))
if len(valid)<20: raise RuntimeError(f'Only parsed {len(valid)} complete clean 2026 staffs; source/parser appears broken')

staff=pd.read_csv(P)
for code,v in valid.items():
    mask=(staff.season==2026)&(staff.team==code)
    staff.loc[mask,'offensive_coordinator']=v['offensive_coordinator']
    staff.loc[mask,'defensive_coordinator']=v['defensive_coordinator']
    if 'offensive_coordinator_source' in staff: staff.loc[mask,'offensive_coordinator_source']='chatffb_current_rollup_clean'
    if 'defensive_coordinator_source' in staff: staff.loc[mask,'defensive_coordinator_source']='chatffb_current_rollup_clean'
    staff.loc[mask,'staff_source']='chatffb_2026_current_rollup_clean'
for code,(oc,dc,src) in OFFICIAL_2026.items():
    mask=(staff.season==2026)&(staff.team==code)
    staff.loc[mask,'offensive_coordinator']=oc; staff.loc[mask,'defensive_coordinator']=dc
    if 'offensive_coordinator_source' in staff: staff.loc[mask,'offensive_coordinator_source']='official_team_site_2026'
    if 'defensive_coordinator_source' in staff: staff.loc[mask,'defensive_coordinator_source']='official_team_site_2026'
    staff.loc[mask,'staff_source']='official_team_site_2026'
    if 'source_page' in staff: staff.loc[mask,'source_page']=src
staff=staff.sort_values(['team','season']).reset_index(drop=True)
for role in ['head_coach','offensive_coordinator','defensive_coordinator']:
    prev=staff.groupby('team')[role].shift(1); known=staff[role].notna() & prev.notna()
    staff[f'{role}_changed']=np.nan
    staff.loc[known,f'{role}_changed']=staff.loc[known,role].astype(str).ne(prev.loc[known].astype(str)).astype(float)
staff['coordinator_changes']=staff[['offensive_coordinator_changed','defensive_coordinator_changed']].sum(axis=1,min_count=2)
staff['major_staff_changes']=staff[['head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed']].sum(axis=1,min_count=3)
staff.to_csv(P,index=False)
try: status=json.loads(STATUS.read_text())
except Exception: status={}
remaining=staff[(staff.season==2026)&(staff.offensive_coordinator.isna()|staff.defensive_coordinator.isna())].team.astype(str).tolist()
status['chatffb_2026']={'ok':True,'teams_parsed_clean':len(valid),'rollup_missing_teams':missing,'url':URL}
status['official_2026_overrides']={'teams':sorted(OFFICIAL_2026),'remaining_incomplete_teams':remaining}
status['oc_filled']=int(staff.offensive_coordinator.notna().sum()); status['dc_filled']=int(staff.defensive_coordinator.notna().sum())
status['both_coordinators_filled']=int((staff.offensive_coordinator.notna()&staff.defensive_coordinator.notna()).sum())
STATUS.write_text(json.dumps(status,indent=2,default=str))
print(json.dumps(status['chatffb_2026'],indent=2))
print(json.dumps(status['official_2026_overrides'],indent=2))
print('=== 2026 all staffs ===')
print(staff[staff.season.eq(2026)][['team','head_coach','offensive_coordinator','defensive_coordinator','offensive_coordinator_changed','defensive_coordinator_changed','staff_source']].sort_values('team').to_string(index=False))
