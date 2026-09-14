from __future__ import annotations

from pathlib import Path
import io
import json
import os
import re
import requests
import numpy as np
import pandas as pd

SOURCE_ROOT=Path(os.environ.get('NFL_SOURCE_ROOT','/home/anestishkurti92/nfl-predictor-v1'))
CTX=Path(os.environ.get('NFL_CONTEXT_ROOT','/home/appwiza-runner/nfl-context-data'))
OUT=CTX/'raw'/'coaching_staff_2006_2026.csv'
STATUS=CTX/'NFL_COACHING_STAFF_STATUS.json'
OUT.parent.mkdir(parents=True,exist_ok=True)
SCHEDULES=SOURCE_ROOT/'data'/'raw'/'schedules_2006_2026.parquet'

UA={'User-Agent':'Mozilla/5.0 (compatible; AppWizaNFLResearch/1.0; +https://appwiza.com)'}
ALIASES={'LAR':'LA','STL':'LA','SD':'LAC','OAK':'LV','WSH':'WAS','ARZ':'ARI','BLT':'BAL'}
TEAM_NAMES={
 'Arizona Cardinals':'ARI','Atlanta Falcons':'ATL','Baltimore Ravens':'BAL','Buffalo Bills':'BUF','Carolina Panthers':'CAR','Chicago Bears':'CHI','Cincinnati Bengals':'CIN','Cleveland Browns':'CLE',
 'Dallas Cowboys':'DAL','Denver Broncos':'DEN','Detroit Lions':'DET','Green Bay Packers':'GB','Houston Texans':'HOU','Indianapolis Colts':'IND','Jacksonville Jaguars':'JAX','Kansas City Chiefs':'KC',
 'Las Vegas Raiders':'LV','Los Angeles Chargers':'LAC','Los Angeles Rams':'LA','Miami Dolphins':'MIA','Minnesota Vikings':'MIN','New England Patriots':'NE','New Orleans Saints':'NO','New York Giants':'NYG',
 'New York Jets':'NYJ','Philadelphia Eagles':'PHI','Pittsburgh Steelers':'PIT','San Francisco 49ers':'SF','Seattle Seahawks':'SEA','Tampa Bay Buccaneers':'TB','Tennessee Titans':'TEN','Washington Commanders':'WAS'
}
def canon(x):
    if pd.isna(x): return None
    s=str(x).strip().upper()
    return ALIASES.get(s,s)

def clean(x):
    if pd.isna(x): return None
    s=re.sub(r'\s+',' ',str(x)).strip(' \t\r\n,')
    s=re.sub(r'\s*\(HC\)\s*$', '', s, flags=re.I).strip()
    return s or None

def flat_cols(df):
    df=df.copy()
    df.columns=[' '.join(str(y) for y in c if str(y)!='nan').strip() if isinstance(c,tuple) else str(c).strip() for c in df.columns]
    return df

def find_col(df,*need):
    for c in df.columns:
        low=c.lower()
        if all(n in low for n in need): return c
    return None

def get_tables(url):
    r=requests.get(url,headers=UA,timeout=40)
    r.raise_for_status()
    return [flat_cols(x) for x in pd.read_html(io.StringIO(r.text))]

# Head coach from nflverse schedules is authoritative and gives full 2006-2026 coverage.
s=pd.read_parquet(SCHEDULES)
s=s[s.game_type.eq('REG')].copy()
parts=[]
for side in ['home','away']:
    z=pd.DataFrame({'season':pd.to_numeric(s.season).astype(int),'team':s[f'{side}_team'],'head_coach':s[f'{side}_coach']})
    z['team']=z.team.map(canon)
    parts.append(z)
h=pd.concat(parts,ignore_index=True)
h=h.groupby(['season','team'],as_index=False).agg(head_coach=('head_coach',lambda x:x.dropna().astype(str).mode().iloc[0] if len(x.dropna()) else None))

source_rows=[]; source_status={}

