from pathlib import Path
import json, os
import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
DATA=CTX/'coaching_everything'/'coaching_enriched_team_sides_2006_2025.parquet'
SCHED=ROOT/'data'/'raw'/'schedules_2006_2026.parquet'
NW=REPO/'nfl/next_wave_deployment_audit/next_wave_ready.csv'
COACH=REPO/'nfl/coaching_deployment_audit/coaching_methods_ready.csv'
TRAVEL=REPO/'nfl/travel_deployment_audit/travel_methods_ready.csv'
OUT=CTX/'new_method_reg_only_revalidation'; OUT.mkdir(parents=True,exist_ok=True)

def num(x):return pd.to_numeric(x,errors='coerce')
def implied(o):
    o=num(o);return np.where(o>0,100/(o+100),np.where(o<0,np.abs(o)/(np.abs(o)+100),np.nan))
def profit(win,odds):
    odds=num(odds); win=num(win); return np.where(win.eq(1),np.where(odds>0,odds/100,100/np.abs(odds)),-1.0)
def metrics(x):
    if not len(x):return {'n':0,'roi':np.nan,'wins':0,'losses':0}
    return {'n':int(len(x)),'roi':float(x.profit_units.mean()),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum())}

d=pd.read_parquet(DATA)
s=pd.read_parquet(SCHED,columns=['game_id','game_type'])
regids=set(s.loc[s.game_type.astype(str).eq('REG'),'game_id'].astype(str))
d=d[d.game_id.astype(str).isin(regids)&d.season.between(2006,2025)].copy()
d=d[num(d.get('prior_games',np.nan))>=3].copy()
d=d[num(d.win).isin([0,1])&num(d.moneyline).notna()].copy()
d['week']=num(d.week);d['win']=num(d.win);d['moneyline']=num(d.moneyline);d['profit_units']=profit(d.win,d.moneyline)
d['market_prob_use']=num(d.market_prob) if 'market_prob' in d else pd.Series(implied(d.moneyline),index=d.index)
d['market_prob_use']=d.market_prob_use.fillna(pd.Series(implied(d.moneyline),index=d.index))
price={'DOG20_34':(.20,.3499),'DOG35_44':(.35,.4499),'DOG35_49':(.35,.4999),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75)}
tracks={'LONG':{'train':(2006,2013),'validation':(2014,2019),'holdout':(2020,2025)},'MODERN':{'train':(2012,2017),'validation':(2018,2020),'holdout':(2021,2025)}}
def era(track,p):lo,hi=tracks.get(track,tracks['LONG'])[p];return d.season.between(lo,hi)
def cond(c,op,q):
    if c not in d.columns:return pd.Series(False,index=d.index)
    z=num(d[c]);return z>=float(q) if op=='>=' else z<=float(q)
def apply_rule(row,source):
    if source=='TRAVEL':
        feats=[(row.feature,row.op,row.threshold)]
    else:
        feats=[]
        for j in [1,2,3]:
            c=row.get(f'feature{j}')
            if pd.notna(c) and str(c):feats.append((c,row.get(f'op{j}'),row.get(f'threshold{j}')))
    m=pd.Series(True,index=d.index)
    for c,op,q in feats:m &= cond(str(c),op,q)
    if source=='NEXT_WAVE' and pd.notna(row.get('live_p_lo')) and pd.notna(row.get('live_p_hi')):
        lo,hi=float(row.live_p_lo),float(row.live_p_hi)
    else:lo,hi=price[row.price_band]
    m &= num(d.market_prob_use).between(lo,hi,inclusive='both')
    if row.venue=='AWAY':m &= num(d.is_home).eq(0)
    elif row.venue=='HOME':m &= num(d.is_home).eq(1)
    return m

