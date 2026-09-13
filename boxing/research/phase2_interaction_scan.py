"""Phase-2 boxing interaction search with deterministic multi-book pricing.

All ROI remains exploratory because archived quote timing and settlement are not
verified.  The rule universe and thresholds are fixed in code before results are
read.  Outer-year tests select rules only from earlier-year evidence.
"""
from __future__ import annotations

import json, math
from collections import Counter, defaultdict
from pathlib import Path
from market_consensus import load_master, canonical_market_rows

ROOT=Path(__file__).resolve().parent
PRICE_FLOORS=(1.20,1.30,1.40)


def n(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError):
        return None


def gap(a,b):
    a,b=n(a),n(b)
    return a-b if a is not None and b is not None else None


def feature_rows(markets):
    rows=[]
    for m in markets:
        fr=m['favorite_row']; f=fr.get('fighter') or {}; o=fr.get('opponent') or {}
        fs=f.get('summary') or {}; os=o.get('summary') or {}
        fe=f.get('extended') or {}; oe=o.get('extended') or {}
        fb=n(fs.get('observed_prior_bouts')) or 0; ob=n(os.get('observed_prior_bouts')) or 0
        last5f=n(fs.get('last5_observed_wins')); last5o=n(os.get('last5_observed_wins'))
        restf=n(fs.get('rest_days')); resto=n(os.get('rest_days'))
        ko=n(fs.get('career_observed_ko_wins'))
        stoploss=n(os.get('career_observed_stoppage_losses'))
        oppstop5=n(oe.get('last5_stoppage_losses'))
        row={k:v for k,v in m.items() if k not in ('favorite_row','opponent_row')}
        row.update({
            'elo_gap':gap(f.get('elo'),o.get('elo')),
            'elo_depth':min(n(f.get('elo_prior_verified_bouts')) or 0,n(o.get('elo_prior_verified_bouts')) or 0),
            'younger_by':gap(o.get('age_from_biography'),f.get('age_from_biography')),
            'experience_gap':fb-ob,
            'form_gap':(last5f-last5o) if last5f is not None and last5o is not None else None,
            'rest_edge':(resto-restf) if restf is not None and resto is not None else None,
            'favorite_rest':restf,'opponent_rest':resto,
            'opponent_strength_gap':gap(fe.get('mean_prior_opponent_observed_win_fraction'),oe.get('mean_prior_opponent_observed_win_fraction')),
            'favorite_opponent_strength_known':n(fe.get('opponent_strength_known_bouts')) or 0,
            'opponent_opponent_strength_known':n(oe.get('opponent_strength_known_bouts')) or 0,
            'favorite_ko_rate':ko/fb if ko is not None and fb else None,
            'opponent_stoppage_loss_rate':stoploss/ob if stoploss is not None and ob else None,
            'opponent_last5_stoppage_losses':oppstop5,
            'favorite_last8_wins':n(f.get('last8_wins')),
            'opponent_last8_wins':n(o.get('last8_wins')),
        })
        rows.append(row)
    return rows


def wilson(w,N,z=1.96):
    if not N:return None
    p=w/N; den=1+z*z/N
    return (p+z*z/(2*N)-z*math.sqrt(p*(1-p)/N+z*z/(4*N*N)))/den


def metrics(rows):
    N=len(rows); w=sum(bool(r['win']) for r in rows)
    pm=sum(r['profit_median'] for r in rows); pb=sum(r['profit_best'] for r in rows)
    return {'bets':N,'wins':w,'losses':N-w,'win_pct':100*w/N if N else None,
            'wilson95_lower_pct':100*wilson(w,N) if N else None,
            'profit_median_units':pm,'roi_median_pct':100*pm/N if N else None,
            'profit_best_units':pb,'roi_best_pct':100*pb/N if N else None,
            'avg_market_prob':sum(r['market_prob'] for r in rows)/N if N else None,
            'avg_book_count':sum(r['book_count'] for r in rows)/N if N else None}


