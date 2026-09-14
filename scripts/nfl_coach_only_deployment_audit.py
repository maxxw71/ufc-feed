from pathlib import Path
import os,json,ast
import numpy as np
import pandas as pd
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'coach_only_deployment_audit';OUT.mkdir(parents=True,exist_ok=True)
METHODS=REPO/'nfl/coach_only_discovery/coach_only_methods.csv'
DATA=CTX/'coach_quality_expansion'/'coach_quality_enriched_team_sides.parquet'

def num(x):return pd.to_numeric(x,errors='coerce')
def implied(ml):ml=num(ml);return np.where(ml<0,(-ml)/((-ml)+100),100/(ml+100))
def profit(win,ml):ml=num(ml);return np.where(num(win).eq(1),np.where(ml>0,ml/100,100/np.abs(ml)),-1.0)
def metrics(x):
    if not len(x):return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi':np.nan}
    return {'n':len(x),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),'win_pct':float(x.win.mean()),'roi':float(x.profit.mean())}
def condmask(x,conds):
    m=pd.Series(True,index=x.index)
    for c,op,t in conds:
        if c not in x:return pd.Series(False,index=x.index)
        v=num(x[c]);m &= (v>=t) if op=='>=' else (v<=t)
    return m
bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}
tracks={'LONG':((2006,2013),(2014,2019),(2020,2025)),'MODERN':((2012,2017),(2018,2020),(2021,2025))}
d=pd.read_parquet(DATA);d=d[d.season.between(2006,2025)].copy();d['market_prob']=implied(d.moneyline);d['win']=num(d.win);d['profit']=profit(d.win,d.moneyline)
meth=pd.read_csv(METHODS).copy()
# focus on methods already preferred + top survivors, then rebuild exact bet sets for concentration, timing and overlap
meth=meth.sort_values(['user_preferred','score'],ascending=[False,False]).head(80).reset_index(drop=True)
rows=[];sets={}
for i,r in meth.iterrows():
    cid=f'CO{i+1:03d}'
    conds=json.loads(r.conditions)
    plo,phi=bands[r.price_band]
    x=d[(d.market_prob>=plo)&(d.market_prob<phi)&(num(d.prior_games)>=3)].copy()
    if r.venue=='AWAY':x=x[~num(x.is_home).eq(1)]
    elif r.venue=='HOME':x=x[num(x.is_home).eq(1)]
    x=x[condmask(x,conds)].copy()
    sets[cid]=set((x.game_id.astype(str)+'|'+x.team.astype(str)).tolist())
    m=metrics(x)
    if not m['n']:continue
    top=x.team.value_counts(normalize=True);top3=float(top.head(3).sum()) if len(top) else np.nan
    # timing
    segs={}
    for name,a,b in [('W4_6',4,6),('W7_10',7,10),('W11_14',11,14),('W15_18',15,18)]:
        mm=metrics(x[num(x.week).between(a,b)]);segs[name+'_n']=mm['n'];segs[name+'_win_pct']=mm['win_pct'];segs[name+'_roi']=mm['roi']
    # eras from declared track
    tr,va,ho=tracks[r.track]
    mt=metrics(x[x.season.between(*tr)]);mv=metrics(x[x.season.between(*va)]);mh=metrics(x[x.season.between(*ho)])
    y=x.groupby('season').profit.sum();pos=float((y>0).mean()) if len(y) else np.nan
    minweek=int(num(x.week).min());recstart=4
    early=metrics(x[num(x.week).between(4,6)])
    if minweek>4:recstart=minweek
    elif early['n']<15 or (pd.notna(early['roi']) and early['roi']<.05):recstart=7
    rows.append({'method_id':cid,'track':r.track,'price_band':r.price_band,'venue':r.venue,'conditions':r.conditions,**m,'train_n':mt['n'],'train_roi':mt['roi'],'validation_n':mv['n'],'validation_roi':mv['roi'],'holdout_n':mh['n'],'holdout_roi':mh['roi'],'positive_season_ratio':pos,'top_team':top.index[0] if len(top) else None,'top_team_share':float(top.iloc[0]) if len(top) else np.nan,'top3_team_share':top3,'recommended_start_week':recstart,**segs})
a=pd.DataFrame(rows)
# overlap dedupe: keep best by holdout/full ROI + sample; tag >=.75 overlap
if len(a):
    a['robust_score']=a.holdout_roi*.35+a.roi*.25+a.positive_season_ratio*.18+np.log10(a.n.clip(lower=1))*.04-a.top_team_share.fillna(1)*.08
    a=a.sort_values('robust_score',ascending=False).reset_index(drop=True)
    kept=[];dups=[];maxovs=[]
    for _,r in a.iterrows():
        s=sets[r.method_id];best=0;dup=''
        for k in kept:
            t=sets[k];ov=len(s&t)/len(s|t) if s|t else 0
            if ov>best:best=ov
            if ov>=.75 and not dup:dup=k
        dups.append(dup);maxovs.append(best)
        if not dup:kept.append(r.method_id)
    a['duplicate_of']=dups;a['max_jaccard_overlap']=maxovs
    # deployment status
    status=[]
    for _,r in a.iterrows():
        if r.duplicate_of:status.append('DUPLICATE')
        elif r.n>=90 and r.holdout_n>=20 and r.roi>=.15 and r.holdout_roi>=.15 and r.positive_season_ratio>=.70 and r.top_team_share<=.12:status.append('CO_LIVE_READY')
        elif r.n>=70 and r.roi>=.10 and r.holdout_roi>=.10 and r.positive_season_ratio>=.65 and r.top_team_share<=.15:status.append('CO_WATCH')
        else:status.append('CO_RESEARCH')
    a['deployment_status']=status
    a.to_csv(OUT/'coach_only_audit.csv',index=False)
    a[a.deployment_status.eq('CO_LIVE_READY')].to_csv(OUT/'live_ready.csv',index=False)
    a[a.deployment_status.eq('CO_WATCH')].to_csv(OUT/'watch.csv',index=False)
summary={'audited':int(len(a)),'live_ready':int((a.deployment_status=='CO_LIVE_READY').sum()),'watch':int((a.deployment_status=='CO_WATCH').sum()),'duplicates':int((a.deployment_status=='DUPLICATE').sum()),'research':int((a.deployment_status=='CO_RESEARCH').sum())}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL COACH-ONLY DEPLOYMENT AUDIT','',json.dumps(summary,indent=2),'','TOP LIVE READY']
if len(a):
  for _,r in a[a.deployment_status.eq('CO_LIVE_READY')].head(20).iterrows():
    lines.append(f"{r.method_id} {r.track} {r.price_band} {r.venue} | {int(r.wins)}-{int(r.losses)} ({100*r.win_pct:.1f}%) n={int(r.n)} ROI={100*r.roi:+.1f}% hold={100*r.holdout_roi:+.1f}% pos={100*r.positive_season_ratio:.0f}% topteam={r.top_team} {100*r.top_team_share:.1f}% start=W{int(r.recommended_start_week)} | {r.conditions}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
