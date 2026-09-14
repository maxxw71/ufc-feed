from pathlib import Path
import json, os, io, requests, importlib.util
import numpy as np
import pandas as pd

# Exact current-rule re-audit for the Week-1 pass-defense rule and late-season road-favorite rule.
ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
R=ROOT/'research_v2'
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'legacy_live_secondary_exact'; OUT.mkdir(parents=True,exist_ok=True)
CACHE=CTX/'legacy_live_secondary_cache'; CACHE.mkdir(parents=True,exist_ok=True)
SCHED=ROOT/'data/raw/schedules_2006_2026.parquet'
ALIASES={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA'}

def num(x):return pd.to_numeric(x,errors='coerce')
def bet_profit(win,odds):
    if pd.isna(odds):return np.nan
    if win:return float(odds)/100 if odds>0 else 100/abs(float(odds))
    return -1.0
def metrics(x):
    x=x[x.moneyline.notna()].copy()
    if not len(x):return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi':np.nan,'units':0.0}
    return {'n':len(x),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),'win_pct':float(x.win.mean()),'roi':float(x.profit_units.mean()),'units':float(x.profit_units.sum())}
def source_file(kind,stem,year):
    if kind=='stats_team':
        cands=[R/f'data/stats_team/{stem}_{year}.parquet', ROOT/f'data/stats_team/{stem}_{year}.parquet', CACHE/f'{stem}_{year}.parquet']
    elif kind=='pbp':
        cands=list((ROOT/'data/pbp').glob(f'*{year}*.parquet'))+[R/f'data/pbp/play_by_play_{year}.parquet',CACHE/f'play_by_play_{year}.parquet']
    else:
        cands=[R/f'data/snap_counts/{stem}_{year}.parquet',ROOT/f'data/snap_counts/{stem}_{year}.parquet',CACHE/f'{stem}_{year}.parquet']
    for p in cands:
        if p.exists():return p
    if kind=='pbp': url=f'https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{year}.parquet'; dest=CACHE/f'play_by_play_{year}.parquet'
    else: url=f'https://github.com/nflverse/nflverse-data/releases/download/{kind}/{stem}_{year}.parquet'; dest=CACHE/f'{stem}_{year}.parquet'
    r=requests.get(url,timeout=(5,60)); r.raise_for_status(); dest.write_bytes(r.content); return dest

G=pd.read_parquet(SCHED).copy()
if 'game_type' in G.columns:G=G[G.game_type.eq('REG')].copy()
elif 'season_type' in G.columns:G=G[G.season_type.eq('REG')].copy()
for c in ['home_team','away_team']:
    G[c]=G[c].replace(ALIASES)

pd_bets=[]; pd_coverage=[]
for season in range(2007,2026):
    prior=season-1
    try:
        T=pd.read_parquet(source_file('stats_team','stats_team_week',prior),columns=['season','week','season_type','team','passing_epa','rushing_epa','attempts','sacks_suffered','carries'])
        T=T[(T.season==prior)&T.season_type.eq('REG')].copy();T.team=T.team.replace(ALIASES)
        P=G[G.season.eq(prior)].copy()
        sides=pd.concat([P[['game_id','season','week','home_team','away_team']].rename(columns={'home_team':'team','away_team':'opponent'}),P[['game_id','season','week','away_team','home_team']].rename(columns={'away_team':'team','home_team':'opponent'})])
        j=sides.merge(T,on=['season','week','team'],how='left',validate='one_to_one')
        db=num(j.attempts)+num(j.sacks_suffered); j['pass_rate']=num(j.passing_epa)/db; j['offense_rate']=(num(j.passing_epa)+num(j.rushing_epa))/(db+num(j.carries))
        pbp_path=source_file('pbp','play_by_play',prior)
        q=pd.read_parquet(pbp_path,columns=['game_id','posteam','epa','pass_attempt','sack','qb_hit'])
        q=q[q.posteam.notna()&q.epa.notna()&(num(q.pass_attempt)==1)].copy();q.posteam=q.posteam.replace(ALIASES)
        q['hit']=((num(q.sack)==1)|(num(q.qb_hit)==1)).astype(float)
        qr=q.groupby(['game_id','posteam'],as_index=False).hit.mean().rename(columns={'posteam':'team','hit':'hit_rate'})
        j=j.merge(qr,on=['game_id','team'],how='left',validate='one_to_one')
        ranks=pd.DataFrame({'pass_rank':j.groupby('opponent').pass_rate.mean().rank(method='average'),'hit_rank':j.groupby('opponent').hit_rate.mean().rank(ascending=False,method='average'),'offense_rank':j.groupby('team').offense_rate.mean().rank(ascending=False,method='average')})
        cur=G[(G.season==season)&(num(G.week)==1)].copy(); n=0
        for _,g in cur.iterrows():
            ht=g.home_team
            if ht not in ranks.index:continue
            r=ranks.loc[ht]
            if r.pass_rank<=10 and r.hit_rank<=16 and r.offense_rank<=24:
                ml=num(pd.Series([g.get('home_moneyline')])).iloc[0]
                if pd.isna(g.get('home_score')) or pd.isna(g.get('away_score')):continue
                win=float(g.home_score>g.away_score); prof=bet_profit(bool(win),ml)
                pd_bets.append({'method':'pass_defense_offense24','season':season,'week':1,'game_id':g.game_id,'team':ht,'opponent':g.away_team,'moneyline':ml,'win':win,'profit_units':prof,'pass_rank':r.pass_rank,'hit_rank':r.hit_rank,'offense_rank':r.offense_rank});n+=1
        pd_coverage.append({'season':season,'status':'ok','qualifiers':n})
    except Exception as e:
        pd_coverage.append({'season':season,'status':'error','error':type(e).__name__+': '+str(e)[:180]})

