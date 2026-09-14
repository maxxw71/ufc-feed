from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re, time
import requests
import pandas as pd
from pypdf import PdfReader

OUT=Path('data/nfl/coordinator_history_espn_2019_2025.csv')
OUT.parent.mkdir(parents=True,exist_ok=True)
URLS={
  2019:'https://g.espncdn.com/s/ffldraftkit/19/NFLDK2019_CS_ClayProjections.pdf',
  2020:'https://g.espncdn.com/s/ffldraftkit/20/NFLDK2020_CS_ClayProjections.pdf',
  2021:'https://g.espncdn.com/s/ffldraftkit/21/NFLDK2021_CS_ClayProjections2021.pdf',
  2022:'https://g.espncdn.com/s/ffldraftkit/22/NFLDK2022_CS_ClayProjections2022.pdf',
  2023:'https://g.espncdn.com/s/ffldraftkit/23/NFLDK2023_CS_ClayProjections2023.pdf',
  2024:'https://g.espncdn.com/s/ffldraftkit/24/NFLDK2024_CS_ClayProjections2024.pdf',
  2025:'https://g.espncdn.com/s/ffldraftkit/25/NFLDK2025_CS_ClayProjections2025.pdf',
}
TEAM_NAMES={
 'ARI':['Arizona Cardinals'], 'ATL':['Atlanta Falcons'], 'BAL':['Baltimore Ravens'], 'BUF':['Buffalo Bills'],
 'CAR':['Carolina Panthers'], 'CHI':['Chicago Bears'], 'CIN':['Cincinnati Bengals'], 'CLE':['Cleveland Browns'],
 'DAL':['Dallas Cowboys'], 'DEN':['Denver Broncos'], 'DET':['Detroit Lions'], 'GB':['Green Bay Packers'],
 'HOU':['Houston Texans'], 'IND':['Indianapolis Colts'], 'JAX':['Jacksonville Jaguars'], 'KC':['Kansas City Chiefs'],
 'LV':['Las Vegas Raiders','Oakland Raiders'], 'LAC':['Los Angeles Chargers'], 'LA':['Los Angeles Rams'],
 'MIA':['Miami Dolphins'], 'MIN':['Minnesota Vikings'], 'NE':['New England Patriots'], 'NO':['New Orleans Saints'],
 'NYG':['New York Giants'], 'NYJ':['New York Jets'], 'PHI':['Philadelphia Eagles'], 'PIT':['Pittsburgh Steelers'],
 'SEA':['Seattle Seahawks'], 'SF':['San Francisco 49ers'], 'TB':['Tampa Bay Buccaneers'], 'TEN':['Tennessee Titans'],
 'WAS':['Washington Commanders','Washington Football Team','Washington Redskins'],
}

def clean(s):
    if s is None:return None
    s=re.sub(r'\s+',' ',s).strip(' |')
    return s or None

def extract_between(text,start,end):
    # ESPN team-page layout has compact staff lines such as:
    # HC Jonathan Gannon LT Paris Johnson Jr.; OC Drew Petzing LG Evan Brown; DC Nick Rallis RG ...
    m=re.search(rf'(?:^|\s){re.escape(start)}\s+(.+?)\s+{re.escape(end)}(?:\s|$)',text,re.I)
    return clean(m.group(1)) if m else None

def identify_team(text):
    low=text.lower()
    hits=[]
    for code,names in TEAM_NAMES.items():
        if any(name.lower() in low for name in names):hits.append(code)
    return hits[0] if len(hits)==1 else None

sess=requests.Session(); sess.headers.update({'User-Agent':'Mozilla/5.0 AppWiza NFL research'})
rows=[]; season_status={}
for season,url in URLS.items():
    print('download',season,url,flush=True)
    r=sess.get(url,timeout=60); r.raise_for_status()
    if not r.content.startswith(b'%PDF'):raise RuntimeError(f'{season}: response was not PDF ({r.headers.get("content-type")})')
    reader=PdfReader(BytesIO(r.content))
    found={}
    # Team projections are the first 32 content pages after the cover. Search a few extras defensively.
    for page_no,page in enumerate(reader.pages[1:min(36,len(reader.pages))],start=1):
        raw=page.extract_text() or ''
        text=re.sub(r'\s+',' ',raw)
        team=identify_team(text)
        if not team or team in found:continue
        hc=extract_between(text,'HC','LT')
        oc=extract_between(text,'OC','LG')
        dc=extract_between(text,'DC','RG')
        if not (hc and oc and dc):
            # Backup line-oriented extraction for PDF versions that preserve newlines more cleanly.
            lines=[re.sub(r'\s+',' ',x).strip() for x in raw.splitlines() if x.strip()]
            for line in lines:
                if not hc and re.search(r'\bHC\b',line):hc=extract_between(' '+line+' ','HC','LT')
                if not oc and re.search(r'\bOC\b',line):oc=extract_between(' '+line+' ','OC','LG')
                if not dc and re.search(r'\bDC\b',line):dc=extract_between(' '+line+' ','DC','RG')
        if hc or oc or dc:
            found[team]={'season':season,'team':team,'head_coach_espn':hc,'offensive_coordinator':oc,
                         'defensive_coordinator':dc,'staff_source':'espn_mike_clay_projection_guide','staff_page':url,'pdf_page':page_no+1}
    rows.extend(found.values())
    complete=sum(1 for x in found.values() if x['offensive_coordinator'] and x['defensive_coordinator'])
    season_status[season]={'teams_found':len(found),'both_coordinators':complete,
                           'missing_teams':sorted(set(TEAM_NAMES)-set(found))}
    print(season,season_status[season],flush=True)
    time.sleep(.5)

df=pd.DataFrame(rows).sort_values(['season','team'])
print(df[['season','team','offensive_coordinator','defensive_coordinator']].to_string(index=False))
print('STATUS',season_status)
# Require strong coverage but permit a handful of extraction/source omissions; unknowns remain null.
expected=len(URLS)*32
both=(df.offensive_coordinator.notna()&df.defensive_coordinator.notna()).sum() if len(df) else 0
if len(df)<int(expected*.90) or both<int(expected*.85):
    raise RuntimeError(f'ESPN coordinator coverage too weak: rows={len(df)}/{expected}, both={both}/{expected}, status={season_status}')
df.to_csv(OUT,index=False)
print('wrote',OUT,'rows',len(df),'both',int(both))
