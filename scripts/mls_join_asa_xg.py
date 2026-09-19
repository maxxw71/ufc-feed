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
    asa=AmericanSoccerAnalysis()
    teams=asa.get_teams(leagues='mls')
    if not isinstance(teams,pd.DataFrame):teams=pd.DataFrame(teams)
    tid=next((c for c in ['team_id','id'] if c in teams.columns),None)
    tname=next((c for c in ['team_name','name'] if c in teams.columns),None)
    if not tid or not tname:raise RuntimeError('ASA teams schema not recognized: '+repr(list(teams.columns)))
    mp={str(r[tid]):canon_team(r[tname]) for _,r in teams.iterrows()}
    x['home_team']=x.home_team_id.astype(str).map(mp)
    x['away_team']=x.away_team_id.astype(str).map(mp)
    x['xg_date']=pd.to_datetime(x.date_time_utc,errors='coerce',utc=True).dt.tz_convert(None).dt.normalize()
    x['home_goals']=pd.to_numeric(x.home_goals,errors='coerce');x['away_goals']=pd.to_numeric(x.away_goals,errors='coerce')
    for c in ['home_team_xgoals','away_team_xgoals','home_xpoints','away_xpoints']:x[c]=pd.to_numeric(x[c],errors='coerce')
    x=x[x.home_team.notna()&x.away_team.notna()&x.xg_date.notna()].copy()
    # candidate lookup by team pair; accept date +/-1 because UTC can cross local midnight.
    lookup=defaultdict(list)
    for _,r in x.iterrows():lookup[(r.home_team,r.away_team)].append(r)
    current={}
    matched=0
    for i,r in d.iterrows():
        if int(r.season)<2013:continue
        cand=lookup.get((r.home_team,r.away_team),[])
        cand=[q for q in cand if abs((pd.Timestamp(q.xg_date)-pd.Timestamp(r.date).normalize()).days)<=1
              and (pd.isna(q.home_goals) or float(q.home_goals)==float(r.home_score))
              and (pd.isna(q.away_goals) or float(q.away_goals)==float(r.away_score))]
        if len(cand)==1:
            q=cand[0];current[i]=q;matched+=1
    hist=defaultdict(lambda:deque(maxlen=15));season=defaultdict(lambda:[0,0.0,0.0])
    cols={}
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
        q=current.get(i)
        cols.setdefault('asa_xg_current_match_available',{})[i]=bool(q is not None)
        if q is not None:
            hx=float(q.home_team_xgoals);ax=float(q.away_team_xgoals)
            hist[r.home_team].append((hx,ax));hist[r.away_team].append((ax,hx))
            sh=season[(int(r.season),r.home_team)];sh[0]+=1;sh[1]+=hx;sh[2]+=ax
            sa=season[(int(r.season),r.away_team)];sa[0]+=1;sa[1]+=ax;sa[2]+=hx
    for c,m in cols.items():d[c]=pd.Series(m)
    for base in ['last3_xgdpg','last5_xgdpg','last10_xgdpg','season_xgdpg','last5_xgfpg','last5_xgapg']:
        d['edge_home_'+base]=d['home_'+base]-d['away_'+base]
    d.to_parquet(OUT,index=False)
    cov=d[d.season>=2013].groupby('season').agg(matches=('match_id','size'),xg_matched=('asa_xg_current_match_available','sum')).reset_index()
    cov['coverage_pct']=100*cov.xg_matched/cov.matches
    cov.to_csv(REPORTS/'asa_join_coverage.csv',index=False)
    meta={'feature_rows':len(d),'feature_columns':len(d.columns),'asa_xg_rows':len(x),'matched_matches':matched,
          'coverage_2013_2026_pct':100*matched/max(1,len(d[d.season>=2013])),
          'leakage_note':'Current-match ASA xG is used only after pregame rolling features are captured; current-match xG columns are not exposed as research predictors.'}
    (REPORTS/'asa_join_meta.json').write_text(json.dumps(meta,indent=2))
    print(json.dumps(meta,indent=2));print(cov.to_string(index=False))
if __name__=='__main__':main()
