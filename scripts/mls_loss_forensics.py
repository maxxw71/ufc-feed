#!/usr/bin/env python3
from __future__ import annotations
import json,re,math
from pathlib import Path
from collections import Counter
import numpy as np,pandas as pd
import mls_autoresearch as ar

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'research/loss_forensics';OUT.mkdir(parents=True,exist_ok=True)
COMBO=ROOT/'auto_research/reports/weekly_combos.csv'
DATA=ar.DATA

SINGLES=[
 ('S_TRAVEL_AWAY','AWAY','P35_45','edge_travel_miles','>=',844.2),
 ('S_ELO_AWAY','AWAY','P25_35','elo_edge','<=',-41.31),
 ('S_AWAY_FORM','AWAY','P40_50','sel_last5_gdpg','<=',0.4),
 ('S_HOME_OPP_GD','HOME','P55_70','opp_season_gdpg','>=',0.2222),
 ('S_HOME_VENUE_EDGE','HOME','P45_55','edge_venue5_ppg','>=',1.2),
]
def parse_rule(s):
    m=re.match(r'(.+?)\s+(>=|<=)\s+([-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?)$',str(s).strip(),re.I)
    if not m:raise ValueError(s)
    return m.group(1),m.group(2),float(m.group(3))
def apply_rule(df,feat,op,t):
    v=pd.to_numeric(df[feat],errors='coerce')
    return v.ge(t) if op=='>=' else v.le(t)
def pbmask(df,pb):return ar.pmask(df,pb)
def base_selection_data():
    d=pd.read_parquet(DATA)
    s=ar.selection_rows(d)
    # attach identities and base match context
    base=d[['match_id','date','season','home_team','away_team','home_elo','away_elo',
            'home_score','away_score','home_novig_prob','draw_novig_prob','away_novig_prob',
            'home_travel_miles','away_travel_miles','home_days_rest','away_days_rest',
            'home_consecutive_road_pre','away_consecutive_road_pre',
            'home_last5_ppg','away_last5_ppg','home_last5_gdpg','away_last5_gdpg',
            'home_venue5_ppg','away_venue5_ppg']].copy()
    s=s.merge(base,on=['match_id','date','season'],how='left')
    s['selection_team']=np.where(s.outcome.eq('HOME'),s.home_team,np.where(s.outcome.eq('AWAY'),s.away_team,'DRAW'))
    s['opponent_team']=np.where(s.outcome.eq('HOME'),s.away_team,np.where(s.outcome.eq('AWAY'),s.home_team,'DRAW'))
    return s
def met(x):
    m=ar.metrics(x)
    if not m:return {}
    by=x.groupby('season').profit.agg(['count','sum'])
    return {**m,'positive_season_ratio':float((by.loc[by['count']>=5,'sum']>0).mean()) if len(by.loc[by['count']>=5]) else np.nan}
def exact_family(s,name,outcome,pb,rules):
    z=s[s.outcome.eq(outcome)].copy()
    mask=pbmask(z,pb)
    for feat,op,t in rules:mask &= apply_rule(z,feat,op,t)
    x=z[mask].copy()
    x['family']=name
    return x
def year_table(x):
    return x.groupby('season').agg(n=('win','size'),wins=('win','sum'),units=('profit','sum'),roi=('profit','mean'),
                                    avg_odds=('odds','mean'),avg_market=('market_prob','mean')).reset_index()
def team_table(x):
    z=x.groupby('selection_team').agg(n=('win','size'),wins=('win','sum'),units=('profit','sum'),roi=('profit','mean')).reset_index()
    return z.sort_values(['n','units'],ascending=[False,False])
def concentration(x):
    c=x.selection_team.value_counts()
    return {'top_team_share':float(c.iloc[0]/len(x)) if len(x) else 0,
            'top3_team_share':float(c.iloc[:3].sum()/len(x)) if len(x) else 0,
            'teams':int(c.size)}
def threshold_stability(s,outcome,pb,feat,op,t):
    z=s[s.outcome.eq(outcome)].copy()
    # perturb threshold ±10%, plus absolute neighbor based on train std if near zero
    vals=pd.to_numeric(z.loc[ar.period(z,'train'),feat],errors='coerce').dropna()
    scale=max(abs(t)*.10,float(vals.std())*.10 if len(vals) else 0.1,0.05)
    ts=sorted(set([t-2*scale,t-scale,t,t+scale,t+2*scale]))
    rows=[]
    for tt in ts:
        m=pbmask(z,pb)&apply_rule(z,feat,op,tt)
        ev=ar.evaluate(z,m)
        if not ev:continue
        rows.append({'threshold':tt,'n':ev['full']['n'],'roi':ev['full']['roi'],'train_roi':ev['train']['roi'],
                     'validation_roi':ev['validation']['roi'],'holdout_roi':ev['holdout']['roi'],'holdout_n':ev['holdout']['n']})
    return pd.DataFrame(rows)
def price_stability(s,outcome,rules):
    z=s[s.outcome.eq(outcome)].copy();rows=[]
    for pb,_,__ in ar.PRICE_BANDS:
        m=pbmask(z,pb)
        for feat,op,t in rules:m &= apply_rule(z,feat,op,t)
        ev=ar.evaluate(z,m)
        if not ev:continue
        rows.append({'price_band':pb,'n':ev['full']['n'],'roi':ev['full']['roi'],'holdout_n':ev['holdout']['n'],'holdout_roi':ev['holdout']['roi']})
    return pd.DataFrame(rows)
