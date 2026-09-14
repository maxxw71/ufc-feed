from pathlib import Path
import os, json
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
OUT=Path('/home/appwiza-runner/nfl-context-data/preseason_veto_stress');OUT.mkdir(parents=True,exist_ok=True)
PRE=REPO/'nfl/legacy_preseason_staff_filter_audit/espn_preseason_team_seasons.csv'

def num(x):return pd.to_numeric(x,errors='coerce')
def met(x):
    x=x[x.moneyline.notna() & x.win.isin([0,1])]
    if not len(x):return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi':np.nan,'units':0.0}
    return {'n':len(x),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),'win_pct':float(x.win.mean()),'roi':float(x.profit_units.mean()),'units':float(x.profit_units.sum())}

def load(path,name):
    d=pd.read_csv(path,low_memory=False)
    z=pd.DataFrame({'method':name,'season':num(d.season).astype(int),'week':num(d.week).astype(int),'game_id':d.game_id.astype(str),'team':d.home_team,'opponent':d.away_team,'moneyline':num(d.home_moneyline),'win':num(d.win),'profit_units':num(d.profit)})
    return z

pre=pd.read_csv(PRE)
base=pd.concat([
    load(REPO/'nfl/legacy_live_home_opener_exact/original_bets.csv','original'),
    load(REPO/'nfl/legacy_live_home_opener_exact/stricter_bets.csv','stricter')],ignore_index=True)
base=base.merge(pre[['season','team','pre_games','pre_wins','pre_losses','pre_win_pct','pre_margin_pg']],on=['season','team'],how='left')
base['keep_ge2']=num(base.pre_wins)>=2
base['keep_nonlosing']=num(base.pre_win_pct)>=.5
base['keep_positive_margin']=num(base.pre_margin_pg)>=0

rows=[]; yearly=[]; loo=[]; price=[]
for method,x0 in base.groupby('method'):
    for rule,col in [('baseline',None),('preseason_wins_ge2','keep_ge2'),('preseason_nonlosing','keep_nonlosing'),('preseason_positive_margin','keep_positive_margin')]:
        x=x0.copy() if col is None else x0[x0[col].fillna(False)].copy()
        m=met(x)
        # era splits requested: old / mid / holdout / recent3
        eras=[('2007_2013',2007,2013),('2014_2019',2014,2019),('2020_2025',2020,2025),('2023_2025',2023,2025)]
        rec={'method':method,'rule':rule,**m}
        for en,a,b in eras:
            mm=met(x[x.season.between(a,b)])
            rec.update({f'{en}_n':mm['n'],f'{en}_win_pct':mm['win_pct'],f'{en}_roi':mm['roi']})
        # positive active season ratio
        y=x.groupby('season').agg(n=('win','size'),wins=('win','sum'),units=('profit_units','sum')).reset_index();y['win_pct']=y.wins/y.n;y['roi']=y.units/y.n
        active=y[y.n>=2];rec['active_seasons']=len(active);rec['positive_seasons']=int((active.units>0).sum());rec['positive_season_ratio']=rec['positive_seasons']/len(active) if len(active) else np.nan
        rows.append(rec)
        for _,r in y.iterrows():yearly.append({'method':method,'rule':rule,'season':int(r.season),'n':int(r.n),'wins':int(r.wins),'win_pct':float(r.win_pct),'roi':float(r.roi),'units':float(r.units)})
        # leave-one-season-out robustness
        for season in sorted(x.season.unique()):
            z=x[x.season!=season];mm=met(z);loo.append({'method':method,'rule':rule,'omitted_season':int(season),**mm})
        # price buckets
        for lo,hi in [(-1000,-400),(-399,-300),(-299,-200),(-199,-150),(-149,-110),(100,999)]:
            z=x[x.moneyline.between(lo,hi)];mm=met(z);price.append({'method':method,'rule':rule,'odds_lo':lo,'odds_hi':hi,**mm})

summary=pd.DataFrame(rows);summary.to_csv(OUT/'summary.csv',index=False)
pd.DataFrame(yearly).to_csv(OUT/'by_season.csv',index=False)
pd.DataFrame(loo).to_csv(OUT/'leave_one_season_out.csv',index=False)
pd.DataFrame(price).to_csv(OUT/'price_buckets.csv',index=False)

# stability markers for the preferred stricter+>=2 rule
p=summary[(summary.method=='stricter')&(summary.rule=='preseason_wins_ge2')].iloc[0]
loo_df=pd.DataFrame(loo);q=loo_df[(loo_df.method=='stricter')&(loo_df.rule=='preseason_wins_ge2')]
stability={
 'preferred_rule':'stricter + preseason_wins >= 2',
 'record':f"{int(p.wins)}-{int(p.losses)}",
 'win_pct':float(p.win_pct),'roi':float(p.roi),'n':int(p.n),
 'positive_season_ratio':float(p.positive_season_ratio),
 'era_rois':{k:float(p[f'{k}_roi']) if pd.notna(p[f'{k}_roi']) else None for k in ['2007_2013','2014_2019','2020_2025','2023_2025']},
 'era_win_pcts':{k:float(p[f'{k}_win_pct']) if pd.notna(p[f'{k}_win_pct']) else None for k in ['2007_2013','2014_2019','2020_2025','2023_2025']},
 'loo_min_win_pct':float(q.win_pct.min()),'loo_min_roi':float(q.roi.min()),'loo_max_roi':float(q.roi.max()),
 'all_loo_profitable':bool((q.roi>0).all())
}
(OUT/'stability.json').write_text(json.dumps(stability,indent=2))
lines=['NFL PRESEASON VETO STRESS TEST','',json.dumps(stability,indent=2),'','SUMMARY']
for _,r in summary.iterrows():
    lines.append(f"{r.method} / {r.rule}: {int(r.wins)}-{int(r.losses)} ({100*r.win_pct:.1f}%) n={int(r.n)} ROI={100*r.roi:+.1f}% | 07-13 {100*r['2007_2013_win_pct']:.1f}%/{100*r['2007_2013_roi']:+.1f}% | 14-19 {100*r['2014_2019_win_pct']:.1f}%/{100*r['2014_2019_roi']:+.1f}% | 20-25 {100*r['2020_2025_win_pct']:.1f}%/{100*r['2020_2025_roi']:+.1f}% | 23-25 {100*r['2023_2025_win_pct']:.1f}%/{100*r['2023_2025_roi']:+.1f}% | pos seasons {int(r.positive_seasons)}/{int(r.active_seasons)}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
