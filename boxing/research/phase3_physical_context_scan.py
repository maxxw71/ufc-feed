#!/usr/bin/env python3
"""Phase-3 fixed boxing scan: physical, rematch and bout-context families.

Height/reach are identity-linked current biography values treated only as stable
adult proxies, not dated measurements. Stance is exploratory. Bout-context flags
use only facts knowable before the bout (title at stake, vacant title, scheduled
rounds, location); outcome words from record-table notes are never features.
Archived price ROI remains exploratory until quote timing/settlement is verified.
"""
from __future__ import annotations
import json,math,re
from collections import Counter
from pathlib import Path
from market_consensus import load_master,canonical_market_rows
from phase2_interaction_scan import feature_rows,metrics
ROOT=Path(__file__).resolve().parent
PRICE_FLOORS=(1.20,1.30,1.40)

def num(v):
    try:
        x=float(v);return x if math.isfinite(x) else None
    except (TypeError,ValueError):return None

def country_from_location(s):
    x=str(s or '').strip()
    if not x:return None
    tail=x.split(',')[-1].strip().casefold().replace('.','')
    aliases={'us':'united states','usa':'united states','u s':'united states','england':'england','scotland':'scotland','wales':'wales'}
    return aliases.get(tail,tail or None)

def add_features(markets):
    base={tuple(r['key']):r for r in feature_rows(markets)}
    out=[]
    for m in markets:
        key=tuple(m['key']);r=dict(base[key]);fr=m['favorite_row'];f=fr.get('fighter') or {};o=fr.get('opponent') or {}
        fe=f.get('extended') or {};oe=o.get('extended') or {};ctx=fr.get('context') or {}
        fh=num(f.get('height_cm_static_proxy'));oh=num(o.get('height_cm_static_proxy'))
        frch=num(f.get('reach_cm_static_proxy'));orch=num(o.get('reach_cm_static_proxy'))
        fs=str(f.get('stance_static_proxy') or '').casefold();os=str(o.get('stance_static_proxy') or '').casefold()
        nat=str(f.get('nationality_static_proxy') or '').casefold();loc=country_from_location(ctx.get('location'))
        r.update({
          'height_gap':fh-oh if fh is not None and oh is not None else None,
          'reach_gap':frch-orch if frch is not None and orch is not None else None,
          'favorite_stance':fs or None,'opponent_stance':os or None,
          'opposite_stance':fs in ('orthodox','southpaw') and os in ('orthodox','southpaw') and fs!=os,
          'favorite_southpaw_vs_orthodox':fs=='southpaw' and os=='orthodox',
          'title_bout':bool(ctx.get('title_bout')),'vacant_title':bool(ctx.get('vacant_title')),
          'major_world_title':bool(ctx.get('major_world_title_orgs')),
          'scheduled_rounds':num(ctx.get('scheduled_rounds')),
          'prior_meetings':num(fe.get('verified_prior_meetings')) or 0,
          'prior_meeting_wins':num(fe.get('verified_prior_meeting_wins')) or 0,
          'prior_meeting_losses':num(fe.get('verified_prior_meeting_losses')) or 0,
          'location_country':loc,'favorite_nationality_proxy':nat or None,
        })
        out.append(r)
    return out

def universe():
    rules={}
    def add(family,**spec):rules[family+'__'+'__'.join(f'{k}{v}' for k,v in spec.items())]={'family':family,**spec}
    for p in PRICE_FLOORS:
        for reach in (3,5,8):
            add('REACH',price=p,reach=reach)
            for age in (3,5):add('AGE_REACH',price=p,age=age,reach=reach)
            for form in (1,2):add('FORM_REACH',price=p,form=form,reach=reach)
            for elo in (50,100):add('QUALITY_REACH',price=p,elo=elo,reach=reach)
        for height in (3,5,8):
            add('HEIGHT',price=p,height=height)
            for age in (3,5):add('AGE_HEIGHT',price=p,age=age,height=height)
        for age in (3,5):
            add('AGE_TITLE',price=p,age=age,title=1)
            add('AGE_LONG',price=p,age=age,rounds=10)
            add('AGE_WORLD_TITLE',price=p,age=age,world=1)
        for form in (1,2):
            add('FORM_TITLE',price=p,form=form,title=1)
            add('FORM_LONG',price=p,form=form,rounds=10)
        add('SOUTHPAW',price=p,southpaw=1)
        add('SOUTHPAW_FORM',price=p,southpaw=1,form=1)
        add('REMATCH_PRIOR_WIN',price=p,priorwin=1)
        add('REMATCH_REVENGE',price=p,priorloss=1)
        add('VACANT_TITLE',price=p,vacant=1)
    return rules

def match(r,s):
    if r['median_price']<s['price']:return False
    if 'reach' in s and (r['reach_gap'] is None or r['reach_gap']<s['reach']):return False
    if 'height' in s and (r['height_gap'] is None or r['height_gap']<s['height']):return False
    if 'age' in s and (r['younger_by'] is None or r['younger_by']<s['age']):return False
    if 'form' in s and (r['form_gap'] is None or r['form_gap']<s['form']):return False
    if 'elo' in s and (r['elo_gap'] is None or r['elo_depth']<5 or r['elo_gap']<s['elo']):return False
    if 'title' in s and not r['title_bout']:return False
    if 'world' in s and not r['major_world_title']:return False
    if 'rounds' in s and (r['scheduled_rounds'] is None or r['scheduled_rounds']<s['rounds']):return False
    if 'southpaw' in s and not r['favorite_southpaw_vs_orthodox']:return False
    if 'priorwin' in s and r['prior_meeting_wins']<s['priorwin']:return False
    if 'priorloss' in s and r['prior_meeting_losses']<s['priorloss']:return False
    if 'vacant' in s and not r['vacant_title']:return False
    return True

