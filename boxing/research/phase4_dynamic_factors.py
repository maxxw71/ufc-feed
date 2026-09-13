#!/usr/bin/env python3
"""Phase-4 boxing research: dynamic pre-fight factors from chronological history.

Focuses on independent families not covered by age+form or physical/context scans:
absolute age, layoffs/activity, win/loss streaks, recent stoppage vulnerability,
opponent-quality differentials, decision experience, long-fight experience and
explicit market price bands. All features are derived strictly before the bout.
Historical price ROI remains exploratory until quote timing/settlement is verified.
"""
from __future__ import annotations
import json,math
from collections import Counter
from pathlib import Path
from market_consensus import load_master,canonical_market_rows
from phase2_interaction_scan import metrics
ROOT=Path(__file__).resolve().parent

def n(v):
    try:
        x=float(v);return x if math.isfinite(x) else None
    except (TypeError,ValueError):return None

def rows_from(markets):
    out=[]
    for m in markets:
        fr=m['favorite_row'];f=fr.get('fighter') or {};o=fr.get('opponent') or {}
        fs=f.get('summary') or {};os=o.get('summary') or {};fe=f.get('extended') or {};oe=o.get('extended') or {}
        fa=n(f.get('age_from_biography'));oa=n(o.get('age_from_biography'))
        fstr=n(fe.get('mean_prior_opponent_observed_win_fraction'));ostr=n(oe.get('mean_prior_opponent_observed_win_fraction'))
        r={'key':tuple(m['key']),'year':int(m['event_date'][:4]),'favorite':m['favorite_name'],'opponent':m['underdog_name'],
           'win':m['favorite_win'],'median_price':m['favorite_median_price'],'best_price':m['favorite_best_price'],
           'market_prob':m['favorite_market_prob'],'book_count':m['book_count'],
           'fav_age':fa,'opp_age':oa,'younger_by':oa-fa if fa is not None and oa is not None else None,
           'fav_rest':n(fs.get('rest_days')),'opp_rest':n(os.get('rest_days')),
           'fav_last365':n(fe.get('bouts_last_365_days')),'opp_last365':n(oe.get('bouts_last_365_days')),
           'fav_win_streak':n(fs.get('observed_win_streak')) or 0,
           'fav_loss_streak':n(fe.get('loss_streak')) or 0,'opp_loss_streak':n(oe.get('loss_streak')) or 0,
           'fav_last5_stop_wins':n(fe.get('last5_stoppage_wins')) or 0,'opp_last5_stop_losses':n(oe.get('last5_stoppage_losses')) or 0,
           'fav_days_since_stop_loss':n(fe.get('days_since_last_stoppage_loss')),'opp_days_since_stop_loss':n(oe.get('days_since_last_stoppage_loss')),
           'fav_strength':fstr,'opp_strength':ostr,'strength_gap':fstr-ostr if fstr is not None and ostr is not None else None,
           'fav_last5_decision_wins':n(fe.get('last5_decision_wins')) or 0,'opp_last5_decision_losses':n(oe.get('last5_decision_losses')) or 0,
           'fav_round9':n(fe.get('career_reached_round_9_bouts')) or 0,'opp_round9':n(oe.get('career_reached_round_9_bouts')) or 0,
           'fav_12plus':n(fe.get('career_scheduled_12plus_known_bouts')) or 0,'opp_12plus':n(oe.get('career_scheduled_12plus_known_bouts')) or 0,
           'fav_prior_bouts':n(fs.get('observed_prior_bouts')) or 0,'opp_prior_bouts':n(os.get('observed_prior_bouts')) or 0}
        out.append(r)
    return out

def universe():
    rules={}
    def add(fam,**s):rules[fam+'__'+'__'.join(f'{k}{v}' for k,v in s.items())]={'family':fam,**s}
    for pmin,pmax in ((1.15,1.5),(1.2,1.6),(1.2,1.8),(1.3,2.0)):
        for old in (32,34,35,36):
            for age in (2,3,5):add('AGING_OPP',pmin=pmin,pmax=pmax,oppage=old,age=age)
        for rest in (180,270,365):
            add('OPP_LAYOFF',pmin=pmin,pmax=pmax,opprest=rest)
            for favmax in (120,180,270):add('ACTIVITY_LAYOFF',pmin=pmin,pmax=pmax,opprest=rest,favrest=favmax)
            for age in (3,5):add('AGE_LAYOFF',pmin=pmin,pmax=pmax,opprest=rest,age=age)
        for streak in (2,3,4,5):
            add('WIN_STREAK',pmin=pmin,pmax=pmax,streak=streak)
            for loss in (1,2):add('STREAK_MISMATCH',pmin=pmin,pmax=pmax,streak=streak,opploss=loss)
        for loss in (1,2):
            add('OPP_LOSS_STREAK',pmin=pmin,pmax=pmax,opploss=loss)
            add('STOPLOSS',pmin=pmin,pmax=pmax,stoploss=loss)
            for stopwin in (1,2):add('POWER_VULN',pmin=pmin,pmax=pmax,stoploss=loss,stopwin=stopwin)
        for strength in (.05,.10,.15):
            add('SCHEDULE_STRENGTH',pmin=pmin,pmax=pmax,strength=strength)
            for age in (3,5):add('AGE_STRENGTH',pmin=pmin,pmax=pmax,strength=strength,age=age)
        for dec in (1,2,3):add('DECISION_EDGE',pmin=pmin,pmax=pmax,dec=dec,oppdecloss=1)
        for long in (1,2,3):
            add('ROUND9_EXP',pmin=pmin,pmax=pmax,round9=long)
            add('TWELVE_ROUND_EXP',pmin=pmin,pmax=pmax,twelve=long)
    return rules

