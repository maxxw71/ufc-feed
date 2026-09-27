#!/usr/bin/env python3
"""Full boxing method-discovery sweep.

Research/shadow only. Combines existing fixed rule universes, requires explicit
opponent-relative context, checks era stability, nearby-rule robustness,
bootstrap ROI uncertainty, overlap, and historical losses. No live promotion.
"""
from __future__ import annotations
import json, math, random
from collections import defaultdict
from pathlib import Path
from market_consensus import load_master, canonical_market_rows
import phase2_interaction_scan as p2
import phase3_physical_context_scan as p3
import phase4_dynamic_factors as p4

ROOT=Path(__file__).resolve().parent
RUN=Path((ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').read_text().strip())

# Families that explicitly compare the selected fighter with the opponent or
# use opponent-specific vulnerability/context. Pure favorite-only streak/context
# families are intentionally excluded from promotion discovery.
OPPONENT_AWARE={
 'AGE_QUALITY','AGE_FORM','AGE_EXPERIENCE','AGE_POWER','QUALITY_EXPERIENCE',
 'QUALITY_SCHEDULE','QUALITY_FORM','QUALITY_ACTIVITY','POWER_CHIN',
 'REACH','AGE_REACH','FORM_REACH','QUALITY_REACH','HEIGHT','AGE_HEIGHT',
 'AGE_TITLE','AGE_LONG','AGE_WORLD_TITLE','FORM_TITLE','FORM_LONG','SOUTHPAW',
 'SOUTHPAW_FORM','REMATCH_PRIOR_WIN','REMATCH_REVENGE',
 'AGING_OPP','OPP_LAYOFF','ACTIVITY_LAYOFF','AGE_LAYOFF','STREAK_MISMATCH',
 'OPP_LOSS_STREAK','STOPLOSS','POWER_VULN','SCHEDULE_STRENGTH','AGE_STRENGTH',
 'DECISION_EDGE'
}

def metrics(rows):
    return p2.metrics(rows)

def era(rows,a,b):
    return metrics([r for r in rows if a<=int(r['year'])<=b])

def bootstrap_roi(rows, draws=3000, seed=260927):
    if not rows:return {'lo':None,'median':None,'hi':None,'p_gt_0':None}
    rng=random.Random(seed)
    vals=[]
    n=len(rows)
    profits=[float(r['profit_median']) for r in rows]
    for _ in range(draws):
        vals.append(100*sum(profits[rng.randrange(n)] for __ in range(n))/n)
    vals.sort()
    def q(p):return vals[min(len(vals)-1,max(0,int(round((len(vals)-1)*p))))]
    return {'lo':q(.025),'median':q(.5),'hi':q(.975),'p_gt_0':sum(v>0 for v in vals)/len(vals)}

def jaccard(a,b):
    a=set(a);b=set(b)
    return len(a&b)/len(a|b) if a or b else 0.0

def build_candidates(markets):
    rows2=p2.feature_rows(markets)
    rows3=p3.add_features(markets)
    rows4=p4.rows_from(markets)
    sources=[
      ('phase2',rows2,p2.rule_universe(),p2.matches),
      ('phase3',rows3,p3.universe(),p3.match),
      ('phase4',rows4,p4.universe(),p4.match),
    ]
    out=[]
    for phase,rows,rules,match in sources:
        for name,spec in rules.items():
            fam=spec.get('family')
            if fam not in OPPONENT_AWARE:continue
            sel=[r for r in rows if match(r,spec)]
            m=metrics(sel);d=era(sel,2021,2023);l=era(sel,2024,2026)
            stable=(m['bets']>=20 and (m['win_pct'] or 0)>=72 and
                    (m['roi_median_pct'] if m['roi_median_pct'] is not None else -999)>=8 and
                    d['bets']>=5 and l['bets']>=5 and
                    (d['roi_median_pct'] if d['roi_median_pct'] is not None else -999)>0 and
                    (l['roi_median_pct'] if l['roi_median_pct'] is not None else -999)>0)
            if not stable:continue
            keys=[tuple(r['key']) for r in sel]
            losses=[]
            for r in sel:
                if not r['win']:
                    losses.append({k:r.get(k) for k in (
                      'year','favorite','opponent','median_price','market_prob','book_count',
                      'younger_by','form_gap','experience_gap','reach_gap','height_gap',
                      'fav_age','opp_age','fav_rest','opp_rest','rest_edge',
                      'strength_gap','opponent_strength_gap','favorite_ko_rate',
                      'opponent_stoppage_loss_rate','opponent_last5_stoppage_losses'
                    ) if k in r})
            out.append({'phase':phase,'rule':name,'family':fam,'spec':spec,'metrics':m,
                        'dev_2021_2023':d,'later_2024_2026':l,'keys':keys,'losses':losses,
                        'bootstrap':bootstrap_roi(sel)})
    return out

def neighborhood_scores(cands):
    fam=defaultdict(list)
    for c in cands:fam[(c['phase'],c['family'])].append(c)
    for c in cands:
        peers=fam[(c['phase'],c['family'])]
        # Existing grids are predeclared neighboring thresholds; reward families
        # where multiple nearby variants independently pass the same stability gate.
        c['stable_family_variants']=len(peers)
        c['neighborhood_support']=min(1.0,len(peers)/5.0)
        b=c['bootstrap'];m=c['metrics']
        c['score']=(m['wilson95_lower_pct'] or 0)+0.25*(m['roi_median_pct'] or 0)+5*c['neighborhood_support']+3*(b['p_gt_0'] or 0)

def shortlist(cands):
    # Best representative per family, then greedily reduce duplicated fight sets.
    byfam={}
    for c in cands:
        key=(c['phase'],c['family'])
        if key not in byfam or c['score']>byfam[key]['score']:byfam[key]=c
    ranked=sorted(byfam.values(),key=lambda c:(-c['score'],-c['metrics']['bets']))
    chosen=[]
    for c in ranked:
        if any(jaccard(c['keys'],x['keys'])>=.65 for x in chosen):continue
        chosen.append(c)
        if len(chosen)>=8:break
    return chosen,ranked

def main():
    master=load_master(RUN/'boxing_prefight_master.jsonl')
    markets=canonical_market_rows(master,min_books=2)
    cands=build_candidates(markets)
    neighborhood_scores(cands)
    chosen,ranked=shortlist(cands)
    overlap={}
    for a in chosen:
        overlap[a['rule']]={}
        for b in chosen:
            overlap[a['rule']][b['rule']]=round(jaccard(a['keys'],b['keys']),4)
    clean=[]
    for c in chosen:
        x=dict(c);x.pop('keys',None);clean.append(x)
    family_clean=[]
    for c in ranked:
        x=dict(c);x.pop('keys',None);x.pop('losses',None);family_clean.append(x)
    report={
      'status':'RESEARCH_SHADOW_ONLY_NOT_LIVE',
      'eligible_consensus_bouts':len(markets),
      'stable_opponent_aware_rules':len(cands),
      'independent_shortlist':clean,
      'best_per_family':family_clean,
      'shortlist_overlap_jaccard':overlap,
      'promotion_gate':[
        'No live promotion from retrospective discovery alone.',
        'Require opponent-relative context.',
        'Require positive ROI in both 2021-23 and 2024-26 with >=5 bets each.',
        'Require >=20 total bets, >=72% win rate, >=8% exploratory median-price ROI.',
        'Inspect neighboring thresholds/family support and every historical loss.',
        'Track prospectively with verified pre-event sportsbook prices before live promotion.'
      ],
      'limitations':[
        'Historical ROI outside independently verified price rows remains exploratory.',
        'Observed boxing career histories may still be incomplete for some fighters.',
        'Reach/height are stable adult proxies where available, not historical timestamped measurements.',
        'Punch-stat families are intentionally excluded until prefight historical depth is adequate.'
      ]
    }
    (RUN/'full_method_discovery_sweep.json').write_text(json.dumps(report,indent=2,default=list))
    lines=['BOXING FULL METHOD-DISCOVERY SWEEP','='*116,
           f"Consensus-priced bouts: {len(markets)} | stable opponent-aware rules: {len(cands)}",
           'STATUS: RESEARCH/SHADOW ONLY — NOTHING PROMOTED LIVE','',
           'INDEPENDENT SHORTLIST','-'*116]
    for i,c in enumerate(chosen,1):
        m=c['metrics'];d=c['dev_2021_2023'];l=c['later_2024_2026'];b=c['bootstrap']
        lines.append(f"{i}. {c['family']} | {c['rule']}")
        lines.append(f"   n={m['bets']} {m['wins']}-{m['losses']} win={m['win_pct']:.1f}% ROI={m['roi_median_pct']:+.2f}% | dev={d['roi_median_pct']:+.2f}% later={l['roi_median_pct']:+.2f}%")
        lines.append(f"   bootstrap 95% ROI [{b['lo']:+.2f}, {b['hi']:+.2f}] P(ROI>0)={100*b['p_gt_0']:.1f}% | stable family variants={c['stable_family_variants']} | losses={len(c['losses'])}")
    lines+=['','TOP FAMILY REPRESENTATIVES','-'*116]
    for c in ranked[:25]:
        m=c['metrics'];lines.append(f"{c['family']:22s} n={m['bets']:3d} {m['wins']:3d}-{m['losses']:<3d} win={m['win_pct']:5.1f}% ROI={m['roi_median_pct']:+6.2f}% variants={c['stable_family_variants']:2d} score={c['score']:.2f}")
    lines+=['','LOSS AUDIT FOR SHORTLIST','-'*116]
    for c in chosen:
        lines.append(f"{c['family']} / {c['rule']}:")
        if not c['losses']:lines.append('  no losses')
        for x in c['losses']:lines.append('  '+json.dumps(x,sort_keys=True))
    (RUN/'full_method_discovery_sweep.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'eligible':len(markets),'stable_rules':len(cands),'shortlist':[c['rule'] for c in chosen]},indent=2))

if __name__=='__main__':main()
