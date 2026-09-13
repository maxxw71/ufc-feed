#!/usr/bin/env python3
"""Deep validation for frozen leading boxing method candidates.

This does not turn exploratory archived-price results into validated betting
performance.  It stress-tests fixed, interpretable candidates with chronological
splits, price/book sensitivities, leave-one-year-out, worst-book prices,
bootstrap ROI intervals and drawdown.  The development-selected rule is frozen
from the earlier 2021-2023 selection exercise; the rising-prospect rule is
explicitly post-hoc and is tracked separately.
"""
from __future__ import annotations
import json, math, random
from pathlib import Path
from market_consensus import load_master, canonical_market_rows
from phase2_interaction_scan import feature_rows, metrics

ROOT=Path(__file__).resolve().parent
SEED=20260913

RULES={
    'B1_AGE_FORM_CENTER': {
        'younger':3,'form':1,'price':1.20,'less_experienced':False,
        'origin':'Phase-2 descriptive center; historically inspected.'},
    'B1_AGE_FORM_DEV_SELECTED': {
        'younger':4,'form':1,'price':1.20,'less_experienced':False,
        'origin':'Selected using 2021-2023 development evidence only; 2024-2026 is the later-period check.'},
    'B2_RISING_PROSPECT': {
        'younger':3,'form':1,'price':1.20,'less_experienced':True,
        'origin':'Post-hoc hypothesis from AGE+FORM diagnostics; not an untouched discovery.'},
}

def match(r,s):
    if r['median_price']<s['price']: return False
    if r.get('younger_by') is None or r['younger_by']<s['younger']: return False
    if r.get('form_gap') is None or r['form_gap']<s['form']: return False
    if s.get('less_experienced') and not (r.get('experience_gap',0)<0): return False
    return True

def roi_at(rows,price_key):
    if not rows:return None
    p=0.0
    for r in rows:
        price=float(r[price_key])
        p += price-1.0 if r['win'] else -1.0
    return 100*p/len(rows)

def worst_roi(rows):
    if not rows:return None
    p=0.0
    for r in rows:
        price=float(r.get('worst_price') or r['median_price'])
        p += price-1.0 if r['win'] else -1.0
    return 100*p/len(rows)

def drawdown(rows):
    ordered=sorted(rows,key=lambda r:(str(r.get('bout_date') or r['key'][0]),tuple(r['key'])))
    equity=peak=0.0;maxdd=0.0;max_losing=cur_losing=0
    for r in ordered:
        equity += r['profit_median']
        peak=max(peak,equity);maxdd=max(maxdd,peak-equity)
        if r['profit_median']<0:cur_losing+=1;max_losing=max(max_losing,cur_losing)
        else:cur_losing=0
    return {'max_drawdown_units':maxdd,'max_consecutive_losses':max_losing,'ending_units':equity}

def bootstrap(rows,iters=10000):
    if not rows:return {'iters':iters,'roi_p025':None,'roi_median':None,'roi_p975':None,'prob_roi_gt_0':None,'prob_roi_gt_10':None}
    rng=random.Random(SEED+len(rows)); vals=[];n=len(rows)
    profits=[float(r['profit_median']) for r in rows]
    for _ in range(iters):vals.append(100*sum(profits[rng.randrange(n)] for __ in range(n))/n)
    vals.sort()
    q=lambda p: vals[min(len(vals)-1,max(0,int(round(p*(len(vals)-1)))))]
    return {'iters':iters,'roi_p025':q(.025),'roi_median':q(.5),'roi_p975':q(.975),
            'prob_roi_gt_0':sum(v>0 for v in vals)/len(vals),
            'prob_roi_gt_10':sum(v>10 for v in vals)/len(vals)}

def period(rows,lo,hi):return [r for r in rows if lo<=r['year']<=hi]
def mplus(rows):
    x=metrics(rows);x['roi_worst_book_pct']=worst_roi(rows);x['drawdown']=drawdown(rows);return x

