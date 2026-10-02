#!/usr/bin/env python3
from __future__ import annotations

import itertools
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import mls_arsenal_research as arx
import mls_active_portfolio as active

ROOT = Path('/home/anestishkurti92/mls-predictor-v1')
DATA = ROOT / 'data/processed/mls_match_features_master.parquet'
OUT = ROOT / 'research/draw_aware'
OUT.mkdir(parents=True, exist_ok=True)

SPLIT = {'train': (2013, 2018), 'validation': (2019, 2022), 'holdout': (2023, 2025)}
DRAW_BANDS = [
    ('ALL', 0.0, 1.0),
    ('DP20_24', .20, .24),
    ('DP22_26', .22, .26),
    ('DP24_28', .24, .28),
    ('DP26_30', .26, .30),
    ('DP28_32', .28, .32),
    ('DP30_36', .30, .36),
]
KEYS = {'match_id','date','season','outcome','win','odds','market_prob','profit'}
SAFE_FAMILIES = {'base','player_manager','context','weather','salary','style','cross_comp',
                 'goalkeeper_roster','venue','referee','market','drawctx'}

def now():
    return datetime.now(timezone.utc).isoformat()

def period(df, era):
    lo, hi = SPLIT[era]
    return df.season.between(lo, hi)

def metrics(x):
    if x.empty:
        return None
    by = x.groupby('season').profit.agg(['count','sum'])
    active_seasons = by[by['count'] >= 5]
    if 'win' in x.columns:
        w = pd.to_numeric(x['win'], errors='coerce').fillna(0).astype(int)
    else:
        w = (pd.to_numeric(x['profit'], errors='coerce') > 0).astype(int)
    return {
        'n': int(len(x)),
        'wins': int(w.sum()),
        'losses': int(len(x) - w.sum()),
        'win_rate': float(w.mean()),
        'roi': float(x.profit.mean()),
        'units': float(x.profit.sum()),
        'active_seasons': int(len(active_seasons)),
        'positive_seasons': int((active_seasons['sum'] > 0).sum()),
        'positive_season_ratio': float((active_seasons['sum'] > 0).mean()) if len(active_seasons) else 0.0,
    }

def draw_rate(x):
    if x.empty or 'actual_draw' not in x:
        return None
    v = pd.to_numeric(x.actual_draw, errors='coerce').dropna()
    return float(v.mean()) if len(v) else None

def boot(v, n=3000, seed=20261002):
    a = np.asarray(v, float)
    if len(a) < 2:
        return [None, None]
    rng = np.random.default_rng(seed + len(a))
    means = np.empty(n)
    for i in range(n):
        means[i] = rng.choice(a, size=len(a), replace=True).mean()
    return [float(x) for x in np.quantile(means, [.025, .975])]

def cond(df, feature, op, threshold):
    v = pd.to_numeric(df[feature], errors='coerce')
    return v.ge(threshold) if op == '>=' else v.le(threshold)

def thresholds(v):
    v = pd.to_numeric(v, errors='coerce').dropna()
    if v.nunique() < 2:
        return []
    if v.nunique() <= 7:
        return sorted(float(x) for x in v.unique())
    return sorted(set(float(x) for x in v.quantile([.10,.20,.30,.40,.50,.60,.70,.80,.90]).dropna()))

def draw_band_mask(df, band):
    _, lo, hi = next(x for x in DRAW_BANDS if x[0] == band)
    return pd.to_numeric(df.market_prob, errors='coerce').between(lo, hi, inclusive='both')

def family(feature):
    n = feature.lower()
    if n.startswith('market__'):
        return 'market'
    if n.startswith('drawctx__'):
        return 'drawctx'
    if n.startswith('base__'):
        return 'base'
    if n.startswith('pm__'):
        return 'player_manager'
    if n.startswith('ctx__'):
        return 'context'
    if n.startswith('wx__'):
        return 'weather'
    if 'salary_' in n:
        return 'salary'
    if 'style_' in n:
        return 'style'
    if any(x in n for x in ['allcomp_','external_matches','external_minutes','us_open_cup_','leagues_cup_','concacaf_']):
        return 'cross_comp'
    if 'gk_' in n or 'roster_' in n:
        return 'goalkeeper_roster'
    if 'referee_' in n:
        return 'referee'
    if any(x in n for x in ['stadium_','venue_','travel_from_prev_match','surface_']):
        return 'venue'
    return 'other'