# Historical coordinators: one league-wide table, 480 entries, 2010-2024.
try:
    url='https://www.altdraft.com/coordinators'
    tabs=get_tables(url)
    chosen=None
    for t in tabs:
        yc=find_col(t,'year'); tc=find_col(t,'team'); oc=find_col(t,'offensive','coord'); dc=find_col(t,'defensive','coord')
        if yc and tc and oc and dc and len(t)>=300:
            chosen=t; break
    if chosen is None: raise RuntimeError('AltDraft coordinator table not found')
    yc=find_col(chosen,'year'); tc=find_col(chosen,'team'); hc=find_col(chosen,'head','coach'); oc=find_col(chosen,'offensive','coord'); dc=find_col(chosen,'defensive','coord')
    for rec in chosen.to_dict('records'):
        y=pd.to_numeric(rec.get(yc),errors='coerce')
        if pd.isna(y) or not (2010<=int(y)<=2024): continue
        source_rows.append({'season':int(y),'team':canon(rec.get(tc)),'source_head_coach':clean(rec.get(hc)) if hc else None,
                            'offensive_coordinator':clean(rec.get(oc)),'defensive_coordinator':clean(rec.get(dc)),
                            'staff_source':'altdraft_coordinator_explorer'})
    source_status['altdraft']={'ok':True,'rows':sum(x['staff_source']=='altdraft_coordinator_explorer' for x in source_rows)}
except Exception as e:
    source_status['altdraft']={'ok':False,'error':repr(e)}

# 2025 bridge: ESPN's published projection guide has HC/OC/DC on each of the 32 team pages.
# This is a single bulk PDF, avoiding fragile per-team scraping.
try:
    from pypdf import PdfReader
    pdf_url='https://g.espncdn.com/s/ffldraftkit/25/NFLDK2025_CS_ClayProjections2025.pdf'
    r=requests.get(pdf_url,headers=UA,timeout=60); r.raise_for_status()
    reader=PdfReader(io.BytesIO(r.content))
    found={}
    for page in reader.pages[1:34]:
        text=re.sub(r'\s+',' ',page.extract_text() or ' ')
        team_code=None
        for full,code in TEAM_NAMES.items():
            if f'2025 {full} Projections' in text:
                team_code=code; break
        if not team_code: continue
        mh=re.search(r'\bHC\s+(.+?)\s+LT\b',text)
        mo=re.search(r'\bOC\s+(.+?)\s+LG\b',text)
        md=re.search(r'\bDC\s+(.+?)\s+RG\b',text)
        found[team_code]={
          'season':2025,'team':team_code,
          'source_head_coach':clean(mh.group(1)) if mh else None,
          'offensive_coordinator':clean(mo.group(1)) if mo else None,
          'defensive_coordinator':clean(md.group(1)) if md else None,
          'staff_source':'espn_mike_clay_2025_projection_guide'
        }
    if len(found)<28: raise RuntimeError(f'Only parsed {len(found)} of 32 ESPN 2025 team staff pages')
    source_rows.extend(found.values())
    source_status['espn_2025']={'ok':True,'teams':len(found),'oc':sum(bool(x['offensive_coordinator']) for x in found.values()),'dc':sum(bool(x['defensive_coordinator']) for x in found.values())}
except Exception as e:
    source_status['espn_2025']={'ok':False,'error':repr(e)}

# Current 2026: two league-wide sortable coordinator pages. These are used only for names/current-change context.
def parse_current(url, role):
    tabs=get_tables(url)
    for t in tabs:
        tc=find_col(t,'team'); cc=find_col(t,role,'coordinator'); hc=find_col(t,'head','coach')
        if tc and cc and len(t)>=20:
            out=[]
            for rec in t.to_dict('records'):
                tm=canon(rec.get(tc))
                if not tm: continue
                out.append({'team':tm, role+'_coordinator':clean(rec.get(cc)), 'source_head_coach':clean(rec.get(hc)) if hc else None})
            if len(out)>=20:return pd.DataFrame(out).drop_duplicates('team')
    raise RuntimeError(f'2026 {role} coordinator table not found')

