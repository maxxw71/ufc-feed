#!/usr/bin/env python3
from __future__ import annotations

import json, math, time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis

from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed'; REP=ROOT/'reports'
PROC.mkdir(parents=True,exist_ok=True); REP.mkdir(parents=True,exist_ok=True)

BASE=PROC/'mls_match_features_weather_enriched.parquet'
TEAMS=PROC/'asa_teams.parquet'
PXG=PROC/'asa_player_xg_game_2013_present.parquet'
SAL=PROC/'asa_player_salaries_by_season_2013_present.parquet'
OUT=PROC/'mls_salary_features.parquet'
META=REP/'salary_feature_meta.json'

def now(): return datetime.now(timezone.utc).isoformat()

def collect_salaries():
    asa=AmericanSoccerAnalysis()
    parts=[];coverage=[]
    for y in range(2013,2027):
        try:
            z=asa.get_player_salaries(leagues='mls',season_name=str(y))
            if not isinstance(z,pd.DataFrame): z=pd.DataFrame(z)
            if len(z):
                z['_season_requested']=y
                parts.append(z)
            coverage.append({'season':y,'rows':len(z),'columns':list(z.columns)})
        except Exception as e:
            coverage.append({'season':y,'rows':0,'error':type(e).__name__+': '+str(e)[:240]})
        time.sleep(.08)
    if not parts: raise RuntimeError('No ASA salary rows returned')
    sal=pd.concat(parts,ignore_index=True,sort=False)
    # Exact duplicate releases can occur across API pages; retain one.
    keep=[c for c in ['player_id','team_id','season_name','mlspa_release'] if c in sal.columns]
    if keep: sal=sal.drop_duplicates(keep,keep='last')
    sal.to_parquet(SAL,index=False)
    return sal,coverage

def ensure_pxg():
    if PXG.exists(): return pd.read_parquet(PXG)
    asa=AmericanSoccerAnalysis();parts=[]
    for y in range(2013,2027):
        z=asa.get_player_xgoals(leagues='mls',season_name=str(y),split_by_games=True)
        if not isinstance(z,pd.DataFrame):z=pd.DataFrame(z)
        if len(z):
            if len(z)>=10000: raise RuntimeError(f'player xG {y} hit cap {len(z)}; fail closed')
            z['_season_requested']=y;parts.append(z)
        time.sleep(.08)
    if not parts: raise RuntimeError('No player game xG rows')
    x=pd.concat(parts,ignore_index=True,sort=False);x.to_parquet(PXG,index=False);return x

def snapshot_features(snapshot, recent_minutes):
    if snapshot is None or snapshot.empty:
        return {
          'salary_roster_count':np.nan,'salary_total_gc':np.nan,'salary_mean_gc':np.nan,'salary_median_gc':np.nan,
          'salary_max_gc':np.nan,'salary_top3_share':np.nan,'salary_top5_share':np.nan,
          'salary_active5_total_gc':np.nan,'salary_top11_minutes5_gc':np.nan,
          'salary_minutes_weighted_gc5':np.nan,'salary_recent5_covered_minutes_share':np.nan,
        }
    s=snapshot.copy()
    s['gc']=pd.to_numeric(s.guaranteed_compensation,errors='coerce')
    s=s[s.gc.notna()&s.gc.ge(0)]
    if s.empty:return snapshot_features(None,{})
    total=float(s.gc.sum()); vals=sorted(s.gc.astype(float),reverse=True)
    salmap=dict(zip(s.player_id.astype(str),s.gc.astype(float)))
    active={str(k):float(v) for k,v in recent_minutes.items() if float(v)>0}
    covered={p:m for p,m in active.items() if p in salmap}
    total_mins=sum(active.values()); covered_mins=sum(covered.values())
    bymins=sorted(covered.items(),key=lambda kv:kv[1],reverse=True)
    top11=[p for p,_ in bymins[:11]]
    weighted=sum(salmap[p]*m for p,m in covered.items())/covered_mins if covered_mins else np.nan
    return {
      'salary_roster_count':int(len(s)),
      'salary_total_gc':total,
      'salary_mean_gc':float(s.gc.mean()),
      'salary_median_gc':float(s.gc.median()),
      'salary_max_gc':float(max(vals)),
      'salary_top3_share':sum(vals[:3])/total if total>0 else np.nan,
      'salary_top5_share':sum(vals[:5])/total if total>0 else np.nan,
      'salary_active5_total_gc':float(sum(salmap[p] for p in covered)),
      'salary_top11_minutes5_gc':float(sum(salmap[p] for p in top11)),
      'salary_minutes_weighted_gc5':float(weighted) if pd.notna(weighted) else np.nan,
      'salary_recent5_covered_minutes_share':covered_mins/total_mins if total_mins>0 else np.nan,
    }

