#!/usr/bin/env python3
from __future__ import annotations

import json,re,urllib.request
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

import numpy as np,pandas as pd

from mls_bootstrap_warehouse import TEAM_INFO,canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
RAW=ROOT/'data/raw/transactions';RAW.mkdir(parents=True,exist_ok=True)
PROC=ROOT/'data/processed';REP=ROOT/'reports'
BASE_CANDIDATES=[
    PROC/'mls_match_features_confirmed_lineup_enriched.parquet',
    PROC/'mls_match_features_context_enriched.parquet',
]
OUT_TX=PROC/'mls_official_transactions_2021_2024.parquet'
OUT=PROC/'mls_match_features_transaction_enriched.parquet'
REPORT=REP/'transaction_feature_meta.json'

JINA='https://r.jina.ai/'
UA='Mozilla/5.0 AppwizaMLSTransactions/1.0'
URLS={y:f'https://www.mlssoccer.com/news/{y}-mls-transactions' for y in range(2021,2025)}
TEAM_SET={canon_team(x) for x in TEAM_INFO}
TEAM_HEADING_ALIASES={
    'Atlanta United':'Atlanta United FC',
    'Chicago Fire':'Chicago Fire FC',
    'D.C. United':'D.C. United',
    'DC United':'D.C. United',
    'Houston Dynamo':'Houston Dynamo FC',
    'Inter Miami':'Inter Miami CF',
    'LAFC':'Los Angeles FC',
    'Los Angeles Football Club':'Los Angeles FC',
    'Minnesota United':'Minnesota United FC',
    'Montreal Impact':'CF Montréal',
    'CF Montreal':'CF Montréal',
    'New England Revolution':'New England Revolution',
    'New York City FC':'New York City FC',
    'NYCFC':'New York City FC',
    'New York Red Bulls':'New York Red Bulls',
    'Orlando City':'Orlando City SC',
    'Philadelphia Union':'Philadelphia Union',
    'Portland Timbers':'Portland Timbers',
    'Seattle Sounders':'Seattle Sounders FC',
    'Seattle Sounders FC':'Seattle Sounders FC',
    'Sporting Kansas City':'Sporting Kansas City',
    'St. Louis CITY SC':'St. Louis City SC',
    'St. Louis City SC':'St. Louis City SC',
    'Vancouver Whitecaps':'Vancouver Whitecaps FC',
    'Vancouver Whitecaps FC':'Vancouver Whitecaps FC',
}
def canon_tx_team(x):
    raw=clean(x).strip(' :')
    raw=TEAM_HEADING_ALIASES.get(raw,raw)
    return canon_team(raw)

def now():return datetime.now(timezone.utc).isoformat()

def fetch(url):
    req=urllib.request.Request(JINA+url,headers={'User-Agent':UA,'Accept':'text/plain'})
    return urllib.request.urlopen(req,timeout=60).read().decode('utf-8','replace')

def clean(x):
    x=re.sub(r'!\[[^\]]*\]\([^)]*\)',' ',str(x or ''))
    x=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',x)
    x=re.sub(r'^#{1,6}\s*','',x)
    x=x.replace('**','').replace('__','')
    return re.sub(r'\s+',' ',x).strip()

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
        ('signing','SIGNING'),('re-entry draft','RE_ENTRY_DRAFT')
    ]:
        if key in n:return label
    return 'OTHER'

def parse_cell(cell,team,direction,source_year,url):
    cell=clean(cell).strip(' |')
    if not cell:return None
    m=re.match(r'^(?P<pos>[A-Z](?:/[A-Z])?)\s*-\s*(?P<player>.+?)\s*\((?P<detail>.+)\)\s*$',cell)
    if not m:return None
    detail=m.group('detail').strip()
    dm=re.match(r'(?P<date>\d{1,2}/\d{1,2}/\d{2})\s*-\s*(?P<move>.+)$',detail)
    if not dm:return None
    try:dt=pd.to_datetime(dm.group('date'),format='%m/%d/%y',errors='raise')
    except Exception:return None
    move=dm.group('move').strip()
    return {
        'source_year':source_year,'source_url':url,'team':team,'direction':direction,
        'position':m.group('pos'),'player_name':m.group('player').strip(),
        'transaction_date':dt,'movement':move,'transaction_type':classify(move),
    }

