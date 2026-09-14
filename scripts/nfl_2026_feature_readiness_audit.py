from pathlib import Path
import json, os
import pandas as pd
import numpy as np

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'live_2026_readiness'; OUT.mkdir(parents=True,exist_ok=True)
BASE=CTX/'injury_travel_mining'/'complete_pregame_team_sides.parquet'
PCTX=CTX/'derived'/'team_game_pregame_context_2006_2026.parquet'
TRAVEL=CTX/'injury_travel_mining'/'travel_team_game_2006_2026.parquet'
SCHED=ROOT/'data'/'raw'/'schedules_2006_2026.parquet'

def num(x):return pd.to_numeric(x,errors='coerce')

d=pd.read_parquet(BASE)
d['season']=num(d.season).astype('Int64')
x=d[d.season.eq(2026)].copy()
summary={'base_2026_rows':int(len(x)),'base_2026_games':int(x.game_id.nunique()) if 'game_id' in x else 0}
if 'completed' in x:
    summary['completed_rows']=int(num(x.completed).eq(1).sum())
    summary['uncompleted_rows']=int(num(x.completed).ne(1).sum())
if 'week' in x:
    summary['weeks_present']=sorted(int(v) for v in num(x.week).dropna().unique())
if 'prior_games' in x:
    summary['rows_prior_games_ge3']=int(num(x.prior_games).ge(3).sum())
    summary['uncompleted_rows_prior_games_ge3']=int((num(x.get('completed',0)).ne(1)&num(x.prior_games).ge(3)).sum())
if 'moneyline' in x:
    summary['rows_with_moneyline']=int(num(x.moneyline).notna().sum())
    summary['uncompleted_rows_with_moneyline']=int((num(x.get('completed',0)).ne(1)&num(x.moneyline).notna()).sum())

# Current schedule view.
s=pd.read_parquet(SCHED); s=s[(num(s.season)==2026)&s.game_type.astype(str).eq('REG')].copy()
summary['schedule_2026_games']=int(len(s))
summary['schedule_completed_games']=int((num(s.home_score).notna()&num(s.away_score).notna()).sum()) if {'home_score','away_score'}.issubset(s.columns) else None
summary['schedule_unplayed_games']=int((num(s.home_score).isna()|num(s.away_score).isna()).sum()) if {'home_score','away_score'}.issubset(s.columns) else None

# Coverage in supporting context.
if PCTX.exists():
    p=pd.read_parquet(PCTX); p=p[num(p.season).eq(2026)].copy()
    summary['pregame_context_2026_rows']=int(len(p))
    for c in ['qb_prior_starts','head_coach_changed','offensive_coordinator','defensive_coordinator','offensive_coordinator_changed','defensive_coordinator_changed','returning_offense_snap_share','returning_defense_snap_share']:
        if c in p:summary['pctx_'+c+'_nonnull']=int(p[c].notna().sum())
if TRAVEL.exists():
    t=pd.read_parquet(TRAVEL); t=t[num(t.season).eq(2026)].copy()
    summary['travel_2026_rows']=int(len(t))
    for c in ['travel_miles','abs_tz_shift_hours','consecutive_road_games']:
        if c in t:summary['travel_'+c+'_nonnull']=int(t[c].notna().sum())

# Feature availability on any unplayed/mature base rows.
if len(x):
    m=x.copy()
    if 'completed' in m:m=m[num(m.completed).ne(1)]
    if 'prior_games' in m:m=m[num(m.prior_games).ge(3)]
    families={
      'rank_edges':[c for c in m.columns if c.startswith('rank_edge_')],
      'recent_edges':[c for c in m.columns if c.startswith('recent_edge_')],
      'injury':[c for c in m.columns if 'unavail' in c or 'injur' in c],
      'travel':[c for c in m.columns if any(k in c for k in ['travel','tz_','road_games','altitude'])],
      'coaching':[c for c in m.columns if any(k in c for k in ['coach','coordinator','staff_'])],
    }
    summary['mature_unplayed_rows']=int(len(m))
    summary['mature_unplayed_feature_nonnull_counts']={fam:int(sum(m[c].notna().sum() for c in cols)) for fam,cols in families.items()}
    cols=[c for c in ['game_id','season','week','team','opponent','is_home','moneyline','market_prob','prior_games','completed'] if c in m.columns]
    m[cols].to_csv(OUT/'mature_unplayed_2026_rows.csv',index=False)

# Decide architecture.
if summary.get('uncompleted_rows_prior_games_ge3',0)>0:
    architecture='ENRICH_EXISTING_2026_PREGAME_ROWS_WITH_LIVE_ODDS'
else:
    architecture='BUILD_LIVE_UPCOMING_FEATURE_ROWS_FROM_SCHEDULE_AND_PRIOR_COMPLETED_GAMES'
summary['recommended_live_architecture']=architecture
(OUT/'summary.json').write_text(json.dumps(summary,indent=2,default=str))
lines=['NFL 2026 LIVE FEATURE READINESS',json.dumps(summary,indent=2,default=str)]
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
