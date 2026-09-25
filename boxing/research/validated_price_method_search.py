#!/usr/bin/env python3
"""Research boxing methods using only independently verified historical prices.

This pass is deliberately separate from the all-archive exploratory research.
A quote is eligible only when its quote_rowid appears in
HISTORICAL_ODDS_VALIDATED_ROWS.json, whose validator requires an exact pre-event
Wayback event/bookmaker/bout/selection/price match.

Important: verified prices improve price provenance, but do NOT make these
rules pristine out-of-sample discoveries because the rule families were
developed on overlapping historical bouts.
"""
from __future__ import annotations
import json,math
from collections import Counter
from pathlib import Path

from market_consensus import load_master, canonical_market_rows,quote_signature
from phase2_interaction_scan import feature_rows,metrics,rule_universe,matches,period_metrics
from candidate_method_validation import RULES as CANDIDATE_RULES, match as candidate_match

ROOT=Path(__file__).resolve().parent
VALID=ROOT/'HISTORICAL_ODDS_VALIDATED_ROWS.json'

def wilson_safe(m):
    return m.get('wilson95_lower_pct') if m.get('wilson95_lower_pct') is not None else -999

def row_summary(r):
    return {
      'date':r.get('bout_date') or (r.get('key') or [None])[0],
      'favorite':r.get('favorite'),'opponent':r.get('opponent'),
      'win':r.get('win'),'median_price':r.get('median_price'),
      'worst_price':r.get('worst_price'),'best_price':r.get('best_price'),
      'book_count':r.get('book_count'),'books':r.get('books'),
      'younger_by':r.get('younger_by'),'form_gap':r.get('form_gap'),
      'experience_gap':r.get('experience_gap'),'elo_gap':r.get('elo_gap'),
      'favorite_ko_rate':r.get('favorite_ko_rate'),
      'opponent_last5_stoppage_losses':r.get('opponent_last5_stoppage_losses')
    }

def fixed_rule_screen(rows):
    rules=rule_universe()
    out=[]
    for name,spec in rules.items():
        chosen=[r for r in rows if matches(r,spec)]
        m=metrics(chosen)
        if m['bets']<5:
            continue
        out.append({
          'rule':name,'definition':spec,'metrics':m,'periods':period_metrics(chosen),
          'selected_bouts':[row_summary(r) for r in chosen]
        })
    out.sort(key=lambda x:(-wilson_safe(x['metrics']),-(x['metrics'].get('roi_median_pct') or -999),-x['metrics']['bets'],x['rule']))
    return out

def candidate_results(rows):
    out=[]
    for name,spec in CANDIDATE_RULES.items():
        chosen=[r for r in rows if candidate_match(r,spec)]
        out.append({
          'name':name,'definition':spec,'metrics':metrics(chosen),
          'periods':period_metrics(chosen),
          'yearly':{str(y):metrics([r for r in chosen if r['year']==y]) for y in sorted({r['year'] for r in rows})},
          'selected_bouts':[row_summary(r) for r in chosen]
        })
    return out

def rolling_origin(rows):
    rules=rule_universe()
    years=sorted({r['year'] for r in rows})
    out=[]
    for year in years:
        if year==years[0]:
            continue
        eligible=[]
        for name,spec in rules.items():
            train=[r for r in rows if r['year']<year and matches(r,spec)]
            tm=metrics(train)
            if tm['bets']<8 or (tm['roi_median_pct'] is None) or tm['roi_median_pct']<=0:
                continue
            if (tm['win_pct'] or 0)<65:
                continue
            eligible.append((wilson_safe(tm),tm['roi_median_pct'],tm['bets'],name,tm))
        eligible.sort(key=lambda x:(-x[0],-x[1],-x[2],x[3]))
        if not eligible:
            out.append({'test_year':year,'selected_rule':None,'prior':metrics([]),'test':metrics([])})
            continue
        _,_,_,name,prior=eligible[0]
        spec=rules[name]
        test=[r for r in rows if r['year']==year and matches(r,spec)]
        out.append({'test_year':year,'selected_rule':name,'prior':prior,'test':metrics(test)})
    return out

