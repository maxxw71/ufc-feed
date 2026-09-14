from __future__ import annotations

from pathlib import Path
import io, re, time, random
import requests
import pandas as pd

OUT=Path('data/nfl/coordinator_history_pfr_2006_2026.csv')
OUT.parent.mkdir(parents=True,exist_ok=True)
PFR={
 'ARI':'crd','ATL':'atl','BAL':'rav','BUF':'buf','CAR':'car','CHI':'chi','CIN':'cin','CLE':'cle',
 'DAL':'dal','DEN':'den','DET':'det','GB':'gnb','HOU':'htx','IND':'clt','JAX':'jax','KC':'kan',
 'LV':'rai','LAC':'sdg','LA':'ram','MIA':'mia','MIN':'min','NE':'nwe','NO':'nor','NYG':'nyg','NYJ':'nyj',
 'PHI':'phi','PIT':'pit','SF':'sfo','SEA':'sea','TB':'tam','TEN':'oti','WAS':'was'
}
HOSTS=['https://aws.pro-football-reference.com','https://www.pro-football-reference.com']
UA={'User-Agent':'Mozilla/5.0 (compatible; AppWizaNFLResearch/1.0; +https://appwiza.com)'}

def clean(x):
    if x is None or pd.isna(x): return None
    s=str(x).strip()
    s=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',s)
    s=re.sub(r'\s+',' ',s).strip(' |')
    return None if not s or s.lower() in {'nan','none'} else s

def coltext(c):
    return (' '.join(str(x) for x in c if str(x)!='nan') if isinstance(c,tuple) else str(c)).lower().strip()

def find_col(cols,*patterns):
    for pat in patterns:
        for c in cols:
            t=coltext(c)
            if all(k.lower() in t for k in pat): return c
    return None

def parse_html(text,team,url):
    text=re.sub(r'<!--|-->','',text)
    for t in pd.read_html(io.StringIO(text)):
        cols=list(t.columns)
        yc=find_col(cols,('year',)); hc=find_col(cols,('coach',))
        oc=find_col(cols,('coordinators','offense'),('offense',)); dc=find_col(cols,('coordinators','defense'),('defense',))
        if yc is None or hc is None or oc is None or dc is None or len(t)<10: continue
        rows=[]; current_year=None
        for rec in t.to_dict('records'):
            y=pd.to_numeric(rec.get(yc),errors='coerce')
            if pd.notna(y): current_year=int(y)
            if current_year is None or not 2006<=current_year<=2026: continue
            rows.append({'season':current_year,'team':team,'head_coach_pfr':clean(rec.get(hc)),
                         'offensive_coordinator':clean(rec.get(oc)),'defensive_coordinator':clean(rec.get(dc)),
                         'staff_source':'pfr_html','staff_page':url})
        if rows:return rows
    raise RuntimeError('year-by-year coordinator table not found in HTML')

def parse_markdown(text,team,url):
    marker='## Year-by-Year Coaches'
    if marker not in text: raise RuntimeError('year-by-year section missing in proxy text')
    sec=text.split(marker,1)[1]
    if '\n## ' in sec: sec=sec.split('\n## ',1)[0]
    rows=[]; current_year=None
    for line in sec.splitlines():
        if '|' not in line or re.match(r'^\s*[-|: ]+$',line): continue
        cells=[clean(x) for x in line.split('|')]
        cells=[x for x in cells if x is not None]
        if not cells: continue
        first=cells[0]
        if re.fullmatch(r'20\d{2}',first or ''):
            current_year=int(first)
        elif current_year is None:
            continue
        # Header rows and summary lines are ignored.
        if current_year is None or not 2006<=current_year<=2026 or len(cells)<3: continue
        if first in {'Year','Rk'}: continue
        # In PFR reader output the final two columns are offense and defense coordinators.
        oc,dc=cells[-2],cells[-1]
        coach=cells[1] if re.fullmatch(r'20\d{2}',first or '') and len(cells)>3 else cells[0]
        if oc in {'Offense','Defense'} or dc in {'Offense','Defense'}: continue
        rows.append({'season':current_year,'team':team,'head_coach_pfr':coach,
                     'offensive_coordinator':oc,'defensive_coordinator':dc,
                     'staff_source':'pfr_via_jina_proxy','staff_page':url})
    if not rows: raise RuntimeError('no coordinator rows parsed from proxy text')
    return rows

def join_unique(s):
    vals=[]
    for x in s:
        x=clean(x)
        if x and x not in vals:vals.append(x)
    return ' / '.join(vals) if vals else None

sess=requests.Session(); sess.headers.update(UA)
all_rows=[]; failures=[]
for i,(team,code) in enumerate(PFR.items(),1):
    got=None; errors=[]
    for host in HOSTS:
        url=f'{host}/teams/{code}/coaches.htm'
        try:
            r=sess.get(url,timeout=30); r.raise_for_status(); got=parse_html(r.text,team,url); break
        except Exception as e: errors.append(f'{url}: {e!r}')
    if got is None:
        src=f'https://www.pro-football-reference.com/teams/{code}/coaches.htm'
        proxy=f'https://r.jina.ai/{src}'
        try:
            r=sess.get(proxy,timeout=45); r.raise_for_status(); got=parse_markdown(r.text,team,src)
        except Exception as e: errors.append(f'{proxy}: {e!r}')
    if got:
        all_rows.extend(got); print(f'{i:02d}/32 {team}: {len(got)} rows via {got[0]["staff_source"]}',flush=True)
    else:
        failures.append({'team':team,'errors':errors}); print(f'{i:02d}/32 {team}: FAILED',flush=True)
    time.sleep(random.uniform(.4,.8))

if not all_rows: raise RuntimeError('No coordinator rows collected')
df=pd.DataFrame(all_rows)
df=(df.groupby(['season','team'],as_index=False)
      .agg(head_coach_pfr=('head_coach_pfr',join_unique),offensive_coordinator=('offensive_coordinator',join_unique),
           defensive_coordinator=('defensive_coordinator',join_unique),staff_source=('staff_source','first'),staff_page=('staff_page','first')))
df=df.sort_values(['season','team'])
modern=df[df.season>=2010]
coverage=float((modern.offensive_coordinator.notna()&modern.defensive_coordinator.notna()).mean()) if len(modern) else 0
print('rows',len(df),'modern both coordinator coverage',round(coverage*100,2),'failed teams',[x['team'] for x in failures])
if len(df)<500 or coverage<.80:
    raise RuntimeError(f'Coordinator coverage too weak: rows={len(df)} modern_both={coverage:.3f} failures={failures}')
df.to_csv(OUT,index=False)
print('wrote',OUT)
