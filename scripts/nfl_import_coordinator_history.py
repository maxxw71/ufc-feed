from __future__ import annotations

from pathlib import Path
import json, os
import numpy as np
import pandas as pd

SOURCE_ROOT=Path(os.environ.get('NFL_SOURCE_ROOT','/home/anestishkurti92/nfl-predictor-v1'))
CTX=Path(os.environ.get('NFL_CONTEXT_ROOT','/home/appwiza-runner/nfl-context-data'))
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
SRC=REPO/'data'/'nfl'/'coordinator_history_pfr_2006_2026.csv'
OUT=CTX/'raw'/'coaching_staff_2006_2026.csv'
STATUS=CTX/'NFL_COACHING_STAFF_STATUS.json'
SCHEDULES=SOURCE_ROOT/'data'/'raw'/'schedules_2006_2026.parquet'
OUT.parent.mkdir(parents=True,exist_ok=True)
ALIASES={'OAK':'LV','SD':'LAC','STL':'LA','LAR':'LA'}
def canon(x): return ALIASES.get(str(x),str(x))

if not SRC.exists(): raise RuntimeError(f'Missing hosted coordinator table: {SRC}')
c=pd.read_csv(SRC)
need={'season','team','offensive_coordinator','defensive_coordinator'}
if not need.issubset(c.columns): raise RuntimeError(f'Coordinator file missing columns: {need-set(c.columns)}')
c['season']=pd.to_numeric(c.season,errors='coerce').astype('Int64'); c['team']=c.team.map(canon)
c=c.dropna(subset=['season','team']).drop_duplicates(['season','team'])

s=pd.read_parquet(SCHEDULES); s=s[s.game_type.eq('REG')].copy()
parts=[]
for side in ['home','away']:
    z=pd.DataFrame({'season':pd.to_numeric(s.season).astype(int),'team':s[f'{side}_team'],'head_coach':s[f'{side}_coach']})
    z['team']=z.team.map(canon); parts.append(z)
h=pd.concat(parts,ignore_index=True)
h=(h.groupby(['season','team'],as_index=False)
     .agg(head_coach=('head_coach',lambda x:' / '.join(dict.fromkeys(x.dropna().astype(str))) if len(x.dropna()) else None)))
keep=[x for x in ['season','team','offensive_coordinator','defensive_coordinator','staff_source','staff_page'] if x in c.columns]
staff=h.merge(c[keep],on=['season','team'],how='left').sort_values(['team','season']).reset_index(drop=True)
for role in ['head_coach','offensive_coordinator','defensive_coordinator']:
    prev=staff.groupby('team')[role].shift(1); known=staff[role].notna() & prev.notna()
    staff[f'{role}_changed']=np.nan
    staff.loc[known,f'{role}_changed']=staff.loc[known,role].astype(str).ne(prev.loc[known].astype(str)).astype(float)
staff['coordinator_changes']=staff[['offensive_coordinator_changed','defensive_coordinator_changed']].sum(axis=1,min_count=2)
staff['major_staff_changes']=staff[['head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed']].sum(axis=1,min_count=3)
staff.to_csv(OUT,index=False)

both=staff.offensive_coordinator.notna() & staff.defensive_coordinator.notna()
modern=staff[staff.season>=2010]
mb=modern.offensive_coordinator.notna() & modern.defensive_coordinator.notna()
by_season={str(int(y)):{'teams':int(len(g)),'oc':int(g.offensive_coordinator.notna().sum()),'dc':int(g.defensive_coordinator.notna().sum()),'both':int((g.offensive_coordinator.notna()&g.defensive_coordinator.notna()).sum())} for y,g in staff.groupby('season')}
status={'team_seasons':int(len(staff)),'head_coach_filled':int(staff.head_coach.notna().sum()),
        'oc_filled':int(staff.offensive_coordinator.notna().sum()),'dc_filled':int(staff.defensive_coordinator.notna().sum()),
        'both_coordinators_filled':int(both.sum()),'both_coordinator_pct':round(float(both.mean()*100),2),
        'modern_2010_2026_both_pct':round(float(mb.mean()*100),2),'by_season':by_season,
        'output':str(OUT),'source':'repo-hosted PFR coordinator table + nflverse schedule head coaches',
        'notes':['Coordinator source is collected off the AppWiza IP and versioned in GitHub.','Unknown coordinator values remain null.']}
STATUS.write_text(json.dumps(status,indent=2)); print(json.dumps(status,indent=2))
if len(modern)<500 or (len(modern) and float(mb.mean())<.80):
    raise RuntimeError('Imported coordinator coverage is too weak for modeling')
