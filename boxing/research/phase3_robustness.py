#!/usr/bin/env python3
"""Hard validation for leading Phase-3 boxing families.

Tests neighboring thresholds, chronological eras and a fixed development/later
split. This does not promote any method and all ROI remains exploratory until
historical quote timing and settlement are independently verified.
"""
from __future__ import annotations
import json
from pathlib import Path
from market_consensus import load_master,canonical_market_rows
from phase2_interaction_scan import metrics
from phase3_physical_context_scan import add_features
ROOT=Path(__file__).resolve().parent

def select(rows,kind,price,age=None,form=None,height=None,reach=None,rounds=None,world=False,title=False):
    out=[]
    for r in rows:
        if r['median_price']<price:continue
        if age is not None and (r['younger_by'] is None or r['younger_by']<age):continue
        if form is not None and (r['form_gap'] is None or r['form_gap']<form):continue
        if height is not None and (r['height_gap'] is None or r['height_gap']<height):continue
        if reach is not None and (r['reach_gap'] is None or r['reach_gap']<reach):continue
        if rounds is not None and (r['scheduled_rounds'] is None or r['scheduled_rounds']<rounds):continue
        if world and not r['major_world_title']:continue
        if title and not r['title_bout']:continue
        out.append(r)
    return out

def split_metrics(rs):
    return {
      'all':metrics(rs),
      '2021_2023':metrics([r for r in rs if 2021<=r['year']<=2023]),
      '2024_2026':metrics([r for r in rs if 2024<=r['year']<=2026]),
      'by_year':{str(y):metrics([r for r in rs if r['year']==y]) for y in range(2021,2027)}
    }

def main():
    run=Path((ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').read_text().strip())
    rows=add_features(canonical_market_rows(load_master(run/'boxing_prefight_master.jsonl'),min_books=2))
    grid=[]
    # Predeclared neighborhoods around the strongest Phase-3 descriptive families.
    for p in (1.2,1.25,1.3,1.35,1.4):
        for f in (1,2):
            for rnd in (8,10,12):
                grid.append(('FORM_LONG',{'price':p,'form':f,'rounds':rnd}))
        for a in (3,4,5,6):
            for rnd in (8,10,12):grid.append(('AGE_LONG',{'price':p,'age':a,'rounds':rnd}))
            for h in (2,3,4,5):grid.append(('AGE_HEIGHT',{'price':p,'age':a,'height':h}))
            grid.append(('AGE_WORLD',{'price':p,'age':a,'world':True}))
        for f in (1,2):
            grid.append(('FORM_TITLE',{'price':p,'form':f,'title':True}))
            for reach in (2,3,5,8):grid.append(('FORM_REACH',{'price':p,'form':f,'reach':reach}))
    results=[]
    for family,spec in grid:
        rs=select(rows,family,**spec);sm=split_metrics(rs);a=sm['all'];d=sm['2021_2023'];l=sm['2024_2026']
        stable=(a['bets']>=15 and (a['win_pct'] or 0)>=72 and (a['roi_median_pct'] or -999)>=8 and
                d['bets']>=5 and l['bets']>=5 and (d['roi_median_pct'] or -999)>0 and (l['roi_median_pct'] or -999)>0)
        results.append({'family':family,'spec':spec,'stable':stable,**sm})
    results.sort(key=lambda x:(not x['stable'],-(x['all']['wilson95_lower_pct'] or 0),-(x['all']['roi_median_pct'] or -999),-x['all']['bets']))
    # Fixed development-only selector. Candidate must be supported in 2021-23,
    # then is evaluated untouched on 2024-26.
    dev=[]
    for x in results:
        m=x['2021_2023']
        if m['bets']>=10 and (m['win_pct'] or 0)>=70 and (m['roi_median_pct'] or -999)>5:
            dev.append((m['wilson95_lower_pct'] or 0,m['roi_median_pct'],m['bets'],x))
    dev.sort(key=lambda z:(-z[0],-z[1],-z[2]))
    chosen=dev[0][3] if dev else None
    selected_later=None
    if chosen:
        selected_later=chosen['2024_2026']
    family_summary={}
    for fam in sorted({x['family'] for x in results}):
        famrows=[x for x in results if x['family']==fam]
        family_summary[fam]={
          'tested':len(famrows),'stable_count':sum(x['stable'] for x in famrows),
          'best':famrows[0] if famrows else None,
          'positive_both_periods':sum((x['2021_2023']['bets']>=5 and x['2024_2026']['bets']>=5 and
                                      (x['2021_2023']['roi_median_pct'] or -999)>0 and (x['2024_2026']['roi_median_pct'] or -999)>0) for x in famrows)}
    report={'status':'EXPLORATORY_ROBUSTNESS_ONLY','grid_size':len(results),'stable_rules':sum(x['stable'] for x in results),
            'family_summary':family_summary,'top_stable':[x for x in results if x['stable']][:40],
            'development_only_selection':{'chosen':chosen,'later_test_2024_2026':selected_later},
            'limitations':['Threshold neighborhoods were tested after Phase-3 discovery; they are robustness checks, not pristine discovery.',
                           'The 2024-26 later test is cleaner than full-history metrics but not a future prospective holdout.',
                           'Historical ROI remains exploratory because archived price timing/settlement is unverified.']}
    (run/'phase3_robustness.json').write_text(json.dumps(report,indent=2))
    lines=['BOXING PHASE-3 ROBUSTNESS','='*110,
           f"Grid: {len(results)} | stable across 2021-23 and 2024-26: {report['stable_rules']}",'']
    for fam,v in family_summary.items():
        b=v['best'];bm=b['all'] if b else {}
        lines.append(f"{fam}: stable {v['stable_count']}/{v['tested']} | positive both periods {v['positive_both_periods']} | best n={bm.get('bets')} win={bm.get('win_pct')} ROI={bm.get('roi_median_pct')}")
    lines+=['','TOP STABLE RULES','-'*110]
    for x in report['top_stable'][:25]:
        m=x['all'];d=x['2021_2023'];l=x['2024_2026']
        lines.append(f"{x['family']} {x['spec']} | n={m['bets']} {m['wins']}-{m['losses']} ROI={m['roi_median_pct']:+.2f}% | dev={d['roi_median_pct']:+.2f}% later={l['roi_median_pct']:+.2f}%")
    lines+=['','DEVELOPMENT-ONLY SELECTION','-'*110]
    if chosen:
        lines.append(f"Chosen from 2021-23 only: {chosen['family']} {chosen['spec']}")
        lines.append(f"2021-23: {chosen['2021_2023']}")
        lines.append(f"2024-26 untouched later period: {selected_later}")
    else:lines.append('No candidate met the development selector.')
    (run/'phase3_robustness.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'grid':len(results),'stable':report['stable_rules'],'chosen':chosen['family'] if chosen else None,'later':selected_later}))
if __name__=='__main__':main()
