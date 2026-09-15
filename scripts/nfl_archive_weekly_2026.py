from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
import shutil
import pandas as pd
import nflreadpy as nfl

REPO=Path(__file__).resolve().parents[1]
CTX=Path(os.environ.get('NFL_CONTEXT_ROOT','/home/appwiza-runner/nfl-context-data'))
SEASON=int(os.environ.get('NFL_SEASON','2026'))
MODE=os.environ.get('ARCHIVE_MODE','postweek').strip().lower()
ROOT=REPO/'nfl'/'weekly_archive'/str(SEASON)
ROOT.mkdir(parents=True,exist_ok=True)

def to_pd(x): return x.to_pandas() if hasattr(x,'to_pandas') else pd.DataFrame(x)
def num(x): return pd.to_numeric(x,errors='coerce')
def safe_parquet(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    df.to_parquet(path,index=False,compression='zstd')
def hash_file(p: Path):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()
def load_optional(label, candidates):
    errors=[]
    for fn_name,args,kwargs in candidates:
        fn=getattr(nfl,fn_name,None)
        if fn is None:
            errors.append(f'{fn_name}:missing'); continue
        try: return to_pd(fn(*args,**kwargs)),fn_name,None
        except Exception as e: errors.append(f'{fn_name}:{type(e).__name__}:{e}')
    return None,None,' | '.join(errors)
def filter_week(df: pd.DataFrame, week: int, game_ids:set[str]):
    if df is None or not len(df): return df
    x=df.copy()
    if 'season' in x.columns: x=x[num(x.season).eq(SEASON)]
    for wc in ['week','week_num']:
        if wc in x.columns: return x[num(x[wc]).eq(week)].copy()
    for gc in ['game_id','old_game_id']:
        if gc in x.columns and game_ids: return x[x[gc].astype(str).isin(game_ids)].copy()
    return x

now=datetime.now(timezone.utc)
sched=to_pd(nfl.load_schedules([SEASON]))
if 'season' in sched: sched=sched[num(sched.season).eq(SEASON)]
if 'game_type' in sched: sched=sched[sched.game_type.astype(str).eq('REG')]
sched=sched.copy(); sched['week_num']=num(sched['week']).astype('Int64')
score_complete=num(sched.get('home_score')).notna() & num(sched.get('away_score')).notna()
complete_weeks=[]
for w,g in sched.groupby('week_num',dropna=True):
    if len(g) and (num(g.get('home_score')).notna() & num(g.get('away_score')).notna()).all(): complete_weeks.append(int(w))
latest_complete=max(complete_weeks) if complete_weeks else 0
unplayed=sched[~score_complete]
next_week=int(num(unplayed.week_num).dropna().min()) if len(unplayed) and num(unplayed.week_num).notna().any() else None
if MODE=='postweek':
    if latest_complete<1: raise SystemExit('No fully completed regular-season week available to archive')
    week=latest_complete
elif MODE=='pregame':
    if next_week is None: raise SystemExit('No upcoming regular-season week available to archive')
    week=next_week
else: raise SystemExit(f'Unsupported ARCHIVE_MODE={MODE}')

week_sched=sched[num(sched.week_num).eq(week)].copy()
game_ids=set(week_sched.get('game_id',pd.Series(dtype=str)).dropna().astype(str))
out=ROOT/f'week_{week:02d}'/MODE
out.mkdir(parents=True,exist_ok=True)
manifest={'season':SEASON,'week':week,'mode':MODE,'generated_utc':now.isoformat(),'latest_fully_completed_week_at_run':latest_complete,'next_unplayed_week_at_run':next_week,'sources':{},'files':{},'notes':['pregame and postweek snapshots are stored separately to prevent outcome leakage','weeks 1-3 current-season performance is research context; mature live rolling methods require >=3 prior completed games','coach quality is rebuilt prospectively from staff identity plus seasons strictly before 2026; no 2026 outcomes enter coach quality scores','Git history preserves earlier revisions if an upstream stat correction changes a finalized file later']}

safe_parquet(week_sched,out/'schedule.parquet'); week_sched.to_csv(out/'schedule.csv',index=False)
manifest['sources']['schedule']='nflreadpy.load_schedules'

# Dynamic weekly sources. Each endpoint fails soft and records its status so one vendor lag cannot kill the archive.
datasets={
 'team_stats': [('load_team_stats',([SEASON],),{'summary_level':'week'})],
 'player_stats': [('load_player_stats',([SEASON],),{'summary_level':'week'})],
 'pbp': [('load_pbp',([SEASON],),{})],
 'injuries': [('load_injuries',([SEASON],),{})],
 'snap_counts': [('load_snap_counts',([SEASON],),{})],
 'rosters_weekly': [('load_rosters_weekly',([SEASON],),{}),('load_rosters',([SEASON],),{})],
 'depth_charts': [('load_depth_charts',([SEASON],),{})],
 'participation': [('load_participation',([SEASON],),{})],
 'ftn_charting': [('load_ftn_charting',([SEASON],),{})],
 'officials': [('load_officials',([SEASON],),{})],
 'nextgen_passing': [('load_nextgen_stats',([SEASON],),{'stat_type':'passing'})],
 'nextgen_receiving': [('load_nextgen_stats',([SEASON],),{'stat_type':'receiving'})],
 'nextgen_rushing': [('load_nextgen_stats',([SEASON],),{'stat_type':'rushing'})],
 'pfr_adv_pass': [('load_pfr_advstats',([SEASON],),{'stat_type':'pass','summary_level':'week'})],
 'pfr_adv_rush': [('load_pfr_advstats',([SEASON],),{'stat_type':'rush','summary_level':'week'})],
 'pfr_adv_rec': [('load_pfr_advstats',([SEASON],),{'stat_type':'rec','summary_level':'week'})],
 'pfr_adv_def': [('load_pfr_advstats',([SEASON],),{'stat_type':'def','summary_level':'week'})],
 'ff_opportunity_weekly': [('load_ff_opportunity',([SEASON],),{'stat_type':'weekly'})],
}
for label,cands in datasets.items():
    df,used,err=load_optional(label,cands)
    if df is None:
        manifest['sources'][label]={'status':'unavailable','error':err}; continue
    fx=filter_week(df,week,game_ids)
    safe_parquet(fx,out/f'{label}.parquet')
    manifest['sources'][label]={'status':'saved','loader':used,'rows':int(len(fx)),'columns':int(len(fx.columns))}

# Exact market history accumulated prospectively by the existing hourly snapshotter.
market_dir=CTX/'market_snapshots'; market_rows=[]; market_files=0; market_bad=0
if market_dir.exists():
    for p in sorted(market_dir.glob('*.jsonl')):
        market_files+=1
        try:
            with p.open('r',encoding='utf-8') as f:
                for line in f:
                    try:
                        r=json.loads(line)
                        if int(r.get('season',-1))==SEASON and int(r.get('week',-1))==week: market_rows.append(r)
                    except Exception: market_bad+=1
        except Exception: market_bad+=1
if market_rows:
    with gzip.open(out/'market_snapshots.jsonl.gz','wt',encoding='utf-8') as f:
        for r in market_rows: f.write(json.dumps(r,sort_keys=True,default=str)+'\n')
manifest['sources']['market_snapshots']={'status':'saved' if market_rows else 'none_found','rows':len(market_rows),'files_scanned':market_files,'parse_errors':market_bad}

# Our proprietary/research-derived pregame context is frozen separately from public raw data.
local_sources={
 'pregame_context': CTX/'derived'/'team_game_pregame_context_2006_2026.parquet',
 'complete_pregame_team_sides': CTX/'injury_travel_mining'/'complete_pregame_team_sides.parquet',
 'travel_context': CTX/'injury_travel_mining'/'travel_team_game_2006_2026.parquet',
 'coach_quality_live_2026': CTX/'coach_quality_live'/'coach_quality_2026_team_sides.parquet',
}
for label,p in local_sources.items():
    if not p.exists(): manifest['sources'][label]={'status':'missing','path':str(p)}; continue
    try:
        df=pd.read_parquet(p); fx=filter_week(df,week,game_ids); safe_parquet(fx,out/f'{label}.parquet')
        manifest['sources'][label]={'status':'saved','rows':int(len(fx)),'columns':int(len(fx.columns)),'source_path':str(p)}
    except Exception as e: manifest['sources'][label]={'status':'error','error':f'{type(e).__name__}:{e}','path':str(p)}

copy_candidates={
 'coordinator_history.csv': REPO/'data'/'nfl'/'coordinator_history_espn_2019_2026.csv',
 'coach_quality_team_seasons_2026.csv': REPO/'nfl'/'live_coach_quality_2026'/'coach_quality_team_seasons_2026.csv',
 'current_board_snapshot.csv': REPO/'nfl'/'live_2026_current_snapshot'/'current_board_fresh_context.csv',
 'legacy_board_snapshot.csv': REPO/'nfl'/'live_legacy_full_dissection'/'current_board_deep_context.csv',
 'approved_h_methods.json': REPO/'nfl'/'live_stage'/'approved_h_methods.json',
 'coach_only_final_sieve.csv': REPO/'nfl'/'coach_only_final_sieve'/'final_coach_only_arsenal_sieve.csv',
}
for name,src in copy_candidates.items():
    if src.exists(): shutil.copy2(src,out/name)

for p in sorted(out.iterdir()):
    if p.is_file() and p.name!='manifest.json': manifest['files'][p.name]={'bytes':p.stat().st_size,'sha256':hash_file(p)}
manifest['available_nflreadpy_loaders']=[x for x in dir(nfl) if x.startswith('load_')]
(out/'manifest.json').write_text(json.dumps(manifest,indent=2,default=str))
print(json.dumps({'season':SEASON,'week':week,'mode':MODE,'out':str(out),'files':len(manifest['files']),'market_rows':len(market_rows),'latest_complete':latest_complete,'next_week':next_week},indent=2))
