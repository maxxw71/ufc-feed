#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import mls_autoresearch as ar

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
DATA=ROOT/'data/processed/mls_match_features_availability_enriched.parquet'
OUT=ROOT/'research/availability'
OUT.mkdir(parents=True,exist_ok=True)

METHODS=[
    {'id':'MLS-R01','outcome':'HOME','pb':'P40_50','rules':[('edge_last3_xgfpg','>=',0.45538),('elo_edge','<=',0.91)]},
    {'id':'MLS-R02','outcome':'HOME','pb':'P40_50','rules':[('edge_last3_xgfpg','>=',0.45538),('edge_last3_xgapg','>=',0.0536)]},
    {'id':'MLS-R03','outcome':'AWAY','pb':'P25_35','rules':[('elo_edge','<=',-41.31),('edge_last5_xgapg','<',0.01113)]},
    {'id':'MLS-W01','outcome':'HOME','pb':'P40_50','rules':[('edge_last3_xgfpg','>=',0.45538),('edge_season_gdpg','<=',-0.251028),('opp_season_gdpg','<',0.381169)]},
]

BURDEN_METRICS=[
    'out_count',
    'questionable_count',
    'injury_out_count',
    'non_injury_out_count',
    'suspension_out_count',
    'international_duty_out_count',
    'illness_out_count',
    'concussion_out_count',
    'weighted_missing_minutes_share5',
    'weighted_missing_xgi_share5',
    'weighted_missing_gplus_share5',
    'missing_top11_minutes_count5',
    'missing_top3_xgi_count5',
    'gk_out',
]

def now():
    return datetime.now(timezone.utc).isoformat()

def metrics(x):
    if x.empty:
        return None
    return {
        'n':int(len(x)),
        'wins':int(x.win.sum()),
        'losses':int(len(x)-x.win.sum()),
        'win_rate':float(x.win.mean()),
        'roi':float(x.profit.mean()),
        'units':float(x.profit.sum()),
    }

def cnd(df,feature,op,t):
    v=pd.to_numeric(df[feature],errors='coerce')
    if op=='>=':return v.ge(t)
    if op=='<=':return v.le(t)
    if op=='>':return v.gt(t)
    if op=='<':return v.lt(t)
    raise ValueError(op)

def thresholds(series):
    v=pd.to_numeric(series,errors='coerce').dropna()
    if v.nunique()<2:
        return []
    if v.nunique()<=6:
        return sorted(float(x) for x in v.unique())
    return sorted(set(float(x) for x in v.quantile([.20,.35,.50,.65,.80]).dropna()))

def availability_selection_rows(d):
    s=ar.selection_rows(d)
    cols=['match_id','availability_report_quality']
    for side in ['home','away']:
        for metric in BURDEN_METRICS:
            col=f'{side}_availability_{metric}'
            if col in d.columns:
                cols.append(col)
        for extra in ['report_available','explicit_clear','inferred_clear','resolved_players','unresolved_players']:
            col=f'{side}_availability_{extra}'
            if col in d.columns:
                cols.append(col)

    a=d[cols].drop_duplicates('match_id')
    s=s.merge(a,on='match_id',how='left',validate='m:1')

    for metric in BURDEN_METRICS:
        h=f'home_availability_{metric}'
        aw=f'away_availability_{metric}'
        if h not in s.columns or aw not in s.columns:
            continue
        hv=pd.to_numeric(s[h],errors='coerce')
        av=pd.to_numeric(s[aw],errors='coerce')
        s[f'sel_availability_{metric}']=np.where(s.outcome.eq('HOME'),hv,np.where(s.outcome.eq('AWAY'),av,np.nan))
        s[f'opp_availability_{metric}']=np.where(s.outcome.eq('HOME'),av,np.where(s.outcome.eq('AWAY'),hv,np.nan))
        s[f'edge_availability_{metric}']=s[f'opp_availability_{metric}']-s[f'sel_availability_{metric}']
        s[f'balance_availability_{metric}']=(hv-av).abs()
        s[f'combined_availability_{metric}']=hv+av

    hr=pd.to_numeric(s.get('home_availability_report_available'),errors='coerce')
    ar_=pd.to_numeric(s.get('away_availability_report_available'),errors='coerce')
    s['availability_complete']=hr.eq(1)&ar_.eq(1)&pd.to_numeric(s.availability_report_quality,errors='coerce').ge(.75)
    return s

