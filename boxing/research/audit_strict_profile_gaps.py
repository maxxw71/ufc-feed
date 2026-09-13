#!/usr/bin/env python3
"""Rank missing profile fields by impact on the strict priced boxing sample.

This is an audit only. It does not impute or backfill values. Fighters are ranked
by appearances in canonical consensus-priced bouts so future collection work
focuses on fields that can actually change model coverage.
"""
from __future__ import annotations
import collections,json
from pathlib import Path
from market_consensus import load_master,canonical_market_rows

ROOT=Path(__file__).resolve().parent
MASTER_POINTER=ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt'
OUT=ROOT/'PROFILE_GAP_AUDIT.json'

FIELDS={
    'born':'age_from_biography',
    'height_cm':'height_cm_static_proxy',
    'reach_cm':'reach_cm_static_proxy',
    'stance':'stance_static_proxy',
    'nationality':'nationality_static_proxy',
}

def main():
    run=Path(MASTER_POINTER.read_text().strip())
    master=load_master(run/'boxing_prefight_master.jsonl')
    strict=canonical_market_rows(master,min_books=2)
    appearances=collections.Counter(); names={}; values=collections.defaultdict(dict); sources={}
    both={k:0 for k in FIELDS}
    any_field={k:0 for k in FIELDS}
    for bout in strict:
        sides=[bout['favorite_row'],bout['opponent_row']]
        complete={k:True for k in FIELDS}
        for row in sides:
            side=row.get('fighter') or {}
            fid=side.get('id'); name=row.get('fighter_name')
            if not fid: continue
            appearances[fid]+=1; names[fid]=name or fid
            sources[fid]={'career_source':row.get('career_source'),'id':fid}
            for raw,field in FIELDS.items():
                v=side.get(field)
                if v not in (None,''):
                    values[fid][raw]=v
                else:
                    complete[raw]=False
        for raw in FIELDS:
            if complete[raw]: both[raw]+=1
            if any((r.get('fighter') or {}).get(FIELDS[raw]) not in (None,'') for r in sides): any_field[raw]+=1
    fighters=[]
    for fid,n in appearances.items():
        missing=[k for k in FIELDS if values[fid].get(k) in (None,'')]
        fighters.append({'id':fid,'name':names.get(fid,fid),'strict_bout_appearances':n,
                         'missing':missing,'present':values[fid],**sources.get(fid,{})})
    fighters.sort(key=lambda x:(-x['strict_bout_appearances'],-len(x['missing']),x['name']))
    missing_ranked={field:[{'id':x['id'],'name':x['name'],'strict_bout_appearances':x['strict_bout_appearances'],
                            'career_source':x.get('career_source')}
                           for x in fighters if field in x['missing']]
                    for field in FIELDS}
    report={'strict_consensus_bouts':len(strict),'unique_fighters':len(fighters),
            'bout_coverage':{k:{'both_sides':both[k],'both_sides_pct':round(100*both[k]/len(strict),2) if strict else None,
                               'at_least_one_side':any_field[k]} for k in FIELDS},
            'missing_fighter_counts':{k:sum(k in x['missing'] for x in fighters) for k in FIELDS},
            'missing_ranked':missing_ranked,
            'fighters':fighters,
            'policy':'No imputation. Ranked by strict consensus-priced bout appearances; static physical values remain proxy fields.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False));print(json.dumps({k:v for k,v in report.items() if k not in ('fighters','missing_ranked')},indent=2))
if __name__=='__main__':main()
