#!/usr/bin/env python3
from __future__ import annotations

import json, math, os, sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
OUT=REPO/'nfl'/'week2_loss_postmortem'; OUT.mkdir(parents=True,exist_ok=True)
PRE=REPO/'nfl'/'legacy_preseason_staff_filter_audit'/'espn_preseason_team_seasons.csv'
ORIG=REPO/'nfl'/'legacy_live_home_opener_exact'/'original_bets.csv'
STRICT=REPO/'nfl'/'legacy_live_home_opener_exact'/'stricter_bets.csv'
STATE=Path('/srv/appwiza-sports/state/nfl.json')
LEDGER=Path('/home/anestishkurti92/betting-ledger/assumed_bets.sqlite3')
CTX=Path('/home/appwiza-runner/nfl-context-data/derived/team_game_pregame_context_2006_2026.parquet')
TEAMW=REPO/'nfl'/'weekly_archive'/'2026'/'week_02'/'pregame'/'team_stats.parquet'
ALIASES={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA','WSH':'WAS','JAX':'JAC'}
SPLITS={'development':(2007,2013),'validation':(2014,2019),'holdout':(2020,2025),'recent3':(2023,2025)}

def num(x): return pd.to_numeric(x,errors='coerce')
def boolish(x):
    if isinstance(x,bool): return x
    s=str(x or '').strip().lower()
    if s in {'true','1','yes','y'}: return True
    if s in {'false','0','no','n'}: return False
    return None

def metric(x):
    if x is None or not len(x): return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi':np.nan,'units':0.0}
    z=x[num(x.moneyline).notna() & num(x.win).isin([0,1])].copy()
    if not len(z): return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi':np.nan,'units':0.0}
    return {'n':int(len(z)),'wins':int(num(z.win).sum()),'losses':int(len(z)-num(z.win).sum()),
            'win_pct':float(num(z.win).mean()),'roi':float(num(z.profit_units).mean()),'units':float(num(z.profit_units).sum())}

def era_metrics(x):
    out={'full':metric(x)}
    for k,(a,b) in SPLITS.items(): out[k]=metric(x[num(x.season).between(a,b)])
    y=[]
    for season,g in x.groupby('season'):
        m=metric(g); y.append({'season':int(season),**m})
    active=[r for r in y if r['n']>=2]
    out['positive_season_ratio']=float(sum(r['units']>0 for r in active)/len(active)) if active else np.nan
    out['by_season']=y
    return out

def load_exact(path,method):
    d=pd.read_csv(path,low_memory=False)
    out=pd.DataFrame({
        'method':method,'season':num(d.season).astype(int),'week':num(d.week).astype(int),
        'game_id':d.game_id.astype(str),'team':d.home_team.replace(ALIASES),
        'opponent':d.away_team.replace(ALIASES),'moneyline':num(d.home_moneyline),
        'win':num(d.win),'profit_units':num(d.profit),'record_gap':num(d.prior_record_edge),
        'run_def_rank':num(d.run_rank if 'run_rank' in d else d.last_rank_def_allowed_rush_epa_per_carry),
    })
    # Preserve confirmed enriched-veto field from the exact frozen historical sample.
    for c in ['opp_last_rank_def_allowed_giveaway_rate','last_rank_points_against','last_pre_def_allowed_rush_epa_per_carry',
              'opp_last_pre_rush_epa_per_carry','last_pre_pass_epa_per_dropback']:
        if c in d: out[c]=num(d[c])
    return out

def add_preseason(d):
    p=pd.read_csv(PRE,low_memory=False); p['team']=p.team.replace(ALIASES); p['season']=num(p.season).astype('Int64')
    seasons=set(p.season.dropna().astype(int))
    keep=[c for c in ['season','team','pre_games','pre_wins','pre_losses','pre_win_pct','pre_margin_pg'] if c in p]
    x=d.merge(p[keep],on=['season','team'],how='left')
    x['league_had_preseason']=x.season.isin(seasons)
    x['preseason_pass']=(num(x.pre_wins)>=2) | (~x.league_had_preseason)
    x['preseason_missing_fail_closed']=x.league_had_preseason & num(x.pre_wins).isna()
    return x

def hist_filter_stats(base,mask,name):
    x=base[mask.fillna(False)].copy()
    return {'name':name,**era_metrics(x)}

def beta_predictive_zero_two(w,l):
    # Uniform beta prior; posterior predictive probability next two are both losses.
    a=1+w; b=1+l
    return float((b/(a+b))*((b+1)/(a+b+1)))

def live_picks():
    found=[]
    if STATE.exists():
        try:
            d=json.loads(STATE.read_text())
            for c in d.get('cards',[]):
                start=str(c.get('start',''))
                if '2026-09-20' not in start: continue
                found.append({
                    'source':'state','id':c.get('id'),'selection':c.get('selection'),'opponent':c.get('opponent'),
                    'fixture':c.get('fixture'),'start':start,'price':c.get('price'),'book':c.get('book'),
                    'methods':c.get('methods'),'result':c.get('result'),'profit_units':c.get('profit_units'),
                    'withdrawn':c.get('withdrawn',False)
                })
        except Exception as e: found.append({'source':'state_error','error':str(e)})
    if LEDGER.exists():
        try:
            db=sqlite3.connect(LEDGER);db.row_factory=sqlite3.Row
            cols=[x[1] for x in db.execute('pragma table_info(bets)')]
            rows=db.execute("select * from bets where upper(sport)='NFL'").fetchall()
            for r in rows:
                z=dict(r); start=str(z.get('start',''))
                if '2026-09-20' not in start: continue
                found.append({'source':'ledger',**{k:z.get(k) for k in cols}})
        except Exception as e: found.append({'source':'ledger_error','error':str(e)})
    # Dedup only exact representations; preserve both state and ledger.
    return found

def live_method_summary(picks):
    # Extract likely official wagers and method IDs.
    rows=[]
    for p in picks:
        if p.get('source') not in {'state','ledger'}: continue
        sel=str(p.get('selection') or '')
        if not sel: continue
        methods=p.get('methods')
        if isinstance(methods,str):
            try: methods=json.loads(methods)
            except Exception: methods=[methods]
        rows.append({'source':p.get('source'),'selection':sel,'opponent':p.get('opponent'),'price':p.get('price'),
                     'methods':methods,'result':p.get('result'),'id':p.get('id') or p.get('key')})
    return rows

def current_archived_context():
    p=REPO/'nfl'/'weekly_archive'/'2026'/'week_02'/'pregame'/'current_board_snapshot.csv'
    if not p.exists(): return []
    d=pd.read_csv(p,low_memory=False)
    return d[d.team.isin(['BAL','TB'])].replace({np.nan:None}).to_dict('records')

def add_coach_context(base):
    if not CTX.exists(): return base
    try:
        c=pd.read_parquet(CTX)
    except Exception:
        return base
    cols=['game_id','team']
    wanted=['hc_changed_season','oc_changed_season','dc_changed_season','staff_change_count','full_staff_overhaul',
            'coachq_staff_quality_mean','coachq_staff_quality_min','returning_offense_snap_share','returning_defense_snap_share',
            'returning_ol_snap_share','returning_skill_snap_share']
    cols += [x for x in wanted if x in c.columns]
    if len(cols)<=2:return base
    c=c.drop_duplicates(['game_id','team'])
    return base.merge(c[cols],on=['game_id','team'],how='left')

def condition_audit(x,conditions):
    rows=[]
    for name,mask in conditions.items():
        for era,(a,b) in {'development':(2007,2013),'validation':(2014,2019),'holdout':(2020,2025)}.items():
            q=x[num(x.season).between(a,b)]; f=mask.loc[q.index].fillna(False)
            risk=metric(q[f]); safe=metric(q[~f])
            rows.append({'condition':name,'era':era,'risk':risk,'safe':safe,
                         'roi_gap_safe_minus_risk':(safe['roi']-risk['roi']) if pd.notna(safe['roi']) and pd.notna(risk['roi']) else None})
    # Stable only if direction repeats validation + holdout and sizes are nontrivial.
    stable=[]
    for name in conditions:
        rr={r['era']:r for r in rows if r['condition']==name}
        if not all(k in rr for k in ['validation','holdout']):continue
        va,ho=rr['validation'],rr['holdout']
        if va['risk']['n']>=3 and ho['risk']['n']>=3 and va['safe']['n']>=5 and ho['safe']['n']>=5 and (va['roi_gap_safe_minus_risk'] or -9)>0 and (ho['roi_gap_safe_minus_risk'] or -9)>0:
            stable.append({'condition':name,'validation':va,'holdout':ho})
    return rows,stable

def main():
    orig=add_preseason(load_exact(ORIG,'original'))
    strict=add_preseason(load_exact(STRICT,'stricter'))
    orig=add_coach_context(orig); strict=add_coach_context(strict)

    # Canonical historical slices.
    orig_pre=orig[orig.preseason_pass]
    strict_pre=strict[strict.preseason_pass]
    # M1-only = original signals that do not satisfy the >12.5pp M2 edge.
    m1_only=orig_pre[num(orig_pre.record_gap).le(.125)]
    m2=strict_pre

    # Confirmed enriched veto on M1/original from frozen pre-2020 selection.
    enriched_col='opp_last_rank_def_allowed_giveaway_rate'
    if enriched_col in orig_pre:
        orig_pre_enriched=orig_pre[~num(orig_pre[enriched_col]).le(14.8)]
        # fail closed: missing veto feature is not actionable.
        orig_pre_enriched_fc=orig_pre[num(orig_pre[enriched_col]).notna() & ~num(orig_pre[enriched_col]).le(14.8)]
        m1_only_enriched_fc=m1_only[num(m1_only[enriched_col]).notna() & ~num(m1_only[enriched_col]).le(14.8)]
    else:
        orig_pre_enriched=orig_pre.iloc[0:0]; orig_pre_enriched_fc=orig_pre.iloc[0:0]; m1_only_enriched_fc=m1_only.iloc[0:0]

    variants={
      'M1_original_baseline':orig,
      'M1_original_preseason_fail_closed':orig_pre,
      'M1_original_preseason_plus_enriched_veto':orig_pre_enriched_fc,
      'M1_only_preseason':m1_only,
      'M1_only_preseason_plus_enriched_veto':m1_only_enriched_fc,
      'M2_stricter_baseline':strict,
      'M2_stricter_preseason_fail_closed':strict_pre,
    }

    results={k:era_metrics(v) for k,v in variants.items()}
    # Add 2026 observed losses to relevant historical records after live-method mapping below.
    picks=live_picks(); exact=live_method_summary(picks)
    archived=current_archived_context()

    # Outcome assignment from observed final results.
    observed=[]
    for r in exact:
        if str(r.get('result')).lower()!='loss':continue
        s=str(r.get('selection') or '').lower()
        if 'raven' in s or s=='bal' or 'baltimore' in s:
            observed.append({'selection':'BAL','result':'loss','methods':r.get('methods'),'price':r.get('price')})
        elif 'bucc' in s or s=='tb' or 'tampa' in s:
            observed.append({'selection':'TB','result':'loss','methods':r.get('methods'),'price':r.get('price')})
    # Fallback based on current known official user report if source/result is encoded differently.
    if not any(x['selection']=='BAL' for x in observed): observed.append({'selection':'BAL','result':'loss','methods':None,'price':None,'source':'user_confirmed_card'})
    if not any(x['selection']=='TB' for x in observed): observed.append({'selection':'TB','result':'loss','methods':None,'price':None,'source':'user_confirmed_card'})

    # Historical + prospective record math.
    prospective={}
    # BAL is M1/original; TB method attribution comes from state/ledger if available.
    for key in ['M1_original_preseason_fail_closed','M1_only_preseason','M1_original_preseason_plus_enriched_veto','M1_only_preseason_plus_enriched_veto','M2_stricter_preseason_fail_closed']:
        hist=results[key]['full']; add=0
        if key.startswith('M1_'): add += 1 # Baltimore
        # Tampa belongs to M2 if live source says M2/stricter, otherwise M1 at minimum.
        tb_methods=[]
        for r in exact:
            if 'bucc' in str(r.get('selection','')).lower() or 'tampa' in str(r.get('selection','')).lower() or str(r.get('selection','')).upper()=='TB':
                tb_methods += list(r.get('methods') or [])
        tb_m2=any(str(x).upper() in {'M2','STRICTER'} for x in tb_methods)
        if key.startswith('M1_'): add += 1
        if key.startswith('M2_') and tb_m2: add += 1
        prospective[key]={'historical_wins':hist['wins'],'historical_losses':hist['losses'],'added_2026_losses':add,
                          'updated_win_pct':hist['wins']/(hist['n']+add) if hist['n']+add else None,
                          'updated_record':f"{hist['wins']}-{hist['losses']+add}",
                          'posterior_predictive_two_losses_before_2026':beta_predictive_zero_two(hist['wins'],hist['losses'])}

    # Pre-specified context audits; no threshold mining on today's outcomes.
    context={}
    for label,x in [('M1_original_preseason',orig_pre),('M1_only_preseason',m1_only),('M2_stricter_preseason',strict_pre)]:
        cond={}
        if 'staff_change_count' in x:
            cond['2plus_staff_changes']=num(x.staff_change_count).ge(2)
        if 'full_staff_overhaul' in x:
            cond['full_staff_overhaul']=num(x.full_staff_overhaul).eq(1)
        if 'returning_offense_snap_share' in x:
            cond['low_offense_continuity_lt65']=num(x.returning_offense_snap_share).lt(.65)
        if 'returning_defense_snap_share' in x:
            cond['low_defense_continuity_lt65']=num(x.returning_defense_snap_share).lt(.65)
        if cond:
            rows,stable=condition_audit(x,cond);context[label]={'tests':rows,'stable':stable}
        else:context[label]={'tests':[],'stable':[]}

    # Candidate decision table.
    cand=[]
    for k,v in results.items():
        f=v['full'];ho=v['holdout'];rec=v['recent3']
        cand.append({
          'variant':k,'n':f['n'],'wins':f['wins'],'losses':f['losses'],'win_pct':f['win_pct'],'roi':f['roi'],
          'holdout_n':ho['n'],'holdout_win_pct':ho['win_pct'],'holdout_roi':ho['roi'],
          'recent3_n':rec['n'],'recent3_win_pct':rec['win_pct'],'recent3_roi':rec['roi'],
          'positive_season_ratio':v['positive_season_ratio']
        })
    pd.DataFrame(cand).to_csv(OUT/'candidate_refinements.csv',index=False)

    # Production flaw determination.
    production_findings=[
      {
        'finding':'ENRICHED_VETO_NOT_ENFORCED',
        'severity':'CRITICAL',
        'detail':'The frozen research confirmed an enriched veto for original/M1, but nfl_public_page._audit_action did not inspect enriched_veto_status/rule. Baltimore archived with enriched_veto_status=UNKNOWN and remained actionable.'
      },
      {
        'finding':'DEEP_AUDIT_FAILS_OPEN',
        'severity':'CRITICAL',
        'detail':'Missing audit row returns Scanner qualified/monitor and remains actionable instead of failing closed.'
      },
      {
        'finding':'NEW_H_SERIES_NOT_RESPONSIBLE',
        'severity':'INFO',
        'detail':'H-series is shadow-only and Week4+/Week7+ gated; Week2 losses came from legacy M-family.'
      }
    ]

    # Decision logic: prefer already independently validated variants; do not invent new post-loss rule.
    m1fc=results['M1_original_preseason_plus_enriched_veto']['holdout']
    m2fc=results['M2_stricter_preseason_fail_closed']['holdout']
    m1only=results['M1_only_preseason_plus_enriched_veto']['holdout']
    recommendations=[]
    if m1fc['n']>=8 and m1fc['roi']>0:
        recommendations.append('Keep M1 family only with the already-confirmed enriched veto AND fail-closed feature availability; do not allow raw scanner-only M1.')
    else:
        recommendations.append('Demote M1 to shadow: enriched fail-closed variant lacks sufficient holdout support.')
    if m2fc['n']>=8 and m2fc['roi']>0:
        recommendations.append('Keep M2 under the validated preseason>=2 fail-closed gate; continue prospective monitoring rather than deleting after one loss.')
    else:
        recommendations.append('Demote M2 to shadow due insufficient/negative holdout after preseason gate.')
    if m1only['n'] and m1only['roi']<=0:
        recommendations.append('Retire M1-only signals; require M2 overlap or another independently validated gate.')
    recommendations.append('Change production deep-audit behavior from fail-open to fail-closed: missing required audit/veto features => NO BET.')

    summary={
      'built_at':pd.Timestamp.now('UTC').isoformat(),'live_picks':exact,'observed_losses':observed,
      'archived_BAL_TB_context':archived,'historical_variants':results,'prospective_record_update':prospective,
      'context_stability_tests':context,'production_findings':production_findings,'recommendations':recommendations
    }
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2,default=str))
    (OUT/'exact_live_picks.json').write_text(json.dumps({'raw':picks,'normalized':exact},indent=2,default=str))

    lines=['NFL WEEK 2 2026 LOSS POSTMORTEM','='*96,'',
           'Scope: exact live/ledger selections, legacy M-family historical samples, frozen preseason gate,',
           'confirmed preholdout enriched veto, and pre-specified coaching/continuity checks. No weather.','',
           'EXACT LIVE PICKS']
    for r in exact:
        lines.append(f"{r.get('selection')} vs {r.get('opponent')} | {r.get('price')} | methods={r.get('methods')} | result={r.get('result')} | source={r.get('source')}")
    lines += ['','HISTORICAL VARIANTS']
    for r in cand:
        lines.append(f"{r['variant']}: {r['wins']}-{r['losses']} n={r['n']} win={r['win_pct']:.1%} ROI={r['roi']:+.1%} | hold n={r['holdout_n']} win={r['holdout_win_pct']:.1%} ROI={r['holdout_roi']:+.1%} | 2023-25 n={r['recent3_n']} ROI={r['recent3_roi']:+.1%}")
    lines += ['','PRODUCTION FINDINGS']
    for r in production_findings: lines.append(f"{r['severity']} {r['finding']}: {r['detail']}")
    lines += ['','PRE-SPECIFIED CONTEXT STABILITY']
    for label,z in context.items():
        lines.append(label+':')
        if z['stable']:
            for q in z['stable']:
                lines.append(f"  STABLE {q['condition']} | validation risk ROI={q['validation']['risk']['roi']:+.1%} safe={q['validation']['safe']['roi']:+.1%} | hold risk={q['holdout']['risk']['roi']:+.1%} safe={q['holdout']['safe']['roi']:+.1%}")
        else: lines.append('  no coaching/continuity condition passed the repeated validation+holdout stability screen')
    lines += ['','RECOMMENDATIONS']
    lines += [f"- {x}" for x in recommendations]
    (OUT/'report.txt').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))

if __name__=='__main__': main()
