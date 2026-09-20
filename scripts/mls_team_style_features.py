#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict,deque
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd

from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed';REP=ROOT/'reports'
BASE=PROC/'mls_match_features_weather_enriched.parquet'
TEAMS=PROC/'asa_teams.parquet'
XG=PROC/'asa_player_xg_game_2013_present.parquet'
XP=PROC/'asa_player_xpass_game_2013_present.parquet'
GP=PROC/'asa_player_gplus_game_2013_present.parquet'
OUT=PROC/'mls_team_style_features.parquet'
META=REP/'team_style_feature_meta.json'

def now():return datetime.now(timezone.utc).isoformat()

def safe_num(s):
    return pd.to_numeric(s,errors='coerce').fillna(0.0)

def game_style(z):
    if z.empty:return None
    mins=safe_num(z.minutes_played) if 'minutes_played' in z else pd.Series(0,index=z.index,dtype=float)
    active=z[mins.gt(0)].copy()
    if active.empty:return None
    def total(c):return float(safe_num(active[c]).sum()) if c in active else 0.0
    xgi=(safe_num(active.get('xgoals',0))+safe_num(active.get('xassists',0))) if 'xgoals' in active and 'xassists' in active else safe_num(active.get('xgoals_plus_xassists',0))
    touches=safe_num(active.get('share_team_touches',0))
    gpraw=safe_num(active.get('gplus_raw',0))
    posxgi=xgi.clip(lower=0)
    posgp=gpraw.clip(lower=0)
    def top_share(v,n):
        den=float(v.sum())
        return float(v.nlargest(n).sum()/den) if den>0 else np.nan
    touchsum=float(touches.sum())
    touch_probs=touches/touchsum if touchsum>0 else touches
    return {
      'pass_attempts':total('attempted_passes'),
      'pass_completed_over_expected':total('passes_completed_over_expected'),
      'xg':total('xgoals'),'xa':total('xassists'),'xgi':float(xgi.sum()),
      'key_passes':total('key_passes'),
      'gplus_raw':total('gplus_raw'),'gplus_passing':total('gplus_passing'),
      'gplus_receiving':total('gplus_receiving'),'gplus_shooting':total('gplus_shooting'),
      'gplus_interrupting':total('gplus_interrupting'),
      'top3_xgi_share':top_share(posxgi,3),'top3_gplus_share':top_share(posgp,3),
      'top3_touch_share':top_share(touches,3),
      'touch_hhi':float((touch_probs**2).sum()) if touchsum>0 else np.nan,
      'active_players':int(active.player_id.astype(str).nunique()),
    }

def summarize(hist,n):
    h=list(hist)[-n:]
    if len(h)<n:return {}
    keys=[k for k in h[0].keys() if k not in {'game_id','dt'}]
    out={}
    for k in keys:
        vals=[x.get(k) for x in h]
        vals=[float(x) for x in vals if x is not None and pd.notna(x)]
        out[k]=float(np.mean(vals)) if vals else np.nan
    return out

def main():
    for p in [BASE,TEAMS,XG,XP,GP]:
        if not p.exists():raise RuntimeError(f'Missing {p}')
    base=pd.read_parquet(BASE).copy()
    base['dt']=pd.to_datetime(base.date,errors='coerce',utc=True)
    teams=pd.read_parquet(TEAMS)
    tmap={str(r.team_id):canon_team(r.team_name) for _,r in teams.iterrows()}

    xg=pd.read_parquet(XG).copy();xp=pd.read_parquet(XP).copy();gp=pd.read_parquet(GP).copy()
    keys=['player_id','game_id','team_id']
    for z in [xg,xp,gp]:
        for k in keys:z[k]=z[k].astype(str)
        if z[keys].duplicated().any():raise RuntimeError('Duplicate player/game/team rows in ASA style source')

    xcols=[c for c in keys+['minutes_played','xgoals','xassists','xgoals_plus_xassists','key_passes'] if c in xg.columns]
    pcols=[c for c in keys+['attempted_passes','passes_completed_over_expected','share_team_touches'] if c in xp.columns]
    gcols=[c for c in keys+['gplus_raw','gplus_passing','gplus_receiving','gplus_shooting','gplus_interrupting'] if c in gp.columns]
    pg=xg[xcols].merge(xp[pcols],on=keys,how='outer',validate='1:1').merge(gp[gcols],on=keys,how='outer',validate='1:1')
    pg['team']=pg.team_id.map(tmap)
    pg=pg[pg.team.notna()].copy()
    by_game_team={(str(gid),team):game_style(z) for (gid,team),z in pg.groupby(['game_id','team'])}

    hist=defaultdict(lambda:deque(maxlen=12))
    rows=[]
    for _,g in base[base.dt.notna()].sort_values(['dt','match_id']).iterrows():
        row={'match_id':g.match_id}
        for side in ['home','away']:
            team=canon_team(g[f'{side}_team'])
            h=hist[team]
            for n in [3,5,10]:
                sm=summarize(h,n)
                for k,v in sm.items():row[f'{side}_style_{k}_{n}']=v
            # style trend: last3 minus preceding5 (games -8 through -4)
            if len(h)>=8:
                recent=summarize(h,3)
                prev=list(h)[-8:-3]
                pkeys=[k for k in prev[0].keys() if k not in {'game_id','dt'}]
                for k in pkeys:
                    vals=[float(x[k]) for x in prev if x.get(k) is not None and pd.notna(x.get(k))]
                    pv=float(np.mean(vals)) if vals else np.nan
                    rv=recent.get(k,np.nan)
                    row[f'{side}_style_delta3_prev5_{k}']=(rv-pv) if pd.notna(rv) and pd.notna(pv) else np.nan
            row[f'{side}_style_prior_games']=len(h)

        # edges for every paired home/away style field
        hkeys=[k for k in row if k.startswith('home_style_')]
        for hk in hkeys:
            suffix=hk[len('home_'):]
            ak='away_'+suffix
            if ak in row:
                hv=row[hk];av=row[ak]
                row['edge_'+suffix]=(hv-av) if pd.notna(hv) and pd.notna(av) else np.nan
        rows.append(row)

        gid=str(g.get('asa_game_id')) if pd.notna(g.get('asa_game_id')) else None
        if gid:
            for side in ['home','away']:
                team=canon_team(g[f'{side}_team'])
                st=by_game_team.get((gid,team))
                if st:
                    hist[team].append({'game_id':gid,'dt':g.dt,**st})

    feat=pd.DataFrame(rows);feat.to_parquet(OUT,index=False)
    meta={
      'built_at':now(),'rows':len(feat),'columns':len(feat.columns),'output':str(OUT),
      'coverage_any_style':int((feat.home_style_prior_games.gt(0)|feat.away_style_prior_games.gt(0)).sum()),
      'coverage_both_style5':int((feat.home_style_prior_games.ge(5)&feat.away_style_prior_games.ge(5)).sum()),
      'feature_columns':[c for c in feat.columns if c!='match_id'],
      'leakage_note':'Target-match player data never enters target-match style features. Each team style state is read before the match and updated only afterward from that completed match.',
      'source_note':'Derived from ASA player xG/xPass/Goals Added game-level data already present in the warehouse.'
    }
    META.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':main()
