from pathlib import Path
from datetime import date
import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data/injury_travel_mining')
sched=pd.read_parquet(ROOT/'data/raw/schedules_2006_2026.parquet')
sched['gameday_dt']=pd.to_datetime(sched.gameday,errors='coerce')
today=pd.Timestamp(date.today())
current_season=int(sched.loc[sched.gameday_dt.dt.year<=today.year,'season'].max())
played_or_current=sched[(sched.season==current_season)&(sched.gameday_dt<=today)]
max_week=int(pd.to_numeric(played_or_current.week,errors='coerce').max()) if len(played_or_current) else 0
print(f'Current season={current_season}, allowed injury weeks <= {max_week}')

for name in ['injury_player_week_2009_2026.parquet','injury_team_week_2009_2026.parquet']:
    p=CTX/name
    if not p.exists(): continue
    d=pd.read_parquet(p)
    before=len(d)
    d=d[~((pd.to_numeric(d.season,errors='coerce')==current_season)&(pd.to_numeric(d.week,errors='coerce')>max_week))].copy()
    d.to_parquet(p,index=False)
    print(name,before,'->',len(d))

# Clear future-week injury-derived fields from the merged side table as well.
p=CTX/'complete_pregame_team_sides.parquet'
if p.exists():
    d=pd.read_parquet(p)
    fut=(pd.to_numeric(d.season,errors='coerce')==current_season)&(pd.to_numeric(d.week,errors='coerce')>max_week)
    injury_tokens=('injury','unavail','out_players','doubtful','questionable','dnp_','limited_','star_out')
    cols=[c for c in d.columns if any(t in c.lower() for t in injury_tokens)]
    for c in cols:
        if c=='injury_data_available': d.loc[fut,c]=0
        else: d.loc[fut,c]=np.nan
    d.to_parquet(p,index=False)
    print(f'Cleared {len(cols)} injury-derived columns on {int(fut.sum())} future team-game rows')
