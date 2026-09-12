#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
AGE="$ROOT/historical_backtest/master_2006_2026/hybrid_master_2006_2026.csv"
REACH="$ROOT/ufc_reach_method_analysis/reach_market_sample.csv"
PREMIUM="$ROOT/ufc_age_reach_overlap/age_plus_reach4.csv"
STATS="$ROOT/skill_veto_all_methods/all_original_signals_with_skill_veto.csv"
OUTDIR="$ROOT/skill_veto_official_samples"
mkdir -p "$OUTDIR" "$(dirname "$PREMIUM")"

for f in "$AGE" "$REACH" "$STATS"; do
  [ -f "$f" ] || { echo "ERROR: Missing $f"; exit 2; }
done

if [ ! -s "$PREMIUM" ]; then
  echo "Missing official Premium sample; downloading it automatically..."
  curl -6 -L --fail --connect-timeout 10 --max-time 180 --retry 2 --retry-delay 2 \
    "https://raw.githubusercontent.com/maxxw71/ufc-feed/main/ufc_age_reach_overlap/age_plus_reach4.csv?cb=$(date +%s%N)" \
    -o "$PREMIUM"
fi

source "$ROOT/venv/bin/activate"

python - "$AGE" "$REACH" "$PREMIUM" "$STATS" "$OUTDIR" <<'PY'
import sys,re,unicodedata
from pathlib import Path
import numpy as np
import pandas as pd

age_path,reach_path,premium_path,stats_path,outdir=map(Path,sys.argv[1:6])

def norm(v):
    v=unicodedata.normalize('NFKD',str(v))
    v=''.join(ch for ch in v if not unicodedata.combining(ch))
    v=re.sub(r'[^a-z0-9 ]+',' ',v.lower())
    return ' '.join(v.split())

def boolify(s):
    if pd.api.types.is_bool_dtype(s):
        return s.fillna(False)
    return s.astype(str).str.strip().str.lower().map({
        'true':True,'1':True,'yes':True,'win':True,'w':True,
        'false':False,'0':False,'no':False,'loss':False,'l':False
    }).fillna(False).astype(bool)

def first_col(df,names,required=True):
    for c in names:
        if c in df.columns:
            return c
    if required:
        raise RuntimeError('Missing expected column. Tried: '+', '.join(names)+'\nAvailable columns: '+', '.join(map(str,df.columns)))
    return None

def profit_series(df, won):
    c=first_col(df,['profit_100','profit100','profit','pl_100','pnl_100','bet_profit_100','flat_profit_100'],False)
    if c:
        return pd.to_numeric(df[c],errors='coerce').fillna(0.0)
    c=first_col(df,['american_odds','fav_ml','best_american_odds','favorite_odds','fav_odds','odds'],False)
    if c:
        a=pd.to_numeric(df[c],errors='coerce')
        winprofit=np.where(a<0,10000/(-a),a)
        return pd.Series(np.where(won,winprofit,-100.0),index=df.index,dtype=float)
    c=first_col(df,['decimal_odds','fav_dec','best_decimal','favorite_decimal','fav_decimal'],False)
    if c:
        dec=pd.to_numeric(df[c],errors='coerce')
        return pd.Series(np.where(won,100*(dec-1),-100.0),index=df.index,dtype=float)
    raise RuntimeError('Could not find profit or odds column. Available columns: '+', '.join(map(str,df.columns)))

def metric(g):
    n=len(g)
    if not n:
        return dict(n=0,w=0,l=0,roi=np.nan,p=0)
    w=int(g['_won'].sum())
    p=float(g['_profit100'].sum())
    return dict(n=n,w=w,l=n-w,roi=p/(100*n),p=p)

# Skill-veto lookup generated from pre-fight UFC history.
stats=pd.read_csv(stats_path,low_memory=False)
stats['event_date']=pd.to_datetime(stats['event_date'],errors='coerce').dt.normalize()
stats['favn']=stats['favorite'].map(norm)
stats['dogn']=stats['underdog'].map(norm)
for c in ['diff_sig_diff_pm','diff_td_def']:
    stats[c]=pd.to_numeric(stats[c],errors='coerce')
stats['skill_veto_flag']=((stats.diff_sig_diff_pm<=-1.0)|(stats.diff_td_def<=-0.20)).fillna(False).astype(bool)
skill=stats[['event_date','favn','dogn','diff_sig_diff_pm','diff_td_def','skill_veto_flag']].drop_duplicates(['event_date','favn','dogn'])

def merge_skill(df,date_col,fav_col,dog_col):
    z=df.copy()
    z['event_date']=pd.to_datetime(z[date_col],errors='coerce').dt.normalize()
    z['favn']=z[fav_col].map(norm)
    z['dogn']=z[dog_col].map(norm)
    return z.merge(skill,on=['event_date','favn','dogn'],how='left')

