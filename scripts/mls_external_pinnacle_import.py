#!/usr/bin/env python3
from __future__ import annotations

import io,json,urllib.request
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd

from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
RAW=ROOT/'data/raw/external_pinnacle';RAW.mkdir(parents=True,exist_ok=True)
PROC=ROOT/'data/processed';REP=ROOT/'reports'
BASE=PROC/'mls_match_features_weather_enriched.parquet'
RAW_XLSX=RAW/'USA.xlsx'
NORMALIZED=PROC/'mls_external_pinnacle_closing_rows.parquet'
FEATURES=PROC/'mls_external_pinnacle_closing_features.parquet'
REPORT=REP/'external_pinnacle_import_meta.json'

SOURCE_REPO='https://github.com/stephen1-hub/-MLS-Betting-Market-Efficiency-Analysis-2012-2026-'
SOURCE_URL='https://raw.githubusercontent.com/stephen1-hub/-MLS-Betting-Market-Efficiency-Analysis-2012-2026-/main/USA.xlsx'
UA='Mozilla/5.0 AppwizaMLSExternalOdds/1.0'

ALIASES={
    'Atlanta United':'Atlanta United FC',
    'Atlanta Utd':'Atlanta United FC',
    'CF Montreal':'CF Montréal',
    'Montreal Impact':'CF Montréal',
    'D.C. United':'D.C. United',
    'DC United':'D.C. United',
    'Houston Dynamo':'Houston Dynamo FC',
    'Houston Dynamo FC':'Houston Dynamo FC',
    'Inter Miami':'Inter Miami CF',
    'LA Galaxy':'Los Angeles Galaxy',
    'Los Angeles Galaxy':'Los Angeles Galaxy',
    'LAFC':'Los Angeles FC',
    'Los Angeles FC':'Los Angeles FC',
    'Minnesota United':'Minnesota United FC',
    'NYCFC':'New York City FC',
    'New York City':'New York City FC',
    'New York Red Bulls':'New York Red Bulls',
    'Orlando City':'Orlando City SC',
    'Orlando City SC':'Orlando City SC',
    'Seattle Sounders':'Seattle Sounders FC',
    'Seattle Sounders FC':'Seattle Sounders FC',
    'St. Louis City':'St. Louis City SC',
    'St. Louis City SC':'St. Louis City SC',
    'St. Louis CITY SC':'St. Louis City SC',
    'Vancouver Whitecaps':'Vancouver Whitecaps FC',
    'Vancouver Whitecaps FC':'Vancouver Whitecaps FC',
    'San Jose Earthquakes':'San Jose Earthquakes',
    'Sporting Kansas City':'Sporting Kansas City',
    'Sporting KC':'Sporting Kansas City',
    'Columbus Crew':'Columbus Crew',
    'Columbus Crew SC':'Columbus Crew',
    'Chicago Fire':'Chicago Fire FC',
    'Chicago Fire FC':'Chicago Fire FC',
    'New England Revolution':'New England Revolution',
    'Philadelphia Union':'Philadelphia Union',
    'Portland Timbers':'Portland Timbers',
    'Real Salt Lake':'Real Salt Lake',
    'Colorado Rapids':'Colorado Rapids',
    'FC Dallas':'FC Dallas',
    'Toronto FC':'Toronto FC',
    'Charlotte FC':'Charlotte FC',
    'Nashville SC':'Nashville SC',
    'Austin FC':'Austin FC',
    'San Diego FC':'San Diego FC',
}

def now():return datetime.now(timezone.utc).isoformat()

def cteam(x):
    raw=str(x or '').strip()
    return canon_team(ALIASES.get(raw,raw))

