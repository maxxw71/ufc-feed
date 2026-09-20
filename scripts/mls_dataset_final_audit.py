#!/usr/bin/env python3
from __future__ import annotations
import json,math
from datetime import datetime,timezone
from pathlib import Path
import numpy as np,pandas as pd

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed';REP=ROOT/'reports'
MASTER=PROC/'mls_match_features_master.parquet'
META=REP/'dataset_final_audit.json'

def now():return datetime.now(timezone.utc).isoformat()

def main():
    if not MASTER.exists():raise RuntimeError('Master MLS warehouse missing')
    d=pd.read_parquet(MASTER)
    checks={}
    checks['rows']=len(d);checks['columns']=len(d.columns)
    checks['duplicate_match_ids']=int(d.match_id.astype(str).duplicated().sum())
    checks['missing_match_ids']=int(d.match_id.isna().sum())
    checks['season_min']=int(pd.to_numeric(d.season,errors='coerce').min())
    checks['season_max']=int(pd.to_numeric(d.season,errors='coerce').max())

    numeric=d.select_dtypes(include=[np.number])
    checks['infinite_numeric_cells']=int(np.isinf(numeric.to_numpy(dtype=float,copy=True)).sum()) if len(numeric.columns) else 0
    dead=[]
    for c in d.columns:
        n=d[c].nunique(dropna=True)
        if n<=1:dead.append({'column':c,'unique_non_null':int(n),'non_null':int(d[c].notna().sum())})
    checks['constant_or_empty_columns_count']=len(dead)
    checks['constant_or_empty_columns']=dead[:250]

    if 'market_overround' in d:
        x=pd.to_numeric(d.market_overround,errors='coerce')
        checks['negative_overround_rows']=int(x.lt(0).sum())
    probs=[c for c in ['home_novig_prob','draw_novig_prob','away_novig_prob'] if c in d]
    checks['invalid_probability_cells']=int(sum((pd.to_numeric(d[c],errors='coerce').lt(0)|pd.to_numeric(d[c],errors='coerce').gt(1)).sum() for c in probs))
    odds=[c for c in ['home_odds','draw_odds','away_odds'] if c in d]
    checks['invalid_decimal_odds_cells']=int(sum(pd.to_numeric(d[c],errors='coerce').le(1).sum() for c in odds))

    # Price availability over modern analysis eras.
    modern=d[pd.to_numeric(d.season,errors='coerce').ge(2013)].copy()
    priced=modern[[c for c in ['home_odds','draw_odds','away_odds'] if c in modern]].notna().all(axis=1) if odds else pd.Series(False,index=modern.index)
    checks['modern_2013plus_rows']=len(modern)
    checks['modern_2013plus_priced_rows']=int(priced.sum())
    checks['modern_2013plus_priced_share']=float(priced.mean()) if len(priced) else 0

    # Pregame-safety keyword audit: target-match postgame info is allowed as labels/base results,
    # but newly-added feature families must not expose obvious target lineup/card result columns.
    suspicious=[]
    safe_exact={'home_score','away_score','result','total_goals','home_win','draw','away_win'}
    for c in d.columns:
        n=c.lower()
        if c in safe_exact:continue
        if any(x in n for x in ['target_starter','target_lineup','current_match_card','target_match_card','postgame_feature']):
            suspicious.append(c)
    checks['suspicious_leakage_columns']=suspicious

    # Family coverage among 2013+ matches using canonical prefixes.
    families={
      'xg':['xgfpg','xgapg','xgdpg'],
      'player_manager':['player_','manager_'],
      'availability':['availability_'],
      'confirmed_lineup':['confirmed_'],
      'salary':['salary_'],
      'cross_comp':['allcomp_','external_','days_since_external'],
      'transactions':['transaction_','transactions_'],
      'style':['style_'],
      'referee':['referee_'],
      'venue':['stadium_','venue_','travel_from_prev_match'],
      'weather':['weather_'],
    }
    fam={}
    for name,tokens in families.items():
        cols=[c for c in d.columns if any(t in c.lower() for t in tokens)]
        if not cols:
            fam[name]={'columns':0,'rows_any':0,'share_any':0};continue
        nn=modern[cols].notna().any(axis=1)
        fam[name]={'columns':len(cols),'rows_any':int(nn.sum()),'share_any':float(nn.mean())}
    checks['modern_family_coverage']=fam

    critical=[
      checks['duplicate_match_ids']==0,
      checks['missing_match_ids']==0,
      checks['infinite_numeric_cells']==0,
      checks['invalid_probability_cells']==0,
      checks['invalid_decimal_odds_cells']==0,
      len(checks['suspicious_leakage_columns'])==0,
      len(d)==9440,
    ]
    checks['critical_pass']=all(critical)
    payload={'built_at':now(),'master_file':str(MASTER),'checks':checks,
             'status':'PASS' if all(critical) else 'FAIL',
             'note':'This audit checks structural integrity and obvious leakage hazards. It does not claim that every feature is predictive or available for every historical season.'}
    META.write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps(payload,indent=2,default=str))

if __name__=='__main__':main()
