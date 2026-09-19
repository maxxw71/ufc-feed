#!/usr/bin/env python3
from __future__ import annotations
import json, time
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
RAW=ROOT/'data/raw/asa';PROC=ROOT/'data/processed';REPORTS=ROOT/'reports'
for p in [RAW,PROC,REPORTS]:p.mkdir(parents=True,exist_ok=True)

def now():return datetime.now(timezone.utc).isoformat()
def safe_df(fn,*args,**kwargs):
    try:
        z=fn(*args,**kwargs)
        return z if isinstance(z,pd.DataFrame) else pd.DataFrame(z)
    except Exception as e:
        return pd.DataFrame(),{'error':type(e).__name__,'message':str(e)[:500]}
def main():
    asa=AmericanSoccerAnalysis()
    season_reports=[];game_frames=[];xg_frames=[]
    for year in range(2013,2027):
        gerr=xerr=None
        try:
            g=asa.get_games(leagues='mls',season_name=str(year))
            if not isinstance(g,pd.DataFrame):g=pd.DataFrame(g)
        except Exception as e:
            g=pd.DataFrame();gerr={'error':type(e).__name__,'message':str(e)[:500]}
        try:
            x=asa.get_game_xgoals(leagues='mls',season_name=str(year))
            if not isinstance(x,pd.DataFrame):x=pd.DataFrame(x)
        except Exception as e:
            x=pd.DataFrame();xerr={'error':type(e).__name__,'message':str(e)[:500]}
        if len(g):
            g['_season_requested']=year;game_frames.append(g)
            g.to_parquet(RAW/f'games_{year}.parquet',index=False)
        if len(x):
            x['_season_requested']=year;xg_frames.append(x)
            x.to_parquet(RAW/f'game_xgoals_{year}.parquet',index=False)
        season_reports.append({'season':year,'games_rows':len(g),'xg_rows':len(x),'games_columns':list(g.columns),'xg_columns':list(x.columns),'games_error':gerr,'xg_error':xerr})
        print(year,'games',len(g),'xg',len(x),flush=True)
        time.sleep(.15)
    games=pd.concat(game_frames,ignore_index=True,sort=False) if game_frames else pd.DataFrame()
    xg=pd.concat(xg_frames,ignore_index=True,sort=False) if xg_frames else pd.DataFrame()
    if len(games):games.to_parquet(PROC/'asa_mls_games_2013_present.parquet',index=False)
    if len(xg):xg.to_parquet(PROC/'asa_mls_game_xgoals_2013_present.parquet',index=False)
    meta={'built_at':now(),'games_rows':len(games),'xg_rows':len(xg),'seasons':season_reports}
    (REPORTS/'asa_coverage.json').write_text(json.dumps(meta,indent=2,default=str))
    lines=['MLS ASA ADVANCED LAYER','='*90,f'games_rows={len(games):,}',f'xg_rows={len(xg):,}','']
    for r in season_reports:
        lines.append(f"{r['season']}: games={r['games_rows']} xg={r['xg_rows']} games_error={r['games_error']} xg_error={r['xg_error']}")
    (REPORTS/'asa_coverage.txt').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
if __name__=='__main__':main()