def fetch():
    req=urllib.request.Request(SOURCE_URL,headers={'User-Agent':UA,'Accept':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
    data=urllib.request.urlopen(req,timeout=90).read()
    if len(data)<100000:raise RuntimeError(f'External MLS workbook unexpectedly small: {len(data)} bytes')
    RAW_XLSX.write_bytes(data)
    return data

def novig(h,d,a):
    vals=np.array([h,d,a],dtype=float)
    inv=1.0/vals;s=float(inv.sum())
    return inv[0]/s,inv[1]/s,inv[2]/s,s-1.0

def parse_external(data):
    x=pd.read_excel(io.BytesIO(data))
    x.columns=[str(c).strip() for c in x.columns]
    required=['Season','Date','Home','Away','HG','AG','Res','PSCH','PSCD','PSCA']
    miss=[c for c in required if c not in x.columns]
    if miss:raise RuntimeError(f'External workbook missing required columns: {miss}; got={list(x.columns)}')
    x=x[required].copy()
    x['Season']=pd.to_numeric(x.Season,errors='coerce').astype('Int64')
    x['Date']=pd.to_datetime(x.Date,errors='coerce',dayfirst=True)
    for c in ['HG','AG','PSCH','PSCD','PSCA']:x[c]=pd.to_numeric(x[c],errors='coerce')
    x['HomeCanon']=x.Home.map(cteam);x['AwayCanon']=x.Away.map(cteam)
    x=x[x.Season.notna()&x.Date.notna()&x.HomeCanon.notna()&x.AwayCanon.notna()].copy()
    x=x[x.PSCH.gt(1)&x.PSCD.gt(1)&x.PSCA.gt(1)].copy()
    probs=x.apply(lambda r:novig(r.PSCH,r.PSCD,r.PSCA),axis=1,result_type='expand')
    probs.columns=['pinnacle_close_home_novig','pinnacle_close_draw_novig','pinnacle_close_away_novig','pinnacle_close_overround']
    x=pd.concat([x.reset_index(drop=True),probs.reset_index(drop=True)],axis=1)
    x['source_row_id']=np.arange(len(x),dtype=int)
    return x

def main():
    if not BASE.exists():raise RuntimeError(f'Missing base warehouse: {BASE}')
    data=fetch()
    ext=parse_external(data)
    if len(ext)<5000:raise RuntimeError(f'External cleaned dataset too small: {len(ext)}')
    ext.to_parquet(NORMALIZED,index=False)

    base=pd.read_parquet(BASE).copy()
    base['match_day']=pd.to_datetime(base.date,errors='coerce').dt.normalize()
    base['home_canon']=base.home_team.map(cteam);base['away_canon']=base.away_team.map(cteam)
    base['home_score_n']=pd.to_numeric(base.home_score,errors='coerce')
    base['away_score_n']=pd.to_numeric(base.away_score,errors='coerce')

    by_teams={}
    for k,z in base.groupby(['home_canon','away_canon']):
        by_teams[k]=z.copy()

    rows=[];unmatched=[];ambiguous=[]
    for _,r in ext.iterrows():
        cand=by_teams.get((r.HomeCanon,r.AwayCanon),pd.DataFrame()).copy()
        if cand.empty:
            unmatched.append({'source_row_id':int(r.source_row_id),'season':int(r.Season),'date':str(r.Date.date()),'home':r.Home,'away':r.Away,'reason':'TEAM_PAIR'})
            continue
        # First prefer same season and exact date, then +/-1 day with score confirmation.
        cand=cand[pd.to_numeric(cand.season,errors='coerce').eq(int(r.Season))]
        if cand.empty:
            unmatched.append({'source_row_id':int(r.source_row_id),'season':int(r.Season),'date':str(r.Date.date()),'home':r.Home,'away':r.Away,'reason':'SEASON'})
            continue
        target=pd.Timestamp(r.Date).normalize()
        cand['day_diff']=(cand.match_day-target).abs().dt.days
        exact=cand[cand.day_diff.eq(0)].copy()
        if len(exact)==1:
            pick=exact.iloc[0]
        else:
            score=cand[cand.day_diff.le(1)].copy()
            if pd.notna(r.HG) and pd.notna(r.AG):
                score=score[score.home_score_n.eq(float(r.HG))&score.away_score_n.eq(float(r.AG))]
            if len(score)==1:
                pick=score.iloc[0]
            else:
                pool=exact if len(exact) else score
                if len(pool)!=1:
                    ambiguous.append({'source_row_id':int(r.source_row_id),'season':int(r.Season),'date':str(r.Date.date()),'home':r.Home,'away':r.Away,'candidates':int(len(pool))})
                    continue
                pick=pool.iloc[0]

        rows.append({
          'match_id':str(pick.match_id),
          'external_pinnacle_source_row_id':int(r.source_row_id),
          'external_pinnacle_source_season':int(r.Season),
          'external_pinnacle_source_date':r.Date.date().isoformat(),
          'external_pinnacle_home_name':str(r.Home),'external_pinnacle_away_name':str(r.Away),
          'external_pinnacle_close_home_odds':float(r.PSCH),
          'external_pinnacle_close_draw_odds':float(r.PSCD),
          'external_pinnacle_close_away_odds':float(r.PSCA),
          'external_pinnacle_close_home_novig':float(r.pinnacle_close_home_novig),
          'external_pinnacle_close_draw_novig':float(r.pinnacle_close_draw_novig),
          'external_pinnacle_close_away_novig':float(r.pinnacle_close_away_novig),
          'external_pinnacle_close_overround':float(r.pinnacle_close_overround),
          'external_pinnacle_source_result':str(r.Res),
          'external_pinnacle_source_home_goals':None if pd.isna(r.HG) else float(r.HG),
          'external_pinnacle_source_away_goals':None if pd.isna(r.AG) else float(r.AG),
          'external_pinnacle_matched':1,
          'external_pinnacle_source_url':SOURCE_URL,
        })

    feat=pd.DataFrame(rows)
    if feat.empty:raise RuntimeError('No external Pinnacle rows matched')
    # Duplicates must resolve deterministically to one source row per MLS match.
    if feat.match_id.duplicated().any():
        dup=feat[feat.match_id.duplicated(keep=False)].sort_values('match_id')
        raise RuntimeError('Duplicate external Pinnacle matches after matching: '+json.dumps(dup[['match_id','external_pinnacle_source_row_id']].head(20).to_dict('records')))
    feat.to_parquet(FEATURES,index=False)

    seasons={str(int(k)):int(v) for k,v in ext.groupby('Season').size().to_dict().items()}
    matched_seasons={str(int(k)):int(v) for k,v in feat.groupby('external_pinnacle_source_season').size().to_dict().items()}
    meta={
      'built_at':now(),'source_repository':SOURCE_REPO,'source_url':SOURCE_URL,
      'raw_xlsx_bytes':RAW_XLSX.stat().st_size,'source_rows_after_cleaning':len(ext),
      'source_season_rows':seasons,'matched_rows':len(feat),'matched_season_rows':matched_seasons,
      'unmatched_rows':len(unmatched),'ambiguous_rows':len(ambiguous),
      'match_rate':len(feat)/len(ext),
      'features_output':str(FEATURES),'normalized_source_output':str(NORMALIZED),
      'unmatched_sample':unmatched[:50],'ambiguous_sample':ambiguous[:50],
      'import_policy':'Separate external layer only. Existing Appwiza odds columns are not overwritten.',
      'fields':['external_pinnacle_close_home_odds','external_pinnacle_close_draw_odds','external_pinnacle_close_away_odds',
                'external_pinnacle_close_home_novig','external_pinnacle_close_draw_novig','external_pinnacle_close_away_novig',
                'external_pinnacle_close_overround']
    }
    REPORT.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':main()