def market_context(s):
    p = s.pivot_table(index='match_id', columns='outcome', values=['market_prob','odds','win'], aggfunc='first')
    p.columns = [f'{a}_{b.lower()}' for a,b in p.columns]
    p = p.reset_index()
    for c in ['market_prob_home','market_prob_draw','market_prob_away','odds_home','odds_draw','odds_away','win_draw']:
        if c not in p:
            p[c] = np.nan
    ph = pd.to_numeric(p.market_prob_home, errors='coerce')
    pdw = pd.to_numeric(p.market_prob_draw, errors='coerce')
    pa = pd.to_numeric(p.market_prob_away, errors='coerce')
    p['market__home_away_prob_gap_abs'] = (ph-pa).abs()
    p['market__favorite_prob'] = pd.concat([ph,pa],axis=1).max(axis=1)
    p['market__underdog_prob'] = pd.concat([ph,pa],axis=1).min(axis=1)
    p['market__draw_prob'] = pdw
    p['market__draw_vs_favorite_gap'] = p['market__favorite_prob'] - pdw
    p['market__draw_vs_underdog_gap'] = pdw - p['market__underdog_prob']
    eps = 1e-12
    probs = np.vstack([ph.fillna(0), pdw.fillna(0), pa.fillna(0)]).T
    p['market__entropy'] = -np.sum(np.where(probs>0, probs*np.log(probs+eps), 0.0), axis=1)
    p['actual_draw'] = pd.to_numeric(p.win_draw, errors='coerce')
    keep = ['match_id','actual_draw'] + [c for c in p.columns if c.startswith('market__')]
    return p[keep].copy()

def build_draw_context(s):
    mctx = market_context(s)
    d = s[s.outcome.eq('DRAW')].copy()
    numeric = []
    for c in d.columns:
        if c in KEYS or c in {'odds','market_prob'}:
            continue
        v = pd.to_numeric(d[c], errors='coerce')
        if v.notna().sum() >= 300 and v.nunique(dropna=True) >= 2:
            numeric.append(c)
    z = d[['match_id'] + numeric].copy()
    z = z.rename(columns={c:'drawctx__'+c for c in numeric})
    z = z.merge(mctx, on='match_id', how='left', validate='1:1')
    return z

def eligible_draw_features(draws):
    tr = draws[period(draws,'train')]
    va = draws[period(draws,'validation')]
    out = []
    for c in draws.columns:
        if c in KEYS or c in {'odds','market_prob','actual_draw'}:
            continue
        fam = family(c)
        if fam not in SAFE_FAMILIES or fam == 'other':
            continue
        tv = pd.to_numeric(tr[c], errors='coerce')
        vv = pd.to_numeric(va[c], errors='coerce')
        if tv.notna().sum() < 350 or vv.notna().sum() < 250:
            continue
        if tv.nunique(dropna=True) < 2:
            continue
        out.append(c)
    return out

def baseline_draw(draws, band, era):
    return metrics(draws[draw_band_mask(draws,band) & period(draws,era)])

def eval_draw_pre(draws, mask, band):
    out = {}
    for era in ['train','validation']:
        z = metrics(draws[mask & period(draws,era)])
        b = baseline_draw(draws, band, era)
        if not z or not b:
            return None
        out[era] = z
        out[era+'_baseline'] = b
        out[era+'_lift'] = z['roi'] - b['roi']
    pre = period(draws,'train') | period(draws,'validation')
    z = metrics(draws[mask & pre])
    b = metrics(draws[draw_band_mask(draws,band) & pre])
    if not z or not b:
        return None
    out['preholdout'] = z
    out['preholdout_baseline'] = b
    out['preholdout_lift'] = z['roi'] - b['roi']
    return out

def draw_single_gate(e):
    tr, va, pre = e['train'], e['validation'], e['preholdout']
    return (
        tr['n'] >= 45 and va['n'] >= 32 and pre['n'] >= 120
        and tr['roi'] >= .015 and va['roi'] >= .01 and pre['roi'] >= .02
        and e['train_lift'] >= .015 and e['validation_lift'] >= .005 and e['preholdout_lift'] >= .015
        and pre['positive_season_ratio'] >= .60
    )

