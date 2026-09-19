#!/usr/bin/env python3
from __future__ import annotations
import json, math
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
import numpy as np, pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis
from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
RAW=ROOT/'data/raw/players';PROC=ROOT/'data/processed';REP=ROOT/'reports'
for p in [RAW,PROC,REP]:p.mkdir(parents=True,exist_ok=True)

def now(): return datetime.now(timezone.utc).isoformat()
def flatten_gplus(z):
    if z.empty:return z
    out=z[['player_id','game_id','team_id','general_position','minutes_played']].copy()
    totals=[];above=[];bytype=[]
    for arr in z['data']:
        arr=arr or []
        totals.append(sum(float(x.get('goals_added_raw') or 0) for x in arr))
        above.append(sum(float(x.get('goals_added_above_avg') or 0) for x in arr))
        bytype.append({str(x.get('action_type')).lower():float(x.get('goals_added_raw') or 0) for x in arr})
    out['gplus_raw']=totals;out['gplus_above_avg']=above
    for typ in ['dribbling','fouling','interrupting','passing','receiving','shooting']:
        out['gplus_'+typ]=[x.get(typ,0.0) for x in bytype]
    return out
def collect():
    asa=AmericanSoccerAnalysis()
    frames={k:[] for k in ['xg','xpass','gplus','gk']}
    coverage=[]
    for y in range(2013,2027):
        rec={'season':y}
        for name,m in [('xg','get_player_xgoals'),('xpass','get_player_xpass'),('gplus','get_player_goals_added'),('gk','get_goalkeeper_xgoals')]:
            try:
                z=getattr(asa,m)(leagues='mls',season_name=str(y),split_by_games=True)
                if not isinstance(z,pd.DataFrame):z=pd.DataFrame(z)
                if name=='gplus': z=flatten_gplus(z)
                z['_season']=y
                z.to_parquet(RAW/f'{name}_{y}.parquet',index=False)
                frames[name].append(z)
                rec[name+'_rows']=len(z)
            except Exception as e:
                rec[name+'_rows']=0;rec[name+'_error']=type(e).__name__+':'+str(e)[:200]
        coverage.append(rec)
        print(y,rec,flush=True)
    for k,v in frames.items():
        d=pd.concat(v,ignore_index=True,sort=False) if v else pd.DataFrame()
        d.to_parquet(PROC/f'asa_player_{k}_game_2013_present.parquet',index=False)
    # static/reference tables
    refs={}
    for name,m in [('players','get_players'),('teams','get_teams'),('managers','get_managers')]:
        z=getattr(asa,m)(leagues='mls')
        if not isinstance(z,pd.DataFrame):z=pd.DataFrame(z)
        z.to_parquet(PROC/f'asa_{name}.parquet',index=False);refs[name]=len(z)
    # salaries have release dates; preserve all releases
    sal=asa.get_player_salaries(leagues='mls')
    if not isinstance(sal,pd.DataFrame):sal=pd.DataFrame(sal)
    sal.to_parquet(PROC/'asa_player_salaries.parquet',index=False)
    team_sal=asa.get_team_salaries(leagues='mls')
    if not isinstance(team_sal,pd.DataFrame):team_sal=pd.DataFrame(team_sal)
    team_sal.to_parquet(PROC/'asa_team_salaries.parquet',index=False)
    meta={'built_at':now(),'coverage':coverage,'refs':refs,'salary_rows':len(sal),'team_salary_rows':len(team_sal)}
    (REP/'player_game_coverage.json').write_text(json.dumps(meta,indent=2,default=str))
    return meta
