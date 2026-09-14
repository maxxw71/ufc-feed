from __future__ import annotations
from pathlib import Path
import json, os
import numpy as np
import pandas as pd

CTX=Path(os.environ.get('NFL_CONTEXT_ROOT','/home/appwiza-runner/nfl-context-data'))
IN=CTX/'method_audit'/'method_all_bets_context.csv'
OUT=CTX/'method_validation'; OUT.mkdir(parents=True,exist_ok=True)
if not IN.exists(): raise FileNotFoundError(IN)
d=pd.read_csv(IN)

def met(x):
    n=len(x)
    if not n:return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi_pct':np.nan,'profit':0.0}
    p=float(x.bet_profit100.sum()); w=int(x.win.sum())
    return {'n':n,'wins':w,'losses':n-w,'win_pct':100*w/n,'roi_pct':p/n,'profit':p}

def mask(df,col):
    s=df[col]
    return s.astype(str).str.lower().eq('true') | (pd.to_numeric(s,errors='coerce')==1)

def calc(fam,factor,lo,hi):
    z=d[(d.family==fam)&d.season.between(lo,hi)].copy(); b=met(z); r=mask(z,factor)
    risk=met(z[r]); safe=met(z[~r])
    return {'family':fam,'factor':factor.replace('risk_',''),'period':f'{lo}_{hi}',
            **{f'base_{k}':v for k,v in b.items()},**{f'risk_{k}':v for k,v in risk.items()},**{f'safe_{k}':v for k,v in safe.items()},
            'roi_change_if_veto':safe['roi_pct']-b['roi_pct'] if safe['n'] else np.nan}

CANDIDATES=[
 ('YPP OFF vs LEAKY DEF','risk_RETURN_SKILL_LT60'),
 ('PASS RUSH vs WEAK PROTECTION','risk_OPP_HC_CHANGED'),
 ('PASS OFF vs ELITE PASS DEF','risk_PRESEASON_FORM_DISADVANTAGE'),
 ('EPA + YPP DEFENSE','risk_PRESEASON_FORM_DISADVANTAGE'),
 ('EPA + YPP DEFENSE','risk_DC_CHANGED'),
 ('EPA + YPP DEFENSE','risk_OPP_OC_CHANGED'),
 ('SCORING + DEFENSE','risk_RETURN_SKILL_LT70'),
 ('ELITE OFF vs ELITE DEF','risk_PRESEASON_LOSING'),
 ('ELITE OFF vs ELITE DEF','risk_QB_CHANGED'),
 ('BALANCED ELITE','risk_RETURN_OL_LT70'),
 ('ELITE DEF vs WEAK OFF','risk_RETURN_SKILL_LT70'),
]
rows=[]
for fam,f in CANDIDATES:
    for lo,hi in [(2016,2020),(2021,2025),(2016,2025)]: rows.append(calc(fam,f,lo,hi))
    if f in {'risk_DC_CHANGED','risk_OPP_OC_CHANGED','risk_PRESEASON_FORM_DISADVANTAGE'} and fam=='EPA + YPP DEFENSE':
        rows.append(calc(fam,f,2023,2023)); rows.append(calc(fam,f,2024,2025))
res=pd.DataFrame(rows); res.to_csv(OUT/'fixed_candidate_splits.csv',index=False)