def timing(m,track):
    x=d[m]
    early=metrics(d[m&d.week.between(4,6)])
    epre=metrics(d[m&d.week.between(4,6)&(era(track,'train')|era(track,'validation'))])
    eho=metrics(d[m&d.week.between(4,6)&era(track,'holdout')])
    minw=int(x.week.min()) if len(x) else 99
    if minw>4:return minw,'START_AT_HISTORICAL_MIN_WEEK'
    if early['n']>=20 and epre['n']>=10 and eho['n']>=6 and pd.notna(epre['roi']) and pd.notna(eho['roi']) and epre['roi']>=0 and eho['roi']>=0:return 4,'WEEK4_OK_AFTER_3_COMPLETED_GAMES'
    return 7,'START_WEEK7_EARLY_EVIDENCE_WEAK_OR_SPARSE'

rows=[]
for path,source in [(NW,'NEXT_WAVE'),(COACH,'COACHING'),(TRAVEL,'TRAVEL')]:
    if not path.exists():continue
    z=pd.read_csv(path)
    for i,r in z.iterrows():
        rid=r.get('next_method_id') if source=='NEXT_WAVE' else (f'NFL-C{i+1:03d}' if source=='COACHING' else f'NFL-T{i+1:03d}')
        track=r.get('track','LONG');m=apply_rule(r,source)
        full=metrics(d[m]);tr=metrics(d[m&era(track,'train')]);va=metrics(d[m&era(track,'validation')]);ho=metrics(d[m&era(track,'holdout')])
        if not full['n']:continue
        start,tdec=timing(m,track)
        buckets={}
        for nm,lo,hi in [('W4_6',4,6),('W7_10',7,10),('W11_14',11,14),('W15_18',15,18)]:
            q=metrics(d[m&d.week.between(lo,hi)]);buckets[f'{nm}_n']=q['n'];buckets[f'{nm}_roi']=q['roi']
        survive=(full['n']>=70 and tr['roi']>=.05 and va['roi']>=.05 and ho['n']>=15 and ho['roi']>=.10 and full['roi']>=.10)
        rows.append({'method_id':rid,'source':source,'track':track,'price_band':r.price_band,'venue':r.venue,
                     'full_n_reg':full['n'],'full_roi_reg':full['roi'],'train_n_reg':tr['n'],'train_roi_reg':tr['roi'],
                     'validation_n_reg':va['n'],'validation_roi_reg':va['roi'],'holdout_n_reg':ho['n'],'holdout_roi_reg':ho['roi'],
                     'min_week':int(d.loc[m,'week'].min()),'median_week':float(d.loc[m,'week'].median()),'max_week':int(d.loc[m,'week'].max()),
                     'recommended_start_week':start,'timing_decision':tdec,'reg_only_survives':survive,**buckets})
res=pd.DataFrame(rows)
res.to_csv(OUT/'new_methods_reg_only_revalidation.csv',index=False)
ready=res[res.reg_only_survives.eq(True)].sort_values(['holdout_roi_reg','full_n_reg'],ascending=[False,False])
ready.to_csv(OUT/'survivors.csv',index=False)
summary={'audited':int(len(res)),'reg_only_survivors':int(len(ready)),'next_wave_survivors':int(((ready.source=='NEXT_WAVE')).sum()),'coaching_survivors':int(((ready.source=='COACHING')).sum()),'travel_survivors':int(((ready.source=='TRAVEL')).sum()),'week4_ok_survivors':int((ready.recommended_start_week==4).sum()),'week7_or_later_survivors':int((ready.recommended_start_week>=7).sum())}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL NEW-METHOD REG-ONLY REVALIDATION','',json.dumps(summary,indent=2),'','TOP SURVIVORS']
for _,r in ready.head(40).iterrows():
    lines.append(f"{r.method_id} [{r.source}] | n={int(r.full_n_reg)} ROI={100*r.full_roi_reg:+.1f}% | train {100*r.train_roi_reg:+.1f}% val {100*r.validation_roi_reg:+.1f}% hold {100*r.holdout_roi_reg:+.1f}% | start W{int(r.recommended_start_week)} | W4-6 {int(r.W4_6_n)} @ {100*r.W4_6_roi:+.1f}% | W7-10 {int(r.W7_10_n)} @ {100*r.W7_10_roi:+.1f}% | W11-14 {int(r.W11_14_n)} @ {100*r.W11_14_roi:+.1f}% | W15-18 {int(r.W15_18_n)} @ {100*r.W15_18_roi:+.1f}%")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
