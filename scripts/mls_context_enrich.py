#!/usr/bin/env python3
from __future__ import annotations

import json, math
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed'
REP=ROOT/'reports'
BASE_CANDIDATES=[
    PROC/'mls_match_features_availability_enriched.parquet',
    PROC/'mls_match_features_player_enriched.parquet',
    PROC/'mls_match_features_advanced.parquet',
]
GAMES_CANDIDATES=[
    PROC/'asa_mls_games_2012_present.parquet',
    PROC/'asa_mls_games_2013_present.parquet',
]
TEAMS=PROC/'asa_teams.parquet'
PLAYERS=PROC/'asa_player_xg_game_2013_present.parquet'
GK=PROC/'asa_player_gk_game_2013_present.parquet'
OUT=PROC/'mls_match_features_context_enriched.parquet'
FEATURES_OUT=PROC/'mls_context_pregame_features.parquet'
REPORT=REP/'context_feature_meta.json'

def now(): return datetime.now(timezone.utc).isoformat()

def pick_existing(paths):
    for p in paths:
        if p.exists(): return p
    raise RuntimeError('Required MLS source file missing: '+', '.join(str(x) for x in paths))

def n(v,default=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default

def jaccard(a,b):
    if not a and not b:return np.nan
    return len(a & b)/max(1,len(a | b))

def main():
    base_path=pick_existing(BASE_CANDIDATES)
    games_path=pick_existing(GAMES_CANDIDATES)
    base=pd.read_parquet(base_path).copy()
    games=pd.read_parquet(games_path).copy()
    teams=pd.read_parquet(TEAMS).copy()
    px=pd.read_parquet(PLAYERS).copy()
    gk=pd.read_parquet(GK).copy()

    if 'asa_game_id' not in base:
        raise RuntimeError('asa_game_id missing from base warehouse')
    required_games={'game_id','date_time_utc','home_team_id','away_team_id','home_score','away_score','referee_id'}
    miss=required_games-set(games.columns)
    if miss: raise RuntimeError('ASA games missing columns: '+str(sorted(miss)))

    team_map={str(r.team_id):canon_team(r.team_name) for _,r in teams.iterrows()}
    games['game_id']=games.game_id.astype(str)
    games['dt']=pd.to_datetime(games.date_time_utc,errors='coerce',utc=True)
    games['home_team']=games.home_team_id.astype(str).map(team_map)
    games['away_team']=games.away_team_id.astype(str).map(team_map)
    games=games[games.dt.notna()&games.home_team.notna()&games.away_team.notna()].copy()
    games=games.sort_values(['dt','game_id']).drop_duplicates('game_id',keep='last')

    # Restrict feature generation to games that can attach to our warehouse.
    target_ids=set(base.asa_game_id.dropna().astype(str))
    games=games[games.game_id.isin(target_ids)].copy()

    # Player appearances, used only after a game completes to update rolling roster state.
    for c in ['game_id','player_id','team_id']:
        px[c]=px[c].astype(str)
    px['team']=px.team_id.map(team_map)
    px['minutes_played']=pd.to_numeric(px.get('minutes_played'),errors='coerce').fillna(0.0)
    px=px[px.game_id.isin(target_ids)&px.team.notna()].copy()
    pgame={}
    for (gid,team),z in px.groupby(['game_id','team']):
        pgame[(str(gid),team)]=[
            {'player_id':str(r.player_id),'minutes':n(r.minutes_played)}
            for _,r in z.iterrows() if n(r.minutes_played)>0
        ]

    # Goalkeeper per-game table. Primary keeper = most minutes for that team/game.
    if 'game_id' not in gk.columns:
        raise RuntimeError('ASA goalkeeper table is not game-level; game_id missing')
    for c in ['game_id','player_id','team_id']:
        gk[c]=gk[c].astype(str)
    gk['team']=gk.team_id.map(team_map)
    for c in ['minutes_played','shots_faced','goals_conceded','saves','xgoals_gk_faced','goals_minus_xgoals_gk']:
        if c not in gk.columns:gk[c]=0.0
        gk[c]=pd.to_numeric(gk[c],errors='coerce').fillna(0.0)
    gk=gk[gk.game_id.isin(target_ids)&gk.team.notna()].copy()
    ggame={}
    for (gid,team),z in gk.groupby(['game_id','team']):
        if z.empty: continue
        r=z.sort_values('minutes_played',ascending=False).iloc[0]
        ggame[(str(gid),team)]={
            'player_id':str(r.player_id),
            'minutes':n(r.minutes_played),
            'shots_faced':n(r.shots_faced),
            'goals_conceded':n(r.goals_conceded),
            'saves':n(r.saves),
            'xg_faced':n(r.xgoals_gk_faced),
            'goals_minus_xg':n(r.goals_minus_xgoals_gk),
        }

    ref_state=defaultdict(lambda:{'games':0,'home_wins':0,'draws':0,'goals':0.0,'home_gd':0.0,'over25':0})
    roster_hist=defaultdict(lambda:deque(maxlen=10))
    gk_hist=defaultdict(lambda:deque(maxlen=10))
    keeper_team_games=defaultdict(int)
    rows=[]

    def roster_summary(team):
        hist=list(roster_hist[team])
        if not hist:
            return {
              'roster_prior_games':0,'roster_jaccard_recent5_prev5':np.nan,
              'roster_new_players3':np.nan,'roster_departed_players3':np.nan,
              'roster_new_minutes_share3':np.nan,'roster_departed_minutes_share_prev5':np.nan,
              'roster_churn_index':np.nan,
            }
        recent5=hist[-5:];prev5=hist[-10:-5]
        set_recent={p['player_id'] for h in recent5 for p in h['players']}
        set_prev={p['player_id'] for h in prev5 for p in h['players']}
        last3=hist[-3:]
        before3=hist[-10:-3]
        set_last3={p['player_id'] for h in last3 for p in h['players']}
        set_before3={p['player_id'] for h in before3 for p in h['players']}
        entrants=set_last3-set_before3
        departed=set_prev-set_last3 if prev5 else set()

        mins3=defaultdict(float);mins_prev=defaultdict(float)
        for h in last3:
            for p in h['players']: mins3[p['player_id']]+=p['minutes']
        for h in prev5:
            for p in h['players']: mins_prev[p['player_id']]+=p['minutes']
        total3=sum(mins3.values());total_prev=sum(mins_prev.values())
        new_share=sum(mins3[p] for p in entrants)/total3 if total3 else np.nan
        dep_share=sum(mins_prev[p] for p in departed)/total_prev if total_prev else np.nan
        churn=(0 if pd.isna(new_share) else new_share)+(0 if pd.isna(dep_share) else dep_share)
        return {
          'roster_prior_games':len(hist),
          'roster_jaccard_recent5_prev5':jaccard(set_recent,set_prev) if prev5 else np.nan,
          'roster_new_players3':len(entrants) if len(hist)>=4 else np.nan,
          'roster_departed_players3':len(departed) if prev5 else np.nan,
          'roster_new_minutes_share3':new_share,
          'roster_departed_minutes_share_prev5':dep_share,
          'roster_churn_index':churn if (not pd.isna(new_share) or not pd.isna(dep_share)) else np.nan,
        }

    def keeper_summary(team):
        hist=list(gk_hist[team])
        if not hist:
            return {
              'gk_prior_team_games':0,'gk_last5_games':0,'gk_same_keeper_last3':np.nan,
              'gk_save_pct5':np.nan,'gk_goals_minus_xg_p96_5':np.nan,
              'gk_xg_faced_p96_5':np.nan,'gk_goals_conceded_p96_5':np.nan,
              'gk_current_keeper_prior_team_games':np.nan,
            }
        h5=hist[-5:];h3=hist[-3:]
        mins=sum(x['minutes'] for x in h5)
        shots=sum(x['shots_faced'] for x in h5)
        saves=sum(x['saves'] for x in h5)
        gmxg=sum(x['goals_minus_xg'] for x in h5)
        xgf=sum(x['xg_faced'] for x in h5)
        gc=sum(x['goals_conceded'] for x in h5)
        keepers=[x['player_id'] for x in h3 if x.get('player_id')]
        current=hist[-1]['player_id']
        return {
          'gk_prior_team_games':len(hist),
          'gk_last5_games':len(h5),
          'gk_same_keeper_last3':float(len(keepers)>=2 and len(set(keepers))==1),
          'gk_save_pct5':saves/shots if shots>0 else np.nan,
          'gk_goals_minus_xg_p96_5':gmxg*96/mins if mins>0 else np.nan,
          'gk_xg_faced_p96_5':xgf*96/mins if mins>0 else np.nan,
          'gk_goals_conceded_p96_5':gc*96/mins if mins>0 else np.nan,
          'gk_current_keeper_prior_team_games':keeper_team_games[(team,current)],
        }

    for _,g in games.iterrows():
        gid=str(g.game_id);ref=str(g.referee_id) if pd.notna(g.referee_id) else None
        row={'asa_game_id':gid,'date_time_utc':g.dt,'referee_id':ref}
        rs=ref_state[ref] if ref else None
        if rs:
            nref=rs['games']
            row.update({
              'referee_prior_games':nref,
              'referee_prior_home_win_rate':rs['home_wins']/nref if nref else np.nan,
              'referee_prior_draw_rate':rs['draws']/nref if nref else np.nan,
              'referee_prior_total_goals':rs['goals']/nref if nref else np.nan,
              'referee_prior_home_gd':rs['home_gd']/nref if nref else np.nan,
              'referee_prior_over25_rate':rs['over25']/nref if nref else np.nan,
            })
        else:
            row.update({k:np.nan for k in [
              'referee_prior_games','referee_prior_home_win_rate','referee_prior_draw_rate',
              'referee_prior_total_goals','referee_prior_home_gd','referee_prior_over25_rate'
            ]})

        for side,team in [('home',g.home_team),('away',g.away_team)]:
            for k,v in roster_summary(team).items():row[f'{side}_{k}']=v
            for k,v in keeper_summary(team).items():row[f'{side}_{k}']=v

        # Selection-neutral edge fields. Positive means home > away.
        for k in [
          'roster_jaccard_recent5_prev5','roster_new_players3','roster_departed_players3',
          'roster_new_minutes_share3','roster_departed_minutes_share_prev5','roster_churn_index',
          'gk_save_pct5','gk_goals_minus_xg_p96_5','gk_xg_faced_p96_5','gk_goals_conceded_p96_5',
          'gk_current_keeper_prior_team_games'
        ]:
            h=row.get('home_'+k);a=row.get('away_'+k)
            row['edge_'+k]=(h-a) if pd.notna(h) and pd.notna(a) else np.nan
        rows.append(row)

        # Update all state strictly after feature capture.
        hs=n(g.home_score,np.nan);aw=n(g.away_score,np.nan)
        if ref and pd.notna(hs) and pd.notna(aw):
            rs=ref_state[ref]
            rs['games']+=1;rs['home_wins']+=int(hs>aw);rs['draws']+=int(hs==aw)
            rs['goals']+=hs+aw;rs['home_gd']+=hs-aw;rs['over25']+=int(hs+aw>=3)

        for team in [g.home_team,g.away_team]:
            plist=pgame.get((gid,team),[])
            roster_hist[team].append({'game_id':gid,'players':plist})
            kr=ggame.get((gid,team))
            if kr:
                gk_hist[team].append(kr)
                keeper_team_games[(team,kr['player_id'])]+=1

    feat=pd.DataFrame(rows)
    if feat.asa_game_id.duplicated().any():raise RuntimeError('duplicate context feature game IDs')
    feat.to_parquet(FEATURES_OUT,index=False)

    add=[c for c in feat.columns if c not in {'date_time_utc'}]
    out=base.merge(feat[add],on='asa_game_id',how='left',validate='m:1')
    out.to_parquet(OUT,index=False)

    added=[c for c in out.columns if c not in base.columns]
    meta={
      'built_at':now(),'base_file':str(base_path),'rows':len(out),'columns':len(out.columns),
      'added_columns':len(added),'added':added,'feature_rows':len(feat),
      'referee_rows':int(feat.referee_prior_games.notna().sum()),
      'gk_rows':int(feat.home_gk_prior_team_games.notna().sum()+feat.away_gk_prior_team_games.notna().sum()),
      'roster_rows':int(feat.home_roster_prior_games.notna().sum()+feat.away_roster_prior_games.notna().sum()),
      'leakage_note':'Referee, goalkeeper and roster-churn features are read from state before the current match updates that state. Roster churn is an appearance/minutes proxy for signings, departures and rotation; it is not a transaction-date feed.',
      'output':str(OUT),
    }
    REPORT.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':
    main()
