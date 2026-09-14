from pathlib import Path
import json, os
import numpy as np
import pandas as pd

CTX=Path('/home/appwiza-runner/nfl-context-data')
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
DATA=CTX/'coaching_everything'/'coaching_enriched_team_sides_2006_2025.parquet'
REG=REPO/'nfl/live_candidate_finalization/final_candidate_registry.csv'
OUT=CTX/'season_timing_audit'; OUT.mkdir(parents=True,exist_ok=True)

def num(x): return pd.to_numeric(x,errors='coerce')
def implied(o):
    o=num(o); return np.where(o>0,100/(o+100),np.where(o<0,np.abs(o)/(np.abs(o)+100),np.nan))
def profit(win,odds):
    odds=num(odds); win=num(win)
    return np.where(win.eq(1),np.where(odds>0,odds/100,100/np.abs(odds)),-1.0)
def metrics(x):
    if not len(x): return {'n':0,'wins':0,'losses':0,'roi':np.nan,'units':0.0}
    return {'n':int(len(x)),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),'roi':float(x.profit_units.mean()),'units':float(x.profit_units.sum())}

d=pd.read_parquet(DATA)
d=d[d.season.between(2006,2025)].copy()
d['week']=num(d.week)
d['prior_games']=num(d.get('prior_games',np.nan))
d=d[d.prior_games>=3].copy()  # same maturity rule used in discovery/finalization
# Completed historical rows only.
d=d[num(d.win).isin([0,1]) & num(d.moneyline).notna()].copy()
d['win']=num(d.win); d['moneyline']=num(d.moneyline); d['profit_units']=profit(d.win,d.moneyline)
d['market_prob_use']=num(d.market_prob) if 'market_prob' in d else pd.Series(implied(d.moneyline),index=d.index)
d['market_prob_use']=d.market_prob_use.fillna(pd.Series(implied(d.moneyline),index=d.index))

reg=pd.read_csv(REG)
price_bands={'DOG20_34':(.20,.3499),'DOG35_44':(.35,.4499),'DOG35_49':(.35,.4999),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}
tracks={'LONG':{'trainval':(2006,2019),'holdout':(2020,2025)},'MODERN':{'trainval':(2012,2020),'holdout':(2021,2025)}}

def cond(c,op,q):
    if c not in d.columns:return pd.Series(False,index=d.index)
    z=num(d[c]); return z>=float(q) if op=='>=' else z<=float(q)
def mask(r):
    m=pd.Series(True,index=d.index)
    for j in [1,2,3]:
        c=r.get(f'feature{j}')
        if pd.notna(c) and str(c):m &= cond(str(c),r.get(f'op{j}'),r.get(f'threshold{j}'))
    lo=r.get('live_p_lo'); hi=r.get('live_p_hi')
    if pd.isna(lo) or pd.isna(hi):lo,hi=price_bands[r.price_band]
    m &= num(d.market_prob_use).between(float(lo),float(hi),inclusive='both')
    if r.venue=='AWAY':m &= num(d.is_home).eq(0)
    elif r.venue=='HOME':m &= num(d.is_home).eq(1)
    return m

def era(r,name):
    track=r.track if r.track in tracks else 'LONG'; lo,hi=tracks[track][name]
    return d.season.between(lo,hi)

