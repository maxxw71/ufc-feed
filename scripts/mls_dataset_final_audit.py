#!/usr/bin/env python3
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
import numpy as np,pandas as pd

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed';REP=ROOT/'reports'
MASTER=PROC/'mls_match_features_master.parquet'
META=REP/'dataset_final_audit.json'
MOVEMENT_META=REP/'historical_multibook_movement_meta.json'

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
    checks['constant_or_empty_columns']=dead[:300]

    if 'market_overround' in d:
        x=pd.to_numeric(d.market_overround,errors='coerce')
        checks['negative_overround_rows']=int(x.lt(0).sum())
    probs=[c for c in ['home_novig_prob','draw_novig_prob','away_novig_prob'] if c in d]
    checks['invalid_probability_cells']=int(sum((pd.to_numeric(d[c],errors='coerce').lt(0)|pd.to_numeric(d[c],errors='coerce').gt(1)).sum() for c in probs))
    odds=[c for c in ['home_odds','draw_odds','away_odds'] if c in d]
    checks['invalid_decimal_odds_cells']=int(sum(pd.to_numeric(d[c],errors='coerce').le(1).sum() for c in odds))

    hist_cols=[c for c in d.columns if c.startswith('hist_mb_')]
    hist_abs_probs=[c for c in hist_cols if (('_prob_mean' in c and not c.startswith('hist_mb_move_')) or c.endswith('_novig_prob') or c.endswith('_up_share'))]
    hist_move_delta=[c for c in hist_cols if c.startswith('hist_mb_move_') and ('_prob_mean' in c or '_prob_median' in c)]
    hist_odds=[c for c in hist_cols if '_odds_' in c or c.endswith('_odds')]
    checks['historical_multibook_feature_columns']=len(hist_cols)
    checks['invalid_hist_mb_absolute_probability_cells']=int(sum((pd.to_numeric(d[c],errors='coerce').lt(0)|pd.to_numeric(d[c],errors='coerce').gt(1)).sum() for c in hist_abs_probs))
    checks['invalid_hist_mb_movement_delta_cells']=int(sum(pd.to_numeric(d[c],errors='coerce').abs().gt(1).sum() for c in hist_move_delta))
    checks['invalid_hist_mb_decimal_odds_cells']=int(sum(pd.to_numeric(d[c],errors='coerce').le(1).sum() for c in hist_odds))
    if 'hist_mb_movement_available' in d:
        checks['historical_multibook_movement_matches']=int(pd.to_numeric(d.hist_mb_movement_available,errors='coerce').eq(1).sum())
    else:
        checks['historical_multibook_movement_matches']=0

    movement_meta={}
    if MOVEMENT_META.exists():
        try:movement_meta=json.loads(MOVEMENT_META.read_text())
        except Exception as e:movement_meta={'parse_error':str(e)}
    checks['historical_multibook_movement_meta_status']=movement_meta.get('status')
    checks['historical_multibook_movement_meta_mapped_matches']=movement_meta.get('mapped_matches',0)
    checks['historical_multibook_timing_policy_present']=bool(movement_meta.get('timing_policy'))

    modern=d[pd.to_numeric(d.season,errors='coerce').ge(2013)].copy()
    priced=modern[[c for c in ['home_odds','draw_odds','away_odds'] if c in modern]].notna().all(axis=1) if odds else pd.Series(False,index=modern.index)
    checks['modern_2013plus_rows']=len(modern)
    checks['modern_2013plus_priced_rows']=int(priced.sum())
    checks['modern_2013plus_priced_share']=float(priced.mean()) if len(priced) else 0

    suspicious=[]
    safe_exact={'home_score','away_score','result','total_goals','home_win','draw','away_win'}
    for c in d.columns:
        n=c.lower()
        if c in safe_exact:continue
        if any(x in n for x in ['target_starter','target_lineup','current_match_card','target_match_card','postgame_feature']):
            suspicious.append(c)
    checks['suspicious_leakage_columns']=suspicious

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
      'historical_multibook_movement':['hist_mb_'],
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
      checks['invalid_hist_mb_absolute_probability_cells']==0,
      checks['invalid_hist_mb_movement_delta_cells']==0,
      checks['invalid_hist_mb_decimal_odds_cells']==0,
      len(checks['suspicious_leakage_columns'])==0,
      len(d)==9440,
      checks['historical_multibook_feature_columns']>=100,
      checks['historical_multibook_movement_matches']>=400,
      str(checks['historical_multibook_movement_meta_status']).startswith('PUBLIC_CONTINUOUS_MOVEMENT'),
      bool(checks['historical_multibook_timing_policy_present']),
    ]
    checks['critical_pass']=all(critical)
    payload={'built_at':now(),'master_file':str(MASTER),'checks':checks,
             'status':'PASS' if all(critical) else 'FAIL',
             'note':'Structural, odds/probability, timing-metadata and obvious leakage audit. Historical continuous multi-book movement coverage is explicitly required for this final MLS build; feature predictiveness is not asserted.'}
    META.write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps(payload,indent=2,default=str))

if __name__=='__main__':main()
