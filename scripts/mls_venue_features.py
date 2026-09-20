#!/usr/bin/env python3
from __future__ import annotations

import json,math
from collections import Counter,defaultdict,deque
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis

from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed';REP=ROOT/'reports'
BASE=PROC/'mls_match_features_weather_enriched.parquet'
GAMES=PROC/'asa_mls_games_2012_present.parquet'
OUT=PROC/'mls_venue_features.parquet'
STADIA=PROC/'asa_stadia.parquet'
META=REP/'venue_feature_meta.json'

def now():return datetime.now(timezone.utc).isoformat()
def first_col(cols,cands):
    low={str(c).lower():c for c in cols}
    for x in cands:
        if x.lower() in low:return low[x.lower()]
    for c in cols:
        n=str(c).lower()
        if any(x.lower() in n for x in cands):return c
    return None
def hav(lat1,lon1,lat2,lon2):
    if any(pd.isna(x) for x in [lat1,lon1,lat2,lon2]):return np.nan
    r=3958.7613
    a1,a2=math.radians(float(lat1)),math.radians(float(lat2))
    dp=math.radians(float(lat2)-float(lat1));dl=math.radians(float(lon2)-float(lon1))
    a=math.sin(dp/2)**2+math.cos(a1)*math.cos(a2)*math.sin(dl/2)**2
    return 2*r*math.asin(min(1,math.sqrt(a)))

def main():
    if not BASE.exists():raise RuntimeError('MLS base missing')
    if not GAMES.exists():raise RuntimeError('ASA games warehouse missing')
    asa=AmericanSoccerAnalysis()
    stad=asa.get_stadia(leagues='mls')
    if not isinstance(stad,pd.DataFrame):stad=pd.DataFrame(stad)
    if stad.empty:raise RuntimeError('ASA stadia returned zero rows')
    stad.to_parquet(STADIA,index=False)

    sid=first_col(stad.columns,['stadium_id','id'])
    sname=first_col(stad.columns,['stadium_name','name'])
    cap=first_col(stad.columns,['capacity'])
    lat=first_col(stad.columns,['latitude','lat'])
    lon=first_col(stad.columns,['longitude','lon','lng'])
    surface=first_col(stad.columns,['surface','field_type','turf'])
    if not sid:raise RuntimeError(f'No stadium id column: {list(stad.columns)}')
    smeta={}
    for _,r in stad.iterrows():
        k=str(r[sid])
        smeta[k]={
          'name':None if not sname else r[sname],
          'capacity':np.nan if not cap else pd.to_numeric(pd.Series([r[cap]]),errors='coerce').iloc[0],
          'lat':np.nan if not lat else pd.to_numeric(pd.Series([r[lat]]),errors='coerce').iloc[0],
          'lon':np.nan if not lon else pd.to_numeric(pd.Series([r[lon]]),errors='coerce').iloc[0],
          'surface':None if not surface else r[surface],
        }

    games=pd.read_parquet(GAMES).copy()
    games['game_id']=games.game_id.astype(str);games['stadium_id']=games.stadium_id.astype('string')
    gstad=dict(zip(games.game_id,games.stadium_id))
    base=pd.read_parquet(BASE).copy();base['dt']=pd.to_datetime(base.date,errors='coerce',utc=True)

    team_hist=defaultdict(lambda:deque(maxlen=30));home_hist=defaultdict(lambda:deque(maxlen=20))
    rows=[]
    for _,g in base[base.dt.notna()].sort_values(['dt','match_id']).iterrows():
        gid=str(g.asa_game_id) if pd.notna(g.get('asa_game_id')) else None
        stid=str(gstad.get(gid)) if gid and pd.notna(gstad.get(gid)) else None
        meta=smeta.get(stid,{})
        row={'match_id':g.match_id,'venue_data_available':int(bool(stid)),
             'stadium_id':stid,'stadium_name':meta.get('name'),'stadium_capacity':meta.get('capacity',np.nan),
             'stadium_latitude':meta.get('lat',np.nan),'stadium_longitude':meta.get('lon',np.nan),
             'stadium_surface':meta.get('surface')}
        for side in ['home','away']:
            team=canon_team(g[f'{side}_team']);h=list(team_hist[team]);hh=list(home_hist[team])
            row[f'{side}_prior_matches_at_stadium20']=sum(1 for x in h[-20:] if x['stadium_id']==stid) if stid else np.nan
            row[f'{side}_stadium_familiarity20']=(row[f'{side}_prior_matches_at_stadium20']/min(20,len(h))) if stid and h else np.nan
            prev=h[-1] if h else None
            row[f'{side}_travel_from_prev_match_miles']=hav(prev['lat'],prev['lon'],meta.get('lat'),meta.get('lon')) if prev else np.nan
            if side=='home':
                recent_home=hh[-10:]
                modal=Counter(x['stadium_id'] for x in recent_home if x['stadium_id']).most_common(1)
                modal_id=modal[0][0] if modal else None
                row['home_primary_stadium_prior10']=modal_id
                row['home_venue_switch_from_modal10']=int(bool(stid and modal_id and stid!=modal_id)) if modal_id else np.nan
                row['home_same_stadium_last5_rate']=float(np.mean([x['stadium_id']==stid for x in recent_home[-5:]])) if stid and recent_home else np.nan
        h=row.get('home_stadium_familiarity20');a=row.get('away_stadium_familiarity20')
        row['edge_stadium_familiarity20']=(h-a) if pd.notna(h) and pd.notna(a) else np.nan
        rows.append(row)
        if stid:
            rec={'dt':g.dt,'stadium_id':stid,'lat':meta.get('lat',np.nan),'lon':meta.get('lon',np.nan)}
            for side in ['home','away']:
                team=canon_team(g[f'{side}_team']);team_hist[team].append(rec)
            home_hist[canon_team(g.home_team)].append(rec)

    feat=pd.DataFrame(rows);feat.to_parquet(OUT,index=False)
    meta={
      'built_at':now(),'rows':len(feat),'columns':len(feat.columns),'output':str(OUT),
      'stadia_rows':len(stad),'stadia_columns':list(stad.columns),'resolved_schema':{
        'id':sid,'name':sname,'capacity':cap,'latitude':lat,'longitude':lon,'surface':surface},
      'matches_with_stadium':int(feat.venue_data_available.sum()),
      'matches_with_coordinates':int((feat.stadium_latitude.notna()&feat.stadium_longitude.notna()).sum()),
      'matches_with_surface':int(feat.stadium_surface.notna().sum()),
      'leakage_note':'Target stadium identity is a scheduled pregame attribute. Familiarity and travel features use only each team’s completed prior-match venue state.',
      'surface_note':'Surface is used only if exposed by the ASA stadium entity source; otherwise it remains unknown rather than inferred.'
    }
    META.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':main()