def direct_features(outcome):
    if outcome=='DRAW':
        return [f'{p}_availability_{m}' for m in BURDEN_METRICS for p in ['balance','combined']]
    return [f'{p}_availability_{m}' for m in BURDEN_METRICS for p in ['sel','opp','edge']]

def method_mask(df,m):
    x=ar.pmask(df,m['pb'])
    for feat,op,t in m['rules']:
        x &= cnd(df,feat,op,t)
    return x

def main():
    if not DATA.exists():
        raise RuntimeError('Availability-enriched MLS warehouse missing')
    d=pd.read_parquet(DATA)
    s=availability_selection_rows(d)
    avail=s[s.availability_complete & s.season.isin([2024,2025])].copy()

    coverage=(
        avail.drop_duplicates(['match_id','season'])
        .groupby('season').match_id.nunique().to_dict()
    )
    if int(coverage.get(2024,0))<150 or int(coverage.get(2025,0))<150:
        raise RuntimeError(f'Availability history still too thin for two-season research: {coverage}')

    # Direct rules: 2024 discovery only. Freeze diversified contexts, then open 2025.
    direct=[]
    tested_direct=0
    for outcome in ['HOME','DRAW','AWAY']:
        so=avail[avail.outcome.eq(outcome)].copy()
        disc=so[so.season.eq(2024)]
        for feature in direct_features(outcome):
            if feature not in so.columns:
                continue
            vals=pd.to_numeric(disc[feature],errors='coerce')
            if vals.notna().sum()<100 or vals.nunique(dropna=True)<2:
                continue
            for t in thresholds(vals):
                for op in ['>=','<=']:
                    cm=cnd(so,feature,op,t)
                    for pb,_,__ in ar.PRICE_BANDS:
                        tested_direct+=1
                        m=cm&ar.pmask(so,pb)
                        md=metrics(so[m&so.season.eq(2024)])
                        if not md or md['n']<18 or md['roi']<.06 or md['units']<1.0:
                            continue
                        score=md['roi']*math.sqrt(md['n'])
                        direct.append({
                            'outcome':outcome,'price_band':pb,'feature':feature,'op':op,'threshold':t,
                            'discovery':md,'score':score,
                        })

    direct.sort(key=lambda x:(x['score'],x['discovery']['n']),reverse=True)
    frozen_direct=[]
    seen=set()
    for r in direct:
        key=(r['outcome'],r['price_band'],r['feature'])
        if key in seen:
            continue
        seen.add(key)
        frozen_direct.append(r)
        if len(frozen_direct)>=60:
            break

    for r in frozen_direct:
        so=avail[avail.outcome.eq(r['outcome'])].copy()
        m=cnd(so,r['feature'],r['op'],r['threshold'])&ar.pmask(so,r['price_band'])
        r['validation']=metrics(so[m&so.season.eq(2025)])
        r['validated']=bool(r['validation'] and r['validation']['n']>=15 and r['validation']['roi']>0 and r['validation']['units']>0)

    # Availability vetoes for previously frozen MLS method families.
    veto_frozen=[]
    for method in METHODS:
        so=avail[avail.outcome.eq(method['outcome'])].copy()
        base=so[method_mask(so,method)].copy()
        disc=base[base.season.eq(2024)].copy()
        valid=base[base.season.eq(2025)].copy()
        base_disc=metrics(disc)
        base_valid=metrics(valid)
        if not base_disc or base_disc['n']<8:
            continue

        candidates=[]
        for feature in direct_features(method['outcome']):
            if feature not in disc.columns:
                continue
            vals=pd.to_numeric(disc[feature],errors='coerce')
            if vals.notna().sum()<8 or vals.nunique(dropna=True)<2:
                continue
            for t in thresholds(vals):
                for op in ['>=','<=']:
                    risk=cnd(disc,feature,op,t)
                    risk_m=metrics(disc[risk])
                    safe_m=metrics(disc[~risk])
                    if not risk_m or not safe_m:
                        continue
                    if risk_m['n']<3 or safe_m['n']<5:
                        continue
                    improvement=safe_m['roi']-base_disc['roi']
                    risk_drop=base_disc['roi']-risk_m['roi']
                    if improvement<.04 or risk_drop<.10:
                        continue
                    score=improvement+.25*risk_drop+.01*math.sqrt(safe_m['n'])
                    candidates.append({
                        'method':method['id'],'feature':feature,'op':op,'threshold':t,
                        'score':score,'discovery_base':base_disc,'discovery_risk':risk_m,'discovery_safe':safe_m,
                    })

        candidates.sort(key=lambda x:x['score'],reverse=True)
        seen_features=set()
        frozen=[]
        for r in candidates:
            if r['feature'] in seen_features:
                continue
            seen_features.add(r['feature'])
            frozen.append(r)
            if len(frozen)>=10:
                break

        for r in frozen:
            if base_valid:
                risk=cnd(valid,r['feature'],r['op'],r['threshold'])
                r['validation_base']=base_valid
                r['validation_risk']=metrics(valid[risk])
                r['validation_safe']=metrics(valid[~risk])
                vs=r['validation_safe']
                r['validated']=bool(
                    vs and vs['n']>=4 and vs['roi']>base_valid['roi'] and vs['roi']>0
                )
            else:
                r['validation_base']=None
                r['validation_risk']=None
                r['validation_safe']=None
                r['validated']=False
            veto_frozen.append(r)

    payload={
        'built_at':now(),
        'dataset':str(DATA),
        'coverage_matches':{str(k):int(v) for k,v in coverage.items()},
        'tested_direct_contexts':tested_direct,
        'pre2025_direct_survivors':len(direct),
        'frozen_direct_candidates':len(frozen_direct),
        'direct_2025_validated':sum(r['validated'] for r in frozen_direct),
        'frozen_veto_candidates':len(veto_frozen),
        'veto_2025_validated':sum(r['validated'] for r in veto_frozen),
        'design':'2024 discovery only; rules frozen before 2025 validation. No 2025 thresholds are used to tune a rule.',
        'status':'EXPLORATORY_SHADOW_ONLY',
        'promotion_note':'Two-season availability history is insufficient for promotion regardless of validation result.',
        'top_direct_discovery_order':frozen_direct[:30],
        'availability_vetoes':veto_frozen,
    }
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))

    lines=[
        'MLS AVAILABILITY / MISSING-PLAYER SHADOW RESEARCH',
        '='*100,
        f"coverage={coverage}",
        f"direct contexts tested={tested_direct:,} frozen={len(frozen_direct)} validated_2025={payload['direct_2025_validated']}",
        f"veto candidates frozen={len(veto_frozen)} validated_2025={payload['veto_2025_validated']}",
        '2024 is discovery. 2025 is validation only. Nothing here can be promoted from two seasons.',
        '',
        'DIRECT RULES — FROZEN IN 2024 ORDER',
    ]
    for i,r in enumerate(frozen_direct[:20],1):
        v=r.get('validation') or {}
        lines.append(
            f"{i:02d}. {r['outcome']} {r['price_band']} | {r['feature']} {r['op']} {r['threshold']:.6g} "
            f"| 2024 n={r['discovery']['n']} ROI={r['discovery']['roi']:+.1%} "
            f"| 2025 n={v.get('n',0)} ROI={v.get('roi',float('nan')):+.1%} validated={r['validated']}"
        )
    lines+=['','VETOES ON FROZEN MLS METHODS']
    for r in veto_frozen:
        vb=r.get('validation_base') or {}
        vs=r.get('validation_safe') or {}
        lines.append(
            f"{r['method']} | veto risk when {r['feature']} {r['op']} {r['threshold']:.6g} "
            f"| 2024 base={r['discovery_base']['roi']:+.1%} safe={r['discovery_safe']['roi']:+.1%} risk={r['discovery_risk']['roi']:+.1%} "
            f"| 2025 base={vb.get('roi',float('nan')):+.1%} safe={vs.get('roi',float('nan')):+.1%} validated={r['validated']}"
        )
    report='\n'.join(lines)+'\n'
    (OUT/'report.txt').write_text(report)
    print(report)

if __name__=='__main__':
    main()
