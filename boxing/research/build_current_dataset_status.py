#!/usr/bin/env python3
"""Build the authoritative current boxing dataset coverage snapshot.

This report intentionally summarizes the latest strict/research-relevant outputs.
The older public_reports/DATA_GAP_AUDIT.json has a different broad/raw scope and
must not be used as the current strict research coverage source of truth.
"""
from __future__ import annotations
import datetime as dt,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PHASE2=ROOT/'public_phase2'
RANK=ROOT/'rankings'
PROFILE=ROOT/'profile_supplements'
PUNCH=ROOT/'punch_supplements'
ODDS=ROOT/'prospective_odds'
OUT=PHASE2/'CURRENT_DATASET_STATUS.json'

def load(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except Exception:return {}

def pct(n,d):
    return round(100*n/d,2) if d else None

def ranking(org):
    x=load(RANK/f'{org.lower()}_monthly_rankings_meta.json')
    docs=x.get('documents') or []
    dates=[]
    for d in docs:
        for key in ('safe_effective_date','published_date','post_date','posting_date','date'):
            v=str(d.get(key) or '')
            if len(v)>=10 and v[:4].isdigit():
                dates.append(v[:10]);break
    years=x.get('years') or sorted({int(d.get('year')) for d in docs if str(d.get('year') or '').isdigit()})
    if not years and dates:
        years=sorted({int(v[:4]) for v in dates if v[:4].isdigit()})
    return {
      'ranking_rows':x.get('ranking_rows'),
      'champion_rows':x.get('champion_rows'),
      'parsed_documents':x.get('parsed_documents'),
      'usable_documents':x.get('usable_documents'),
      'rating_periods':x.get('rating_periods'),
      'years':years,
      'date_min':x.get('date_min') or (min(dates) if dates else None),
      'date_max':x.get('date_max') or (max(dates) if dates else None),
      'policy':x.get('policy') or x.get('safe_date_policy')
    }

gap=load(PHASE2/'PROFILE_GAP_AUDIT.json')
cov=load(PHASE2/'coverage.json')
punch=load(PHASE2/'punch_profile_coverage.json')
summary=load(PUNCH/'boxingscene_compubox_summary_report.json')
archived_summary=load(PUNCH/'archived_compubox_summary_report.json')
reach=load(PROFILE/'cross_source_reach_report.json')
pros=load(ODDS/'coverage.json')
settle=load(ODDS/'settled_bouts.json')
db=load(PHASE2/'database_status.json')

fighters=int(gap.get('unique_fighters') or 0)
missing=gap.get('missing_fighter_counts') or {}
profile_cov={}
for field in ('born','height_cm','reach_cm','stance','nationality'):
    miss=int(missing.get(field) or 0)
    profile_cov[field]={
      'fighters_present':fighters-miss if fighters else None,
      'fighters_total':fighters or None,
      'fighter_coverage_pct':pct(fighters-miss,fighters) if fighters else None,
      'missing_fighters':miss,
      'priced_bout_both_sides':(gap.get('bout_coverage') or {}).get(field)
    }

out={
  'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
  'authoritative_for_current_strict_research_coverage':True,
  'scope':'strict/research-relevant boxing dataset and current prospective price collection',
  'do_not_use_as_current_strict_coverage':'boxing/public_reports/DATA_GAP_AUDIT.json',
  'phase2_build_at':cov.get('built_at'),
  'database':{
    'status':db.get('status'),
    'tables':db.get('tables'),
    'source_rows_present':'source_rows' in set(db.get('tables') or []),
    'source_rows_required_for_phase2':False,
    'source_rows_note':'Compact public research DB may omit source_rows; Phase 2 retains supplemental quote links and skips only raw-source price rematching.'
  },
  'research_universe':{
    'fighter_bout_rows':cov.get('rows'),
    'strict_profile_fighters':fighters or None,
    'consensus_priced_bouts':((load(PHASE2/'phase2_interaction_summary.json').get('coverage') or {}).get('canonical_consensus_bouts')),
    'verified_graph_bouts':cov.get('verified_graph_bouts'),
    'identity_links':cov.get('identity_links'),
    'career_sources':cov.get('career_sources'),
    'years':sorted((cov.get('years') or {}).keys())
  },
  'fighter_profiles':profile_cov,
  'rankings':{org:ranking(org) for org in ('WBC','WBA','WBO','IBF')},
  'punch_data':{
    'full_round_reports':punch.get('unique_report_ids'),
    'full_round_fighter_observations':punch.get('fighter_fight_observations'),
    'fighters_with_any_prior_full_punch_fight':punch.get('fighters_with_any_prior_punch_fight'),
    'snapshots_with_3plus_prior_full_punch_fights':punch.get('snapshots_with_3plus_prior_punch_fights'),
    'summary_articles_discovered':summary.get('articles_discovered'),
    'summary_rows':summary.get('summary_rows'),
    'summary_distinct_bouts':summary.get('distinct_bouts'),
    'prefight_baseline_rows':summary.get('prefight_historical_baseline_rows'),
    'prefight_baseline_bouts':summary.get('prefight_historical_baseline_bouts'),
    'explicit_full_stat_targets':summary.get('explicit_full_stat_target_count'),
    'archived_compubox_summary_rows':archived_summary.get('merged_fighter_rows'),
    'archived_compubox_summary_bouts':archived_summary.get('distinct_bouts'),
    'archived_compubox_summary_date_min':archived_summary.get('date_min'),
    'archived_compubox_summary_date_max':archived_summary.get('date_max'),
    'latest_cross_source_reach_backfill':{
      'generated_at':reach.get('generated_at'),'targets':reach.get('targets'),
      'accepted':reach.get('accepted'),'policy':reach.get('policy')
    }
  },
  'historical_pricing':{
    'validated_price_rows':cov.get('validated_price_rows'),
    'status':'exploratory_only_until_archived_quote timing/settlement is independently verified'
  },
  'prospective_pricing':{
    'generated_at':pros.get('generated_at'),
    'raw_quote_observations':pros.get('raw_quote_observations'),
    'verified_pre_event_quote_observations':pros.get('verified_pre_event_quote_observations'),
    'verified_pre_event_sportsbook_quote_observations':pros.get('verified_pre_event_sportsbook_quote_observations'),
    'verification_pct':pros.get('verification_pct'),
    'unique_verified_bouts':pros.get('unique_verified_bouts'),
    'unique_verified_sportsbook_bouts':pros.get('unique_verified_sportsbook_bouts'),
    'settled_verified_bouts':pros.get('settled_verified_bouts'),
    'past_unresolved_bouts':pros.get('past_unresolved_bouts'),
    'future_or_today_bouts':pros.get('future_or_today_bouts'),
    'settlement_generated_at':settle.get('generated_at')
  },
  'largest_remaining_gaps':[
    'verified reach coverage',
    'historical full-fight/round-level punch depth',
    'independently validated historical archived quote timing and settlement',
    'older WBC official ranking depth'
  ]
}
PHASE2.mkdir(parents=True,exist_ok=True)
OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(out,indent=2,ensure_ascii=False))
