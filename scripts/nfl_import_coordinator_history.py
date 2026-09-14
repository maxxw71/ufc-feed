from __future__ import annotations

from pathlib import Path
import json, os
import numpy as np
import pandas as pd

SOURCE_ROOT=Path(os.environ.get('NFL_SOURCE_ROOT','/home/anestishkurti92/nfl-predictor-v1'))
CTX=Path(os.environ.get('NFL_CONTEXT_ROOT','/home/appwiza-runner/nfl-context-data'))
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
PFR_SRC=REPO/'data'/'nfl'/'coordinator_history_pfr_2006_2026.csv'
ESPN_SRC=REPO/'data'/'nfl'/'coordinator_history_espn_2019_2025.csv'
OUT=CTX/'raw'/'coaching_staff_2006_2026.csv'
STATUS=CTX/'NFL_COACHING_STAFF_STATUS.json'
SCHEDULES=SOURCE_ROOT/'data'/'raw'/'schedules_2006_2026.parquet'
OUT.parent.mkdir(parents=True,exist_ok=True)
ALIASES={'OAK':'LV','SD':'LAC','STL':'LA','LAR':'LA','ARZ':'ARI','BLT':'BAL','CLV':'CLE','HST':'HOU'}
def canon(x): return ALIASES.get(str(x),str(x))

sources=[]
for priority,path,label in [(1,PFR_SRC,'pfr_hosted'),(2,ESPN_SRC,'espn_guides')]:
    if not path.exists(): continue
    d=pd.read_csv(path)
    need={'season','team','offensive_coordinator','defensive_coordinator'}
    if not need.issubset(d.columns): raise RuntimeError(f'{label} missing columns: {need-set(d.columns)}')
    d['season']=pd.to_numeric(d.season,errors='coerce').astype('Int64'); d['team']=d.team.map(canon)
    d=d.dropna(subset=['season','team']).copy(); d['_priority']=priority; d['_source_file']=label
    sources.append(d)
if not sources: raise RuntimeError('No hosted coordinator source has been published yet')
c=pd.concat(sources,ignore_index=True).sort_values(['season','team','_priority'])
# Fill each team-season field from the highest-priority available source without inventing missing roles.
def first_nonnull(s):
    q=s.dropna()
    return q.iloc[0] if len(q) else None
agg={k:first_nonnull for k in ['offensive_coordinator','defensive_coordinator','staff_source','staff_page'] if k in c.columns}
c=(c.groupby(['season','team'],as_index=False).agg(agg) if agg else c[['season','team']].drop_duplicates())

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
recent=staff[staff.season.between(2019,2025)]
rb=recent.offensive_coordinator.notna() & recent.defensive_coordinator.notna()
by_season={str(int(y)):{'teams':int(len(g)),'oc':int(g.offensive_coordinator.notna().sum()),'dc':int(g.defensive_coordinator.notna().sum()),'both':int((g.offensive_coordinator.notna()&g.defensive_coordinator.notna()).sum())} for y,g in staff.groupby('season')}
status={'team_seasons':int(len(staff)),'head_coach_filled':int(staff.head_coach.notna().sum()),
        'oc_filled':int(staff.offensive_coordinator.notna().sum()),'dc_filled':int(staff.defensive_coordinator.notna().sum()),
        'both_coordinators_filled':int(both.sum()),'both_coordinator_pct':round(float(both.mean()*100),2),
        '2019_2025_both_pct':round(float(rb.mean()*100),2) if len(recent) else 0,
        'source_files':[str(x) for x in [PFR_SRC,ESPN_SRC] if x.exists()], 'by_season':by_season,
        'output':str(OUT),'source':'versioned public coordinator sources + nflverse schedule head coaches',
        'notes':['Head coach comes from nflverse schedules.','2019-2025 coordinator history is sourced from ESPN annual projection guides when available.','Older unknown coordinator values remain null and never count as no-change.','2026 is patched separately from current public staff sources.']}
STATUS.write_text(json.dumps(status,indent=2)); print(json.dumps(status,indent=2))
if len(recent)<220 or (len(recent) and float(rb.mean())<.85):
    raise RuntimeError('2019-2025 coordinator coverage is too weak for modeling')
