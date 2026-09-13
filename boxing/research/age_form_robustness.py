"""Hard robustness pass for the boxing AGE + FORM family.

Uses the same chronological master and multi-book consensus price policy as
Phase 2.  Threshold grids are fixed here before outputs are read.  Archived
quote timing/settlement is still unverified, so ROI remains exploratory.
"""
from __future__ import annotations
import json, math
from pathlib import Path
from market_consensus import load_master, canonical_market_rows
from phase2_interaction_scan import feature_rows, metrics

ROOT=Path(__file__).resolve().parent
AGES=(2,3,4,5,6,7)
FORMS=(0,1,2)
PRICES=(1.15,1.20,1.25,1.30,1.35,1.40,1.50)


def select(rows,age,form,price):
    return [r for r in rows if r['median_price']>=price and r['younger_by'] is not None and r['younger_by']>=age and r['form_gap'] is not None and r['form_gap']>=form]


def yearly(rows):
    return {str(y):metrics([r for r in rows if r['year']==y]) for y in range(2021,2027)}


def split(rows,lo,hi):
    return metrics([r for r in rows if lo<=r['year']<=hi])


def score(m):
    # Stability-oriented selection score: lower-bound accuracy first, then ROI,
    # then sample.  Never use the later holdout in development selection.
    return (m.get('wilson95_lower_pct') or -999,m.get('roi_median_pct') or -999,m.get('bets') or 0)


