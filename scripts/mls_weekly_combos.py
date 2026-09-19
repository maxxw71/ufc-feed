#!/usr/bin/env python3
from __future__ import annotations
import itertools,json,math
from datetime import datetime,timezone
from pathlib import Path
import numpy as np,pandas as pd
import mls_autoresearch as ar

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'auto_research/reports'
OUT.mkdir(parents=True,exist_ok=True)

def main():
    d=pd.read_parquet(ar.DATA)
    s=ar.selection_rows(d)
    pre=[]
    tested_single=0
    for outcome in ['HOME','DRAW','AWAY']:
        so=s[s.outcome.eq(outcome)].copy()
        tr=so[ar.period(so,'train')]
        feats=ar.eligible_features(so,outcome)
        for feat in feats:
            vals=pd.to_numeric(tr[feat],errors='coerce').dropna()
            if len(vals)<200:continue
            for t in sorted(set(float(x) for x in vals.quantile([.2,.35,.5,.65,.8]).dropna())):
                for op in ['>=','<=']:
                    cm=pd.to_numeric(so[feat],errors='coerce').ge(t) if op=='>=' else pd.to_numeric(so[feat],errors='coerce').le(t)
                    for pb,_,__ in ar.PRICE_BANDS:
                        tested_single+=1
                        m=cm&ar.pmask(so,pb)
                        mt=ar.metrics(so[m&ar.period(so,'train')])
                        if not mt or mt['n']<35 or mt['roi']<.04:continue
                        mv=ar.metrics(so[m&ar.period(so,'validation')])
                        if not mv or mv['n']<25 or mv['roi']<.03:continue
                        score=min(mt['roi'],mv['roi'])*math.sqrt(min(mt['n'],mv['n']))
                        pre.append({'score':score,'outcome':outcome,'pb':pb,'feature':feat,'op':op,'t':t})
    # diversify before pair creation
    pre=sorted(pre,key=lambda x:x['score'],reverse=True)
    diverse=[];caps={}
    for r in pre:
        key=(r['outcome'],r['pb'],r['feature'])
        if caps.get(key,0)>=1:continue
        diverse.append(r);caps[key]=1
        if len(diverse)>=180:break
    combos=[];tested_pairs=0
    groups={}
    for r in diverse:groups.setdefault((r['outcome'],r['pb']),[]).append(r)
    for (outcome,pb),rules in groups.items():
        so=s[s.outcome.eq(outcome)].copy()
        for a,b in itertools.combinations(rules[:32],2):
            if a['feature']==b['feature']:continue
            tested_pairs+=1
            va=pd.to_numeric(so[a['feature']],errors='coerce')
            vb=pd.to_numeric(so[b['feature']],errors='coerce')
            ma=va.ge(a['t']) if a['op']=='>=' else va.le(a['t'])
            mb=vb.ge(b['t']) if b['op']=='>=' else vb.le(b['t'])
            m=ma&mb&ar.pmask(so,pb)
            ev=ar.evaluate(so,m)
            if not ev:continue
            tr,vl,ho,fu=ev['train'],ev['validation'],ev['holdout'],ev['full']
            # Interaction gate: enough sample and every era profitable.
            if not (tr['n']>=30 and vl['n']>=20 and ho['n']>=25 and fu['n']>=100):continue
            if not (tr['roi']>=.06 and vl['roi']>=.05 and ho['roi']>=.05 and fu['roi']>=.08):continue
            if ev['positive_ratio']<.65:continue
            combos.append({'outcome':outcome,'price_band':pb,
                           'rule1':f"{a['feature']} {a['op']} {a['t']:.6g}",
                           'rule2':f"{b['feature']} {b['op']} {b['t']:.6g}",
                           'n':fu['n'],'wins':fu['wins'],'losses':fu['losses'],'win_rate':fu['win_rate'],'roi':fu['roi'],
                           'train_n':tr['n'],'train_roi':tr['roi'],'validation_n':vl['n'],'validation_roi':vl['roi'],
                           'holdout_n':ho['n'],'holdout_roi':ho['roi'],'positive_ratio':ev['positive_ratio']})
    combos=sorted(combos,key=lambda x:(x['holdout_roi'],x['n']),reverse=True)
    # Dedupe very similar rule pairs.
    seen=set();chosen=[]
    for r in combos:
        key=(r['outcome'],r['price_band'],tuple(sorted([r['rule1'].split()[0],r['rule2'].split()[0]])))
        if key in seen:continue
        seen.add(key);chosen.append(r)
        if len(chosen)>=60:break
    pd.DataFrame(chosen).to_csv(OUT/'weekly_combos.csv',index=False)
    lines=['MLS WEEKLY INTERACTION SEARCH','='*100,
           f'selection_rows={len(s):,} single_contexts_tested={tested_single:,} pair_contexts_tested={tested_pairs:,} survivors={len(chosen)}',
           'Rules were screened on 2012-18 train + 2019-22 validation before 2023-25 holdout evaluation.',
           'SHADOW ONLY — no live/email/website promotion.','',
           'TOP COMBINATIONS']
    for r in chosen[:30]:
        lines.append(f"{r['outcome']} {r['price_band']} | {r['rule1']} AND {r['rule2']} | n={r['n']} {r['wins']}-{r['losses']} win={r['win_rate']:.1%} ROI={r['roi']:+.1%} | train={r['train_roi']:+.1%} val={r['validation_roi']:+.1%} hold={r['holdout_roi']:+.1%} n={r['holdout_n']} | +season={r['positive_ratio']:.0%}")
    report='\n'.join(lines)+'\n'
    (OUT/'weekly_latest.txt').write_text(report)
    (OUT/f'{datetime.now().strftime("%Y%m%d_%H%M%S")}_weekly_combos.txt').write_text(report)
    print(report)
if __name__=='__main__':main()
