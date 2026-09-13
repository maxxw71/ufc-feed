#!/usr/bin/env python3
"""Final combined-risk validation for the UFC Striking + TD Defense method.

This pass combines the two independently motivated risk dimensions discovered
in the postmortem: age conflict and opponent-power/favorite-durability risk.
It is intentionally small-grid and reports neighboring thresholds so a live
rule is only considered if performance is stable rather than a single lucky cut.
"""
from pathlib import Path
import numpy as np
import pandas as pd

OUT=Path('ufc_striking_td_postmortem')
IN=OUT/'baseline_bets_with_age.csv'

def roi(g):
    return float(g.profit100.sum()/(100*len(g))) if len(g) else np.nan

def metrics(g):
    old=g[g.event_date<pd.Timestamp('2020-01-01')]
    rec=g[g.event_date>=pd.Timestamp('2020-01-01')]
    y22=g[g.event_date>=pd.Timestamp('2022-01-01')]
    w=int(g.won.sum()) if len(g) else 0
    return dict(n=len(g),wins=w,losses=len(g)-w,win_rate=float(g.won.mean()) if len(g) else np.nan,
                roi=roi(g),pre_n=len(old),pre_roi=roi(old),recent_n=len(rec),recent_roi=roi(rec),
                y2022_n=len(y22),y2022_roi=roi(y22))