def rule_universe():
    rules={}
    def add(family,**spec):
        name=family+'__'+'__'.join(f'{k}{v}' for k,v in spec.items())
        rules[name]={'family':family,**spec}
    for p in PRICE_FLOORS:
        for age in (3,5,7):
            for elo in (50,100,150): add('AGE_QUALITY',price=p,younger=age,elo=elo)
            for form in (1,2): add('AGE_FORM',price=p,younger=age,form=form)
            for exp in (5,10,20): add('AGE_EXPERIENCE',price=p,younger=age,experience=exp)
            for ko in (.30,.40,.50): add('AGE_POWER',price=p,younger=age,ko=ko)
        for elo in (50,100,150):
            for exp in (5,10,20): add('QUALITY_EXPERIENCE',price=p,elo=elo,experience=exp)
            for strength in (.05,.10): add('QUALITY_SCHEDULE',price=p,elo=elo,strength=strength)
            for form in (1,2): add('QUALITY_FORM',price=p,elo=elo,form=form)
            for restedge in (60,120,180): add('QUALITY_ACTIVITY',price=p,elo=elo,restedge=restedge)
        for ko in (.30,.40,.50):
            for stoploss in (1,2): add('POWER_CHIN',price=p,ko=ko,stoploss=stoploss)
    return rules


def matches(r,s):
    if r['median_price']<s['price']:return False
    family=s['family']
    if 'elo' in s and (r['elo_gap'] is None or r['elo_depth']<5 or r['elo_gap']<s['elo']):return False
    if 'younger' in s and (r['younger_by'] is None or r['younger_by']<s['younger']):return False
    if 'form' in s and (r['form_gap'] is None or r['form_gap']<s['form']):return False
    if 'experience' in s and r['experience_gap']<s['experience']:return False
    if 'strength' in s:
        if min(r['favorite_opponent_strength_known'],r['opponent_opponent_strength_known'])<3:return False
        if r['opponent_strength_gap'] is None or r['opponent_strength_gap']<s['strength']:return False
    if 'restedge' in s and (r['rest_edge'] is None or r['rest_edge']<s['restedge']):return False
    if 'ko' in s and (r['favorite_ko_rate'] is None or r['favorite_ko_rate']<s['ko']):return False
    if family=='POWER_CHIN':
        if r['opponent_last5_stoppage_losses'] is None or r['opponent_last5_stoppage_losses']<s['stoploss']:return False
    if family=='AGE_POWER':
        if r['opponent_last5_stoppage_losses'] is None or r['opponent_last5_stoppage_losses']<1:return False
    return True


def period_metrics(rows):
    return {
        '2021_2023':metrics([r for r in rows if 2021<=r['year']<=2023]),
        '2024_2026':metrics([r for r in rows if 2024<=r['year']<=2026]),
    }


