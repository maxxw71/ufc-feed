#!/usr/bin/env python3
from __future__ import annotations
import itertools,json,math,re
from pathlib import Path
import numpy as np,pandas as pd
import mls_autoresearch as ar

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'research/failure_sieve';OUT.mkdir(parents=True,exist_ok=True)

FAMILIES=[
 {'id':'C01','outcome':'HOME','pb':'P40_50','rules':[('edge_last3_xgfpg','>=',0.45538),('elo_edge','<=',0.91)]},
 {'id':'C02','outcome':'HOME','pb':'P40_50','rules':[('edge_last3_xgfpg','>=',0.45538),('edge_last3_xgapg','>=',0.0536)]},
 {'id':'C03','outcome':'HOME','pb':'P40_50','rules':[('edge_last3_xgfpg','>=',0.45538),('edge_season_gdpg','<=',-0.251028)]},
 {'id':'C04','outcome':'HOME','pb':'P45_55','rules':[('sel_prior_road_miles5','>=',7092.68),('sel_last10_ppg','<=',1.5)]},
 {'id':'S_ELO_AWAY','outcome':'AWAY','pb':'P25_35','rules':[('elo_edge','<=',-41.31)]},
]

def cond(df,feat,op,t):
    v=pd.to_numeric(df[feat],errors='coerce')
    return v.ge(t) if op=='>=' else v.le(t)
def family_mask(df,f):
    m=ar.pmask(df,f['pb'])
    for feat,op,t in f['rules']:m &= cond(df,feat,op,t)
    return m
def met(df):
    m=ar.metrics(df)
    return m or {'n':0,'wins':0,'losses':0,'win_rate':np.nan,'roi':np.nan,'units':0,'active_seasons':0,'positive_seasons':0}
def period(df,name):return df[ar.period(df,name)]
def risk_stats(base,mask):
    risk=base[mask];safe=base[~mask]
    return met(risk),met(safe)