try:
    o=parse_current('https://profootballmania.com/nfl-offensive-coordinators/','offensive')
    source_status['current_2026_offense']={'ok':True,'rows':len(o)}
except Exception as e:
    o=pd.DataFrame(columns=['team','offensive_coordinator']); source_status['current_2026_offense']={'ok':False,'error':repr(e)}
try:
    d=parse_current('https://profootballmania.com/nfl-defensive-coordinators/','defensive')
    source_status['current_2026_defense']={'ok':True,'rows':len(d)}
except Exception as e:
    d=pd.DataFrame(columns=['team','defensive_coordinator']); source_status['current_2026_defense']={'ok':False,'error':repr(e)}
if len(o) or len(d):
    current=o.merge(d,on='team',how='outer',suffixes=('_off','_def'))
    for rec in current.to_dict('records'):
        source_rows.append({'season':2026,'team':rec.get('team'),'source_head_coach':rec.get('source_head_coach_off') or rec.get('source_head_coach_def'),
                            'offensive_coordinator':rec.get('offensive_coordinator'),'defensive_coordinator':rec.get('defensive_coordinator'),
                            'staff_source':'profootballmania_2026_league_tables'})

src=pd.DataFrame(source_rows)
if len(src):
    src=src.groupby(['season','team'],as_index=False).agg(
        source_head_coach=('source_head_coach',lambda x:next((clean(v) for v in x if clean(v)),None)),
        offensive_coordinator=('offensive_coordinator',lambda x:next((clean(v) for v in x if clean(v)),None)),
        defensive_coordinator=('defensive_coordinator',lambda x:next((clean(v) for v in x if clean(v)),None)),
        staff_source=('staff_source',lambda x:';'.join(sorted(set(str(v) for v in x if pd.notna(v))))))
else:
    src=pd.DataFrame(columns=['season','team','source_head_coach','offensive_coordinator','defensive_coordinator','staff_source'])

staff=h.merge(src,on=['season','team'],how='left').sort_values(['team','season'])
staff['head_coach_source_mismatch']=np.where(staff.source_head_coach.notna() & staff.head_coach.notna(),
    staff.source_head_coach.astype(str).str.replace(r'\s+',' ',regex=True).ne(staff.head_coach.astype(str).str.replace(r'\s+',' ',regex=True)).astype(float),np.nan)

for role in ['head_coach','offensive_coordinator','defensive_coordinator']:
    prev=staff.groupby('team')[role].shift(1)
    known=staff[role].notna() & prev.notna()
    staff[f'{role}_changed']=np.nan
    staff.loc[known,f'{role}_changed']=staff.loc[known,role].astype(str).ne(prev.loc[known].astype(str)).astype(float)

staff['coordinator_changes']=staff[['offensive_coordinator_changed','defensive_coordinator_changed']].sum(axis=1,min_count=2)
staff['major_staff_changes']=staff[['head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed']].sum(axis=1,min_count=3)
staff.to_csv(OUT,index=False)

status={
 'team_seasons':len(staff),'head_coach_filled':int(staff.head_coach.notna().sum()),
 'oc_filled':int(staff.offensive_coordinator.notna().sum()),'dc_filled':int(staff.defensive_coordinator.notna().sum()),
 'both_coordinators_filled':int((staff.offensive_coordinator.notna()&staff.defensive_coordinator.notna()).sum()),
 'by_season':{str(int(y)):{'teams':len(z),'oc':int(z.offensive_coordinator.notna().sum()),'dc':int(z.defensive_coordinator.notna().sum())} for y,z in staff.groupby('season')},
 'sources':source_status,'output':str(OUT),
 'limitations':['Historical coordinator coverage begins in 2010; 2006-2009 remain unknown unless another audited source is added.','Unknown coordinator change values remain null, never zero.','2026 current coordinator names come from league-wide current coordinator tables and should be periodically refreshed.']
}
STATUS.write_text(json.dumps(status,indent=2,default=str))
print(json.dumps(status,indent=2,default=str))
