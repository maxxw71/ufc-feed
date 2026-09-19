#!/usr/bin/env python3
from __future__ import annotations

import json, math, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from mls_bootstrap_warehouse import TEAM_INFO, canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed'
RAW=ROOT/'data/raw/weather';RAW.mkdir(parents=True,exist_ok=True)
REP=ROOT/'reports'

BASE_CANDIDATES=[
    PROC/'mls_match_features_transaction_enriched.parquet',
    PROC/'mls_match_features_confirmed_lineup_enriched.parquet',
    PROC/'mls_match_features_context_enriched.parquet',
]
GAMES_CANDIDATES=[
    PROC/'asa_mls_games_2012_present.parquet',
    PROC/'asa_mls_games_2013_present.parquet',
]
OUT=PROC/'mls_match_features_weather_enriched.parquet'
REPORT=REP/'weather_feature_meta.json'

API='https://archive-api.open-meteo.com/v1/archive'
UA='Mozilla/5.0 AppwizaMLSWeather/1.0'
HOURLY=[
    'temperature_2m','relative_humidity_2m','apparent_temperature',
    'precipitation','wind_speed_10m','wind_direction_10m'
]

def now():return datetime.now(timezone.utc).isoformat()

def pick(paths):
    for p in paths:
        if p.exists():return p
    raise RuntimeError('No source found: '+', '.join(str(x) for x in paths))

def fetch_json(url,retries=4):
    last=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json'})
            return json.loads(urllib.request.urlopen(req,timeout=90).read().decode())
        except Exception as e:
            last=e;time.sleep(1.5*(i+1))
    raise last

def cache_key(team,year):
    safe=''.join(ch if ch.isalnum() else '_' for ch in team).strip('_')
    return RAW/f'{year}_{safe}.json'

def fetch_team_year(team,year,lat,lon):
    path=cache_key(team,year)
    if path.exists():
        return json.loads(path.read_text())
    params={
        'latitude':lat,'longitude':lon,
        'start_date':f'{year}-01-01','end_date':f'{year}-12-31',
        'hourly':','.join(HOURLY),
        'timezone':'GMT',
        'temperature_unit':'fahrenheit',
        'wind_speed_unit':'mph',
        'precipitation_unit':'inch',
        'models':'era5',
    }
    url=API+'?'+urllib.parse.urlencode(params)
    d=fetch_json(url)
    path.write_text(json.dumps(d,separators=(',',':')))
    return d

def table(d):
    h=d.get('hourly') or {}
    t=pd.to_datetime(h.get('time') or [],errors='coerce',utc=True)
    z=pd.DataFrame({'time':t})
    for c in HOURLY:
        vals=h.get(c) or []
        z[c]=pd.to_numeric(pd.Series(vals),errors='coerce').reindex(range(len(z))).to_numpy()
    return z[z.time.notna()].copy()

