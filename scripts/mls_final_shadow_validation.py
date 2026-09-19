#!/usr/bin/env python3
from __future__ import annotations
import json,math
from pathlib import Path
import numpy as np,pandas as pd
import mls_autoresearch as ar

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'research/final_validation';OUT.mkdir(parents=True,exist_ok=True)

METHODS=[
 {'id':'MLS-R01','name':'Home xG Surge vs Market/Elo',
  'outcome':'HOME','pb':'P40_50',
  'rules':[('edge_last3_xgfpg','>=',0.45538),('elo_edge','<=',0.91)]},
 {'id':'MLS-R02','name':'Home xG Surge + Defensive Instability',
  'outcome':'HOME','pb':'P40_50',
  'rules':[('edge_last3_xgfpg','>=',0.45538),('edge_last3_xgapg','>=',0.0536)]},
 {'id':'MLS-R03','name':'Away Elo Underdog + xGA Veto',
  'outcome':'AWAY','pb':'P25_35',
  'rules':[('elo_edge','<=',-41.31),('edge_last5_xgapg','<',0.01113)]},
 {'id':'MLS-W01','name':'Home xG Surge vs Season GD + Opponent GD Veto',
  'outcome':'HOME','pb':'P40_50',
  'rules':[('edge_last3_xgfpg','>=',0.45538),('edge_season_gdpg','<=',-0.251028),('opp_season_gdpg','<',0.381169)]},
]

def cnd(df,feat,op,t):
    v=pd.to_numeric(df[feat],errors='coerce')
    if op=='>=':return v.ge(t)
    if op=='<=':return v.le(t)
    if op=='<':return v.lt(t)
    if op=='>':return v.gt(t)
    raise ValueError(op)
def mask(df,m,pb=None,rules=None):
    x=ar.pmask(df,pb or m['pb'])
    for feat,op,t in (rules or m['rules']):x &= cnd(df,feat,op,t)
    return x
def met(df):return ar.metrics(df) or {'n':0,'wins':0,'losses':0,'win_rate':np.nan,'roi':np.nan,'units':0,'active_seasons':0,'positive_seasons':0}
def periods(x):
    return {k:met(x[ar.period(x,k)]) for k in ['train','validation','holdout']}|{'full':met(x)}
def boot(v,n=5000,seed=11):
    if len(v)<2:return (np.nan,np.nan)
    rng=np.random.default_rng(seed);a=np.asarray(v,float)
    means=np.array([rng.choice(a,size=len(a),replace=True).mean() for _ in range(n)])
    return tuple(np.quantile(means,[.025,.975]))
def loo(x):
    vals=[]
    for y in sorted(x.season.unique()):
        z=x[~x.season.eq(y)]
        vals.append((int(y),float(z.profit.mean())))
    return vals
def thirds(x):
    x=x.sort_values('date').reset_index(drop=True);rows=[]
    for i,idx in enumerate(np.array_split(np.arange(len(x)),3),1):
        z=x.iloc[idx];rows.append((i,len(z),float(z.profit.mean()) if len(z) else np.nan))
    return rows
def neighbor_rules(m):
    if m['id']=='MLS-R01':
        for a in [0.35,0.40,0.45538,0.50,0.55]:
            for e in [-20,0.91,20]:
                yield [('edge_last3_xgfpg','>=',a),('elo_edge','<=',e)]
    elif m['id']=='MLS-R02':
        for a in [0.35,0.40,0.45538,0.50,0.55]:
            for b in [-0.05,0.0,0.0536,0.10,0.15]:
                yield [('edge_last3_xgfpg','>=',a),('edge_last3_xgapg','>=',b)]
    elif m['id']=='MLS-R03':
        for e in [-60,-50,-41.31,-30,-20]:
            for v in [-0.10,-0.05,0.01113,0.05,0.10]:
                yield [('elo_edge','<=',e),('edge_last5_xgapg','<',v)]
    else:
        for a in [0.40,0.45538,0.50]:
            for g in [-0.35,-0.251028,-0.15]:
                for o in [0.25,0.381169,0.50]:
                    yield [('edge_last3_xgfpg','>=',a),('edge_season_gdpg','<=',g),('opp_season_gdpg','<',o)]