def main():
    if not IN.exists(): raise SystemExit(f'Missing {IN}')
    b=pd.read_csv(IN,low_memory=False)
    b['event_date']=pd.to_datetime(b.event_date,errors='coerce').dt.normalize()
    b['won']=b.won.astype(str).str.lower().isin({'true','1','yes','w','win'})
    for c in ['age_adv_repaired','o_kd15','f_kd_abs15','power_risk_product','market_prob']:
        b[c]=pd.to_numeric(b[c],errors='coerce')
    base=metrics(b)

    rows=[]
    age_thresholds=[-3,-2,-1,0,1]
    power_thresholds=[.10,.11,.12,.13,.14,.15]
    for age_min in age_thresholds:
        for pmax in power_thresholds:
            keep=(b.age_adv_repaired>=age_min)&(b.power_risk_product<pmax)
            g=b[keep.fillna(False)].copy(); rm=b[~keep.fillna(False)].copy()
            s=metrics(g); r=metrics(rm)
            rows.append({'age_min':age_min,'power_max':pmax,**s,
                         'removed_n':r['n'],'removed_wins':r['wins'],'removed_losses':r['losses'],
                         'removed_roi':r['roi'],
                         'loss_capture_rate':r['losses']/max(1,base['losses']),
                         'winner_removal_rate':r['wins']/max(1,base['wins'])})
    z=pd.DataFrame(rows)
    z['roi_change_pp']=(z.roi-base['roi'])*100
    z['win_change_pp']=(z.win_rate-base['win_rate'])*100
    z['sample_retained']=z.n/base['n']
    z['robust_screen']=(z.n>=30)&(z.pre_n>=10)&(z.recent_n>=10)&(z.pre_roi>0)&(z.recent_roi>0)&(z.y2022_roi>0)&(z.roi>base['roi'])&(z.loss_capture_rate>=2*z.winner_removal_rate)
    z=z.sort_values(['robust_screen','roi','n'],ascending=[False,False,False])
    z.to_csv(OUT/'combined_risk_grid.csv',index=False)

    # Neighborhood stability: evaluate each cell against one-step age/power neighbors.
    lookup={(float(r.age_min),float(r.power_max)):r for _,r in z.iterrows()}
    nr=[]
    for _,r in z.iterrows():
        ai=age_thresholds.index(int(r.age_min)); pi=power_thresholds.index(round(float(r.power_max),2))
        neighbors=[]
        for da,dp in [(-1,0),(1,0),(0,-1),(0,1)]:
            aa,pp=ai+da,pi+dp
            if 0<=aa<len(age_thresholds) and 0<=pp<len(power_thresholds):
                q=lookup.get((float(age_thresholds[aa]),float(power_thresholds[pp])))
                if q is not None: neighbors.append(q)
        rois=np.array([float(q.roi) for q in neighbors]) if neighbors else np.array([])
        both=all((q.pre_roi>0 and q.recent_roi>0) for q in neighbors)
        nr.append({**r.to_dict(),'neighbors':len(neighbors),
                   'neighbor_mean_roi':float(rois.mean()) if len(rois) else np.nan,
                   'neighbor_min_roi':float(rois.min()) if len(rois) else np.nan,
                   'neighbors_both_eras_positive':both,
                   'stable_center':bool(r.robust_screen and len(neighbors)>=3 and both and np.all(rois>base['roi']))})
    ndf=pd.DataFrame(nr).sort_values(['stable_center','roi','n'],ascending=[False,False,False])
    ndf.to_csv(OUT/'combined_risk_stability.csv',index=False)

    # Explicit recommended center is only selected from stable centers and favors sample size + central thresholds.
    stable=ndf[ndf.stable_center].copy()
    recommendation=None
    if len(stable):
        stable['distance_from_center']=(stable.age_min-(-1)).abs()+10*(stable.power_max-.12).abs()
        stable=stable.sort_values(['distance_from_center','n','roi'],ascending=[True,False,False])
        recommendation=stable.iloc[0]

    # Fiorot-Grasso case already reconstructed in phase 2.
    case_path=OUT/'fiorot_grasso_prefight_case.csv'
    case=pd.read_csv(case_path).iloc[0] if case_path.exists() else None

    lines=['UFC STRIKING + TD DEFENSE — FINAL COMBINED-RISK VALIDATION','='*108,'',
           f"Baseline: {base['n']} bets | {base['wins']}-{base['losses']} | win {base['win_rate']*100:.1f}% | ROI {base['roi']*100:+.2f}%",
           '', 'Stable combined age + power centers','-'*108]
    centers=ndf[ndf.stable_center]
    if centers.empty:
        lines.append('None. Do not deploy a combined hard veto.')
    else:
        for _,r in centers.head(20).iterrows():
            lines.append(f"ageAdv>={r.age_min:+.0f}y AND powerProduct<{r.power_max:.2f} | n={int(r.n):2d} {int(r.wins)}-{int(r.losses)} | win={r.win_rate*100:5.1f}% ROI={r.roi*100:+6.2f}% | pre={r.pre_roi*100:+6.2f}% recent={r.recent_roi*100:+6.2f}% 2022+={r.y2022_roi*100:+6.2f}% | removed {int(r.removed_wins)}W/{int(r.removed_losses)}L | neighbor min={r.neighbor_min_roi*100:+6.2f}%")
    lines+=['','Recommended center','-'*108]
    if recommendation is None:
        lines.append('No recommendation.')
    else:
        r=recommendation
        lines.append(f"Keep only when favorite age advantage >= {r.age_min:+.0f} years AND power risk product < {r.power_max:.2f}.")
        lines.append(f"Historical: {int(r.n)} bets | {int(r.wins)}-{int(r.losses)} | {r.win_rate*100:.1f}% wins | ROI {r.roi*100:+.2f}% | pre {r.pre_roi*100:+.2f}% | recent {r.recent_roi*100:+.2f}% | 2022+ {r.y2022_roi*100:+.2f}%.")
        lines.append(f"Removed: {int(r.removed_wins)} winners / {int(r.removed_losses)} losses from the original 54.")
        if case is not None:
            age=float(case['age_adv']); power=float(case['power_risk_product'])
            passes=(age>=float(r.age_min)) and (power<float(r.power_max))
            lines.append(f"Fiorot-Grasso: ageAdv={age:+.2f}y, powerProduct={power:.3f} => {'KEEP' if passes else 'VETO'} under recommended center.")
    lines+=['','Important','-'*108,
            'This is still retrospective on a 54-bet parent sample. A 0-loss filtered backtest is not a guarantee.',
            'Use the filter as a qualification/risk gate, not as a claimed 100% future win rate.',
            'The power product is opponent KD/15 multiplied by favorite KD-absorbed/15; ageAdv = opponent age - favorite age.']
    (OUT/'final_validation_report.txt').write_text('\n'.join(lines))
    print('\n'.join(lines))

if __name__=='__main__': main()
