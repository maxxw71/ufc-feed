#!/usr/bin/env python3
from __future__ import annotations

import json, math
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed'
REP=ROOT/'reports'
OUT=PROC/'mls_individual_player_pregame_features.parquet'
META=REP/'individual_player_feature_meta.json'

GAMES_CANDIDATES=[PROC/'asa_mls_games_2012_present.parquet',PROC/'asa_mls_games_2013_present.parquet']
XG=PROC/'asa_player_xg_game_2013_present.parquet'
XP=PROC/'asa_player_xpass_game_2013_present.parquet'
GP=PROC/'asa_player_gplus_game_2013_present.parquet'
PLAYERS=PROC/'asa_players.parquet'
TEAMS=PROC/'asa_teams.parquet'

STAT_COLS=[
 'goals','shots','shots_on_target','xgoals','key_passes','primary_assists','xassists',
 'xgoals_plus_xassists','points_added','xpoints_added','attempted_passes',
 'passes_completed_over_expected','share_team_touches','gplus_raw','gplus_above_avg',
 'gplus_passing','gplus_receiving','gplus_shooting','gplus_interrupting'
]

def now(): return datetime.now(timezone.utc).isoformat()

def pick_games():
    for p in GAMES_CANDIDATES:
        if p.exists(): return p
    raise RuntimeError('ASA MLS games file missing')

def available(df, cols):
    return [c for c in cols if c in df.columns]

def dedup(df):
    keys=[c for c in ['player_id','game_id','team_id'] if c in df.columns]
    return df.drop_duplicates(keys,keep='last').reset_index(drop=True)

def load_player_games():
    if not all(p.exists() for p in [XG,XP,GP,PLAYERS,TEAMS]):
        missing=[str(p) for p in [XG,XP,GP,PLAYERS,TEAMS] if not p.exists()]
        raise RuntimeError('Missing player inputs: '+','.join(missing))
    xg=dedup(pd.read_parquet(XG))
    xp=dedup(pd.read_parquet(XP))
    gp=dedup(pd.read_parquet(GP))
    key=['player_id','game_id','team_id']

    xcols=available(xg,['general_position','minutes_played','goals','shots','shots_on_target','xgoals',
                       'key_passes','primary_assists','xassists','xgoals_plus_xassists','points_added','xpoints_added'])
    pcols=available(xp,['attempted_passes','passes_completed_over_expected','share_team_touches'])
    gcols=available(gp,['gplus_raw','gplus_above_avg','gplus_passing','gplus_receiving','gplus_shooting','gplus_interrupting'])

    pg=xg[key+xcols].merge(xp[key+pcols],on=key,how='outer').merge(gp[key+gcols],on=key,how='outer')
    for c in ['minutes_played']+STAT_COLS:
        if c in pg.columns: pg[c]=pd.to_numeric(pg[c],errors='coerce').fillna(0.0)
    return dedup(pg)

def rate(agg,pid,stat):
    if pid is None or pid not in agg:return np.nan
    m=float(agg[pid].get('minutes',0) or 0)
    if m<=0:return np.nan
    return 90.0*float(agg[pid].get(stat,0) or 0)/m

def build_agg(games):
    a=defaultdict(lambda:defaultdict(float))
    age_num=defaultdict(float);age_den=defaultdict(float)
    for g in games:
        for p in g['players']:
            pid=str(p['player_id']);m=float(p.get('minutes',0) or 0)
            a[pid]['minutes']+=m
            for s in STAT_COLS:
                a[pid][s]+=float(p.get(s,0) or 0)
            if pd.notna(p.get('age')):
                age_num[pid]+=m*float(p['age']);age_den[pid]+=m
    for pid in a:
        a[pid]['age']=age_num[pid]/age_den[pid] if age_den[pid] else np.nan
    return a

def eligible_rank(agg,stat,min_minutes):
    vals=[]
    for pid,d in agg.items():
        m=float(d.get('minutes',0) or 0)
        if m<min_minutes:continue
        v=90.0*float(d.get(stat,0) or 0)/m
        if math.isfinite(v):vals.append((v,pid))
    vals.sort(reverse=True)
    return vals

def rank_val(vals,k=0):
    return float(vals[k][0]) if len(vals)>k else np.nan