late_path=R/'late_season_method.py'
spec=importlib.util.spec_from_file_location('late_live',late_path);late=importlib.util.module_from_spec(spec);spec.loader.exec_module(late)
late_bets=[];late_cov=[]
for season in range(2006,2026):
    try:
        T=pd.read_parquet(source_file('stats_team','stats_team_week',season))
        S=pd.read_parquet(source_file('snap_counts','snap_counts',season))
        if 'team' in T:T.team=T.team.replace(ALIASES)
        if 'team' in S:S.team=S.team.replace(ALIASES)
        qual=0
        for week in range(11,19):
            games=G[(G.season==season)&(num(G.week)==week)&G.home_score.notna()&G.away_score.notna()].copy()
            if games.empty:continue
            try:profiles=late.build_profiles(G,T,S,season,week)
            except Exception:continue
            for _,g in games.iterrows():
                if g.away_team not in profiles or g.home_team not in profiles:continue
                a=profiles[g.away_team];h=profiles[g.home_team];edge=h['defense_rank']-a['defense_rank'];rest=float(g.away_rest-g.home_rest)
                ml=num(pd.Series([g.get('away_moneyline')])).iloc[0]
                if pd.isna(ml):continue
                if late.qualifies(week,a['prior_games'],h['prior_games'],edge,a['continuity'],h['protection_rank'],rest,float(ml)):
                    win=float(g.away_score>g.home_score);prof=bet_profit(bool(win),ml)
                    late_bets.append({'method':late.RULE,'season':season,'week':week,'game_id':g.game_id,'team':g.away_team,'opponent':g.home_team,'moneyline':ml,'win':win,'profit_units':prof,'defense_edge':edge,'ol_continuity':a['continuity'],'opp_protection_rank':h['protection_rank'],'rest_edge':rest});qual+=1
        late_cov.append({'season':season,'status':'ok','qualifiers':qual})
    except Exception as e:
        late_cov.append({'season':season,'status':'error','error':type(e).__name__+': '+str(e)[:180]})

bets=pd.concat([pd.DataFrame(pd_bets),pd.DataFrame(late_bets)],ignore_index=True) if (pd_bets or late_bets) else pd.DataFrame()
if len(bets): bets.to_csv(OUT/'bets.csv',index=False)
pd.DataFrame(pd_coverage).to_csv(OUT/'pass_defense_coverage.csv',index=False);pd.DataFrame(late_cov).to_csv(OUT/'late_coverage.csv',index=False)
rows=[];yearly=[]
for method,x in bets.groupby('method') if len(bets) else []:
    allm=metrics(x);old=metrics(x[x.season<=2019]);recent=metrics(x[x.season>=2020]);recent3=metrics(x[x.season>=2023])
    y=x.groupby('season').agg(n=('win','size'),wins=('win','sum'),units=('profit_units','sum'));y['win_pct']=y.wins/y.n;y['roi']=y.units/y.n
    active=y[y.n>=2];pos=int((active.units>0).sum());act=len(active)
    rows.append({'method':method,**allm,'older_n':old['n'],'older_win_pct':old['win_pct'],'older_roi':old['roi'],'recent_n':recent['n'],'recent_win_pct':recent['win_pct'],'recent_roi':recent['roi'],'recent3_n':recent3['n'],'recent3_win_pct':recent3['win_pct'],'recent3_roi':recent3['roi'],'positive_seasons':pos,'active_seasons':act,'positive_season_ratio':pos/act if act else np.nan})
    yy=y.reset_index();yy['method']=method;yearly.append(yy)
pd.DataFrame(rows).to_csv(OUT/'summary.csv',index=False)
if yearly:pd.concat(yearly,ignore_index=True).to_csv(OUT/'by_season.csv',index=False)
lines=['LIVE SECONDARY LEGACY RULE RE-AUDIT','']
for r in rows:
    lines.append(f"{r['method']} | {r['wins']}-{r['losses']} ({100*r['win_pct']:.1f}% win) | n={r['n']} ROI={100*r['roi']:+.1f}% | <=2019 win={100*r['older_win_pct']:.1f}% ROI={100*r['older_roi']:+.1f}% | 2020-25 win={100*r['recent_win_pct']:.1f}% ROI={100*r['recent_roi']:+.1f}% | 2023-25 win={100*r['recent3_win_pct']:.1f}% ROI={100*r['recent3_roi']:+.1f}% | positive seasons={r['positive_seasons']}/{r['active_seasons']}")
lines+=['',f'pass_defense seasons ok={sum(r["status"]=="ok" for r in pd_coverage)}/{len(pd_coverage)}',f'late seasons ok={sum(r["status"]=="ok" for r in late_cov)}/{len(late_cov)}']
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
