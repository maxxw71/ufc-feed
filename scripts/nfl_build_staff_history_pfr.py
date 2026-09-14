from __future__ import annotations

from pathlib import Path
import io, json, os, random, re, time
import requests
import pandas as pd

SOURCE_ROOT=Path(os.environ.get('NFL_SOURCE_ROOT','/home/anestishkurti92/nfl-predictor-v1'))
CTX=Path(os.environ.get('NFL_CONTEXT_ROOT','/home/appwiza-runner/nfl-context-data'))
OUT=CTX/'raw'/'coaching_staff_2006_2026.csv'
STATUS=CTX/'NFL_COACHING_STAFF_STATUS.json'
OUT.parent.mkdir(parents=True,exist_ok=True)
SCHEDULES=SOURCE_ROOT/'data'/'raw'/'schedules_2006_2026.parquet'

PFR={
 'ARI':'crd','ATL':'atl','BAL':'rav','BUF':'buf','CAR':'car','CHI':'chi','CIN':'cin','CLE':'cle',
 'DAL':'dal','DEN':'den','DET':'det','GB':'gnb','HOU':'htx','IND':'clt','JAX':'jax','KC':'kan',
 'LV':'rai','LAC':'sdg','LA':'ram','MIA':'mia','MIN':'min','NE':'nwe','NO':'nor','NYG':'nyg','NYJ':'nyj',
 'PHI':'phi','PIT':'pit','SF':'sfo','SEA':'sea','TB':'tam','TEN':'oti','WAS':'was'
}
PFR_HOSTS=['https://aws.pro-football-reference.com','https://www.pro-football-reference.com']
ALIASES={'OAK':'LV','SD':'LAC','STL':'LA','LAR':'LA'}
def canon(x): return ALIASES.get(str(x),str(x))

def clean_name(x):
    if pd.isna(x): return None
    s=re.sub(r'\s+',' ',str(x)).strip()
    s=re.sub(r'\s*\([^)]*\)\s*$','',s).strip()
    return s if s and s.lower() not in {'nan','none',''} else None

def coltext(c):
    if isinstance(c,tuple): return ' '.join(str(x) for x in c if str(x)!='nan').lower().strip()
    return str(c).lower().strip()

def find_col(cols,*patterns):
    texts={c:coltext(c) for c in cols}
    for pat in patterns:
        keys=[x.lower() for x in pat]
        for c,t in texts.items():
            if all(k in t for k in keys): return c
    return None

def join_unique(s):
    vals=[]
    for x in s:
        x=clean_name(x)
        if x and x not in vals: vals.append(x)
    return ' / '.join(vals) if vals else None

sess=requests.Session()
sess.headers.update({'User-Agent':'Mozilla/5.0 (compatible; AppWizaNFLResearch/1.0; +https://appwiza.com)'})
rows=[]; failures=[]
for i,(team,code) in enumerate(PFR.items(),1):
    used_url=None; chosen=None; mapping=None; last_error=None
    for host in PFR_HOSTS:
        url=f'{host}/teams/{code}/coaches.htm'
        try:
            r=sess.get(url,timeout=30); r.raise_for_status()
            text=re.sub(r'<!--|-->','',r.text)
            tables=pd.read_html(io.StringIO(text))
            for t in tables:
                cols=list(t.columns)
                yc=find_col(cols,('year',))
                hc=find_col(cols,('coach',))
                oc=find_col(cols,('coordinators','offense'),('offense',))
                dc=find_col(cols,('coordinators','defense'),('defense',))
                gc=find_col(cols,('reg. season','g'),('regular season','g'))
                if yc is not None and hc is not None and oc is not None and dc is not None and len(t)>=10:
                    chosen=t; mapping=(yc,hc,oc,dc,gc); used_url=url; break
            if chosen is not None: break
            last_error=RuntimeError('year-by-year coordinator table not found')
        except Exception as e:
            last_error=e
    if chosen is None:
        failures.append({'team':team,'url':used_url or f'{PFR_HOSTS[0]}/teams/{code}/coaches.htm','error':repr(last_error)})
        print(f'{i:02d}/32 {team}: ERROR {last_error}',flush=True)
        time.sleep(random.uniform(.6,1.0)); continue
    yc,hc,oc,dc,gc=mapping
    before=len(rows)
    for rec in chosen.to_dict('records'):
        y=pd.to_numeric(rec.get(yc),errors='coerce')
        if pd.isna(y) or int(y)<2006 or int(y)>2026: continue
        rows.append({
          'season':int(y),'team':team,'head_coach_pfr':clean_name(rec.get(hc)),
          'offensive_coordinator':clean_name(rec.get(oc)),
          'defensive_coordinator':clean_name(rec.get(dc)),
          'coach_games':pd.to_numeric(rec.get(gc),errors='coerce') if gc is not None else None,
          'staff_source':'pro_football_reference_franchise_coaches','staff_page':used_url,
        })
    print(f'{i:02d}/32 {team}: rows={len(rows)-before} source={used_url}',flush=True)
    time.sleep(random.uniform(.6,1.0))

