#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed';REP=ROOT/'reports'
BASE=PROC/'mls_match_features_weather_enriched.parquet'
OUT=PROC/'mls_match_features_master.parquet'
META=REP/'master_feature_meta.json'

LAYERS=[
  ('availability',PROC/'mls_availability_pregame_features.parquet',True),
  ('game_notes_availability',PROC/'mls_game_notes_availability_features.parquet',False),
  ('confirmed_lineup',PROC/'mls_confirmed_lineup_card_pregame_features.parquet',True),
  ('salary',PROC/'mls_salary_features.parquet',False),
  ('cross_comp',PROC/'mls_cross_comp_features.parquet',False),
  ('transactions',PROC/'mls_transaction_features.parquet',False),
  ('team_style',PROC/'mls_team_style_features.parquet',False),
  ('referee_discipline',PROC/'mls_referee_discipline_features.parquet',False),
  ('venue',PROC/'mls_venue_features.parquet',False),
  ('external_pinnacle',PROC/'mls_external_pinnacle_closing_features.parquet',False),
  ('historical_multibook_odds',PROC/'mls_historical_multibook_market_features.parquet',False),
]

def now():return datetime.now(timezone.utc).isoformat()

def normalize_key(d):
    if 'match_id' not in d.columns:raise RuntimeError('Feature layer missing match_id')
    d=d.copy();d['match_id']=d.match_id.astype(str)
    if d.match_id.duplicated().any():
        dup=d.loc[d.match_id.duplicated(),'match_id'].head(10).tolist()
        raise RuntimeError(f'Duplicate match_id rows: {dup}')
    return d

def family_coverage(d,cols):
    cols=[c for c in cols if c in d.columns]
    if not cols:return {'columns':0,'rows_any':0,'rows_all':0,'mean_non_null':0.0,'by_season':{}}
    nn=d[cols].notna()
    by={}
    for season,g in d.assign(_any=nn.any(axis=1),_all=nn.all(axis=1)).groupby('season'):
        by[str(int(season))]={'rows':len(g),'any':int(g._any.sum()),'all':int(g._all.sum())}
    return {'columns':len(cols),'rows_any':int(nn.any(axis=1).sum()),'rows_all':int(nn.all(axis=1).sum()),
            'mean_non_null':float(nn.mean().mean()),'by_season':by}

def main():
    if not BASE.exists():raise RuntimeError(f'Missing {BASE}')
    d=pd.read_parquet(BASE).copy();d['match_id']=d.match_id.astype(str)
    if d.match_id.duplicated().any():raise RuntimeError('Base master key duplicate')
    base_cols=set(d.columns);layer_meta={};family_cols={}
    for name,path,refresh in LAYERS:
        if not path.exists():
            layer_meta[name]={'status':'MISSING','path':str(path)};continue
        z=normalize_key(pd.read_parquet(path))
        ignore={'asa_game_id','season','asa_matchday'}
        payload=[c for c in z.columns if c!='match_id' and c not in ignore]
        if len(z)==0 or not payload:
            layer_meta[name]={'status':'NO_COVERAGE','path':str(path),'source_rows':len(z),'usable_columns':len(payload)}
            continue
        collisions=[c for c in payload if c in d.columns]
        if collisions and not refresh:
            layer_meta[name]={'status':'COLLISION_REJECTED','path':str(path),'collisions':collisions[:50]}
            raise RuntimeError(f'Unexpected {name} collisions: {collisions[:10]}')
        if refresh:
            for c in collisions:d=d.drop(columns=[c])
        use=['match_id']+payload
        before=len(d)
        d=d.merge(z[use],on='match_id',how='left',validate='1:1')
        if len(d)!=before:raise RuntimeError(f'{name} merge changed row count')
        family_cols[name]=payload
        layer_meta[name]={'status':'MERGED_REFRESH' if refresh else 'MERGED','path':str(path),
                          'source_rows':len(z),'added_or_refreshed_columns':len(payload),'collisions_refreshed':collisions}
    d.to_parquet(OUT,index=False)

    coverage={name:family_coverage(d,cols) for name,cols in family_cols.items()}
    # Core families already embedded in the base warehouse.
    embedded={
      'base_market_form':[c for c in d.columns if any(x in c for x in ['elo','last5','last10','market_prob','odds'])],
      'weather':[c for c in d.columns if c.startswith('weather_') or c.startswith('home_weather_') or c.startswith('away_weather_')],
      'player_manager':[c for c in d.columns if c.startswith('home_player_') or c.startswith('away_player_') or 'manager_' in c],
      'goalkeeper_roster_referee':[c for c in d.columns if any(x in c for x in ['gk_','roster_','referee_prior_'])],
    }
    for name,cols in embedded.items():coverage[name]=family_coverage(d,cols)

    meta={
      'built_at':now(),'base_file':str(BASE),'output':str(OUT),'rows':len(d),'columns':len(d.columns),
      'base_columns':len(base_cols),'layers':layer_meta,'coverage':coverage,
      'season_rows':{str(int(k)):int(v) for k,v in d.groupby('season').size().to_dict().items()},
      'missing_layers':[k for k,v in layer_meta.items() if not str(v.get('status','')).startswith('MERGED')],
      'leakage_policy':'Master merge is structural only. Each source layer is responsible for strict pregame construction; audit metadata is preserved separately. No missing feature is imputed as zero during merge.'
    }
    META.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps({'built_at':meta['built_at'],'rows':meta['rows'],'columns':meta['columns'],
                      'layers':meta['layers'],'missing_layers':meta['missing_layers'],
                      'coverage_summary':{k:{x:v[x] for x in ['columns','rows_any','rows_all','mean_non_null']} for k,v in coverage.items()}},indent=2,default=str))

if __name__=='__main__':main()
