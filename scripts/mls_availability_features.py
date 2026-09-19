#!/usr/bin/env python3
from __future__ import annotations

import json,re,unicodedata
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz,process

from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed'
ARC=ROOT/'data/raw/availability_archive/availability_archive_2024_2025.parquet'
BASE=PROC/'mls_match_features_player_enriched.parquet'
ADV=PROC/'mls_match_features_advanced.parquet'
XG=PROC/'asa_player_xg_game_2013_present.parquet'
GP=PROC/'asa_player_gplus_game_2013_present.parquet'
PLAYERS=PROC/'asa_players.parquet'
TEAMS=PROC/'asa_teams.parquet'
OUT_FEATURES=PROC/'mls_availability_pregame_features.parquet'
OUT_WAREHOUSE=PROC/'mls_match_features_availability_enriched.parquet'
REPORT=ROOT/'reports/availability_feature_meta.json'

STATUS_WEIGHT={'OUT':1.0,'QUESTIONABLE':0.5}

def now():
    return datetime.now(timezone.utc).isoformat()

def norm(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    x=re.sub(r'[^a-z0-9]+',' ',x)
    return re.sub(r'\s+',' ',x).strip()

def reason_category(reason):
    n=norm(reason)
    if not n:return 'unspecified'
    if 'suspend' in n or 'red card' in n or 'yellow card' in n:return 'suspension'
    if 'international' in n:return 'international_duty'
    if 'not due to injury' in n:return 'non_injury'
    if 'illness' in n:return 'illness'
    if 'concussion' in n or 'head injury evaluation' in n:return 'concussion'
    return 'injury'

def safe_div(num,den):
    return float(num/den) if den and np.isfinite(den) else 0.0

def main():
    if not ARC.exists():raise RuntimeError('MLS availability archive missing')
    base_path=BASE if BASE.exists() else ADV
    if not base_path.exists():raise RuntimeError('MLS feature warehouse missing')

    base=pd.read_parquet(base_path).copy()
    arc=pd.read_parquet(ARC).copy()
    xg=pd.read_parquet(XG).copy()
    gp=pd.read_parquet(GP).copy()
    players=pd.read_parquet(PLAYERS).copy()
    teams=pd.read_parquet(TEAMS).copy()

    base['date']=pd.to_datetime(base['date'],errors='coerce',utc=True)
    base['asa_game_id']=base['asa_game_id'].astype('string')
    arc['year']=pd.to_numeric(arc['year'],errors='coerce').astype('Int64')
    arc['matchday']=pd.to_numeric(arc['matchday'],errors='coerce').astype('Int64')
    arc['team']=arc['team_reported'].map(canon_team)
    arc['status']=arc['status'].astype(str).str.upper()
    arc['reason_category']=arc['reason'].map(reason_category)

    team_map={str(r.team_id):canon_team(r.team_name) for _,r in teams.iterrows()}
    pinfo=players.drop_duplicates('player_id',keep='last').copy()
    by_pid={str(r.player_id):r for _,r in pinfo.iterrows()}
    exact=defaultdict(list)
    for _,r in pinfo.iterrows():exact[norm(r.player_name)].append(str(r.player_id))

    # Map ASA game IDs to warehouse pregame dates. Player-game rows without a
    # warehouse game are irrelevant to this historical match feature build.
    game_meta=base[base.asa_game_id.notna()][['asa_game_id','date','season','home_team','away_team']].drop_duplicates('asa_game_id')
    game_date=dict(zip(game_meta.asa_game_id.astype(str),game_meta.date))
    game_season=dict(zip(game_meta.asa_game_id.astype(str),game_meta.season))

    xg['game_id']=xg.game_id.astype(str);xg['player_id']=xg.player_id.astype(str);xg['team_id']=xg.team_id.astype(str)
    gp['game_id']=gp.game_id.astype(str);gp['player_id']=gp.player_id.astype(str);gp['team_id']=gp.team_id.astype(str)
    keys=['player_id','game_id','team_id']
    xcols=[c for c in ['player_id','game_id','team_id','general_position','minutes_played','xgoals_plus_xassists'] if c in xg.columns]
    gcols=[c for c in ['player_id','game_id','team_id','gplus_raw'] if c in gp.columns]
    pg=xg[xcols].merge(gp[gcols],on=keys,how='left')
    pg['team']=pg.team_id.map(team_map)
    pg['date']=pg.game_id.map(game_date)
    pg['season']=pg.game_id.map(game_season)
    pg=pg[pg.date.notna()&pg.team.notna()].copy()
    for c in ['minutes_played','xgoals_plus_xassists','gplus_raw']:
        if c not in pg:pg[c]=0.0
        pg[c]=pd.to_numeric(pg[c],errors='coerce').fillna(0.0)
    pg['gplus_positive']=pg.gplus_raw.clip(lower=0.0)

    by_team_game={(team,str(gid)):z.copy() for (team,gid),z in pg.groupby(['team','game_id'])}

    # Team schedule is derived strictly from the feature warehouse.
    team_games=defaultdict(list)
    game_sides={}
    for _,g in game_meta.sort_values('date').iterrows():
        gid=str(g.asa_game_id)
        for side in ['home','away']:
            team=canon_team(g[f'{side}_team'])
            team_games[team].append((g.date,gid))
            game_sides[(gid,team)]=side

    prior5={}
    for team,games in team_games.items():
        hist=[]
        for dt,gid in sorted(games):
            prior5[(team,gid)]=hist[-5:].copy()
            hist.append((dt,gid))

    def player_value(team,gid):
        prev=prior5.get((team,gid),[])
        gids=[x[1] for x in prev]
        frames=[by_team_game.get((team,x)) for x in gids]
        frames=[x for x in frames if x is not None and len(x)]
        if not frames:
            return pd.DataFrame(),{'minutes':0.0,'xgi':0.0,'gplus_positive':0.0}
        z=pd.concat(frames,ignore_index=True,sort=False)
        agg=z.groupby('player_id',as_index=False).agg(
            minutes=('minutes_played','sum'),
            xgi=('xgoals_plus_xassists','sum'),
            gplus_positive=('gplus_positive','sum'),
            position=('general_position',lambda s: next((str(x) for x in s if pd.notna(x)),None)),
        )
        totals={'minutes':float(agg.minutes.sum()),'xgi':float(agg.xgi.sum()),'gplus_positive':float(agg.gplus_positive.sum())}
        return agg,totals

    value_cache={}
    def get_values(team,gid):
        key=(team,gid)
        if key not in value_cache:value_cache[key]=player_value(team,gid)
        return value_cache[key]

    def resolve(name,team,gid,vals):
        ids=exact.get(norm(name),[])
        if len(ids)==1:return ids[0],'exact',100.0
        prior_ids=set(vals.player_id.astype(str)) if len(vals) else set()
        same=[pid for pid in ids if pid in prior_ids]
        if len(same)==1:return same[0],'exact_prior_team',100.0
        if prior_ids:
            choices={pid:str(getattr(by_pid.get(pid),'player_name',pid)) for pid in prior_ids if pid in by_pid}
            ranked=process.extract(name,choices,scorer=fuzz.WRatio,limit=2)
            if ranked:
                _,score,pid=ranked[0]
                second=ranked[1][1] if len(ranked)>1 else 0.0
                if score>=80 and score-second>=8:return str(pid),'fuzzy_prior_team',float(score)
        return None,'unresolved',None

    # Quality-gate reports against actual warehouse matchday teams. This drops
    # malformed pages (notably 2024 MD19) rather than turning parse failures into zeros.
    md_numeric=pd.to_numeric(base['asa_matchday'],errors='coerce')
    valid_reports=[];rejected_reports=[]
    report_rows={}
    report_quality={}
    for (year,md,url),r in arc.groupby(['year','matchday','url'],dropna=False):
        if pd.isna(year) or pd.isna(md):continue
        year=int(year);md=int(md)
        matches=base[(base.season.eq(year))&md_numeric.eq(md)&base.asa_game_id.notna()].copy()
        match_teams=set(matches.home_team.map(canon_team))|set(matches.away_team.map(canon_team))
        report_teams=set(r.team.dropna())
        overlap=len(match_teams&report_teams)
        quality=overlap/max(1,len(match_teams))
        key=(year,md)
        report_quality[key]=quality
        rec={'year':year,'matchday':md,'url':str(url),'matches':len(matches),'match_teams':len(match_teams),
             'reported_status_teams':len(report_teams),'overlap':overlap,'quality':quality}
        if len(matches)>0 and quality>=0.75:
            valid_reports.append(rec);report_rows[key]=r.copy()
        else:rejected_reports.append(rec)

    feature_rows=[];match_count=0;side_count=0;inferred_clear=0;explicit_status=0;resolved=0;unresolved=0
    for (year,md),r in sorted(report_rows.items()):
        matches=base[(base.season.eq(year))&md_numeric.eq(md)&base.asa_game_id.notna()].copy()
        quality=report_quality[(year,md)]
        by_team={canon_team(t):z.copy() for t,z in r.groupby('team')}
        for _,g in matches.iterrows():
            gid=str(g.asa_game_id);row={'match_id':g.match_id,'asa_game_id':gid,'season':int(year),'asa_matchday':int(md),
                                       'availability_report_quality':quality}
            match_count+=1
            for side in ['home','away']:
                team=canon_team(g[f'{side}_team']);entries=by_team.get(team)
                vals,tot=get_values(team,gid)
                prefix=f'{side}_availability_'
                known=entries is not None and len(entries)>0
                if known:explicit_status+=1
                else:inferred_clear+=1
                side_count+=1
                feats={
                    'report_available':1.0,'explicit_status_rows':float(len(entries) if known else 0),
                    'inferred_clear':0.0 if known else 1.0,'out_count':0.0,'questionable_count':0.0,
                    'injury_out_count':0.0,'non_injury_out_count':0.0,'suspension_out_count':0.0,
                    'international_duty_out_count':0.0,'illness_out_count':0.0,'concussion_out_count':0.0,
                    'weighted_missing_minutes_share5':0.0,'weighted_missing_xgi_share5':0.0,
                    'weighted_missing_gplus_share5':0.0,'missing_top11_minutes_count5':0.0,
                    'missing_top3_xgi_count5':0.0,'gk_out':0.0,'resolved_players':0.0,'unresolved_players':0.0,
                }
                if known:
                    top11=set(vals.nlargest(11,'minutes').player_id.astype(str)) if len(vals) else set()
                    top3x=set(vals.nlargest(3,'xgi').player_id.astype(str)) if len(vals) else set()
                    vindex=vals.set_index(vals.player_id.astype(str)) if len(vals) else pd.DataFrame()
                    for _,e in entries.iterrows():
                        status=str(e.status).upper();w=STATUS_WEIGHT.get(status,0.0)
                        if status=='OUT':
                            feats['out_count']+=1
                            cat=str(e.reason_category)
                            k={'injury':'injury_out_count','non_injury':'non_injury_out_count','suspension':'suspension_out_count',
                               'international_duty':'international_duty_out_count','illness':'illness_out_count',
                               'concussion':'concussion_out_count'}.get(cat)
                            if k:feats[k]+=1
                        elif status=='QUESTIONABLE':feats['questionable_count']+=1
                        pid,method,score=resolve(e.player_name,team,gid,vals)
                        if pid is None or not len(vals) or pid not in set(vals.player_id.astype(str)):
                            feats['unresolved_players']+=1;unresolved+=1;continue
                        resolved+=1;feats['resolved_players']+=1
                        vr=vals[vals.player_id.astype(str).eq(pid)].iloc[0]
                        feats['weighted_missing_minutes_share5']+=w*safe_div(float(vr.minutes),tot['minutes'])
                        feats['weighted_missing_xgi_share5']+=w*safe_div(float(vr.xgi),tot['xgi'])
                        feats['weighted_missing_gplus_share5']+=w*safe_div(float(vr.gplus_positive),tot['gplus_positive'])
                        if status=='OUT' and pid in top11:feats['missing_top11_minutes_count5']+=1
                        if status=='OUT' and pid in top3x:feats['missing_top3_xgi_count5']+=1
                        if status=='OUT' and str(vr.position).upper()=='GK':feats['gk_out']=1.0
                for k,v in feats.items():row[prefix+k]=v
            # Positive edge means the away side carries the larger missing-player burden.
            for metric in ['weighted_missing_minutes_share5','weighted_missing_xgi_share5','weighted_missing_gplus_share5',
                           'out_count','questionable_count','missing_top11_minutes_count5','missing_top3_xgi_count5']:
                row['edge_home_availability_'+metric]=row['away_availability_'+metric]-row['home_availability_'+metric]
            feature_rows.append(row)

    feat=pd.DataFrame(feature_rows)
    if feat.empty:raise RuntimeError('Availability feature build produced zero match rows')
    if feat.asa_game_id.duplicated().any():raise RuntimeError('Duplicate availability feature ASA game IDs')
    feat.to_parquet(OUT_FEATURES,index=False)

    keep=['asa_game_id']+[c for c in feat.columns if c not in {'match_id','season','asa_matchday','asa_game_id'}]
    enriched=base.merge(feat[keep],on='asa_game_id',how='left',validate='m:1')
    enriched.to_parquet(OUT_WAREHOUSE,index=False)

    meta={
      'built_at':now(),'base_file':str(base_path),'rows':len(enriched),'columns':len(enriched.columns),
      'availability_feature_rows':len(feat),'valid_reports':len(valid_reports),'rejected_reports':rejected_reports,
      'valid_report_details':valid_reports,'team_sides':side_count,'explicit_status_sides':explicit_status,
      'inferred_clear_sides':inferred_clear,'resolved_status_players_with_prior5':resolved,
      'unresolved_or_no_prior5_status_players':unresolved,
      'coverage_seasons':sorted(int(x) for x in feat.season.dropna().unique()),
      'output':str(OUT_WAREHOUSE),
      'leakage_note':'Availability is joined only to its official season/matchday report. Missing-player impact uses only each team player rows from its five matches strictly before the target match. Reports failing schedule-team coverage >=75% are rejected. Teams without parsed status rows inside a validated full matchday report are marked inferred_clear separately.'
    }
    REPORT.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':
    main()