buckets=[('W4_6',4,6),('W7_10',7,10),('W11_14',11,14),('W15_18',15,18)]
rows=[]; detail=[]
for _,r in reg.iterrows():
    if not bool(r.get('rule_reproducible',True)):continue
    m=mask(r); x=d[m].copy()
    if not len(x):continue
    full=metrics(x); tv=metrics(d[m&era(r,'trainval')]); ho=metrics(d[m&era(r,'holdout')])
    rec={
      'candidate_id':r.candidate_id,'source':r.source,'deployment_status':r.deployment_status,'track':r.track,
      'full_n':full['n'],'full_roi':full['roi'],'trainval_n':tv['n'],'trainval_roi':tv['roi'],'holdout_n':ho['n'],'holdout_roi':ho['roi'],
      'min_week':int(x.week.min()),'median_week':float(x.week.median()),'max_week':int(x.week.max()),
      'min_prior_games':int(x.prior_games.min()),'median_prior_games':float(x.prior_games.median())
    }
    for name,lo,hi in buckets:
        bm=m&d.week.between(lo,hi)
        z=metrics(d[bm]); ztv=metrics(d[bm&era(r,'trainval')]); zho=metrics(d[bm&era(r,'holdout')])
        rec[f'{name}_n']=z['n']; rec[f'{name}_share']=z['n']/max(1,full['n']); rec[f'{name}_roi']=z['roi']
        rec[f'{name}_trainval_n']=ztv['n']; rec[f'{name}_trainval_roi']=ztv['roi']; rec[f'{name}_holdout_n']=zho['n']; rec[f'{name}_holdout_roi']=zho['roi']
        detail.append({'candidate_id':r.candidate_id,'bucket':name,'week_lo':lo,'week_hi':hi,**z,
                       'trainval_n':ztv['n'],'trainval_roi':ztv['roi'],'holdout_n':zho['n'],'holdout_roi':zho['roi']})
    # Deployment timing guard. Discovery already required prior_games>=3. Only allow W4 start when early evidence exists in both eras.
    e_n=rec['W4_6_n']; e_tv=rec['W4_6_trainval_n']; e_ho=rec['W4_6_holdout_n']; e_tv_roi=rec['W4_6_trainval_roi']; e_ho_roi=rec['W4_6_holdout_roi']
    if rec['min_week']>4:
        timing='START_AT_HISTORICAL_MIN_WEEK'; start=int(rec['min_week'])
    elif e_n>=20 and e_tv>=10 and e_ho>=6 and pd.notna(e_tv_roi) and pd.notna(e_ho_roi) and e_tv_roi>=0 and e_ho_roi>=0:
        timing='WEEK4_OK_AFTER_3_COMPLETED_GAMES'; start=4
    elif e_n<15 or e_ho<5:
        timing='INSUFFICIENT_EARLY_EVIDENCE_START_WEEK7'; start=7
    else:
        timing='EARLY_WINDOW_WEAK_START_WEEK7'; start=7
    rec['recommended_start_week']=start; rec['timing_decision']=timing
    rows.append(rec)

res=pd.DataFrame(rows)
res.to_csv(OUT/'method_season_timing.csv',index=False)
pd.DataFrame(detail).to_csv(OUT/'method_week_bucket_performance.csv',index=False)
# Focus tables
live=res[res.deployment_status.eq('LIVE_READY')].copy()
watch=res[res.deployment_status.eq('WATCHLIST')].copy()
live.to_csv(OUT/'live_ready_timing.csv',index=False)
watch.sort_values(['holdout_roi','full_n'],ascending=[False,False]).head(100).to_csv(OUT/'top_watchlist_timing.csv',index=False)
summary={
 'methods_audited':int(len(res)),
 'live_ready_audited':int(len(live)),
 'live_ready_week4_ok':int((live.recommended_start_week==4).sum()) if len(live) else 0,
 'live_ready_start_week7_or_later':int((live.recommended_start_week>=7).sum()) if len(live) else 0,
 'all_min_week':int(res.min_week.min()) if len(res) else None,
 'global_maturity_rule':'prior_games >= 3 completed games for selected team',
}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL SEASON TIMING + MATURITY AUDIT','',json.dumps(summary,indent=2),'','LIVE READY TIMING']
for _,r in live.sort_values(['recommended_start_week','holdout_roi'],ascending=[True,False]).iterrows():
    lines.append(f"{r.candidate_id} | start=W{int(r.recommended_start_week)} ({r.timing_decision}) | historical weeks {int(r.min_week)}-{int(r.max_week)}, median W{r.median_week:.1f} | W4-6 n={int(r.W4_6_n)} ROI={100*r.W4_6_roi:+.1f}% (pre {100*r.W4_6_trainval_roi:+.1f}% / hold {100*r.W4_6_holdout_roi:+.1f}%) | W7-10 n={int(r.W7_10_n)} ROI={100*r.W7_10_roi:+.1f}% | W11-14 n={int(r.W11_14_n)} ROI={100*r.W11_14_roi:+.1f}% | W15-18 n={int(r.W15_18_n)} ROI={100*r.W15_18_roi:+.1f}%")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
