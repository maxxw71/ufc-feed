from pathlib import Path
import json, os
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
OUT=Path('/home/appwiza-runner/nfl-context-data/live_staging')
OUT.mkdir(parents=True,exist_ok=True)
LIVE=REPO/'nfl/live_candidate_finalization/live_ready.csv'
TIMING=REPO/'nfl/season_timing_audit/live_ready_timing.csv'

W4={'NFL-H002','NFL-H003','NFL-H008','NFL-H027','NFL-H029','NFL-H017','NFL-H036','NFL-H033'}
W7={'NFL-H005','NFL-H010','NFL-H028','NFL-H035'}
EXPECTED=W4|W7

live=pd.read_csv(LIVE)
timing=pd.read_csv(TIMING)[['candidate_id','recommended_start_week','timing_decision','min_week','max_week']]
df=live[live.candidate_id.isin(EXPECTED)].merge(timing,on='candidate_id',how='left',validate='one_to_one')
if set(df.candidate_id)!=EXPECTED:
    raise SystemExit(f'Missing staged methods: {sorted(EXPECTED-set(df.candidate_id))}')
for _,r in df.iterrows():
    expected_start=4 if r.candidate_id in W4 else 7
    if int(r.recommended_start_week)!=expected_start:
        raise SystemExit(f'{r.candidate_id}: timing changed to W{r.recommended_start_week}; refuse stale stage')
    if r.deployment_status!='LIVE_READY':
        raise SystemExit(f'{r.candidate_id}: no longer LIVE_READY')

methods=[]
for _,r in df.sort_values(['recommended_start_week','candidate_id']).iterrows():
    rules=[]
    for j in (1,2,3):
        c=r.get(f'feature{j}')
        if pd.notna(c) and str(c):
            rules.append({'feature':str(c),'op':str(r.get(f'op{j}')),'threshold':float(r.get(f'threshold{j}'))})
    veto=None
    if pd.notna(r.get('hard_veto_feature')) and str(r.get('hard_veto_feature')):
        veto={'source':str(r.get('hard_veto_source') or ''),'feature':str(r.hard_veto_feature),'op':str(r.hard_veto_op),'threshold':float(r.hard_veto_threshold)}
    methods.append({
        'method_id':r.candidate_id,
        'stage_status':'SHADOW_ELIGIBLE' if int(r.recommended_start_week)==4 else 'LOCKED_UNTIL_WEEK_7',
        'start_week':int(r.recommended_start_week),
        'min_prior_completed_games':3,
        'season_type':'REG',
        'venue':str(r.venue),
        'price_band':str(r.price_band),
        'live_probability_min':float(r.live_p_lo),
        'live_probability_max':float(r.live_p_hi),
        'live_odds_range':str(r.live_odds_range),
        'price_decision':str(r.price_decision),
        'rules':rules,
        'hard_veto':veto,
        'historical':{
            'full_n':int(r.full_n),'full_roi':float(r.full_roi),
            'holdout_n':int(r.holdout_n),'holdout_roi':float(r.holdout_roi),
            'positive_season_ratio':float(r.positive_season_ratio),
            'historical_min_week':int(r.min_week),'historical_max_week':int(r.max_week),
            'timing_decision':str(r.timing_decision),
        }
    })

manifest={
    'schema_version':1,
    'season':2026,
    'mode':'SHADOW_ONLY',
    'public_website_enabled':False,
    'email_enabled':False,
    'bet_tracker_enabled':False,
    'global_guards':{
        'season_type':'REG',
        'selected_team_prior_completed_games_min':3,
        'require_current_actionable_moneyline':True,
        'require_method_price_gate':True,
        'require_all_method_rules':True,
        'apply_one_validated_hard_veto_max':True,
        'fail_closed_on_missing_feature':True,
    },
    'week4_methods':sorted(W4),
    'week7_methods':sorted(W7),
    'methods':methods,
}
(OUT/'approved_h_methods.json').write_text(json.dumps(manifest,indent=2)+'\n')
report=[
    'NFL H-METHOD LIVE STAGE',
    'mode=SHADOW_ONLY',
    f'week4_methods={len(W4)}',
    f'week7_methods={len(W7)}',
    'public_website_enabled=false',
    'email_enabled=false',
    'bet_tracker_enabled=false',
    'global_rule=REG only + selected team prior_games>=3 + exact price gate + fail closed',
    '',
]
for m in methods:
    report.append(f"{m['method_id']} | start W{m['start_week']} | {m['venue']} | {m['live_odds_range']} | {m['stage_status']}" + (f" | veto {m['hard_veto']['feature']} {m['hard_veto']['op']} {m['hard_veto']['threshold']}" if m['hard_veto'] else ''))
(OUT/'report.txt').write_text('\n'.join(report)+'\n')
print('\n'.join(report))
