#!/usr/bin/env python3
from __future__ import annotations
import json
from collections import defaultdict,deque
from pathlib import Path
import numpy as np,pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis
from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
BASE=ROOT/'data/processed/mls_match_features_1996_present.parquet'
XG=ROOT/'data/processed/asa_mls_game_xgoals_2013_present.parquet'
GAMES_NEW=ROOT/'data/processed/asa_mls_games_2012_present.parquet'
GAMES_OLD=ROOT/'data/processed/asa_mls_games_2013_present.parquet'
OUT=ROOT/'data/processed/mls_match_features_advanced.parquet'
REPORTS=ROOT/'reports'

def rm(hist,n):
    v=list(hist)[-n:]
    if not v:return (np.nan,np.nan,np.nan)
    xgf=sum(x[0] for x in v)/len(v);xga=sum(x[1] for x in v)/len(v)
    return xgf,xga,xgf-xga

def main():
    d=pd.read_parquet(BASE).sort_values(['date','match_id']).reset_index(drop=True)
    x=pd.read_parquet(XG).copy()
    gp=GAMES_NEW if GAMES_NEW.exists() else GAMES_OLD
    if not gp.exists():raise RuntimeError('ASA games metadata file missing')
    g=pd.read_parquet(gp).copy()

    asa=AmericanSoccerAnalysis()
    teams=asa.get_teams(leagues='mls')
    if not isinstance(teams,pd.DataFrame):teams=pd.DataFrame(teams)
    tid=next((c for c in ['team_id','id'] if c in teams.columns),None)
    tname=next((c for c in ['team_name','name'] if c in teams.columns),None)
    if not tid or not tname:raise RuntimeError('ASA teams schema not recognized: '+repr(list(teams.columns)))
    mp={str(r[tid]):canon_team(r[tname]) for _,r in teams.iterrows()}

    for z in [g,x]:
        z['home_team']=z.home_team_id.astype(str).map(mp)
        z['away_team']=z.away_team_id.astype(str).map(mp)

    g['asa_date']=pd.to_datetime(g.date_time_utc,errors='coerce',utc=True).dt.tz_convert(None).dt.normalize()
    for c in ['home_score','away_score']:
        if c in g:g[c]=pd.to_numeric(g[c],errors='coerce')
    g=g[g.home_team.notna()&g.away_team.notna()&g.asa_date.notna()].copy()

    x['xg_date']=pd.to_datetime(x.date_time_utc,errors='coerce',utc=True).dt.tz_convert(None).dt.normalize()
    for c in ['home_goals','away_goals','home_team_xgoals','away_team_xgoals','home_xpoints','away_xpoints']:
        if c in x:x[c]=pd.to_numeric(x[c],errors='coerce')
    x=x[x.home_team.notna()&x.away_team.notna()&x.xg_date.notna()].copy()
    x_by_game={str(r.game_id):r for _,r in x.iterrows()}

    # Match our historical row to an ASA game using canonical teams and date.
    # If date +/-1 yields a unique game, accept it; score is only a disambiguator.
    lookup=defaultdict(list)
    for _,r in g.iterrows():lookup[(r.home_team,r.away_team)].append(r)

    game_current={};game_matched=0;game_unmatched=0;game_ambiguous=0
    for i,r in d.iterrows():
        if int(r.season)<2012:continue
        cand=lookup.get((r.home_team,r.away_team),[])
        cand=[q for q in cand if abs((pd.Timestamp(q.asa_date)-pd.Timestamp(r.date).normalize()).days)<=1]
        if len(cand)>1:
            score=[q for q in cand if
                   (pd.isna(q.get('home_score')) or float(q.home_score)==float(r.home_score)) and
                   (pd.isna(q.get('away_score')) or float(q.away_score)==float(r.away_score))]
            if len(score)==1:cand=score
        if len(cand)==1:
            game_current[i]=cand[0];game_matched+=1
        elif len(cand)>1:
            game_ambiguous+=1
        else:
            game_unmatched+=1

    hist=defaultdict(lambda:deque(maxlen=15));season=defaultdict(lambda:[0,0.0,0.0])
    cols={}
    xg_matched=0
    for i,r in d.iterrows():
        for side,team in [('home',r.home_team),('away',r.away_team)]:
            for n in [3,5,10]:
                a,b,c=rm(hist[team],n)
                cols.setdefault(f'{side}_last{n}_xgfpg',{})[i]=a
                cols.setdefault(f'{side}_last{n}_xgapg',{})[i]=b
                cols.setdefault(f'{side}_last{n}_xgdpg',{})[i]=c
            st=season[(int(r.season),team)]
            cols.setdefault(f'{side}_season_xgfpg',{})[i]=st[1]/st[0] if st[0] else np.nan
            cols.setdefault(f'{side}_season_xgapg',{})[i]=st[2]/st[0] if st[0] else np.nan
            cols.setdefault(f'{side}_season_xgdpg',{})[i]=(st[1]-st[2])/st[0] if st[0] else np.nan

        ag=game_current.get(i)
        cols.setdefault('asa_game_available',{})[i]=bool(ag is not None)
        cols.setdefault('asa_game_id',{})[i]=None if ag is None else str(ag.game_id)
        cols.setdefault('asa_knockout_game',{})[i]=pd.NA if ag is None else bool(ag.get('knockout_game'))
        cols.setdefault('asa_matchday',{})[i]=np.nan if ag is None else ag.get('matchday')
        cols.setdefault('asa_status',{})[i]=None if ag is None else ag.get('status')
        cols.setdefault('asa_home_manager_id',{})[i]=None if ag is None else ag.get('home_manager_id')
        cols.setdefault('asa_away_manager_id',{})[i]=None if ag is None else ag.get('away_manager_id')

        q=x_by_game.get(str(ag.game_id)) if ag is not None else None
        cols.setdefault('asa_xg_current_match_available',{})[i]=bool(q is not None)
        if q is not None and pd.notna(q.home_team_xgoals) and pd.notna(q.away_team_xgoals):
            xg_matched+=1
            hx=float(q.home_team_xgoals);ax=float(q.away_team_xgoals)
            hist[r.home_team].append((hx,ax));hist[r.away_team].append((ax,hx))
            sh=season[(int(r.season),r.home_team)];sh[0]+=1;sh[1]+=hx;sh[2]+=ax
            sa=season[(int(r.season),r.away_team)];sa[0]+=1;sa[1]+=ax;sa[2]+=hx

    for c,m in cols.items():d[c]=pd.Series(m)
    for base in ['last3_xgdpg','last5_xgdpg','last10_xgdpg','season_xgdpg','last5_xgfpg','last5_xgapg']:
        d['edge_home_'+base]=d['home_'+base]-d['away_'+base]

    d.to_parquet(OUT,index=False)

    cov=d[d.season>=2012].groupby('season').agg(
        matches=('match_id','size'),
        game_matched=('asa_game_available','sum'),
        xg_matched=('asa_xg_current_match_available','sum')
    ).reset_index()
    cov['game_coverage_pct']=100*cov.game_matched/cov.matches
    cov['xg_coverage_pct']=100*cov.xg_matched/cov.matches
    reg=d[(d.season>=2012)&d.asa_game_available.eq(True)&d.asa_knockout_game.eq(False)]
    ko=d[(d.season>=2012)&d.asa_game_available.eq(True)&d.asa_knockout_game.eq(True)]
    cov.to_csv(REPORTS/'asa_join_coverage.csv',index=False)
    meta={
      'feature_rows':len(d),'feature_columns':len(d.columns),'asa_games_rows':len(g),'asa_xg_rows':len(x),
      'game_matched_matches':game_matched,'game_unmatched_matches':game_unmatched,'game_ambiguous_matches':game_ambiguous,
      'xg_matched_matches':xg_matched,
      'game_coverage_2012_present_pct':100*game_matched/max(1,len(d[d.season>=2012])),
      'xg_coverage_2012_present_pct':100*xg_matched/max(1,len(d[d.season>=2012])),
      'regular_season_tagged_rows':len(reg),'knockout_tagged_rows':len(ko),
      'leakage_note':'ASA current-match xG is used only after pregame rolling features are captured. knockout_game is metadata used to define the research universe, not an outcome-derived predictor.'
    }
    (REPORTS/'asa_join_meta.json').write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2));print(cov.to_string(index=False))

if __name__=='__main__':main()