def summarize(hist):
    if not hist:return {}
    h3=list(hist)[-3:];h5=list(hist)[-5:];h10=list(hist)[-10:]
    a3=build_agg(h3);a5=build_agg(h5);a10=build_agg(h10)

    mins5=sum(float(d.get('minutes',0) or 0) for d in a5.values())
    xgi_total5=sum(max(0,float(d.get('xgoals_plus_xassists',0) or 0)) for d in a5.values())
    gp_total5=sum(max(0,float(d.get('gplus_raw',0) or 0)) for d in a5.values())

    ranks={}
    for label,stat in [
      ('xgi','xgoals_plus_xassists'),('xg','xgoals'),('xa','xassists'),('sot','shots_on_target'),
      ('keypass','key_passes'),('gplus','gplus_raw'),('gplus_shooting','gplus_shooting'),
      ('gplus_passing','gplus_passing'),('points_added','points_added'),('pcoe','passes_completed_over_expected')
    ]:
        ranks[label]=eligible_rank(a5,stat,90)

    star_xgi=ranks['xgi'][0][1] if ranks['xgi'] else None
    star_gplus=ranks['gplus'][0][1] if ranks['gplus'] else None
    star_xg=ranks['xg'][0][1] if ranks['xg'] else None
    star_xa=ranks['xa'][0][1] if ranks['xa'] else None

    def share(pid,stat,total):
        if pid is None or pid not in a5 or total<=0:return np.nan
        return max(0,float(a5[pid].get(stat,0) or 0))/total

    def age(pid):
        return float(a5[pid].get('age')) if pid in a5 and pd.notna(a5[pid].get('age')) else np.nan

    def delta(pid,stat):
        if pid is None:return np.nan
        r3=rate(a3,pid,stat);r10=rate(a10,pid,stat)
        return r3-r10 if pd.notna(r3) and pd.notna(r10) else np.nan

    out={
      'recent_games':len(hist),
      'eligible_players5':sum(float(d.get('minutes',0) or 0)>=90 for d in a5.values()),
      'top1_xgi90_5':rank_val(ranks['xgi'],0),'top2_xgi90_5':rank_val(ranks['xgi'],1),
      'top1_xgi90_gap2_5':rank_val(ranks['xgi'],0)-rank_val(ranks['xgi'],1) if len(ranks['xgi'])>1 else np.nan,
      'top1_xg90_5':rank_val(ranks['xg'],0),'top2_xg90_5':rank_val(ranks['xg'],1),
      'top1_xa90_5':rank_val(ranks['xa'],0),'top2_xa90_5':rank_val(ranks['xa'],1),
      'top1_sot90_5':rank_val(ranks['sot'],0),'top1_keypass90_5':rank_val(ranks['keypass'],0),
      'top1_gplus90_5':rank_val(ranks['gplus'],0),'top2_gplus90_5':rank_val(ranks['gplus'],1),
      'top1_gplus_shooting90_5':rank_val(ranks['gplus_shooting'],0),
      'top1_gplus_passing90_5':rank_val(ranks['gplus_passing'],0),
      'top1_points_added90_5':rank_val(ranks['points_added'],0),
      'top1_pcoe90_5':rank_val(ranks['pcoe'],0),
      'star_xgi_share5':share(star_xgi,'xgoals_plus_xassists',xgi_total5),
      'star_gplus_share5':share(star_gplus,'gplus_raw',gp_total5),
      'star_xgi_age5':age(star_xgi),'star_gplus_age5':age(star_gplus),
      'star_xgi_minutes_share5':float(a5[star_xgi].get('minutes',0))/mins5 if star_xgi and mins5>0 else np.nan,
      'star_xgi_delta3v10':delta(star_xgi,'xgoals_plus_xassists'),
      'star_gplus_delta3v10':delta(star_gplus,'gplus_raw'),
      'best_shooter_delta3v10':delta(star_xg,'xgoals'),
      'best_creator_delta3v10':delta(star_xa,'xassists'),
      'xgi_depth_035_5':sum(v>=0.35 for v,_ in ranks['xgi']),
      'xgi_depth_050_5':sum(v>=0.50 for v,_ in ranks['xgi']),
      'gplus_positive_depth5':sum(v>0 for v,_ in ranks['gplus']),
      'gplus_020_depth5':sum(v>=0.20 for v,_ in ranks['gplus']),
    }

    # Top-3 raw contribution concentration and ages of highest-impact attackers.
    if xgi_total5>0:
        xs=sorted([max(0,float(d.get('xgoals_plus_xassists',0) or 0)) for d in a5.values()],reverse=True)
        out['top3_xgi_share5']=sum(xs[:3])/xgi_total5
    else:out['top3_xgi_share5']=np.nan
    if gp_total5>0:
        gs=sorted([max(0,float(d.get('gplus_raw',0) or 0)) for d in a5.values()],reverse=True)
        out['top3_gplus_share5']=sum(gs[:3])/gp_total5
    else:out['top3_gplus_share5']=np.nan

    # Same features on a 10-game window for stability/depth.
    for label,stat in [('xgi','xgoals_plus_xassists'),('xg','xgoals'),('xa','xassists'),('gplus','gplus_raw')]:
        rr=eligible_rank(a10,stat,180)
        out[f'top1_{label}90_10']=rank_val(rr,0)
        out[f'top2_{label}90_10']=rank_val(rr,1)
    return out

