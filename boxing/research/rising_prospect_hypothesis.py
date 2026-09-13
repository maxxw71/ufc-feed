"""Post-Phase-2 validation of a newly observed rising-prospect archetype.

This hypothesis was motivated by diagnostics inside the AGE+FORM sample, so it
is explicitly POST-HOC and must not be represented as an untouched discovery.
We freeze a small, interpretable variant set and report chronological splits.
Archived price timing/settlement remains unverified.
"""
from __future__ import annotations
import json
from pathlib import Path
from market_consensus import load_master, canonical_market_rows
from phase2_interaction_scan import feature_rows, metrics

ROOT=Path(__file__).resolve().parent


def base(r,age=3,form=1,price=1.20):
    return (r['median_price']>=price and r['younger_by'] is not None and r['younger_by']>=age and
            r['form_gap'] is not None and r['form_gap']>=form)


def variants(rows):
    defs={
        'AGE_FORM_LESS_EXPERIENCED':lambda r:base(r) and r['experience_gap']<0,
        'AGE_FORM_LOWER_ELO':lambda r:base(r) and r['elo_gap'] is not None and r['elo_depth']>=5 and r['elo_gap']<0,
        'AGE_FORM_LESS_EXP_AND_LOWER_ELO':lambda r:base(r) and r['experience_gap']<0 and r['elo_gap'] is not None and r['elo_depth']>=5 and r['elo_gap']<0,
        'AGE5_LESS_EXPERIENCED':lambda r:base(r,age=5,form=0) and r['experience_gap']<0,
        'AGE5_FORM1_LESS_EXPERIENCED':lambda r:base(r,age=5,form=1) and r['experience_gap']<0,
    }
    return defs


def split(rows,lo,hi):return metrics([r for r in rows if lo<=r['year']<=hi])

def yearly(rows):return {str(y):metrics([r for r in rows if r['year']==y]) for y in range(2021,2027)}


def main():
    run=Path((ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').read_text().strip())
    rows=feature_rows(canonical_market_rows(load_master(run/'boxing_prefight_master.jsonl'),min_books=2))
    results=[]
    for name,fn in variants(rows).items():
        g=[r for r in rows if fn(r)]
        results.append({'name':name,'all':metrics(g),'development_2021_2023':split(g,2021,2023),
                        'later_2024_2026':split(g,2024,2026),'yearly':yearly(g)})
    report={'status':'POST_HOC_HYPOTHESIS_EXPLORATORY_UNVERIFIED_PRICES',
            'origin':'Motivated by experience/Elo diagnostics inside the previously inspected AGE+FORM sample; variants frozen before this run.',
            'variants':results,
            'promotion_rule':'Do not promote from this retrospective run. Freeze strongest simple variant for prospective/future-event tracking if it remains positive in both periods with useful sample.',
            'limitations':['Archived displayed prices lack verified pre-fight quote timing/settlement rules.','The hypothesis was generated after viewing Phase-2 diagnostics and is therefore not an independent discovery.','2026 is partial.']}
    (run/'rising_prospect_hypothesis.json').write_text(json.dumps(report,indent=2))
    lines=['BOXING RISING-PROSPECT HYPOTHESIS','='*106,
           'POST-HOC: inspired by AGE+FORM diagnostics; not an untouched discovery.','']
    for x in results:
        a,d,l=x['all'],x['development_2021_2023'],x['later_2024_2026']
        lines.append(f"{x['name']}: ALL n={a['bets']} {a['wins']}-{a['losses']} win={a['win_pct']} ROI={a['roi_median_pct']}")
        lines.append(f"  2021-23 n={d['bets']} {d['wins']}-{d['losses']} win={d['win_pct']} ROI={d['roi_median_pct']}")
        lines.append(f"  2024-26 n={l['bets']} {l['wins']}-{l['losses']} win={l['win_pct']} ROI={l['roi_median_pct']}")
    (run/'rising_prospect_hypothesis.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps({x['name']:{'all':x['all'],'later':x['later_2024_2026']} for x in results}))

if __name__=='__main__':main()