def main():
    run=Path((ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').read_text().strip())
    rows=feature_rows(canonical_market_rows(load_master(run/'boxing_prefight_master.jsonl'),min_books=2))
    grid=[]
    for age in AGES:
        for form in FORMS:
            for price in PRICES:
                g=select(rows,age,form,price)
                m=metrics(g);dev=split(g,2021,2023);late=split(g,2024,2026)
                grid.append({'age':age,'form':form,'price':price,'all':m,'development':dev,'later':late,'yearly':yearly(g)})

    # Fixed center discovered in Phase 2; report without re-selection.
    center=next(x for x in grid if x['age']==3 and x['form']==1 and math.isclose(x['price'],1.20))

    # Neighborhood around the Phase-2 center: one practical step in each axis.
    neigh=[x for x in grid if x['age'] in (2,3,4) and x['form'] in (0,1,2) and x['price'] in (1.15,1.20,1.25)]
    positive_later=[x for x in neigh if x['later']['bets']>=8 and (x['later']['roi_median_pct'] or -999)>0 and (x['development']['roi_median_pct'] or -999)>0]

    # Strict development-only rule selection from 2021-2023.  Later years are
    # not consulted in candidate eligibility or ranking.
    dev_candidates=[]
    for x in grid:
        d=x['development']
        if d['bets']>=12 and (d['win_pct'] or 0)>=70 and (d['roi_median_pct'] or -999)>0:
            dev_candidates.append(x)
    dev_candidates.sort(key=lambda x:score(x['development']),reverse=True)
    dev_choice=dev_candidates[0] if dev_candidates else None

    # Leave-one-year-out around the fixed center.  The threshold is never
    # changed; this simply shows concentration of P/L and hit rate.
    center_rows=select(rows,3,1,1.20)
    loo={}
    for y in range(2021,2027):
        loo[str(y)]={'held_out':metrics([r for r in center_rows if r['year']==y]),
                     'remaining':metrics([r for r in center_rows if r['year']!=y])}

    # Price/market structure for center: determine whether edge is concentrated
    # in very short favorites or persists at more generous prices.
    bands=[]
    for lo,hi,label in [(1.20,1.30,'1.20-1.29'),(1.30,1.50,'1.30-1.49'),(1.50,2.00,'1.50-1.99'),(2.00,99,'2.00+')]:
        g=[r for r in center_rows if lo<=r['median_price']<hi]
        bands.append({'band':label,**metrics(g)})

    # Opponent-strength / Elo context diagnostics only; not optimized vetoes.
    context={
        'elo_gap_nonnegative':metrics([r for r in center_rows if r['elo_gap'] is not None and r['elo_gap']>=0]),
        'elo_gap_negative':metrics([r for r in center_rows if r['elo_gap'] is not None and r['elo_gap']<0]),
        'experience_gap_nonnegative':metrics([r for r in center_rows if r['experience_gap']>=0]),
        'experience_gap_negative':metrics([r for r in center_rows if r['experience_gap']<0]),
        'rest_edge_nonnegative':metrics([r for r in center_rows if r['rest_edge'] is not None and r['rest_edge']>=0]),
        'rest_edge_negative':metrics([r for r in center_rows if r['rest_edge'] is not None and r['rest_edge']<0]),
    }

    report={'status':'EXPLORATORY_ONLY_UNVERIFIED_ARCHIVED_PRICES',
            'center_definition':{'median_decimal_price_min':1.20,'favorite_younger_by_years_min':3,'favorite_last5_win_gap_min':1},
            'center':center,'center_leave_one_year_out':loo,'center_price_bands':bands,'center_context':context,
            'neighbor_count':len(neigh),'neighbors_positive_both_periods':len(positive_later),
            'neighbors':[{'age':x['age'],'form':x['form'],'price':x['price'],'all':x['all'],'development':x['development'],'later':x['later']} for x in neigh],
            'development_only_choice':None if not dev_choice else {'age':dev_choice['age'],'form':dev_choice['form'],'price':dev_choice['price'],'development':dev_choice['development'],'later_holdout':dev_choice['later'],'yearly':dev_choice['yearly']},
            'grid_size':len(grid),
            'limitations':['Archived displayed prices still lack verified pre-fight quote time and settlement rules.',
                           '2026 is partial.','The Phase-2 center was found after inspecting historical data; development-only selection is reported separately to reduce hindsight bias.']}
    (run/'age_form_robustness.json').write_text(json.dumps(report,indent=2))

    c=center['all'];d=center['development'];l=center['later']
    lines=['BOXING AGE + FORM ROBUSTNESS','='*108,
           'Fixed Phase-2 center: favorite >=3 years younger, last-5 wins gap >=1, median decimal price >=1.20',
           f"ALL: {c['bets']} bets | {c['wins']}-{c['losses']} | win {c['win_pct']:.1f}% | median-price ROI {c['roi_median_pct']:+.2f}% | Wilson LB {c['wilson95_lower_pct']:.1f}%",
           f"2021-23: {d['bets']} | {d['wins']}-{d['losses']} | win {d['win_pct']:.1f}% | ROI {d['roi_median_pct']:+.2f}%",
           f"2024-26: {l['bets']} | {l['wins']}-{l['losses']} | win {l['win_pct']:.1f}% | ROI {l['roi_median_pct']:+.2f}%",'',
           f"Neighborhood: {len(positive_later)}/{len(neigh)} nearby grid points positive in both 2021-23 and 2024-26 (with >=8 later bets).",'',
           'YEAR BY YEAR','-'*108]
    for y,m in center['yearly'].items():
        lines.append(f"{y}: n={m['bets']} {m['wins']}-{m['losses']} win={m['win_pct']} ROI={m['roi_median_pct']}")
    lines+=['','PRICE BANDS','-'*108]
    for b in bands:lines.append(f"{b['band']}: n={b['bets']} {b['wins']}-{b['losses']} win={b['win_pct']} ROI={b['roi_median_pct']}")
    lines+=['','DEVELOPMENT-ONLY SELECTION','-'*108]
    if dev_choice:
        lines.append(f"Selected from 2021-23 only: age>={dev_choice['age']} form>={dev_choice['form']} price>={dev_choice['price']}")
        lines.append(f"Development: {dev_choice['development']}")
        lines.append(f"Later 2024-26 holdout: {dev_choice['later']}")
    else:lines.append('No development-only rule cleared the fixed eligibility screen.')
    (run/'age_form_robustness.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'center':center['all'],'later':center['later'],'positive_neighbors':len(positive_later),'neighbors':len(neigh),'development_choice':None if not dev_choice else [dev_choice['age'],dev_choice['form'],dev_choice['price']]}))

if __name__=='__main__':main()