pfr=pd.DataFrame(rows)
if pfr.empty: raise RuntimeError('PFR coordinator collection returned zero rows')
pfr=(pfr.groupby(['season','team'],as_index=False)
       .agg(head_coach_pfr=('head_coach_pfr',join_unique),
            offensive_coordinator=('offensive_coordinator',join_unique),
            defensive_coordinator=('defensive_coordinator',join_unique),
            staff_source=('staff_source','first'),staff_page=('staff_page','first')))

s=pd.read_parquet(SCHEDULES); s=s[s.game_type.eq('REG')].copy()
parts=[]
for side in ['home','away']:
    z=pd.DataFrame({'season':pd.to_numeric(s.season).astype(int),'team':s[f'{side}_team'],'head_coach':s[f'{side}_coach']})
    z['team']=z.team.map(canon); parts.append(z)
h=pd.concat(parts,ignore_index=True)
h=(h.groupby(['season','team'],as_index=False)
     .agg(head_coach=('head_coach',lambda x:' / '.join(dict.fromkeys(x.dropna().astype(str))) if len(x.dropna()) else None)))
staff=h.merge(pfr.drop(columns=['head_coach_pfr'],errors='ignore'),on=['season','team'],how='left').sort_values(['team','season'])

for role in ['head_coach','offensive_coordinator','defensive_coordinator']:
    prev=staff.groupby('team')[role].shift(1)
    known=staff[role].notna() & prev.notna()
    staff[f'{role}_changed']=pd.Series(pd.NA,index=staff.index,dtype='Float64')
    staff.loc[known,f'{role}_changed']=staff.loc[known,role].astype(str).ne(prev[known].astype(str)).astype(float)
staff['coordinator_changes']=staff[['offensive_coordinator_changed','defensive_coordinator_changed']].sum(axis=1,min_count=2)
staff['major_staff_changes']=staff[['head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed']].sum(axis=1,min_count=3)
staff.to_csv(OUT,index=False)

both=staff.offensive_coordinator.notna() & staff.defensive_coordinator.notna()
by_season={str(int(y)):{'teams':int(len(g)),'oc':int(g.offensive_coordinator.notna().sum()),'dc':int(g.defensive_coordinator.notna().sum()),'both':int((g.offensive_coordinator.notna()&g.defensive_coordinator.notna()).sum())} for y,g in staff.groupby('season')}
status={
 'team_seasons':len(staff),'head_coach_filled':int(staff.head_coach.notna().sum()),
 'oc_filled':int(staff.offensive_coordinator.notna().sum()),'dc_filled':int(staff.defensive_coordinator.notna().sum()),
 'both_coordinators_filled':int(both.sum()),'both_coordinator_pct':round(float(both.mean()*100),2),
 'franchise_pages_succeeded':32-len(failures),'franchise_pages_failed':len(failures),'failures':failures,
 'by_season':by_season,'output':str(OUT),
 'source':'Pro Football Reference year-by-year franchise coaches pages via AWS mirror first; HC cross-checked to nflverse schedules',
 'notes':['Unknown coordinators remain null, never inferred.','Multiple named coordinators within one season are preserved with / separators.']
}
STATUS.write_text(json.dumps(status,indent=2)); print(json.dumps(status,indent=2))