def loss_rows(x):
    cols=['family','season','date','selection_team','opponent_team','odds','market_prob','profit','home_team','away_team',
          'home_score','away_score','home_elo','away_elo','home_travel_miles','away_travel_miles','home_days_rest','away_days_rest',
          'home_consecutive_road_pre','away_consecutive_road_pre','home_last5_ppg','away_last5_ppg','home_last5_gdpg','away_last5_gdpg',
          'home_venue5_ppg','away_venue5_ppg']
    cols=[c for c in cols if c in x.columns]
    return x[x.win.eq(0)][cols].sort_values(['season','date'])
def thirds(x):
    x=x.sort_values('date').reset_index(drop=True);parts=np.array_split(x,3);rows=[]
    for i,p in enumerate(parts,1):
        m=met(p);rows.append({'third':i,**m})
    return rows
def leave_one_season_out(x):
    rows=[]
    for y in sorted(x.season.unique()):
        z=x[~x.season.eq(y)]
        m=met(z);rows.append({'left_out':int(y),'n':m.get('n'),'roi':m.get('roi')})
    return rows
def bootstrap_roi(x,n=3000,seed=7):
    if len(x)<2:return [np.nan,np.nan]
    rng=np.random.default_rng(seed);v=x.profit.to_numpy(float)
    means=[rng.choice(v,size=len(v),replace=True).mean() for _ in range(n)]
    return [float(np.quantile(means,.025)),float(np.quantile(means,.975))]
def main():
    s=base_selection_data()
    families=[]
    for name,out,pb,feat,op,t in SINGLES:
        families.append((name,out,pb,[(feat,op,t)]))
    if COMBO.exists():
        c=pd.read_csv(COMBO).head(8)
        for i,r in c.iterrows():
            f1,o1,t1=parse_rule(r.rule1);f2,o2,t2=parse_rule(r.rule2)
            families.append((f'C{i+1:02d}',r.outcome,r.price_band,[(f1,o1,t1),(f2,o2,t2)]))
    summaries=[];all_losses=[]
    report=['MLS LOSS FORENSICS & ROBUSTNESS','='*100,
            'Universe: MLS regular season only. Historical odds sample 2012-2025. 2026 remains prospective.','']
    for name,out,pb,rules in families:
        x=exact_family(s,name,out,pb,rules);m=met(x)
        if not m:continue
        ev=ar.evaluate(s[s.outcome.eq(out)].copy(), (s[s.outcome.eq(out)].index.isin(x.index))) if False else None
        train=met(x[x.season.between(*ar.SPLIT['train'])]);val=met(x[x.season.between(*ar.SPLIT['validation'])]);hold=met(x[x.season.between(*ar.SPLIT['holdout'])])
        ci=bootstrap_roi(x);conc=concentration(x);yrs=year_table(x);teams=team_table(x)
        loo=leave_one_season_out(x);loo_rois=[r['roi'] for r in loo if r.get('roi') is not None]
        summaries.append({'family':name,'outcome':out,'price_band':pb,'rules':' AND '.join(f'{a} {b} {c:g}' for a,b,c in rules),
                          **m,'train_n':train.get('n'),'train_roi':train.get('roi'),'validation_n':val.get('n'),'validation_roi':val.get('roi'),
                          'holdout_n':hold.get('n'),'holdout_roi':hold.get('roi'),'bootstrap_lo':ci[0],'bootstrap_hi':ci[1],
                          'loo_min_roi':min(loo_rois) if loo_rois else np.nan,'loo_max_roi':max(loo_rois) if loo_rois else np.nan,**conc})
        loss=loss_rows(x);all_losses.append(loss)
        report += [f"{name} | {out} {pb} | {' AND '.join(f'{a} {b} {c:g}' for a,b,c in rules)}",
                   f"n={m['n']} {m['wins']}-{m['losses']} win={m['win_rate']:.1%} ROI={m['roi']:+.1%} | train={train.get('roi',np.nan):+.1%} val={val.get('roi',np.nan):+.1%} hold={hold.get('roi',np.nan):+.1%} (n={hold.get('n',0)}) | bootstrap95={ci[0]:+.1%}..{ci[1]:+.1%} | LOO={min(loo_rois) if loo_rois else np.nan:+.1%}..{max(loo_rois) if loo_rois else np.nan:+.1%} | teams={conc['teams']} top1={conc['top_team_share']:.1%} top3={conc['top3_team_share']:.1%}",
                   'yearly: '+' | '.join(f"{int(r.season)} n={int(r.n)} ROI={r.roi:+.1%}" for _,r in yrs.iterrows()),
                   'thirds: '+' | '.join(f"{r['third']} n={r.get('n',0)} ROI={r.get('roi',np.nan):+.1%}" for r in thirds(x)),
                   'top teams: '+' | '.join(f"{r.selection_team} n={int(r.n)} ROI={r.roi:+.1%}" for _,r in teams.head(5).iterrows()),
                   f"losses={len(loss)}",'']
        if len(rules)==1:
            stab=threshold_stability(s,out,pb,*rules[0])
            stab.to_csv(OUT/f'{name}_threshold_stability.csv',index=False)
        price_stability(s,out,rules).to_csv(OUT/f'{name}_price_stability.csv',index=False)
        yrs.to_csv(OUT/f'{name}_yearly.csv',index=False)
        teams.to_csv(OUT/f'{name}_teams.csv',index=False)
        loss.to_csv(OUT/f'{name}_losses.csv',index=False)
        pd.DataFrame(loo).to_csv(OUT/f'{name}_loo.csv',index=False)
    pd.DataFrame(summaries).sort_values('holdout_roi' if 'holdout_roi' in pd.DataFrame(summaries).columns else 'roi',ascending=False).to_csv(OUT/'family_summary.csv',index=False)
    if all_losses:pd.concat(all_losses,ignore_index=True).to_csv(OUT/'all_losses.csv',index=False)
    (OUT/'report.txt').write_text('\n'.join(report)+'\n')
    print('\n'.join(report))
if __name__=='__main__':main()