def fmt_rule(r):return f"{r['feature']} {r['op']} {r['threshold']:.6g}"
def main():
    d=pd.read_parquet(ar.DATA);s=ar.selection_rows(d)
    all_summary=[];all_vetoes=[];report=['MLS FAILURE-MODE SIEVE','='*100,
      'Veto discovery uses only 2012-2018 train + 2019-2022 validation. 2023-2025 holdout is opened only after the veto is frozen.',
      'SHADOW ONLY. No promotion decisions are made here.','']
    for fam in FAMILIES:
        so=s[s.outcome.eq(fam['outcome'])].copy();m0=family_mask(so,fam);base=so[m0].copy()
        tr=period(base,'train');va=period(base,'validation');ho=period(base,'holdout')
        bmtr,bmva,bmho,bmfull=met(tr),met(va),met(ho),met(base)
        fam_feats={x[0] for x in fam['rules']}
        feats=[x for x in ar.eligible_features(so,fam['outcome']) if x not in fam_feats]
        candidates=[]
        for feat in feats:
            vals=pd.to_numeric(tr[feat],errors='coerce').dropna()
            if len(vals)<30 or vals.nunique()<8:continue
            for t in sorted(set(float(x) for x in vals.quantile([.20,.35,.50,.65,.80]).dropna())):
                for op in ['>=','<=']:
                    rtr=cond(tr,feat,op,t);rva=cond(va,feat,op,t)
                    risk_tr,safe_tr=risk_stats(tr,rtr);risk_va,safe_va=risk_stats(va,rva)
                    if risk_tr['n']<max(6,int(.10*len(tr))) or risk_va['n']<max(5,int(.10*len(va))):continue
                    if safe_tr['n']<max(20,int(.55*len(tr))) or safe_va['n']<max(15,int(.55*len(va))):continue
                    loss_lift_tr=(1-risk_tr['win_rate'])-(1-bmtr['win_rate'])
                    loss_lift_va=(1-risk_va['win_rate'])-(1-bmva['win_rate'])
                    roi_drop_tr=bmtr['roi']-risk_tr['roi'];roi_drop_va=bmva['roi']-risk_va['roi']
                    imp_tr=safe_tr['roi']-bmtr['roi'];imp_va=safe_va['roi']-bmva['roi']
                    if loss_lift_tr<.07 or loss_lift_va<.07:continue
                    if roi_drop_tr<.08 or roi_drop_va<.08:continue
                    if imp_tr<-.01 or imp_va<-.01:continue
                    if (imp_tr+imp_va)/2<.025:continue
                    score=(imp_tr+imp_va)/2 + .10*(loss_lift_tr+loss_lift_va)/2 + .002*math.sqrt(safe_tr['n']+safe_va['n'])
                    candidates.append({'family':fam['id'],'feature':feat,'op':op,'threshold':t,'score':score,
                       'train_risk_n':risk_tr['n'],'train_risk_roi':risk_tr['roi'],'train_safe_n':safe_tr['n'],'train_safe_roi':safe_tr['roi'],
                       'validation_risk_n':risk_va['n'],'validation_risk_roi':risk_va['roi'],'validation_safe_n':safe_va['n'],'validation_safe_roi':safe_va['roi'],
                       'train_improvement':imp_tr,'validation_improvement':imp_va,'train_loss_lift':loss_lift_tr,'validation_loss_lift':loss_lift_va})
        # one threshold per feature/direction, preholdout score only
        candidates=sorted(candidates,key=lambda x:x['score'],reverse=True)
        ded=[];seen=set()
        for r in candidates:
            k=(r['feature'],r['op'])
            if k in seen:continue
            seen.add(k);ded.append(r)
        top=ded[:12]
        all_vetoes.extend(top)
        frozen=[]
        if top:
            frozen.append(('single',[top[0]]))
        # pair search using only train/validation to select.
        best_pair=None
        for a,b in itertools.combinations(top[:8],2):
            if a['feature']==b['feature']:continue
            def keep(df):
                risk=cond(df,a['feature'],a['op'],a['threshold']) | cond(df,b['feature'],b['op'],b['threshold'])
                return df[~risk]
            st,sv=keep(tr),keep(va);mt,mv=met(st),met(sv)
            if mt['n']<max(18,int(.45*len(tr))) or mv['n']<max(13,int(.45*len(va))):continue
            it,iv=mt['roi']-bmtr['roi'],mv['roi']-bmva['roi']
            if it<.02 or iv<.02:continue
            score=(it+iv)/2+.002*math.sqrt(mt['n']+mv['n'])
            if best_pair is None or score>best_pair[0]:best_pair=(score,[a,b],mt,mv)
        if best_pair:frozen.append(('pair',best_pair[1]))
        report += [f"{fam['id']} | {fam['outcome']} {fam['pb']} | "+' AND '.join(f'{a} {b} {t:g}' for a,b,t in fam['rules']),
                   f"BASE full n={bmfull['n']} ROI={bmfull['roi']:+.1%} | train n={bmtr['n']} ROI={bmtr['roi']:+.1%} | val n={bmva['n']} ROI={bmva['roi']:+.1%} | hold n={bmho['n']} ROI={bmho['roi']:+.1%}",
                   f"preholdout veto candidates={len(ded)}"]
        for rank,r in enumerate(top[:5],1):
            report.append(f"  veto{rank}: {fmt_rule(r)} | train safe {r['train_safe_n']} ROI={r['train_safe_roi']:+.1%} vs risk {r['train_risk_n']} ROI={r['train_risk_roi']:+.1%} | val safe {r['validation_safe_n']} ROI={r['validation_safe_roi']:+.1%} vs risk {r['validation_risk_n']} ROI={r['validation_risk_roi']:+.1%}")
        for typ,rules in frozen:
            def keep(df):
                risk=pd.Series(False,index=df.index)
                for r in rules:risk |= cond(df,r['feature'],r['op'],r['threshold'])
                return df[~risk]
            st,sv,sh,sf=keep(tr),keep(va),keep(ho),keep(base)
            mt,mv,mh,mf=met(st),met(sv),met(sh),met(sf)
            row={'family':fam['id'],'type':typ,'rules':' OR '.join(fmt_rule(r) for r in rules),
                 'base_full_n':bmfull['n'],'base_full_roi':bmfull['roi'],'base_holdout_n':bmho['n'],'base_holdout_roi':bmho['roi'],
                 'filtered_full_n':mf['n'],'filtered_full_roi':mf['roi'],'filtered_train_n':mt['n'],'filtered_train_roi':mt['roi'],
                 'filtered_validation_n':mv['n'],'filtered_validation_roi':mv['roi'],'filtered_holdout_n':mh['n'],'filtered_holdout_roi':mh['roi'],
                 'holdout_roi_delta':mh['roi']-bmho['roi'] if mh['n'] else np.nan,'holdout_removed':bmho['n']-mh['n']}
            all_summary.append(row)
            report.append(f"  FROZEN {typ}: {row['rules']} | full {mf['n']} ROI={mf['roi']:+.1%} | train={mt['roi']:+.1%} val={mv['roi']:+.1%} | HOLDOUT={mh['roi']:+.1%} (n={mh['n']}, delta={row['holdout_roi_delta']:+.1%}, removed={row['holdout_removed']})")
        report.append('')
    pd.DataFrame(all_vetoes).to_csv(OUT/'preholdout_veto_candidates.csv',index=False)
    pd.DataFrame(all_summary).to_csv(OUT/'frozen_veto_holdout.csv',index=False)
    (OUT/'report.txt').write_text('\n'.join(report)+'\n')
    print('\n'.join(report))
if __name__=='__main__':main()