def match(r,s):
    if not (s['pmin']<=r['median_price']<=s['pmax']):return False
    if 'oppage' in s and (r['opp_age'] is None or r['opp_age']<s['oppage']):return False
    if 'age' in s and (r['younger_by'] is None or r['younger_by']<s['age']):return False
    if 'opprest' in s and (r['opp_rest'] is None or r['opp_rest']<s['opprest']):return False
    if 'favrest' in s and (r['fav_rest'] is None or r['fav_rest']>s['favrest']):return False
    if 'streak' in s and r['fav_win_streak']<s['streak']:return False
    if 'opploss' in s and r['opp_loss_streak']<s['opploss']:return False
    if 'stoploss' in s and r['opp_last5_stop_losses']<s['stoploss']:return False
    if 'stopwin' in s and r['fav_last5_stop_wins']<s['stopwin']:return False
    if 'strength' in s and (r['strength_gap'] is None or r['strength_gap']<s['strength']):return False
    if 'dec' in s and r['fav_last5_decision_wins']<s['dec']:return False
    if 'oppdecloss' in s and r['opp_last5_decision_losses']<s['oppdecloss']:return False
    if 'round9' in s and r['fav_round9']<s['round9']:return False
    if 'twelve' in s and r['fav_12plus']<s['twelve']:return False
    return True

def periods(rs):return {'2021_2023':metrics([r for r in rs if 2021<=r['year']<=2023]),'2024_2026':metrics([r for r in rs if 2024<=r['year']<=2026])}

def main():
    run=Path((ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').read_text().strip())
    rows=rows_from(canonical_market_rows(load_master(run/'boxing_prefight_master.jsonl'),min_books=2))
    rules=universe();sel={k:[r for r in rows if match(r,s)] for k,s in rules.items()}
    candidates=[]
    for name,rs in sel.items():
        m=metrics(rs);p=periods(rs);stable=(m['bets']>=20 and (m['win_pct'] or 0)>=72 and (m['roi_median_pct'] or -999)>=8 and
                                             p['2021_2023']['bets']>=5 and p['2024_2026']['bets']>=5 and
                                             (p['2021_2023']['roi_median_pct'] or -999)>0 and (p['2024_2026']['roi_median_pct'] or -999)>0)
        if stable:candidates.append({'rule':name,'definition':rules[name],'metrics':m,'periods':p})
    candidates.sort(key=lambda x:(-(x['metrics']['wilson95_lower_pct'] or 0),-(x['metrics']['roi_median_pct'] or -999),-x['metrics']['bets']))
    years=sorted({r['year'] for r in rows});outer=[]
    for year in years:
        ranked=[]
        for name,rs in sel.items():
            valid=[]
            for inner in years:
                if inner>=year:continue
                train=[r for r in rs if r['year']<inner]
                if len(train)>=15:valid.extend(r for r in rs if r['year']==inner)
            vm=metrics(valid)
            if vm['bets']>=12 and (vm['win_pct'] or 0)>=65 and (vm['roi_median_pct'] or -999)>0:
                ranked.append((vm['wilson95_lower_pct'] or 0,vm['roi_median_pct'] or -999,vm['bets'],name,vm))
        ranked.sort(key=lambda x:(-x[0],-x[1],-x[2],x[3]));best=ranked[0][3] if ranked else None
        families={}
        for _,_,_,name,vm in ranked:families.setdefault(rules[name]['family'],{'rule':name,'validation':vm})
        votes=Counter()
        for x in families.values():
            for r in sel[x['rule']]:
                if r['year']==year:votes[tuple(r['key'])]+=1
        outer.append({'year':year,'best_rule_from_earlier_years':best,'best_rule_test':metrics([r for r in sel.get(best,[]) if r['year']==year]),
                      'family_choices':families,'consensus':{str(k):metrics([r for r in rows if r['year']==year and votes[tuple(r['key'])]>=k]) for k in (2,3,4,5)}})
    report={'status':'EXPLORATORY_DYNAMIC_FACTORS','eligible_bouts':len(rows),'rules_tested':len(rules),'targets':candidates,'outer_walk_forward':outer,
            'limitations':['All historical prices remain timing/settlement-unverified.','Observed career histories may be incomplete.','Absolute age requires verified DOB; missing ages remain excluded.']}
    (run/'phase4_dynamic_factors.json').write_text(json.dumps(report,indent=2))
    lines=['BOXING PHASE-4 DYNAMIC PRE-FIGHT FACTORS','='*110,f"Eligible bouts: {len(rows)} | rules tested: {len(rules)}",'',
           'TOP STABLE DESCRIPTIVE TARGETS (NOT PROMOTED)','-'*110]
    if not candidates:lines.append('No Phase-4 rule passed the stability screen.')
    for x in candidates[:35]:
        m=x['metrics'];d=x['periods']['2021_2023'];l=x['periods']['2024_2026']
        lines.append(f"{x['rule']} | n={m['bets']} {m['wins']}-{m['losses']} win={m['win_pct']:.1f}% ROI={m['roi_median_pct']:+.2f}% | dev={d['roi_median_pct']:+.2f}% later={l['roi_median_pct']:+.2f}%")
    lines+=['','OUTER WALK-FORWARD','-'*110]
    for o in outer:
        m=o['best_rule_test'];lines.append(f"{o['year']} | {o['best_rule_from_earlier_years']} | n={m['bets']} win={m['win_pct']} ROI={m['roi_median_pct']}")
    (run/'phase4_dynamic_factors.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'bouts':len(rows),'rules':len(rules),'targets':len(candidates)}))
if __name__=='__main__':main()