# Honest early-era selection: choose one non-coordinator factor using 2016-2020 only, then grade 2021-2025 untouched.
# This answers whether a generic context-veto discovery process would actually generalize.
EXCLUDE={'risk_OC_CHANGED','risk_DC_CHANGED','risk_STAFF_CHANGES_GE2','risk_OPP_OC_CHANGED','risk_OPP_DC_CHANGED','risk_OPP_STAFF_CHANGES_GE2'}
RISK=[c for c in d.columns if c.startswith('risk_') and c not in EXCLUDE]
selrows=[]
for fam in sorted(d.family.unique()):
    tr=d[(d.family==fam)&d.season.between(2016,2020)].copy(); te=d[(d.family==fam)&d.season.between(2021,2025)].copy(); bm=met(tr)
    cand=[]
    for f in RISK:
        known=tr[f].notna(); r=mask(tr,f); rz=tr[known&r]; sz=tr[known&~r]
        mr,ms=met(rz),met(sz)
        if int(known.sum())<15 or mr['n']<5 or ms['n']<5: continue
        if mr['roi_pct']>=ms['roi_pct'] or mr['win_pct']>=ms['win_pct']: continue
        kept=tr[~r]; mk=met(kept); imp=mk['roi_pct']-bm['roi_pct']
        if imp<=0: continue
        score=imp*np.sqrt(mr['n'])
        cand.append((score,f,imp,mr,ms,mk))
    cand.sort(reverse=True,key=lambda x:x[0])
    chosen=cand[0][1] if cand else None
    test_base=met(te)
    test_new=met(te[~mask(te,chosen)]) if chosen else test_base
    selrows.append({'family':fam,'selected_factor':None if chosen is None else chosen.replace('risk_',''),
                    'train_base_n':bm['n'],'train_base_roi_pct':bm['roi_pct'],
                    'test_base_n':test_base['n'],'test_base_wins':test_base['wins'],'test_base_losses':test_base['losses'],'test_base_roi_pct':test_base['roi_pct'],
                    'test_filtered_n':test_new['n'],'test_filtered_wins':test_new['wins'],'test_filtered_losses':test_new['losses'],'test_filtered_roi_pct':test_new['roi_pct'],
                    'holdout_roi_change':test_new['roi_pct']-test_base['roi_pct']})
sel=pd.DataFrame(selrows); sel.to_csv(OUT/'early_selected_late_holdout.csv',index=False)

# Decision labels. Coordinator factors cannot be called validated because historical coverage begins only in 2021.
decisions=[]
for fam,f in CANDIDATES:
    name=f.replace('risk_',''); early=res[(res.family==fam)&(res.factor==name)&(res.period=='2016_2020')]
    late=res[(res.family==fam)&(res.factor==name)&(res.period=='2021_2025')]
    status='REJECT'
    reason='Did not show adequate two-era confirmation.'
    if name in {'DC_CHANGED','OPP_OC_CHANGED','OC_CHANGED','OPP_DC_CHANGED','STAFF_CHANGES_GE2','OPP_STAFF_CHANGES_GE2'}:
        status='PROSPECTIVE_ONLY'; reason='Coordinator coverage is complete only from 2021; freeze as a warning/candidate for 2026+.'
    elif len(early) and len(late):
        a=early.iloc[0]; b=late.iloc[0]
        if a.risk_n>=5 and a.safe_n>=5 and b.risk_n>=5 and b.safe_n>=5 and a.risk_roi_pct<a.safe_roi_pct and b.risk_roi_pct<b.safe_roi_pct:
            status='TWO_ERA_SUPPORTED'; reason='Risk bucket underperformed safe bucket in both 2016-2020 and 2021-2025.'
    decisions.append({'family':fam,'factor':name,'status':status,'reason':reason})
pd.DataFrame(decisions).to_csv(OUT/'candidate_decisions.csv',index=False)

report=['NFL CONTEXT CANDIDATE VALIDATION']
for _,r in sel.iterrows():
    report.append(f"{r.family}: early-selected={r.selected_factor or 'NONE'} | 2021-25 base ROI {r.test_base_roi_pct:+.2f}% -> filtered {r.test_filtered_roi_pct:+.2f}% ({r.holdout_roi_change:+.2f} pts), n {int(r.test_base_n)}->{int(r.test_filtered_n)}")
report.append('\nFIXED CANDIDATE DECISIONS')
for x in decisions: report.append(f"{x['family']} / {x['factor']}: {x['status']} — {x['reason']}")
(OUT/'validation_report.txt').write_text('\n'.join(report)+'\n')
print('\n'.join(report))