def main():
    run=Path((ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').read_text().strip())
    master=load_master(run/'boxing_prefight_master.jsonl')
    markets=canonical_market_rows(master,min_books=2)
    rows=feature_rows(markets)
    rules=rule_universe()
    selected={name:[r for r in rows if matches(r,s)] for name,s in rules.items()}
    years=sorted({r['year'] for r in rows})

    # Descriptive all-history target screen.  This is NOT the prospective result.
    targets=[]
    for name,rs in selected.items():
        m=metrics(rs); eras=period_metrics(rs)
        robust=(m['bets']>=20 and (m['win_pct'] or 0)>=75 and (m['roi_median_pct'] or -999)>=10 and
                all((z['bets']<5 or (z['roi_median_pct'] or -999)>0) for z in eras.values()))
        if robust:
            targets.append({'rule':name,'definition':rules[name],'metrics':m,'periods':eras})
    targets.sort(key=lambda x:(-(x['metrics']['wilson95_lower_pct'] or 0),-(x['metrics']['roi_median_pct'] or -999),-x['metrics']['bets']))

    outer=[]
    for year in years:
        candidates=[]
        for name,rs in selected.items():
            valid=[]
            for inner in years:
                if inner>=year:continue
                train=[r for r in rs if r['year']<inner]
                if len(train)>=15:
                    valid.extend(r for r in rs if r['year']==inner)
            vm=metrics(valid)
            if vm['bets']>=12 and (vm['roi_median_pct'] or -999)>0 and (vm['win_pct'] or 0)>=65:
                candidates.append((vm['wilson95_lower_pct'] or 0,vm['roi_median_pct'] or -999,vm['bets'],name,vm))
        candidates.sort(key=lambda x:(-x[0],-x[1],-x[2],x[3]))
        chosen={}
        for _,_,_,name,vm in candidates:
            chosen.setdefault(rules[name]['family'],{'rule':name,'validation':vm})
        votes=Counter()
        for item in chosen.values():
            for r in selected[item['rule']]:
                if r['year']==year:votes[tuple(r['key'])]+=1
        consensus={str(k):metrics([r for r in rows if r['year']==year and votes[tuple(r['key'])]>=k]) for k in (2,3,4,5)}
        best=candidates[0][3] if candidates else None
        outer.append({'year':year,'best_rule_from_earlier_years':best,
                      'best_rule_test':metrics([r for r in selected.get(best,[]) if r['year']==year]),
                      'family_choices':chosen,'consensus':consensus})

    report={
        'status':'EXPLORATORY_ONLY_UNVERIFIED_ARCHIVED_PRICES',
        'price_policy':{'min_clean_two_sided_books':2,'favorite':'median no-vig probability across books',
                        'primary_roi_price':'median displayed decimal across clean books',
                        'sensitivity_price':'best displayed decimal across clean books'},
        'coverage':{'canonical_consensus_bouts':len(rows),'years':dict(Counter(r['year'] for r in rows)),
                    'average_books':sum(r['book_count'] for r in rows)/len(rows) if rows else None},
        'rules_tested':len(rules),'outer_walk_forward':outer,'target_screen':targets,
        'all_rules':{name:{'definition':rules[name],'all_years':metrics(rs),'periods':period_metrics(rs)} for name,rs in selected.items()},
        'limitations':['Archived quote timestamps and settlement rules remain unverified; ROI is exploratory arithmetic.',
                       'The years have already been examined during development and are not pristine holdouts.',
                       'Current biography DOB is treated as stable identity data; current height/reach/stance remain excluded.',
                       'CompuBox interaction families remain separate until dated identity-linked coverage is adequate.']}
    (run/'phase2_interaction_research.json').write_text(json.dumps(report,indent=2))
    (run/'phase2_target_candidates.json').write_text(json.dumps(targets,indent=2))
    compact=[]
    for x in targets[:25]:
        m=x['metrics']; compact.append(f"{x['rule']} | n={m['bets']} {m['wins']}-{m['losses']} win={m['win_pct']:.1f}% ROI(median)={m['roi_median_pct']:+.2f}% best={m['roi_best_pct']:+.2f}% WilsonLB={m['wilson95_lower_pct']:.1f}%")
    lines=['BOXING PHASE-2 INTERACTION RESEARCH','='*110,
           f"Consensus-priced eligible bouts: {len(rows)} | fixed rules tested: {len(rules)}",
           'ROI below remains exploratory because archived quote timing/settlement is unverified.','',
           'TOP DESCRIPTIVE TARGETS (NOT PROMOTED)','-'*110]+(compact or ['No rule cleared n>=20, win>=75%, median-price ROI>=10% with era screen.'])
    lines+=['','OUTER WALK-FORWARD','-'*110]
    for o in outer:
        t=o['best_rule_test']; lines.append(f"{o['year']} | {o['best_rule_from_earlier_years']} | n={t['bets']} win={t['win_pct']} ROI={t['roi_median_pct']}")
    (run/'phase2_report.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'run':str(run),'eligible_bouts':len(rows),'rules':len(rules),'targets':len(targets),'outer_years':len(outer)}))

if __name__=='__main__':main()
