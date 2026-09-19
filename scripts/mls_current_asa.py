#!/usr/bin/env python3
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'live/current';OUT.mkdir(parents=True,exist_ok=True)

def main():
    asa=AmericanSoccerAnalysis()
    g=asa.get_games(leagues='mls',season_name='2026')
    if not isinstance(g,pd.DataFrame):g=pd.DataFrame(g)
    if g.empty:raise RuntimeError('ASA returned no 2026 MLS games')
    g.to_parquet(OUT/'asa_games_2026.parquet',index=False)
    g.to_csv(OUT/'asa_games_2026.csv',index=False)
    status_col='status' if 'status' in g.columns else None
    date_col='date_time_utc' if 'date_time_utc' in g.columns else None
    now=datetime.now(timezone.utc)
    summary={'captured_at':now.isoformat(),'rows':len(g),'columns':list(g.columns)}
    if status_col:
        summary['status_counts']={str(k):int(v) for k,v in g[status_col].value_counts(dropna=False).items()}
    if date_col:
        dt=pd.to_datetime(g[date_col],errors='coerce',utc=True)
        summary['latest_game_time']=None if dt.isna().all() else dt.max().isoformat()
        summary['completed_through']=None
        if status_col:
            z=dt[g[status_col].astype(str).str.lower().eq('fulltime')]
            if len(z) and z.notna().any():summary['completed_through']=z.max().isoformat()
        summary['future_games']=int((dt>pd.Timestamp(now)).sum())
    (OUT/'latest_summary.json').write_text(json.dumps(summary,indent=2,default=str))
    print(json.dumps(summary,indent=2,default=str))
if __name__=='__main__':main()
