#!/usr/bin/env python3
"""Inventory predictive boxing data coverage without changing research eligibility.

Designed for the public research database or the server copy. Reports what can
be added safely from existing historical rows before reaching for new sources.
"""
from __future__ import annotations
import collections,json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DB=ROOT/'boxing.sqlite3'

def pct(n,d): return round(100*n/d,2) if d else None

def main():
    con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
    out={
      '_meta':{
        'scope':'broad/raw public database inventory, not the strict priced research universe',
        'authoritative_for_current_strict_research_coverage':False,
        'current_strict_coverage_report':'boxing/public_phase2/CURRENT_DATASET_STATUS.json',
        'warning':'Do not compare these broad normalized-fighter percentages directly with strict Phase 2 profile coverage.'
      }
    }
    bouts=[dict(r) for r in con.execute("select * from bouts where source='wikipedia' and status='FINISHED' and date>='1990-01-01'")]
    out['wikipedia_finished_1990_plus']=len(bouts)
    keys=collections.Counter();values=collections.defaultdict(collections.Counter)
    for r in bouts:
        try:d=json.loads(r.get('data') or '{}')
        except Exception:d={}
        for k,v in d.items():
            k=str(k).strip().casefold(); keys[k]+=1
            if v not in (None,'') and len(str(v))<120: values[k][str(v).strip()]+=1
    out['record_table_keys']={k:{'rows':n,'coverage_pct':pct(n,len(bouts)),'examples':[x for x,c in values[k].most_common(8)]} for k,n in keys.most_common()}
    out['bout_columns']={k:{'nonempty':sum(r.get(k) not in (None,'') for r in bouts),'coverage_pct':pct(sum(r.get(k) not in (None,'') for r in bouts),len(bouts))} for k in ['method','rounds','scheduled_rounds','division','venue','url']}
    prof=[dict(r) for r in con.execute("select * from normalized_fighters")]
    out['normalized_fighters']=len(prof)
    out['profile_coverage']={k:{'nonempty':sum(r.get(k) not in (None,'') for r in prof),'coverage_pct':pct(sum(r.get(k) not in (None,'') for r in prof),len(prof))} for k in ['born','height_cm','reach_cm','stance','nationality','weight_class_snapshot']}
    # Existing pre-bout research layer coverage.
    if con.execute("select 1 from sqlite_master where type='table' and name='enriched_pre_bout'").fetchone():
        rows=[json.loads(r['features_json']) for r in con.execute('select features_json from enriched_pre_bout')]
        checks={
          'fighter_5_prior':lambda x:(x.get('fighter') or {}).get('observed_prior_bouts',0)>=5,
          'opponent_history':lambda x:x.get('opponent') is not None,
          'opponent_5_prior':lambda x:(x.get('opponent') or {}).get('observed_prior_bouts',0)>=5,
          'opponent_strength':lambda x:(x.get('fighter') or {}).get('opponent_strength_known_bouts',0)>0,
          'round_history':lambda x:(x.get('fighter') or {}).get('career_rounds_known_bouts',0)>0,
        }
        out['enriched_rows']=len(rows)
        out['enriched_coverage']={k:{'rows':sum(fn(x) for x in rows),'coverage_pct':pct(sum(fn(x) for x in rows),len(rows))} for k,fn in checks.items()}
    # Price/research coverage.
    if con.execute("select 1 from sqlite_master where type='table' and name='priced_bout_research'").fetchone():
        q=[dict(r) for r in con.execute("select * from priced_bout_research where result in ('WIN','LOSS')")]
        out['decisive_price_rows']=len(q)
        out['distinct_price_bouts']=len({r['odds_bout_id'] for r in q})
        out['bookmakers']=collections.Counter(r['bookmaker'] for r in q).most_common(30)
    out['recommended_existing_data_additions']=[]
    rk=out['record_table_keys']
    for candidate in ['location','notes','title','titles','weight','weight class','type','record']:
        if candidate in rk and rk[candidate]['coverage_pct'] and rk[candidate]['coverage_pct']>=10:
            out['recommended_existing_data_additions'].append(candidate)
    Path('DATA_GAP_AUDIT.json').write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