def main():
    d=pd.read_parquet(ar.DATA);s=ar.selection_rows(d)
    registry=[];report=['MLS FINAL SHADOW VALIDATION','='*100,
      'Primary candidate rules are frozen from prior train/validation discovery. Neighbor grids assess local stability; they do not redefine the center rule.',
      '2023-2025 remains the final historical holdout. 2026 is excluded from all metrics.','']
    for m in METHODS:
        so=s[s.outcome.eq(m['outcome'])].copy()
        x=so[mask(so,m)].copy();p=periods(x);ci=boot(x.profit);l=loo(x);th=thirds(x)
        pos=p['full']['positive_seasons']/max(1,p['full']['active_seasons'])
        report += [f"{m['id']} — {m['name']}",
          f"RULE: {m['outcome']} {m['pb']} | "+' AND '.join(f'{f} {o} {t:g}' for f,o,t in m['rules']),
          f"FULL n={p['full']['n']} {p['full']['wins']}-{p['full']['losses']} win={p['full']['win_rate']:.1%} ROI={p['full']['roi']:+.1%} units={p['full']['units']:+.2f}",
          f"TRAIN n={p['train']['n']} ROI={p['train']['roi']:+.1%} | VAL n={p['validation']['n']} ROI={p['validation']['roi']:+.1%} | HOLD n={p['holdout']['n']} ROI={p['holdout']['roi']:+.1%}",
          f"bootstrap95={ci[0]:+.1%}..{ci[1]:+.1%} | LOO={min(v for _,v in l):+.1%}..{max(v for _,v in l):+.1%} | +season={pos:.0%}",
          'thirds: '+' | '.join(f'{i}: n={n} ROI={r:+.1%}' for i,n,r in th)]
        neigh=[]
        for rules in neighbor_rules(m):
            y=so[mask(so,m,rules=rules)].copy();pp=periods(y)
            if pp['holdout']['n']<20 or pp['full']['n']<60:continue
            neigh.append({'rules':' AND '.join(f'{f} {o} {t:g}' for f,o,t in rules),
                          'n':pp['full']['n'],'roi':pp['full']['roi'],'train_roi':pp['train']['roi'],
                          'validation_roi':pp['validation']['roi'],'holdout_n':pp['holdout']['n'],'holdout_roi':pp['holdout']['roi']})
        nd=pd.DataFrame(neigh)
        if len(nd):
            nd.to_csv(OUT/f"{m['id']}_neighbors.csv",index=False)
            stable=nd[(nd.train_roi>0)&(nd.validation_roi>0)&(nd.holdout_roi>0)]
            report.append(f"neighbors tested={len(nd)} positive_all_eras={len(stable)} ({len(stable)/len(nd):.0%}) holdout ROI range={nd.holdout_roi.min():+.1%}..{nd.holdout_roi.max():+.1%}")
        price=[]
        for pb,_,__ in ar.PRICE_BANDS:
            y=so[mask(so,m,pb=pb)].copy();pp=periods(y)
            if pp['full']['n']:
                price.append({'price_band':pb,'n':pp['full']['n'],'roi':pp['full']['roi'],'holdout_n':pp['holdout']['n'],'holdout_roi':pp['holdout']['roi']})
        pd.DataFrame(price).to_csv(OUT/f"{m['id']}_price.csv",index=False)
        status='SHADOW_READY'
        if p['holdout']['n']<30 or p['holdout']['roi']<=0 or ci[0]<=0:status='WATCHLIST'
        if m['id']=='MLS-W01':status='WATCHLIST'
        registry.append({'id':m['id'],'name':m['name'],'status':status,'outcome':m['outcome'],'price_band':m['pb'],'rules':m['rules'],
                         'n':p['full']['n'],'wins':p['full']['wins'],'losses':p['full']['losses'],'win_rate':p['full']['win_rate'],'roi':p['full']['roi'],
                         'train_roi':p['train']['roi'],'validation_roi':p['validation']['roi'],'holdout_n':p['holdout']['n'],'holdout_roi':p['holdout']['roi'],
                         'bootstrap95':[ci[0],ci[1]],'positive_season_ratio':pos})
        report.append('STATUS: '+status);report.append('')
    (OUT/'registry.json').write_text(json.dumps({'built_at':pd.Timestamp.utcnow().isoformat(),'methods':registry,'official_autopromotions':0},indent=2))
    (OUT/'report.txt').write_text('\n'.join(report)+'\n')
    print('\n'.join(report))
if __name__=='__main__':main()