def main():
    base_path=pick(BASE_CANDIDATES)
    games_path=pick(GAMES_CANDIDATES)
    d=pd.read_parquet(base_path).copy()
    games=pd.read_parquet(games_path).copy()
    if 'asa_game_id' not in d:raise RuntimeError('asa_game_id missing')

    games['game_id']=games.game_id.astype(str)
    games['kickoff_utc']=pd.to_datetime(games.date_time_utc,errors='coerce',utc=True)
    kickoff=dict(zip(games.game_id,games.kickoff_utc))
    d['asa_game_id']=d.asa_game_id.astype('string')
    d['kickoff_utc']=d.asa_game_id.astype(str).map(kickoff)

    # Weather features are only built when venue context is trustworthy:
    # non-neutral games with a known home-team coordinate and precise ASA kickoff.
    if 'neutral' not in d:d['neutral']=False
    eligible=d[
        d.kickoff_utc.notna()&
        ~d.neutral.fillna(False).astype(bool)&
        d.home_team.map(canon_team).isin(TEAM_INFO)
    ].copy()
    eligible['weather_team']=eligible.home_team.map(canon_team)
    eligible['weather_year']=eligible.kickoff_utc.dt.year.astype(int)

    lookup={}
    failures=[]
    combos=eligible[['weather_team','weather_year']].drop_duplicates().sort_values(['weather_year','weather_team'])
    for _,r in combos.iterrows():
        team=r.weather_team;year=int(r.weather_year)
        info=TEAM_INFO.get(team)
        if not info:continue
        _,lat,lon,_,_=info
        try:
            lookup[(team,year)]=table(fetch_team_year(team,year,lat,lon))
        except Exception as e:
            failures.append({'team':team,'year':year,'error':type(e).__name__+':'+str(e)[:180]})

    rows=[]
    for _,g in d.iterrows():
        row={'match_id':g.match_id,'weather_available':0}
        ko=g.kickoff_utc
        team=canon_team(g.home_team)
        if pd.notna(ko) and not bool(g.neutral) and team in TEAM_INFO:
            z=lookup.get((team,int(ko.year)))
            if z is not None and len(z):
                delta=(z.time-ko).abs()
                i=delta.idxmin()
                if delta.loc[i] <= pd.Timedelta(hours=2):
                    r=z.loc[i]
                    row.update({
                        'weather_available':1,
                        'weather_observation_utc':r.time,
                        'weather_hour_offset':(r.time-ko).total_seconds()/3600,
                        'weather_temperature_f':r.temperature_2m,
                        'weather_humidity_pct':r.relative_humidity_2m,
                        'weather_apparent_temperature_f':r.apparent_temperature,
                        'weather_precipitation_in':r.precipitation,
                        'weather_wind_mph':r.wind_speed_10m,
                        'weather_wind_direction_deg':r.wind_direction_10m,
                    })
                    temp=float(r.temperature_2m) if pd.notna(r.temperature_2m) else np.nan
                    app=float(r.apparent_temperature) if pd.notna(r.apparent_temperature) else np.nan
                    hum=float(r.relative_humidity_2m) if pd.notna(r.relative_humidity_2m) else np.nan
                    pr=float(r.precipitation) if pd.notna(r.precipitation) else np.nan
                    wind=float(r.wind_speed_10m) if pd.notna(r.wind_speed_10m) else np.nan
                    row['weather_hot']=float(temp>=85) if pd.notna(temp) else np.nan
                    row['weather_cold']=float(temp<=40) if pd.notna(temp) else np.nan
                    row['weather_high_heat_index']=float(app>=90) if pd.notna(app) else np.nan
                    row['weather_high_humidity']=float(hum>=75) if pd.notna(hum) else np.nan
                    row['weather_wet']=float(pr>0) if pd.notna(pr) else np.nan
                    row['weather_windy']=float(wind>=15) if pd.notna(wind) else np.nan
        rows.append(row)

    f=pd.DataFrame(rows)
    out=d.drop(columns=['kickoff_utc'],errors='ignore').merge(f,on='match_id',how='left',validate='1:1')
    out.to_parquet(OUT,index=False)
    added=[c for c in out.columns if c not in d.columns and c!='kickoff_utc']
    covered=int(f.weather_available.eq(1).sum())
    seasons=sorted(int(x) for x in d.loc[f.weather_available.eq(1).values,'season'].dropna().unique())
    meta={
        'built_at':now(),'source':'Open-Meteo Historical Weather API / ERA5',
        'base_file':str(base_path),'rows':len(out),'columns':len(out.columns),
        'added_columns':len(added),'added':added,'weather_matches':covered,
        'weather_match_pct':100*covered/max(1,len(out)),'coverage_seasons':seasons,
        'fetch_failures':failures,'output':str(OUT),
        'research_only':True,
        'policy_note':'Weather is context for research only and is not an automatic veto/filter for official MLS selections.',
        'leakage_note':'Historical weather is joined to known kickoff/venue context. Weather variables describe conditions and are not used as a proxy for information unavailable before kickoff.'
    }
    REPORT.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':main()