def main():
    if not BASE.exists():raise RuntimeError(f'Missing base {BASE}')
    if not TEAMS.exists():raise RuntimeError('ASA teams table missing')
    base=pd.read_parquet(BASE).copy()
    base['date_utc']=pd.to_datetime(base.date,errors='coerce',utc=True)
    teams=pd.read_parquet(TEAMS)
    tid=next(c for c in ['team_id','id'] if c in teams.columns)
    tname=next(c for c in ['team_name','name'] if c in teams.columns)
    team_map={str(r[tid]):canon_team(r[tname]) for _,r in teams.iterrows()}

    sal,coverage=collect_salaries()
    req={'player_id','team_id','season_name','guaranteed_compensation','mlspa_release'}
    miss=req-set(sal.columns)
    if miss:raise RuntimeError(f'Salary schema missing {sorted(miss)}')
    sal['player_id']=sal.player_id.astype(str);sal['team']=sal.team_id.astype(str).map(team_map)
    sal['release_dt']=pd.to_datetime(sal.mlspa_release,errors='coerce',utc=True)
    sal['season_int']=pd.to_numeric(sal.season_name,errors='coerce').astype('Int64')
    sal=sal[sal.team.notna()&sal.release_dt.notna()&sal.season_int.notna()].copy()

    # Preindex snapshots by season/team/release.
    snaps={}
    releases=defaultdict(list)
    for (season,team,rel),g in sal.groupby(['season_int','team','release_dt']):
        k=(int(season),str(team),pd.Timestamp(rel))
        snaps[k]=g.copy();releases[(int(season),str(team))].append(pd.Timestamp(rel))
    for k in releases:releases[k]=sorted(set(releases[k]))

    px=ensure_pxg().copy()
    px['player_id']=px.player_id.astype(str);px['team']=px.team_id.astype(str).map(team_map)
    px['minutes_played']=pd.to_numeric(px.minutes_played,errors='coerce').fillna(0)
    px['game_id']=px.game_id.astype(str)
    game_players={k:z[['player_id','minutes_played']].copy() for k,z in px[px.team.notna()].groupby(['game_id','team'])}

    # Build recent prior-five player-minute state from target MLS matches only.
    hist=defaultdict(lambda:deque(maxlen=5))
    rows=[]
    ordered=base[base.date_utc.notna()].sort_values(['date_utc','match_id']).copy()
    for _,g in ordered.iterrows():
        row={'match_id':g.match_id}
        season=int(g.season) if pd.notna(g.season) else int(g.date_utc.year)
        for side in ['home','away']:
            team=canon_team(g[f'{side}_team'])
            rels=[r for r in releases.get((season,team),[]) if r < g.date_utc]
            rel=max(rels) if rels else None
            recent=defaultdict(float)
            for game in hist[team]:
                for p,m in game.items():recent[p]+=m
            snap=snaps.get((season,team,rel)) if rel is not None else None
            sf=snapshot_features(snap,recent)
            row[f'{side}_salary_data_available']=int(snap is not None and len(snap)>0)
            row[f'{side}_salary_release_date']=rel.isoformat() if rel is not None else None
            for k,v in sf.items():row[f'{side}_{k}']=v

        for k in ['salary_total_gc','salary_mean_gc','salary_median_gc','salary_max_gc','salary_top3_share','salary_top5_share',
                  'salary_active5_total_gc','salary_top11_minutes5_gc','salary_minutes_weighted_gc5','salary_recent5_covered_minutes_share']:
            h=row.get('home_'+k);a=row.get('away_'+k)
            row['edge_'+k]=(h-a) if pd.notna(h) and pd.notna(a) else np.nan
        rows.append(row)

        # update recent minutes strictly after feature capture
        gid=str(g.get('asa_game_id')) if pd.notna(g.get('asa_game_id')) else None
        if gid:
            for side in ['home','away']:
                team=canon_team(g[f'{side}_team'])
                z=game_players.get((gid,team))
                if z is not None:
                    hist[team].append({str(r.player_id):float(r.minutes_played) for _,r in z.iterrows() if float(r.minutes_played)>0})

    feat=pd.DataFrame(rows)
    feat.to_parquet(OUT,index=False)
    coverage_rows=int((feat.home_salary_data_available.eq(1)|feat.away_salary_data_available.eq(1)).sum())
    both_rows=int((feat.home_salary_data_available.eq(1)&feat.away_salary_data_available.eq(1)).sum())
    meta={
      'built_at':now(),'rows':len(feat),'columns':len(feat.columns),'output':str(OUT),
      'salary_rows':len(sal),'salary_api_coverage':coverage,
      'matches_any_salary_snapshot':coverage_rows,'matches_both_salary_snapshots':both_rows,
      'seasons_with_salary':sorted(int(x) for x in sal.season_int.dropna().unique()),
      'leakage_note':'Salary snapshots use the latest MLSPA release strictly before the target match. Recent-player salary features use only player minutes from prior MLS matches.',
      'designation_note':'DP/U22 designations are not inferred from salary. They remain absent until an authoritative dated designation source is available.'
    }
    META.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':main()