def tier(master,allowed,min_books):
    markets=canonical_market_rows(master,min_books=min_books,allowed_quote_signatures=allowed)
    rows=feature_rows(markets)
    screen=fixed_rule_screen(rows)
    # A compact descriptive screen only. These are not promoted methods.
    notable=[x for x in screen if x['metrics']['bets']>=8 and (x['metrics'].get('roi_median_pct') or -999)>0][:25]
    return {
      'min_verified_two_sided_books':min_books,
      'eligible_verified_price_bouts':len(rows),
      'years':dict(sorted(Counter(r['year'] for r in rows).items())),
      'average_verified_books':(sum(r['book_count'] for r in rows)/len(rows)) if rows else None,
      'existing_candidates':candidate_results(rows),
      'fixed_rules_tested_with_at_least_5_bets':len(screen),
      'notable_fixed_rule_screen':notable,
      'rolling_origin':rolling_origin(rows)
    }

def main():
    run=Path((ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').read_text().strip())
    master=load_master(run/'boxing_prefight_master.jsonl')
    valid=json.loads(VALID.read_text())
    vrows=valid.get('rows') or []
    allowed={quote_signature({
      'event_date':x.get('event_date'),
      'odds_bout_id':x.get('bout_id'),
      'bookmaker':x.get('bookmaker'),
      'selection':x.get('selection'),
      'decimal_price':x.get('stored_decimal_price')
    }) for x in vrows}
    allowed.discard(None)
    report={
      'status':'INDEPENDENTLY_VERIFIED_HISTORICAL_PRICE_SUBSET_RESEARCH',
      'generated_from_validation_artifact':str(VALID.name),
      'validated_quote_rows':len(vrows),
      'stable_verified_quote_signatures':len(allowed),
      'distinct_validated_bouts':len({str(x.get('bout_id') or '') for x in vrows if str(x.get('bout_id') or '')}),
      'distinct_validated_events':len({str(x.get('event_url') or '') for x in vrows if str(x.get('event_url') or '')}),
      'validation_policy':valid.get('policy'),
      'tiers':{
        'one_or_more_verified_books':tier(master,allowed,1),
        'two_or_more_verified_books':tier(master,allowed,2)
      },
      'interpretation_policy':[
        'Only independently verified pre-event quote rows are used in this report.',
        'One-book results verify price provenance but are less robust to bookmaker-specific pricing than the 2+ book tier.',
        'Historical verified-price results are not pristine holdouts because rule families and thresholds were developed on overlapping historical bouts.',
        'No rule is promoted live from this report alone; prospective timestamped settlement remains the cleanest confirmation.'
      ]
    }
    (run/'validated_price_method_search.json').write_text(json.dumps(report,indent=2))
    lines=['BOXING VERIFIED-PRICE METHOD RESEARCH','='*112,
           f"Validated quote rows: {report['validated_quote_rows']} | bouts: {report['distinct_validated_bouts']} | events: {report['distinct_validated_events']}",'']
    for key,t in report['tiers'].items():
        lines += [key.upper(),'-'*112,
                  f"Eligible bouts: {t['eligible_verified_price_bouts']} | years={t['years']} | avg verified books={t['average_verified_books']}"]
        for c in t['existing_candidates']:
            m=c['metrics']
            lines.append(f"{c['name']}: n={m['bets']} {m['wins']}-{m['losses']} win={m['win_pct']} ROI={m['roi_median_pct']} WilsonLB={m['wilson95_lower_pct']}")
        lines.append('Top fixed-rule verified-price screen:')
        for x in t['notable_fixed_rule_screen'][:10]:
            m=x['metrics']
            lines.append(f"  {x['rule']} | n={m['bets']} {m['wins']}-{m['losses']} win={m['win_pct']:.1f}% ROI={m['roi_median_pct']:+.2f}% WilsonLB={m['wilson95_lower_pct']:.1f}%")
        lines.append('')
    (run/'validated_price_method_search.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps({
      'validated_quote_rows':report['validated_quote_rows'],
      'distinct_validated_bouts':report['distinct_validated_bouts'],
      'distinct_validated_events':report['distinct_validated_events'],
      'tier_bouts':{k:v['eligible_verified_price_bouts'] for k,v in report['tiers'].items()},
      'candidate_metrics':{k:{c['name']:c['metrics'] for c in v['existing_candidates']} for k,v in report['tiers'].items()}
    }))

if __name__=='__main__':
    main()
