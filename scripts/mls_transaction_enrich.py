#!/usr/bin/env python3
from __future__ import annotations

import json,re,urllib.request
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd
from bs4 import BeautifulSoup

from mls_bootstrap_warehouse import TEAM_INFO,canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
RAW=ROOT/'data/raw/transactions';RAW.mkdir(parents=True,exist_ok=True)
PROC=ROOT/'data/processed';REP=ROOT/'reports'
BASE=PROC/'mls_match_features_weather_enriched.parquet'
OUT_TX=PROC/'mls_official_transactions.parquet'
OUT=PROC/'mls_transaction_features.parquet'
REPORT=REP/'transaction_feature_meta.json'
UA='Mozilla/5.0 AppwizaMLSTransactions/2.0'

HIST_URLS={y:f'https://www.mlssoccer.com/news/{y}-mls-transactions' for y in range(2021,2025)}
CURRENT_URL='https://www.mlssoccer.com/league-reports/all-transfers/'
TEAM_SET={canon_team(x) for x in TEAM_INFO}
ALIASES={
    'Atlanta United':'Atlanta United FC','Chicago Fire':'Chicago Fire FC','DC United':'D.C. United',
    'Houston Dynamo':'Houston Dynamo FC','Inter Miami':'Inter Miami CF','LAFC':'Los Angeles FC',
    'Los Angeles Football Club':'Los Angeles FC','Minnesota United':'Minnesota United FC',
    'Montreal Impact':'CF Montréal','CF Montreal':'CF Montréal','NYCFC':'New York City FC',
    'Orlando City':'Orlando City SC','Seattle Sounders':'Seattle Sounders FC',
    'St. Louis CITY SC':'St. Louis City SC','Vancouver Whitecaps':'Vancouver Whitecaps FC',
}

def now():return datetime.now(timezone.utc).isoformat()
def clean(x):return re.sub(r'\s+',' ',str(x or '')).strip()
def cteam(x):return canon_team(ALIASES.get(clean(x).strip(' :'),clean(x).strip(' :')))