def analyze(rows,name,spec):
    chosen=[r for r in rows if match(r,spec)]
    years={str(y):mplus([r for r in chosen if r['year']==y]) for y in range(2021,2027)}
    bands=[]
    for lo,hi,label in [(1.20,1.30,'1.20-1.29'),(1.30,1.50,'1.30-1.49'),(1.50,2.00,'1.50-1.99'),(2.00,99,'2.00+')]:
        g=[r for r in chosen if lo<=r['median_price']<hi];bands.append({'band':label,**mplus(g)})
    books={str(k):mplus([r for r in chosen if r['book_count']>=k]) for k in (2,3,4,5)}
    loo={str(y):{'held_out':mplus([r for r in chosen if r['year']==y]),
                 'remaining':mplus([r for r in chosen if r['year']!=y])} for y in range(2021,2027)}
    chronological=[]
    for cutoff in range(2022,2027):
        train=[r for r in chosen if r['year']<cutoff];test=[r for r in chosen if r['year']==cutoff]
        chronological.append({'test_year':cutoff,'prior':mplus(train),'test':mplus(test)})
    return {'name':name,'definition':spec,'all':mplus(chosen),
            'development_2021_2023':mplus(period(chosen,2021,2023)),
            'later_2024_2026':mplus(period(chosen,2024,2026)),
            'yearly':years,'price_bands':bands,'min_book_sensitivity':books,
            'leave_one_year_out':loo,'rolling_origin':chronological,'bootstrap':bootstrap(chosen),
            'selected_bouts':[{'date':r.get('bout_date') or r['key'][0],'favorite':r['favorite'],'opponent':r['opponent'],
                              'win':r['win'],'median_price':r['median_price'],'worst_price':r.get('worst_price'),
                              'books':r['book_count'],'younger_by':r['younger_by'],'form_gap':r['form_gap'],
                              'experience_gap':r['experience_gap']} for r in sorted(chosen,key=lambda z:(str(z.get('bout_date') or z['key'][0]),tuple(z['key'])))]}

def main():
    run=Path((ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').read_text().strip())
    rows=feature_rows(canonical_market_rows(load_master(run/'boxing_prefight_master.jsonl'),min_books=2))
    results=[analyze(rows,n,s) for n,s in RULES.items()]
    report={'status':'CANDIDATE_METHOD_STRESS_TEST_EXPLORATORY_ARCHIVED_PRICES',
            'eligible_consensus_bouts':len(rows),'candidates':results,
            'promotion_policy':{
                'research_watchlist':'Can be tracked prospectively if interpretable and later-period positive.',
                'live_method':'Do not call validated until prospective timestamped odds accumulate or an independently verified historical price source is added.'},
            'limitations':['Historical archived quote timing and settlement remain unverified.',
                           'B1 center and B2 were influenced by prior inspection; B1 development-selected is the cleaner later-period check.',
                           'Bootstrap resamples observed bets and does not remove selection bias or serial dependence.']}
    (run/'candidate_method_validation.json').write_text(json.dumps(report,indent=2))
    lines=['BOXING CANDIDATE METHOD STRESS TEST','='*116,
           'Historical ROI is exploratory until archived price timing/settlement is independently verified.','']
    for x in results:
        a=x['all'];d=x['development_2021_2023'];l=x['later_2024_2026'];b=x['bootstrap']
        lines += [x['name'], '-'*116,
                  f"Rule: younger>={x['definition']['younger']}y | last5 win gap>={x['definition']['form']} | median decimal>={x['definition']['price']} | less experienced={x['definition']['less_experienced']}",
                  f"Origin: {x['definition']['origin']}",
                  f"ALL: n={a['bets']} {a['wins']}-{a['losses']} win={a['win_pct']} ROI={a['roi_median_pct']} worst-book ROI={a['roi_worst_book_pct']} WilsonLB={a['wilson95_lower_pct']}",
                  f"2021-23: n={d['bets']} {d['wins']}-{d['losses']} ROI={d['roi_median_pct']} | 2024-26: n={l['bets']} {l['wins']}-{l['losses']} ROI={l['roi_median_pct']}",
                  f"Bootstrap ROI 95% interval: {b['roi_p025']} to {b['roi_p975']} | P(ROI>0)={b['prob_roi_gt_0']} | P(ROI>10%)={b['prob_roi_gt_10']}",
                  f"Drawdown: {a['drawdown']}",'']
    (run/'candidate_method_validation.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps({x['name']:{'all':x['all'],'later':x['later_2024_2026'],'bootstrap':x['bootstrap']} for x in results}))

if __name__=='__main__':main()