def parse_page(year,text,url):
    rows=[];team=None
    for raw in text.splitlines():
        line=clean(raw)
        if not line:continue
        cand=canon_tx_team(line)
        if cand in TEAM_SET and ('|' not in line):
            team=cand
            continue
        if not team or '|' not in line:continue
        if 'PLAYERS IN' in line.upper() or re.fullmatch(r'[-: |]+',line):continue
        table_line=line.strip()
        if table_line.startswith('|'):table_line=table_line[1:]
        if table_line.endswith('|'):table_line=table_line[:-1]
        cells=[x.strip() for x in table_line.split('|')]
        if len(cells)<2:continue
        left=parse_cell(cells[0],team,'IN',year,url)
        right=parse_cell(cells[1],team,'OUT',year,url)
        if left:rows.append(left)
        if right:rows.append(right)
    return rows

def main():
    rows=[];coverage={}
    for year,url in URLS.items():
        text=fetch(url)
        parsed=parse_page(year,text,url)
        if len(parsed)<100:raise RuntimeError(f'{year} MLS transaction page parsed only {len(parsed)} rows; fail closed')
        rows.extend(parsed);coverage[str(year)]=len(parsed)
        (RAW/f'{year}.json').write_text(json.dumps(parsed,indent=2,default=str,ensure_ascii=False))
        print(year,'transactions',len(parsed),flush=True)

    tx=pd.DataFrame(rows)
    tx['transaction_date']=pd.to_datetime(tx.transaction_date,errors='coerce',utc=True)
    tx=tx[tx.transaction_date.notna()].drop_duplicates(['team','direction','player_name','transaction_date','movement']).copy()
    tx.to_parquet(OUT_TX,index=False)

    base_path=next((p for p in BASE_CANDIDATES if p.exists()),None)
    if not base_path:raise RuntimeError('MLS context warehouse missing')
    d=pd.read_parquet(base_path).copy()
    d['date']=pd.to_datetime(d.date,errors='coerce',utc=True)

    # Coverage is season-page availability, not an assumption of zero transactions.
    source_years=set(URLS)
    feature_rows=[]
    team_tx={t:z.sort_values('transaction_date') for t,z in tx.groupby('team')}
    for _,g in d.iterrows():
        row={'match_id':g.match_id}
        season=int(g.season) if pd.notna(g.season) else None
        for side in ['home','away']:
            team=canon_team(g[f'{side}_team']);z=team_tx.get(team,pd.DataFrame())
            available=int(season in source_years)
            row[f'{side}_transaction_data_available']=available
            for days in [14,30,60,90]:
                if available and len(z):
                    lo=g.date-pd.Timedelta(days=days)
                    q=z[(z.transaction_date.lt(g.date))&(z.transaction_date.ge(lo))]
                    nin=int(q.direction.eq('IN').sum());nout=int(q.direction.eq('OUT').sum())
                else:
                    nin=nout=np.nan
                row[f'{side}_transactions_in_{days}d']=nin
                row[f'{side}_transactions_out_{days}d']=nout
                row[f'{side}_transaction_net_{days}d']=(nin-nout) if pd.notna(nin) and pd.notna(nout) else np.nan
            if available and len(z):
                q=z[z.transaction_date.lt(g.date)]
                last=q.transaction_date.max() if len(q) else pd.NaT
                row[f'{side}_days_since_transaction']=(g.date-last).total_seconds()/86400 if pd.notna(last) else np.nan
            else:row[f'{side}_days_since_transaction']=np.nan
        for days in [14,30,60,90]:
            for kind in ['transactions_in','transactions_out','transaction_net']:
                h=row[f'home_{kind}_{days}d'];a=row[f'away_{kind}_{days}d']
                row[f'edge_{kind}_{days}d']=(h-a) if pd.notna(h) and pd.notna(a) else np.nan
        feature_rows.append(row)

    f=pd.DataFrame(feature_rows)
    out=d.merge(f,on='match_id',how='left',validate='1:1')
    out.to_parquet(OUT,index=False)
    added=[c for c in out.columns if c not in d.columns]
    meta={
        'built_at':now(),'source_pages':URLS,'transaction_rows':len(tx),'page_parse_rows':coverage,
        'base_file':str(base_path),'rows':len(out),'columns':len(out.columns),'added_columns':len(added),'added':added,
        'season_coverage':sorted(source_years),'output':str(OUT),
        'leakage_note':'Each match uses only official MLS transaction entries dated strictly before the target match. Uncovered seasons remain NaN with explicit data-available flags; they are never treated as zero transactions.'
    }
    REPORT.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':
    main()