def fetch_html(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html'})
    return urllib.request.urlopen(req,timeout=60).read().decode('utf-8','replace')

def classify(detail):
    n=detail.lower()
    for key,label in [
        ('option declined','OPTION_DECLINED'),('out of contract','OUT_OF_CONTRACT'),
        ('contract expired','CONTRACT_EXPIRED'),('loan expired','LOAN_EXPIRED'),
        ('loan expiration','LOAN_EXPIRED'),('mutual contract termination','MUTUAL_TERMINATION'),
        ('contract termination','TERMINATION'),('retired','RETIRED'),('retirement','RETIRED'),
        ('waived','WAIVED'),('buyout','BUYOUT'),('trade','TRADE'),('loan','LOAN'),
        ('transfer','TRANSFER'),('free agent','FREE_AGENT'),('free transfer','FREE_AGENT'),
        ('re-signed','RE_SIGNED'),('homegrown','HOMEGROWN'),('superdraft','SUPERDRAFT'),
        ('generation adidas','GENERATION_ADIDAS'),('mls next pro','MLS_NEXT_PRO'),
        ('signing','SIGNING'),('signed','SIGNING'),('re-entry draft','RE_ENTRY_DRAFT'),
        ('free','FREE_AGENT')
    ]:
        if key in n:return label
    return 'OTHER'

def parse_entry(text,team,direction,source_year,url):
    cell=clean(text).strip(' |')
    if not cell or cell.lower() in {'none','n/a','na','-'}:return None
    m=re.match(r'^(?P<pos>[A-Z](?:/[A-Z])?)\s*-\s*(?P<player>.+?)\s*\((?P<date>\d{1,2}/\d{1,2}/\d{2})\s*-\s*(?P<move>.+)\)\s*$',cell)
    if not m:return None
    try:dt=pd.to_datetime(m.group('date'),format='%m/%d/%y',errors='raise',utc=True)
    except Exception:return None
    move=m.group('move').strip()
    return {'source_year':source_year,'source_url':url,'team':team,'direction':direction,
            'position':m.group('pos'),'player_name':m.group('player').strip(),
            'transaction_date':dt,'movement':move,'transaction_type':classify(move)}

def nearest_team(table):
    # Team heading is present in the real MLS HTML even though some Markdown relays drop it.
    for node in table.find_all_previous(['h2','h3','h4','h5','strong','p'],limit=30):
        txt=clean(node.get_text(' ',strip=True))
        c=cteam(txt)
        if c in TEAM_SET and len(txt)<80:return c
    return None

def parse_historical(year,url,html):
    soup=BeautifulSoup(html,'html.parser');rows=[];tables=0;teams=set()
    for table in soup.find_all('table'):
        team=nearest_team(table)
        if team not in TEAM_SET:continue
        trs=table.find_all('tr')
        if not trs:continue
        headers=[clean(x.get_text(' ',strip=True)).upper() for x in trs[0].find_all(['th','td'])]
        if not any('PLAYERS IN' in h for h in headers) or not any('PLAYERS OUT' in h for h in headers):continue
        tables+=1;teams.add(team)
        for tr in trs[1:]:
            cells=tr.find_all(['td','th'])
            if len(cells)<2:continue
            left=parse_entry(cells[0].get_text(' ',strip=True),team,'IN',year,url)
            right=parse_entry(cells[1].get_text(' ',strip=True),team,'OUT',year,url)
            if left:rows.append(left)
            if right:rows.append(right)
    return rows,{'tables':tables,'teams':len(teams)}

def parse_current(year,url,html):
    soup=BeautifulSoup(html,'html.parser')
    # Parse linear visible strings. The real page preserves club headings and Players In/Out labels.
    vals=[clean(x) for x in soup.stripped_strings if clean(x)]
    rows=[];team=None;direction=None;teams=set()
    for text in vals:
        c=cteam(text)
        if c in TEAM_SET and len(text)<80:
            team=c;direction=None;teams.add(team);continue
        n=text.lower()
        if n in {'players in','player in'}:direction='IN';continue
        if n in {'players out','player out'}:direction='OUT';continue
        if team and direction:
            r=parse_entry(text,team,direction,year,url)
            if r:rows.append(r)
    return rows,{'teams':len(teams)}

def main():
    allrows=[];source_audit={}
    for year,url in HIST_URLS.items():
        html=fetch_html(url)
        rows,audit=parse_historical(year,url,html)
        if len(rows)<100 or audit['teams']<15:
            raise RuntimeError(f'{year} direct MLS HTML parse insufficient: rows={len(rows)} teams={audit["teams"]}')
        allrows.extend(rows);source_audit[str(year)]={'rows':len(rows),**audit,'url':url}
        (RAW/f'{year}.json').write_text(json.dumps(rows,indent=2,default=str,ensure_ascii=False))
        print(year,'rows',len(rows),'teams',audit['teams'],flush=True)

    # Current first-party live table. This is authoritative for 2026 and includes offseason moves dated late 2025.
    html=fetch_html(CURRENT_URL)
    rows,audit=parse_current(2026,CURRENT_URL,html)
    if len(rows)<100 or audit['teams']<20:
        raise RuntimeError(f'2026 all-transfers parse insufficient: rows={len(rows)} teams={audit["teams"]}')
    allrows.extend(rows);source_audit['2026']={'rows':len(rows),**audit,'url':CURRENT_URL}
    (RAW/'2026.json').write_text(json.dumps(rows,indent=2,default=str,ensure_ascii=False))

    tx=pd.DataFrame(allrows)
    tx['transaction_date']=pd.to_datetime(tx.transaction_date,errors='coerce',utc=True)
    tx=tx[tx.transaction_date.notna()].drop_duplicates(['team','direction','player_name','transaction_date','movement']).copy()
    tx.to_parquet(OUT_TX,index=False)

    if not BASE.exists():raise RuntimeError('MLS base warehouse missing')
    d=pd.read_parquet(BASE).copy();d['dt']=pd.to_datetime(d.date,errors='coerce',utc=True)
    team_tx={t:z.sort_values('transaction_date') for t,z in tx.groupby('team')}
    source_years={2021,2022,2023,2024,2026}
    rows=[]
    for _,g in d.iterrows():
        row={'match_id':g.match_id};season=int(g.season) if pd.notna(g.season) else None
        for side in ['home','away']:
            team=cteam(g[f'{side}_team']);z=team_tx.get(team,pd.DataFrame())
            available=int(season in source_years)
            row[f'{side}_transaction_data_available']=available
            for days in [14,30,60,90]:
                if available and len(z):
                    lo=g.dt-pd.Timedelta(days=days)
                    q=z[(z.transaction_date.lt(g.dt))&(z.transaction_date.ge(lo))]
                    nin=int(q.direction.eq('IN').sum());nout=int(q.direction.eq('OUT').sum())
                    high=int(q.transaction_type.isin(['TRANSFER','TRADE','LOAN','SIGNING','FREE_AGENT']).sum())
                elif available:
                    nin=nout=high=0
                else:
                    nin=nout=high=np.nan
                row[f'{side}_transactions_in_{days}d']=nin
                row[f'{side}_transactions_out_{days}d']=nout
                row[f'{side}_transactions_high_activity_{days}d']=high
                row[f'{side}_transaction_net_{days}d']=(nin-nout) if pd.notna(nin) else np.nan
            if available and len(z):
                q=z[z.transaction_date.lt(g.dt)]
                last=q.transaction_date.max() if len(q) else pd.NaT
                row[f'{side}_days_since_transaction']=(g.dt-last).total_seconds()/86400 if pd.notna(last) else np.nan
            else:row[f'{side}_days_since_transaction']=np.nan
        for days in [14,30,60,90]:
            for kind in ['transactions_in','transactions_out','transactions_high_activity','transaction_net']:
                h=row[f'home_{kind}_{days}d'];a=row[f'away_{kind}_{days}d']
                row[f'edge_{kind}_{days}d']=(h-a) if pd.notna(h) and pd.notna(a) else np.nan
        rows.append(row)
    feat=pd.DataFrame(rows);feat.to_parquet(OUT,index=False)
    meta={'built_at':now(),'transaction_rows':len(tx),'source_audit':source_audit,
          'coverage_seasons':sorted(source_years),'known_gap_seasons':[2025],
          'rows':len(feat),'columns':len(feat.columns),'output':str(OUT),
          'leakage_note':'Only official MLS transaction entries dated strictly before each target match are used. Seasons without verified team-attributed source pages remain NaN, never zero.',
          'source_note':'Historical 2021-24 pages are parsed from direct first-party MLS HTML so team headings are preserved. 2026 uses the live first-party All Transfers report.'}
    REPORT.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':main()
