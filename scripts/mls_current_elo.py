#!/usr/bin/env python3
from __future__ import annotations
import csv,io,json,math,urllib.request
from datetime import datetime,timezone
from pathlib import Path

import pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis
from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'live/current';OUT.mkdir(parents=True,exist_ok=True)
UA='Appwiza-MLS-Elo-Bridge/1.0'
BASE='https://raw.githubusercontent.com/philo92/mls-elo/main/'
K=20.0

def fetch_csv(name):
    req=urllib.request.Request(BASE+name,headers={'User-Agent':UA})
    raw=urllib.request.urlopen(req,timeout=30).read().decode('utf-8-sig')
    return pd.read_csv(io.StringIO(raw))
def expected(h,a,ha,neutral=False):
    return 1/(1+10**(-((h-a)+(0 if neutral else ha))/400))
def update(h,a,hs,as_,ha,neutral=False):
    exp=expected(h,a,ha,neutral)
    act=1.0 if hs>as_ else .5 if hs==as_ else 0.0
    gd=abs(float(hs)-float(as_))
    mult=1.0 if gd<=1 else math.sqrt(gd)
    delta=K*mult*(act-exp)
    return h+delta,a-delta,delta
def main():
    teams=fetch_csv('teams.csv')
    hfa=fetch_csv('home_advantage.csv')
    published=fetch_csv('results.csv')
    ha=float(hfa.iloc[0].home_advantage)
    seed_date=pd.to_datetime(published.date,errors='coerce').max().date()
    ratings={}
    seed_dates={}
    for _,r in teams.iterrows():
        team=canon_team(r.team)
        try:elo=float(str(r.elo).replace(',',''))
        except Exception:continue
        ratings[team]=elo
        seed_dates[team]=str(r.last_updated_date)

    pub=[]
    for _,r in published[pd.to_datetime(published.date,errors='coerce').dt.year.eq(2026)].iterrows():
        pub.append({
          'date':pd.Timestamp(r.date).date(),'home':canon_team(r.home_team),'away':canon_team(r.away_team),
          'hs':float(r.home_score),'as':float(r.away_score)
        })

    asa=AmericanSoccerAnalysis()
    games=asa.get_games(leagues='mls',season_name='2026')
    t=asa.get_teams(leagues='mls')
    if not isinstance(games,pd.DataFrame):games=pd.DataFrame(games)
    if not isinstance(t,pd.DataFrame):t=pd.DataFrame(t)
    tid=next((c for c in ['team_id','id'] if c in t.columns),None)
    tname=next((c for c in ['team_name','name'] if c in t.columns),None)
    mp={str(r[tid]):canon_team(r[tname]) for _,r in t.iterrows()}
    games['home_team']=games.home_team_id.astype(str).map(mp)
    games['away_team']=games.away_team_id.astype(str).map(mp)
    games['dt']=pd.to_datetime(games.date_time_utc,errors='coerce',utc=True)
    for c in ['home_score','away_score']:games[c]=pd.to_numeric(games[c],errors='coerce')
    completed=games[games.status.astype(str).str.lower().eq('fulltime') & games.home_score.notna() & games.away_score.notna()].sort_values('dt')

    def already_published(r):
        # Published source uses local calendar dates; allow ±1 UTC-day shift and require exact teams/scores.
        d=r.dt.date()
        for q in pub:
            if q['home']==r.home_team and q['away']==r.away_team and q['hs']==float(r.home_score) and q['as']==float(r.away_score):
                if abs((q['date']-d).days)<=1:return True
        return False

    bridge=[]
    for _,r in completed.iterrows():
        if already_published(r):continue
        home,away=r.home_team,r.away_team
        if not home or not away or home not in ratings or away not in ratings:continue
        h0,a0=ratings[home],ratings[away]
        h1,a1,delta=update(h0,a0,float(r.home_score),float(r.away_score),ha,False)
        ratings[home],ratings[away]=h1,a1
        bridge.append({
          'game_id':str(r.game_id),'date_time_utc':r.dt.isoformat(),'home_team':home,'away_team':away,
          'home_score':float(r.home_score),'away_score':float(r.away_score),
          'home_elo_pre':h0,'away_elo_pre':a0,'home_elo_post':h1,'away_elo_post':a1,'home_delta':delta
        })

    latest_complete=max([pd.Timestamp(x['date_time_utc']) for x in bridge],default=pd.Timestamp(seed_date,tz='UTC'))
    payload={
      'built_at':datetime.now(timezone.utc).isoformat(),
      'method':'philo92 compatible bridge: 400-point Elo, HA from source, K=20, sqrt(goal_diff) multiplier',
      'seed_results_through':str(seed_date),'home_advantage':ha,
      'bridge_games':len(bridge),'bridged_through':latest_complete.isoformat(),
      'source_seed_dates':seed_dates,
      'ratings':dict(sorted(ratings.items())),
      'bridge':bridge,
      'freshness_days':(pd.Timestamp(datetime.now(timezone.utc))-latest_complete).total_seconds()/86400,
    }
    (OUT/'elo_current.json').write_text(json.dumps(payload,indent=2,default=str))
    pd.DataFrame(bridge).to_csv(OUT/'elo_bridge_games.csv',index=False)
    print(json.dumps({k:payload[k] for k in ['built_at','seed_results_through','home_advantage','bridge_games','bridged_through','freshness_days']},indent=2))
    for x in bridge:print(x['date_time_utc'],x['home_team'],x['home_score'],x['away_score'],x['away_team'],round(x['home_elo_post'],2),round(x['away_elo_post'],2))
if __name__=='__main__':main()
