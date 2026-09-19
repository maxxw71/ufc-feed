#!/usr/bin/env python3
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
BASE=ROOT/'data/processed/mls_match_features_advanced.parquet'
PLAYER=ROOT/'data/processed/mls_player_manager_pregame_features.parquet'
OUT=ROOT/'data/processed/mls_match_features_player_enriched.parquet'
REPORT=ROOT/'reports/player_merge_meta.json'

def main():
    if not BASE.exists():raise RuntimeError('advanced MLS feature warehouse missing')
    if not PLAYER.exists():raise RuntimeError('player/manager pregame features missing')
    d=pd.read_parquet(BASE);p=pd.read_parquet(PLAYER)
    if 'asa_game_id' not in d or 'asa_game_id' not in p:raise RuntimeError('asa_game_id missing')
    p=p[p.asa_game_id.notna()].copy()
    if p.asa_game_id.duplicated().any():
        raise RuntimeError('duplicate player feature asa_game_id rows')
    keep=['asa_game_id']+[c for c in p.columns if c!='asa_game_id' and (c.startswith('home_player_') or c.startswith('away_player_') or c.startswith('edge_player_') or c.startswith('home_manager_') or c.startswith('away_manager_') or c.startswith('edge_manager_'))]
    z=d.merge(p[keep],on='asa_game_id',how='left',validate='m:1')
    added=[c for c in z.columns if c not in d.columns]
    matched=int(z[added].notna().any(axis=1).sum()) if added else 0
    z.to_parquet(OUT,index=False)
    meta={
      'built_at':datetime.now(timezone.utc).isoformat(),'rows':len(z),'columns':len(z.columns),
      'base_columns':len(d.columns),'added_columns':len(added),'added':added,
      'matched_matches':matched,'matched_pct':100*matched/max(1,len(z)),
      'regular_season_2013_2025_rows':int(z[z.season.between(2013,2025)&z.asa_game_available.eq(True)&z.asa_knockout_game.eq(False)].shape[0]),
      'leakage_note':'All player/manager features are captured from prior games before current-game player rows and manager results update state.'
    }
    REPORT.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2))
if __name__=='__main__':main()
