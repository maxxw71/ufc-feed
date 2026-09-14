from __future__ import annotations

from pathlib import Path
import io
import json
import os
import random
import re
import time
import requests
import pandas as pd

SOURCE_ROOT=Path(os.environ.get('NFL_SOURCE_ROOT','/home/anestishkurti92/nfl-predictor-v1'))
CTX=Path(os.environ.get('NFL_CONTEXT_ROOT','/home/appwiza-runner/nfl-context-data'))
OUT=CTX/'raw'/'coaching_staff_2006_2026.csv'
STATUS=CTX/'NFL_COACHING_STAFF_STATUS.json'
OUT.parent.mkdir(parents=True,exist_ok=True)
SCHEDULES=SOURCE_ROOT/'data'/'raw'/'schedules_2006_2026.parquet'

# Current PFR franchise page IDs. Franchise coach pages include the full historical lineage.
PFR={
 'ARI':'crd','ATL':'atl','BAL':'rav','BUF':'buf','CAR':'car','CHI':'chi','CIN':'cin','CLE':'cle',
 'DAL':'dal','DEN':'den','DET':'det','GB':'gnb','HOU':'htx','IND':'clt','JAX':'jax','KC':'kan',
 'LV':'rai','LAC':'sdg','LA':'ram','MIA':'mia','MIN':'min','NE':'nwe','NO':'nor','NYG':'nyg','NYJ':'nyj',
 'PHI':'phi','PIT':'pit','SF':'sfo','SEA':'sea','TB':'tam','TEN':'oti','WAS':'was'
}
ALIASES={'OAK':'LV','SD':'LAC','STL':'LA','LAR':'LA'}
def canon(x): return ALIASES.get(str(x),str(x))

def clean_name(x):
    if pd.isna(x): return None
    s=re.sub(r'\s+',' ',str(x)).strip()
    s=re.sub(r'\s*\([^)]*\)\s*$','',s).strip()
    return s or None

def find_col(cols, keys):
    for c in cols:
        t=' '.join(c) if isinstance(c,tuple) else str(c)
        low=t.lower().strip()
        if all(k in low for k in keys): return c
    return None

sess=requests.Session()
sess.headers.update({'User-Agent':'Mozilla/5.0 (compatible; AppWizaNFLResearch/1.0; +https://appwiza.com)'})
rows=[]; failures=[]
for i,(team,code) in enumerate(PFR.items(),1):
    url=f'https://www.pro-football-reference.com/teams/{code}/coaches.htm'
    try:
        r=sess.get(url,timeout=30)
        r.raise_for_status()
        text=re.sub(r'<!--|-->','',r.text)
        tables=pd.read_html(io.StringIO(text))
        chosen=None
        for t in tables:
            cols=list(t.columns)
            yc=find_col(cols,['year'])
            cc=find_col(cols,['coach'])
            if yc is not None and cc is not None and len(t)>=10:
                chosen=t; break
        if chosen is None: raise RuntimeError('coaches_year table not found')
        cols=list(chosen.columns)
        yc=find_col(cols,['year']); hc=find_col(cols,['coach'])
        oc=find_col(cols,['offensive','coordinator']) or find_col(cols,['off','coord']) or find_col(cols,['oc'])
        dc=find_col(cols,['defensive','coordinator']) or find_col(cols,['def','coord']) or find_col(cols,['dc'])
        for rec in chosen.to_dict('records'):
            y=pd.to_numeric(rec.get(yc),errors='coerce')
            if pd.isna(y) or int(y)<2006 or int(y)>2026: continue
            rows.append({
              'season':int(y),'team_canon':team,
              'head_coach_pfr':clean_name(rec.get(hc)),
              'offensive_coordinator':clean_name(rec.get(oc)) if oc is not None else None,
              'defensive_coordinator':clean_name(rec.get(dc)) if dc is not None else None,
              'staff_source':'pro_football_reference_franchise_coaches','staff_page':url,
            })
        print(f'{i:02d}/32 {team}: rows={sum(1 for x in rows if x["team_canon"]==team)}',flush=True)
    except Exception as e:
        failures.append({'team':team,'url':url,'error':repr(e)})
        print(f'{i:02d}/32 {team}: ERROR {e}',flush=True)
    time.sleep(random.uniform(2.2,3.4))

pfr=pd.DataFrame(rows)
# Head coach in nflverse schedules remains authoritative for each season/team.
s=pd.read_parquet(SCHEDULES)
s=s[s.game_type.eq('REG')].copy()
parts=[]
for side in ['home','away']:
    z=pd.DataFrame({'season':pd.to_numeric(s.season).astype(int),'team':s[f'{side}_team'],'head_coach':s[f'{side}_coach']})
    z['team_canon']=z.team.map(canon)
    parts.append(z)
h=pd.concat(parts,ignore_index=True)
h=h.groupby(['season','team_canon'],as_index=False).agg(head_coach=('head_coach',lambda x:x.dropna().astype(str).mode().iloc[0] if len(x.dropna()) else None))
staff=h.merge(pfr.drop(columns=['head_coach_pfr'],errors='ignore'),on=['season','team_canon'],how='left').sort_values(['team_canon','season'])
for role in ['head_coach','offensive_coordinator','defensive_coordinator']:
    prev=staff.groupby('team_canon')[role].shift(1)
    known=staff[role].notna() & prev.notna()
    staff[f'{role}_changed']=pd.Series(pd.NA,index=staff.index,dtype='Float64')
    staff.loc[known,f'{role}_changed']=staff.loc[known,role].astype(str).ne(prev[known].astype(str)).astype(float)
staff['coordinator_changes']=staff[['offensive_coordinator_changed','defensive_coordinator_changed']].sum(axis=1,min_count=2)
staff['major_staff_changes']=staff[['head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed']].sum(axis=1,min_count=3)
staff=staff.rename(columns={'team_canon':'team'})
staff.to_csv(OUT,index=False)
status={
 'team_seasons':len(staff),'head_coach_filled':int(staff.head_coach.notna().sum()),
 'oc_filled':int(staff.offensive_coordinator.notna().sum()),'dc_filled':int(staff.defensive_coordinator.notna().sum()),
 'both_coordinators_filled':int((staff.offensive_coordinator.notna()&staff.defensive_coordinator.notna()).sum()),
 'franchise_pages_succeeded':32-len(failures),'franchise_pages_failed':len(failures),'failures':failures,
 'output':str(OUT),'source':'Pro Football Reference year-by-year franchise coaches pages; HC cross-checked to nflverse schedules'
}
STATUS.write_text(json.dumps(status,indent=2))
print(json.dumps(status,indent=2))
