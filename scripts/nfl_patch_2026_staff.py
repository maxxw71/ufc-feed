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

def clean(s): return re.sub(r'\s+',' ',s).strip(' ,') if s else None

r=requests.get(URL,headers={'User-Agent':'Mozilla/5.0 (compatible; AppWizaNFLResearch/1.0)'},timeout=40); r.raise_for_status()
text=unescape(re.sub(r'<[^>]+>',' ',r.text))
text=clean(text) or ''
positions=[]
for full,code in TEAM_NAMES.items():
    i=text.find(full)
    if i>=0: positions.append((i,full,code))
positions.sort(); found={}
for idx,(start,full,code) in enumerate(positions):
    end=positions[idx+1][0] if idx+1<len(positions) else min(len(text),start+6000)
    block=text[start:end]
    mo=re.search(r'([A-Z][A-Za-zÀ-ÖØ-öø-ÿ\.\'\- ]{2,80}?)\s+Offensive Coordinator\b',block)
    md=re.search(r'([A-Z][A-Za-zÀ-ÖØ-öø-ÿ\.\'\- ]{2,80}?)\s+Defensive Coordinator\b',block)
    def tail(m):
        if not m:return None
        s=clean(m.group(1)); toks=s.split()
        if len(toks)>5:s=' '.join(toks[-4:])
        return s
    found[code]={'offensive_coordinator':tail(mo),'defensive_coordinator':tail(md)}
valid={k:v for k,v in found.items() if v['offensive_coordinator'] and v['defensive_coordinator']}
missing=sorted(set(TEAM_NAMES.values())-set(valid))
print('2026 complete parsed teams:',len(valid),sorted(valid))
print('2026 missing/incomplete teams:',missing)
for code in missing: print(code, found.get(code))
if len(valid)<20: raise RuntimeError(f'Only parsed {len(valid)} complete 2026 staffs; source/parser appears broken')

staff=pd.read_csv(P)
for code,v in valid.items():
    mask=(staff.season==2026)&(staff.team==code)
    staff.loc[mask,'offensive_coordinator']=v['offensive_coordinator']
    staff.loc[mask,'defensive_coordinator']=v['defensive_coordinator']
    if 'offensive_coordinator_source' in staff: staff.loc[mask,'offensive_coordinator_source']='chatffb_current_rollup'
    if 'defensive_coordinator_source' in staff: staff.loc[mask,'defensive_coordinator_source']='chatffb_current_rollup'
    staff.loc[mask,'staff_source']='chatffb_2026_current_rollup'
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
status['chatffb_2026']={'ok':True,'teams_parsed':len(valid),'missing_teams':missing,'url':URL}
status['oc_filled']=int(staff.offensive_coordinator.notna().sum()); status['dc_filled']=int(staff.defensive_coordinator.notna().sum())
status['both_coordinators_filled']=int((staff.offensive_coordinator.notna()&staff.defensive_coordinator.notna()).sum())
STATUS.write_text(json.dumps(status,indent=2,default=str))
print(json.dumps(status['chatffb_2026'],indent=2))
print(staff[(staff.team=='LAC') & staff.season.isin([2024,2025,2026])].to_string(index=False))