def draw_pair_gate(e):
    tr, va, pre = e['train'], e['validation'], e['preholdout']
    return (
        tr['n'] >= 36 and va['n'] >= 26 and pre['n'] >= 95
        and tr['roi'] >= .035 and va['roi'] >= .02 and pre['roi'] >= .035
        and e['train_lift'] >= .025 and e['validation_lift'] >= .01 and e['preholdout_lift'] >= .025
        and pre['positive_season_ratio'] >= .60
    )

def draw_triple_gate(e):
    tr, va, pre = e['train'], e['validation'], e['preholdout']
    return (
        tr['n'] >= 30 and va['n'] >= 22 and pre['n'] >= 80
        and tr['roi'] >= .05 and va['roi'] >= .035 and pre['roi'] >= .05
        and e['train_lift'] >= .035 and e['validation_lift'] >= .02 and e['preholdout_lift'] >= .035
        and pre['positive_season_ratio'] >= .65
    )

def pre_score(e):
    return min(e['train']['roi'],e['validation']['roi']) * math.sqrt(max(1,min(e['train']['n'],e['validation']['n'])))

def open_draw_candidate(draws, r):
    m = draw_band_mask(draws,r['price_band'])
    rules = []
    if 'feature' in r:
        rules = [r]
    else:
        rules = [r[k] for k in sorted([k for k in r if k.startswith('rule')])]
    for q in rules:
        m &= cond(draws,q['feature'],q['op'],q['threshold'])
    x = draws[m].copy()
    hold = metrics(x[period(x,'holdout')])
    full = metrics(x)
    hb = baseline_draw(draws,r['price_band'],'holdout')
    lift = hold['roi'] - hb['roi'] if hold and hb else None
    ci = boot(x.profit,seed=20261003+len(rules)) if len(x) else [None,None]
    status = 'RESEARCH_ONLY'
    min_hold = 40 if len(rules)==1 else (32 if len(rules)==2 else 28)
    min_full = 180 if len(rules)==1 else (145 if len(rules)==2 else 120)
    if hold and full and hold['n']>=min_hold and full['n']>=min_full and hold['roi']>=.05 and lift is not None and lift>=0 and full['roi']>=.05 and full['positive_season_ratio']>=.65 and ci[0] is not None and ci[0]>0:
        status = 'DRAW_PRIORITY_SHADOW'
    elif hold and full and hold['n']>=max(20,min_hold//2) and hold['roi']>0 and lift is not None and lift>=0 and full['positive_season_ratio']>=.60:
        status = 'DRAW_WATCH'
    return {**r,'holdout':hold,'holdout_baseline':hb,'holdout_lift':lift,'full':full,'bootstrap95_roi':ci,'status':status}

def scan_direct_draw(draws):
    feats = eligible_draw_features(draws)
    tr = draws[period(draws,'train')]
    singles = []
    tested = 0
    for f in feats:
        fam = family(f)
        for t in thresholds(tr[f]):
            for op in ['>=','<=']:
                cm = cond(draws,f,op,t)
                for band,_,__ in DRAW_BANDS:
                    tested += 1
                    m = cm & draw_band_mask(draws,band)
                    e = eval_draw_pre(draws,m,band)
                    if e and draw_single_gate(e):
                        singles.append({'outcome':'DRAW','price_band':band,'feature':f,'family':fam,'op':op,'threshold':float(t),'pre_score':pre_score(e),'pre':e})
    singles.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    frozen=[];seen=set()
    for r in singles:
        k=(r['price_band'],r['feature'])
        if k in seen:
            continue
        seen.add(k);frozen.append(r)
        if len(frozen)>=180:
            break
    opened_s = [open_draw_candidate(draws,r) for r in frozen]

    groups=defaultdict(list)
    for r in frozen:
        groups[r['price_band']].append(r)
    pairs=[];tested_pairs=0
    for band,rules in groups.items():
        for a,b in itertools.combinations(rules[:32],2):
            if a['family']==b['family']:
                continue
            tested_pairs += 1
            m=draw_band_mask(draws,band)
            m &= cond(draws,a['feature'],a['op'],a['threshold'])
            m &= cond(draws,b['feature'],b['op'],b['threshold'])
            e=eval_draw_pre(draws,m,band)
            if e and draw_pair_gate(e):
                pairs.append({'outcome':'DRAW','price_band':band,
                              'rule1':{k:a[k] for k in ['feature','family','op','threshold']},
                              'rule2':{k:b[k] for k in ['feature','family','op','threshold']},
                              'pre_score':pre_score(e),'pre':e})
    pairs.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    fp=[];seen=set()
    for r in pairs:
        k=(r['price_band'],tuple(sorted([r['rule1']['feature'],r['rule2']['feature']])))
        if k in seen:
            continue
        seen.add(k);fp.append(r)
        if len(fp)>=100:
            break
    opened_p=[open_draw_candidate(draws,r) for r in fp]

    triples=[];tested_triples=0
    for band,rules in groups.items():
        top=rules[:16]
        for a,b,c in itertools.combinations(top,3):
            if len({a['family'],b['family'],c['family']})<2:
                continue
            tested_triples += 1
            m=draw_band_mask(draws,band)
            for q in [a,b,c]:
                m &= cond(draws,q['feature'],q['op'],q['threshold'])
            e=eval_draw_pre(draws,m,band)
            if e and draw_triple_gate(e):
                triples.append({'outcome':'DRAW','price_band':band,
                    'rule1':{k:a[k] for k in ['feature','family','op','threshold']},
                    'rule2':{k:b[k] for k in ['feature','family','op','threshold']},
                    'rule3':{k:c[k] for k in ['feature','family','op','threshold']},
                    'pre_score':pre_score(e),'pre':e})
    triples.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    ft=triples[:60]
    opened_t=[open_draw_candidate(draws,r) for r in ft]
    return {
        'eligible_features':len(feats),'tested_singles':tested,'preholdout_single_survivors':len(singles),
        'tested_pairs':tested_pairs,'preholdout_pair_survivors':len(pairs),
        'tested_triples':tested_triples,'preholdout_triple_survivors':len(triples),
        'single_results':opened_s,'pair_results':opened_p,'triple_results':opened_t
    }

def side_veto_features(z):
    tr=z[period(z,'train')];va=z[period(z,'validation')]
    out=[]
    for c in z.columns:
        if not (c.startswith('drawctx__') or c.startswith('market__')):
            continue
        tv=pd.to_numeric(tr[c],errors='coerce');vv=pd.to_numeric(va[c],errors='coerce')
        if tv.notna().sum()<80 or vv.notna().sum()<55 or tv.nunique(dropna=True)<2:
            continue
        out.append(c)
    return out

def veto_metrics(z,mask,era):
    q=z[mask & period(z,era)]
    return metrics(q), draw_rate(q)

def scan_side_vetoes(s,draw_context):
    old=s.copy()
    out=[]
    for method in active.METHODS:
        mid=method['id']
        base=active.method_rows(old,method).copy()
        if base.empty or base.outcome.eq('DRAW').any():
            continue
        base=base.merge(draw_context,on='match_id',how='left',validate='m:1')
        base_summary={}
        for era in ['train','validation','holdout']:
            q=base[period(base,era)]
            base_summary[era]={'metrics':metrics(q),'draw_rate':draw_rate(q)}
        feats=side_veto_features(base)
        tr=base[period(base,'train')]
        candidates=[]
        tested=0
        for f in feats:
            for t in thresholds(tr[f]):
                for op in ['>=','<=']:
                    # "keep" condition; excluded rows are treated as draw-risk vetoes.
                    keep=cond(base,f,op,t)
                    tested += 1
                    tm,td=veto_metrics(base,keep,'train');vm,vd=veto_metrics(base,keep,'validation')
                    bt=base_summary['train']['metrics'];bv=base_summary['validation']['metrics']
                    btd=base_summary['train']['draw_rate'];bvd=base_summary['validation']['draw_rate']
                    if not all([tm,vm,bt,bv]) or td is None or vd is None or btd is None or bvd is None:
                        continue
                    if tm['n']<max(25,int(.55*bt['n'])) or vm['n']<max(20,int(.50*bv['n'])):
                        continue
                    if tm['roi']-bt['roi']<.015 or vm['roi']-bv['roi']<.01:
                        continue
                    if td>btd-.01 or vd>bvd-.005:
                        continue
                    score=min(tm['roi']-bt['roi'],vm['roi']-bv['roi'])*math.sqrt(min(tm['n'],vm['n']))
                    candidates.append({'method_id':mid,'feature':f,'op':op,'threshold':float(t),'pre_score':score,
                        'train':tm,'validation':vm,'train_draw_rate':td,'validation_draw_rate':vd,
                        'base_train':bt,'base_validation':bv,'base_train_draw_rate':btd,'base_validation_draw_rate':bvd})
        candidates.sort(key=lambda x:x['pre_score'],reverse=True)
        frozen=[];seen=set()
        for r in candidates:
            if r['feature'] in seen:
                continue
            seen.add(r['feature']);frozen.append(r)
            if len(frozen)>=40:
                break
        opened=[]
        for r in frozen:
            keep=cond(base,r['feature'],r['op'],r['threshold'])
            h=base[keep & period(base,'holdout')]
            hm=metrics(h);hd=draw_rate(h)
            bh=base_summary['holdout']['metrics'];bhd=base_summary['holdout']['draw_rate']
            full=metrics(base[keep]);full_dr=draw_rate(base[keep])
            status='RESEARCH_ONLY'
            if hm and bh and hd is not None and bhd is not None and hm['n']>=max(18,int(.45*bh['n'])) and hm['roi']>=bh['roi'] and hd<=bhd and full and full['roi']>0:
                status='SIDE_DRAW_VETO_WATCH'
            opened.append({**r,'holdout':hm,'holdout_draw_rate':hd,'base_holdout':bh,'base_holdout_draw_rate':bhd,
                           'full':full,'full_draw_rate':full_dr,'status':status})
        out.append({'method_id':mid,'tested_vetoes':tested,'preholdout_survivors':len(candidates),
                    'base':base_summary,'results':opened})
    return out

def calibration_tables(s):
    out={}
    for outcome in ['HOME','DRAW','AWAY']:
        z=s[s.outcome.eq(outcome)].copy()
        z['prob_bin']=pd.cut(z.market_prob,bins=[0,.20,.25,.30,.35,.40,.45,.50,.60,.70,1.0],include_lowest=True)
        rows=[]
        for era in ['train','validation','holdout']:
            q=z[period(z,era)]
            for b,g in q.groupby('prob_bin',observed=True):
                if len(g)<20:
                    continue
                mp=float(g.market_prob.mean());wr=float(g.win.mean())
                rows.append({'era':era,'bin':str(b),'n':int(len(g)),'market_prob_mean':mp,'empirical_rate':wr,'calibration_edge':wr-mp})
        out[outcome]=rows
    return out

def compact(r):
    z={k:v for k,v in r.items() if k!='pre'}
    if 'pre' in r:
        z['train']=r['pre']['train'];z['validation']=r['pre']['validation'];z['preholdout']=r['pre']['preholdout']
        z['preholdout_baseline']=r['pre']['preholdout_baseline'];z['preholdout_lift']=r['pre']['preholdout_lift']
    return z

def main():
    if not DATA.exists():
        raise RuntimeError(f'Missing master warehouse: {DATA}')
    d=pd.read_parquet(DATA).copy()
    d['match_id']=d.match_id.astype(str)
    s=arx.merged_selection_rows(d).copy()
    s['match_id']=s.match_id.astype(str)
    dc=build_draw_context(s)

    draws=s[s.outcome.eq('DRAW')].copy().merge(dc,on='match_id',how='left',validate='1:1',suffixes=('','__ctx'))
    direct=scan_direct_draw(draws)
    veto=scan_side_vetoes(s,dc)
    calibration=calibration_tables(s)

    direct_all=direct['single_results']+direct['pair_results']+direct['triple_results']
    priority=[compact(x) for x in direct_all if x['status']=='DRAW_PRIORITY_SHADOW']
    watch=[compact(x) for x in direct_all if x['status']=='DRAW_WATCH']
    veto_watch=[]
    for block in veto:
        for x in block['results']:
            if x['status']=='SIDE_DRAW_VETO_WATCH':
                veto_watch.append(x)

    payload={
        'built_at':now(),'dataset':str(DATA),'rows':int(len(d)),'columns':int(len(d.columns)),
        'selection_rows':int(len(s)),'splits':SPLIT,'status':'SHADOW_RESEARCH_ONLY',
        'market_scope':'REAL_1X2_ONLY',
        'pricing_note':'Uses observed historical home/draw/away odds only. No synthetic DNB, double-chance, totals, BTTS, or Asian-handicap returns are treated as real backtests.',
        'direct_draw':{
            'eligible_features':direct['eligible_features'],'tested_singles':direct['tested_singles'],
            'preholdout_single_survivors':direct['preholdout_single_survivors'],
            'tested_pairs':direct['tested_pairs'],'preholdout_pair_survivors':direct['preholdout_pair_survivors'],
            'tested_triples':direct['tested_triples'],'preholdout_triple_survivors':direct['preholdout_triple_survivors'],
            'priority_count':len(priority),'watch_count':len(watch),
            'priority_preholdout_order':priority,'watch_preholdout_order':watch,
            'all_frozen_singles':[compact(x) for x in direct['single_results']],
            'all_frozen_pairs':[compact(x) for x in direct['pair_results']],
            'all_frozen_triples':[compact(x) for x in direct['triple_results']],
        },
        'side_draw_vetoes':{'watch_count':len(veto_watch),'watch_candidates':veto_watch,'per_method':veto},
        'market_calibration':calibration,
        'design':'Direct DRAW thresholds and rankings are frozen on 2013-18 train plus 2019-22 validation before opening 2023-25 holdout. Side draw-risk vetoes must improve ROI and lower realized draw-loss rate in both train and validation before holdout is opened. 2026 remains prospective only.'
    }
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))

    lines=[]
    lines.append('MLS DRAW-AWARE 1X2 DISCOVERY')
    lines.append('='*100)
    lines.append(f"master={len(d):,} x {len(d.columns):,} | selection_rows={len(s):,} | real 1X2 prices only")
    lines.append('Splits: 2013-18 train | 2019-22 validation | 2023-25 untouched holdout | 2026 prospective')
    lines.append('')
    lines.append('DIRECT DRAW SEARCH')
    lines.append(f"eligible_features={direct['eligible_features']} singles_tested={direct['tested_singles']:,} pairs_tested={direct['tested_pairs']:,} triples_tested={direct['tested_triples']:,}")
    lines.append(f"preholdout survivors: singles={direct['preholdout_single_survivors']} pairs={direct['preholdout_pair_survivors']} triples={direct['preholdout_triple_survivors']}")
    lines.append(f"opened holdout: priority={len(priority)} watch={len(watch)}")
    for i,x in enumerate(priority[:12],1):
        rules=[]
        if 'feature' in x:
            rules=[f"{x['feature']} {x['op']} {x['threshold']:.6g}"]
        else:
            for k in ['rule1','rule2','rule3']:
                if k in x:
                    q=x[k];rules.append(f"{q['feature']} {q['op']} {q['threshold']:.6g}")
        lines.append(f"DRAW PRIORITY {i}: {x['price_band']} | {' + '.join(rules)} | hold={x['holdout']} | full={x['full']} | CI={x['bootstrap95_roi']}")
    for i,x in enumerate(watch[:12],1):
        lines.append(f"DRAW WATCH {i}: band={x['price_band']} hold={x['holdout']} full={x['full']}")
    lines.append('')
    lines.append('SIDE DRAW-RISK VETOES')
    lines.append(f"validated holdout watches={len(veto_watch)}")
    for i,x in enumerate(veto_watch[:20],1):
        lines.append(f"VETO {i}: {x['method_id']} keep when {x['feature']} {x['op']} {x['threshold']:.6g} | hold ROI {x['base_holdout']['roi']:+.1%}->{x['holdout']['roi']:+.1%} | draw rate {x['base_holdout_draw_rate']:.1%}->{x['holdout_draw_rate']:.1%} | n {x['base_holdout']['n']}->{x['holdout']['n']}")
    lines.append('')
    lines.append('No automatic promotion. 2026 remains prospective only.')
    (OUT/'latest.txt').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))

if __name__=='__main__':
    main()