def periods(rs):
    return {'2021_2023':metrics([r for r in rs if 2021<=r['year']<=2023]),
            '2024_2026':metrics([r for r in rs if 2024<=r['year']<=2026])}

def main():
    run=Path((ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').read_text().strip())
    rows=add_features(canonical_market_rows(load_master(run/'boxing_prefight_master.jsonl'),min_books=2))
    rules=universe();selected={k:[r for r in rows if match(r,s)] for k,s in rules.items()}
    candidates=[]
    for name,rs in selected.items():
        m=metrics(rs);p=periods(rs)
        stable=(m['bets']>=15 and (m['win_pct'] or 0)>=72 and (m['roi_median_pct'] or -999)>=8 and
                all(z['bets']<5 or (z['roi_median_pct'] or -999)>0 for z in p.values()))
        if stable:candidates.append({'rule':name,'definition':rules[name],'metrics':m,'periods':p})
    candidates.sort(key=lambda x:(-(x['metrics']['wilson95_lower_pct'] or 0),-(x['metrics']['roi_median_pct'] or -999),-x['metrics']['bets']))
    years=sorted({r['year'] for r in rows});outer=[]
    for year in years:
        ranked=[]
        for name,rs in selected.items():
            valid=[]
            for inner in years:
                if inner>=year:continue
                train=[r for r in rs if r['year']<inner]
                if len(train)>=12:valid.extend(r for r in rs if r['year']==inner)
            vm=metrics(valid)
            if vm['bets']>=10 and (vm['win_pct'] or 0)>=65 and (vm['roi_median_pct'] or -999)>0:
                ranked.append((vm['wilson95_lower_pct'] or 0,vm['roi_median_pct'] or -999,vm['bets'],name,vm))
        ranked.sort(key=lambda x:(-x[0],-x[1],-x[2],x[3]));best=ranked[0][3] if ranked else None
        fam={}
        for _,_,_,name,vm in ranked:fam.setdefault(rules[name]['family'],{'rule':name,'validation':vm})
        votes=Counter()
        for x in fam.values():
            for r in selected[x['rule']]:
                if r['year']==year:votes[tuple(r['key'])]+=1
        outer.append({'year':year,'best_rule_from_earlier_years':best,
                      'best_rule_test':metrics([r for r in selected.get(best,[]) if r['year']==year]),
                      'family_choices':fam,'consensus':{str(k):metrics([r for r in rows if r['year']==year and votes[tuple(r['key'])]>=k]) for k in (2,3,4)}})
    coverage={
      'bouts':len(rows),'reach_pair':sum(r['reach_gap'] is not None for r in rows),'height_pair':sum(r['height_gap'] is not None for r in rows),
      'stance_pair':sum(bool(r['favorite_stance'] and r['opponent_stance']) for r in rows),'title_bouts':sum(r['title_bout'] for r in rows),
      'scheduled_rounds_known':sum(r['scheduled_rounds'] is not None for r in rows),'rematches':sum(r['prior_meetings']>0 for r in rows)}
    report={'status':'EXPLORATORY_STATIC_PHYSICAL_PROXIES_AND_UNVERIFIED_PRICES','coverage':coverage,'rules_tested':len(rules),
            'targets':candidates,'outer_walk_forward':outer,
            'limitations':['Height/reach are current biography snapshots used only as stable adult proxies, not timestamped measurements.',
                           'Stance and nationality are exploratory current-profile attributes and may change or be incomplete.',
                           'Title/context flags expose only pre-fight-knowable facts; outcome wording is excluded.',
                           'Archived odds timing and settlement remain unverified, so ROI is exploratory.']}
    (run/'phase3_physical_context.json').write_text(json.dumps(report,indent=2))
    lines=['BOXING PHASE-3 PHYSICAL / CONTEXT RESEARCH','='*110,
           f"Eligible bouts: {len(rows)} | rules: {len(rules)} | reach-pair: {coverage['reach_pair']} | height-pair: {coverage['height_pair']} | title bouts: {coverage['title_bouts']}",'',
           'TOP STABLE DESCRIPTIVE TARGETS (NOT PROMOTED)','-'*110]
    if not candidates:lines.append('No fixed Phase-3 rule cleared the stability screen.')
    for x in candidates[:30]:
        m=x['metrics'];lines.append(f"{x['rule']} | n={m['bets']} {m['wins']}-{m['losses']} win={m['win_pct']:.1f}% ROI={m['roi_median_pct']:+.2f}% WilsonLB={m['wilson95_lower_pct']:.1f}%")
    lines+=['','OUTER WALK-FORWARD','-'*110]
    for o in outer:
        m=o['best_rule_test'];lines.append(f"{o['year']} | {o['best_rule_from_earlier_years']} | n={m['bets']} win={m['win_pct']} ROI={m['roi_median_pct']}")
    (run/'phase3_physical_context.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'bouts':len(rows),'rules':len(rules),'targets':len(candidates),'coverage':coverage}))
if __name__=='__main__':main()