def build_features():
    games=pd.read_parquet(PROC/'asa_mls_games_2012_present.parquet').copy()
    teams=pd.read_parquet(PROC/'asa_teams.parquet').copy()
    players=pd.read_parquet(PROC/'asa_players.parquet').copy()
    xg=pd.read_parquet(PROC/'asa_player_xg_game_2013_present.parquet').copy()
    xp=pd.read_parquet(PROC/'asa_player_xpass_game_2013_present.parquet').copy()
    gp=pd.read_parquet(PROC/'asa_player_gplus_game_2013_present.parquet').copy()
    gk=pd.read_parquet(PROC/'asa_player_gk_game_2013_present.parquet').copy()
    managers=pd.read_parquet(PROC/'asa_managers.parquet').copy()

    tid=next(c for c in ['team_id','id'] if c in teams.columns);tname=next(c for c in ['team_name','name'] if c in teams.columns)
    team_map={str(r[tid]):canon_team(r[tname]) for _,r in teams.iterrows()}
    games['home_team']=games.home_team_id.astype(str).map(team_map);games['away_team']=games.away_team_id.astype(str).map(team_map)
    games['dt']=pd.to_datetime(games.date_time_utc,errors='coerce',utc=True)
    games=games[games.dt.notna()&games.home_team.notna()&games.away_team.notna()].sort_values('dt').copy()

    # merge player game endpoints
    key=['player_id','game_id','team_id']
    usex=xg[key+['general_position','minutes_played','shots','shots_on_target','goals','xgoals','key_passes','primary_assists','xassists','xgoals_plus_xassists','points_added','xpoints_added']].copy()
    usep=xp[key+['attempted_passes','passes_completed_over_expected','share_team_touches']].copy()
    useg=gp[key+['gplus_raw','gplus_above_avg','gplus_passing','gplus_receiving','gplus_shooting','gplus_interrupting']].copy()
    pg=usex.merge(usep,on=key,how='outer').merge(useg,on=key,how='outer')
    pg['minutes_played']=pd.to_numeric(pg.minutes_played,errors='coerce').fillna(0)
    for c in ['xgoals','xassists','xgoals_plus_xassists','key_passes','points_added','xpoints_added','share_team_touches','gplus_raw','gplus_above_avg']:
        if c in pg:pg[c]=pd.to_numeric(pg[c],errors='coerce').fillna(0)

    # game dates/team names
    gm=games[['game_id','dt','home_team','away_team','home_score','away_score','home_manager_id','away_manager_id']].copy()
    pg=pg.merge(gm[['game_id','dt']],on='game_id',how='left')
    pg['team']=pg.team_id.astype(str).map(team_map)
    pg=pg[pg.dt.notna()&pg.team.notna()].copy()

    # player bios
    pbase=players.drop_duplicates('player_id',keep='last').set_index('player_id')
    if 'birth_date' in pbase.columns:
        bdates=pd.to_datetime(pbase.birth_date,errors='coerce',utc=True)
    else:bdates=pd.Series(dtype='datetime64[ns, UTC]')
    pg['birth_date']=pg.player_id.map(bdates.to_dict())
    pg['age_years']=(pg.dt-pg.birth_date).dt.total_seconds()/(365.25*86400)

    # Pre-index player-game rows by game/team
    game_team={k:z.copy() for k,z in pg.groupby(['game_id','team'])}
    recent_team_games=defaultdict(lambda:deque(maxlen=10))
    manager_state=defaultdict(lambda:{'games':0,'pts':0,'gf':0,'ga':0,'first':None})
    rows=[]

    def summarize(team):
        hist=list(recent_team_games[team])
        if not hist:return {
          'recent_games':0,'active_players5':np.nan,'top11_minutes_share5':np.nan,'top3_xgi_share5':np.nan,
          'top3_gplus_share5':np.nan,'weighted_age5':np.nan,'starter_proxy_continuity':np.nan,
          'gk_continuity3':np.nan,'high_load_players3':np.nan,'minutes_entropy5':np.nan}
        h5=hist[-5:];h3=hist[-3:]
        mins=defaultdict(float);xgi=defaultdict(float);gplus=defaultdict(float);age_num=0;age_den=0
        for h in h5:
            for p in h['players']:
                pid=p['player_id'];m=float(p.get('minutes',0));mins[pid]+=m;xgi[pid]+=float(p.get('xgi',0));gplus[pid]+=float(p.get('gplus',0))
                if pd.notna(p.get('age')):age_num+=m*float(p['age']);age_den+=m
        total=sum(mins.values());top11=sum(sorted(mins.values(),reverse=True)[:11])
        sx=sum(max(0,v) for v in xgi.values());sg=sum(max(0,v) for v in gplus.values())
        top3x=sum(sorted([max(0,v) for v in xgi.values()],reverse=True)[:3]);top3g=sum(sorted([max(0,v) for v in gplus.values()],reverse=True)[:3])
        probs=[v/total for v in mins.values() if total>0 and v>0]
        ent=-sum(p*math.log(p) for p in probs) if probs else np.nan
        cont=np.nan
        if len(hist)>=2:
            a={p['player_id'] for p in hist[-1]['players'] if p.get('minutes',0)>=45}
            b={p['player_id'] for p in hist[-2]['players'] if p.get('minutes',0)>=45}
            cont=len(a&b)/max(1,len(a|b))
        gks=[]
        for h in h3:
            cand=[p for p in h['players'] if p.get('pos')=='GK' and p.get('minutes',0)>=45]
            gks.append(cand[0]['player_id'] if cand else None)
        gkcont=(len(set(x for x in gks if x))==1 and len([x for x in gks if x])>=2) if gks else np.nan
        load=defaultdict(float)
        for h in h3:
            for p in h['players']:load[p['player_id']]+=float(p.get('minutes',0))
        return {
          'recent_games':len(hist),'active_players5':len(mins),'top11_minutes_share5':top11/total if total else np.nan,
          'top3_xgi_share5':top3x/sx if sx>0 else np.nan,'top3_gplus_share5':top3g/sg if sg>0 else np.nan,
          'weighted_age5':age_num/age_den if age_den else np.nan,'starter_proxy_continuity':cont,
          'gk_continuity3':float(gkcont) if isinstance(gkcont,bool) else gkcont,
          'high_load_players3':sum(v>=240 for v in load.values()),'minutes_entropy5':ent}

    for _,g in games.iterrows():
        row={'asa_game_id':str(g.game_id),'date_time_utc':g.dt,'season':int(g._season_requested) if '_season_requested' in g and pd.notna(g._season_requested) else g.dt.year,
             'home_team':g.home_team,'away_team':g.away_team}
        for side,team,mid in [('home',g.home_team,g.get('home_manager_id')),('away',g.away_team,g.get('away_manager_id'))]:
            sm=summarize(team)
            for k,v in sm.items():row[f'{side}_player_{k}']=v
            ms=manager_state[str(mid)]
            row[f'{side}_manager_prior_games']=ms['games']
            row[f'{side}_manager_prior_ppg']=ms['pts']/ms['games'] if ms['games'] else np.nan
            row[f'{side}_manager_prior_gdpg']=(ms['gf']-ms['ga'])/ms['games'] if ms['games'] else np.nan
            row[f'{side}_manager_new3']=int(ms['games']<3)
            row[f'{side}_manager_id']=str(mid) if pd.notna(mid) else None
        for k in ['top11_minutes_share5','top3_xgi_share5','top3_gplus_share5','weighted_age5','starter_proxy_continuity','gk_continuity3','high_load_players3','minutes_entropy5']:
            h=row.get('home_player_'+k);a=row.get('away_player_'+k)
            row['edge_player_'+k]=(h-a) if pd.notna(h) and pd.notna(a) else np.nan
        row['edge_manager_prior_ppg']=(row['home_manager_prior_ppg']-row['away_manager_prior_ppg']) if pd.notna(row['home_manager_prior_ppg']) and pd.notna(row['away_manager_prior_ppg']) else np.nan
        rows.append(row)

        # update manager after feature capture
        hs=float(g.home_score) if pd.notna(g.home_score) else np.nan;as_=float(g.away_score) if pd.notna(g.away_score) else np.nan
        if pd.notna(hs) and pd.notna(as_):
            for team,mid,gf,ga in [(g.home_team,g.get('home_manager_id'),hs,as_),(g.away_team,g.get('away_manager_id'),as_,hs)]:
                ms=manager_state[str(mid)]
                if ms['first'] is None:ms['first']=g.dt
                ms['games']+=1;ms['gf']+=gf;ms['ga']+=ga;ms['pts']+=3 if gf>ga else 1 if gf==ga else 0
        # update player history after feature capture
        for team in [g.home_team,g.away_team]:
            z=game_team.get((g.game_id,team),pd.DataFrame())
            plist=[]
            for _,p in z.iterrows():
                plist.append({'player_id':p.player_id,'minutes':float(p.minutes_played or 0),'xgi':float(p.get('xgoals_plus_xassists') or 0),
                              'gplus':float(p.get('gplus_raw') or 0),'age':p.get('age_years'),'pos':p.get('general_position')})
            recent_team_games[team].append({'game_id':g.game_id,'date':g.dt,'players':plist})

    out=pd.DataFrame(rows)
    out.to_parquet(PROC/'mls_player_manager_pregame_features.parquet',index=False)
    out.to_csv(PROC/'mls_player_manager_pregame_features.csv',index=False)
    meta={'built_at':now(),'rows':len(out),'columns':len(out.columns),
          'non_null':{c:int(out[c].notna().sum()) for c in out.columns if 'player_' in c or 'manager_' in c}}
    (REP/'player_manager_feature_meta.json').write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps({'rows':len(out),'columns':len(out.columns),'sample_cols':list(out.columns)},indent=2))
def main():
    meta=collect();build_features()
if __name__=='__main__':main()