def finish_method(name,df):
    b=metric(df)
    veto=df['skill_veto_flag'].fillna(False).astype(bool).to_numpy()
    k=metric(df.loc[~veto])
    v=metric(df.loc[veto])
    coverage=int(df['skill_veto_flag'].notna().sum())
    print(name)
    print('-'*108)
    print(f"ORIGINAL: {b['n']} bets | {b['w']}-{b['l']} | ROI={b['roi']*100:+.2f}% | P/L=${b['p']:+,.2f}")
    print(f"WITH SKILL VETO: {k['n']} bets | {k['w']}-{k['l']} | ROI={k['roi']*100:+.2f}% | P/L=${k['p']:+,.2f}")
    print(f"ROI CHANGE: {(k['roi']-b['roi'])*100:+.2f}pp")
    print(f"REMOVED: {v['n']} signals = {v['w']} winners + {v['l']} losses | removed-group ROI={v['roi']*100:+.2f}%")
    print(f"SKILL-DATA COVERAGE: {coverage}/{b['n']} fights")
    print()
    return {
        'method':name,
        'baseline_bets':b['n'],'baseline_wins':b['w'],'baseline_losses':b['l'],
        'baseline_roi':b['roi'],'baseline_profit':b['p'],
        'filtered_bets':k['n'],'filtered_wins':k['w'],'filtered_losses':k['l'],
        'filtered_roi':k['roi'],'filtered_profit':k['p'],
        'roi_change_pp':(k['roi']-b['roi'])*100,
        'removed_bets':v['n'],'removed_wins':v['w'],'removed_losses':v['l'],
        'removed_roi':v['roi'],'skill_data_coverage':coverage
    }

print('SKILL VETO — OFFICIAL HISTORICAL SAMPLES')
print('='*108)
print('Veto if favorite is either >=1.0 sig strike/min worse OR >=20pp worse in takedown defense.\n')
summary=[]

# AGE HYBRID — exact official 889 master.
age=pd.read_csv(age_path,low_memory=False)
date=first_col(age,['event_date','date','fight_date'])
fav=first_col(age,['favorite','fav_name','favorite_name','fighter','fighter_name','pick','bet_fighter'])
dog=first_col(age,['underdog','dog_name','underdog_name','opponent','opponent_name','other_fighter'])
wc=first_col(age,['favorite_won','won','is_win','bet_won'],False)
if wc:
    age['_won']=boolify(age[wc])
else:
    rc=first_col(age,['result','outcome'])
    age['_won']=boolify(age[rc])
age['_profit100']=profit_series(age,age['_won'])
age=merge_skill(age,date,fav,dog)
if len(age)!=889:
    print(f'WARNING: Age master has {len(age)} rows; expected 889.')
summary.append(finish_method('AGE HYBRID — OFFICIAL MASTER',age))

# REACH HYBRID — exact official rule reconstructed from reach-market sample.
reach=pd.read_csv(reach_path,low_memory=False)
for c in ['market_prob','reach_gap','profit_100','american_odds']:
    if c in reach.columns:
        reach[c]=pd.to_numeric(reach[c],errors='coerce')
reach['market_fav_is_p1']=boolify(reach['market_fav_is_p1'])
reach['market_fav_longer']=boolify(reach['market_fav_longer'])
reach['market_fav_won']=boolify(reach['market_fav_won'])
reach['favorite']=np.where(reach.market_fav_is_p1,reach.player1,reach.player2)
reach['underdog']=np.where(reach.market_fav_is_p1,reach.player2,reach.player1)
reach['_won']=reach['market_fav_won']
reach['_profit100']=profit_series(reach,reach['_won'])
reach_off=reach[(reach.market_prob>=.65)&(reach.market_fav_longer)&(reach.reach_gap>=4)].copy()
reach_off=merge_skill(reach_off,'event_date','favorite','underdog')
if len(reach_off)!=415:
    print(f'WARNING: Reach official rule produced {len(reach_off)} rows; expected 415.')
summary.append(finish_method('REACH HYBRID — OFFICIAL 415',reach_off))

# AGE + REACH PREMIUM — use the exact official 133-fight file.
premium=pd.read_csv(premium_path,low_memory=False)
premium['fav_is_p1']=boolify(premium['fav_is_p1'])
premium['fav_won']=boolify(premium['fav_won'])
premium['favorite']=np.where(premium.fav_is_p1,premium.player1,premium.player2)
premium['underdog']=np.where(premium.fav_is_p1,premium.player2,premium.player1)
premium['_won']=premium['fav_won']
premium['_profit100']=profit_series(premium,premium['_won'])
premium=merge_skill(premium,'event_date','favorite','underdog')
if len(premium)!=133:
    print(f'WARNING: Premium official file has {len(premium)} rows; expected 133.')
summary.append(finish_method('AGE + REACH PREMIUM — OFFICIAL 133',premium))

pd.DataFrame(summary).to_csv(outdir/'official_skill_veto_summary.csv',index=False)
age.to_csv(outdir/'age_889_with_skill_veto.csv',index=False)
reach_off.to_csv(outdir/'reach_415_with_skill_veto.csv',index=False)
premium.to_csv(outdir/'premium_133_with_skill_veto.csv',index=False)
print('Saved:',outdir/'official_skill_veto_summary.csv')
PY