def main():
    games_path=pick_games()
    games=pd.read_parquet(games_path).copy()
    teams=pd.read_parquet(TEAMS).copy()
    players=pd.read_parquet(PLAYERS).copy()
    pg=load_player_games()

    tid=next(c for c in ['team_id','id'] if c in teams.columns)
    tname=next(c for c in ['team_name','name'] if c in teams.columns)
    team_map={str(r[tid]):str(r[tname]) for _,r in teams.iterrows()}

    games['dt']=pd.to_datetime(games['date_time_utc'],errors='coerce',utc=True)
    games=games[games.dt.notna()].sort_values('dt').copy()
    games['home_team']=games.home_team_id.astype(str).map(team_map)
    games['away_team']=games.away_team_id.astype(str).map(team_map)
    games=games[games.home_team.notna()&games.away_team.notna()].copy()

    pbase=players.drop_duplicates('player_id',keep='last').set_index('player_id')
    bdates=pd.to_datetime(pbase['birth_date'],errors='coerce',utc=True) if 'birth_date' in pbase else pd.Series(dtype='datetime64[ns, UTC]')
    bdmap=bdates.to_dict()

    pg['team']=pg.team_id.astype(str).map(team_map)
    pg=pg[pg.team.notna()].copy()
    game_dt=games.set_index('game_id')['dt'].to_dict()
    pg['dt']=pg.game_id.map(game_dt)
    pg=pg[pg.dt.notna()].copy()
    pg['birth_date']=pg.player_id.map(bdmap)
    pg['age']=(pg.dt-pg.birth_date).dt.total_seconds()/(365.25*86400)

    for c in ['minutes_played']+STAT_COLS:
        if c not in pg:pg[c]=0.0
        pg[c]=pd.to_numeric(pg[c],errors='coerce').fillna(0.0)
    if 'general_position' not in pg:pg['general_position']=None

    grouped={}
    for (gid,team),z in pg.groupby(['game_id','team'],sort=False):
        plist=[]
        for _,p in z.iterrows():
            row={'player_id':str(p.player_id),'minutes':float(p.minutes_played),'age':p.age,'position':p.general_position}
            for stat in STAT_COLS:row[stat]=float(p[stat]) if stat in p and pd.notna(p[stat]) else 0.0
            plist.append(row)
        grouped[(gid,team)]=plist

    history=defaultdict(lambda:deque(maxlen=10))
    rows=[]
    for _,g in games.iterrows():
        row={'asa_game_id':str(g.game_id),'date_time_utc':g.dt,'season':int(g.dt.year),
             'home_team':g.home_team,'away_team':g.away_team}
        summaries={}
        for side,team in [('home',g.home_team),('away',g.away_team)]:
            sm=summarize(history[team]);summaries[side]=sm
            for k,v in sm.items():row[f'{side}_indplayer_{k}']=v
        keys=sorted(set(summaries.get('home',{}))|set(summaries.get('away',{})))
        for k in keys:
            h=summaries.get('home',{}).get(k);a=summaries.get('away',{}).get(k)
            if isinstance(h,(int,float,np.integer,np.floating)) and isinstance(a,(int,float,np.integer,np.floating)) and pd.notna(h) and pd.notna(a):
                row[f'edge_indplayer_{k}']=float(h)-float(a)
                row[f'balance_indplayer_{k}']=abs(float(h)-float(a))
                row[f'combined_indplayer_{k}']=float(h)+float(a)
        rows.append(row)

        # Update only after pre-match feature capture.
        for team in [g.home_team,g.away_team]:
            history[team].append({'game_id':str(g.game_id),'date':g.dt,'players':grouped.get((g.game_id,team),[])})

    out=pd.DataFrame(rows)
    out.to_parquet(OUT,index=False)
    meta={
      'built_at':now(),'source_games':str(games_path),'rows':int(len(out)),'columns':int(len(out.columns)),
      'feature_columns':[c for c in out.columns if 'indplayer_' in c],
      'non_null':{c:int(out[c].notna().sum()) for c in out.columns if 'indplayer_' in c},
      'leakage_policy':'Every feature is computed from prior team games only. Current-match player rows are appended to history after feature capture.',
      'windows':'Recent 3/5/10 team games; player rates require >=90 minutes in last 5 and >=180 minutes in last 10 where ranked.'
    }
    META.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps({'rows':len(out),'columns':len(out.columns),'feature_count':len(meta['feature_columns']),
                      'sample_features':meta['feature_columns'][:40]},indent=2))

if __name__=='__main__':main()
