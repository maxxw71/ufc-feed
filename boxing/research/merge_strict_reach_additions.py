#!/usr/bin/env python3
"""Merge strictly corroborated reach-addition files into verified profiles.

Only whitelisted addition files produced by strict two-source workflows are
eligible. Existing reach is never overwritten. If multiple new sources for one
fighter disagree by more than 1 cm, that fighter is quarantined rather than
merged.
"""
from __future__ import annotations
import datetime as dt,json,statistics
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SUP=ROOT/'profile_supplements'/'verified_profiles.jsonl'
REPORT=ROOT/'profile_supplements'/'strict_reach_merge_report.json'
FILES=[
 ROOT/'profile_supplements'/'cross_source_reach_additions.jsonl',
 ROOT/'profile_supplements'/'third_source_reach_additions.jsonl',
 ROOT/'profile_supplements'/'boxingscene_wayback_reach_additions.jsonl',
 ROOT/'profile_supplements'/'fitequant_reach_additions.jsonl',
 ROOT/'profile_supplements'/'boxrec_wayback_reach_additions.jsonl',
 ROOT/'profile_supplements'/'boxrec_wiki_reach_additions.jsonl',
 ROOT/'profile_supplements'/'ready_to_fight_reach_additions.jsonl',
 ROOT/'profile_supplements'/'toprank_reach_additions.jsonl',
 ROOT/'profile_supplements'/'pbc_reach_additions.jsonl',
 ROOT/'profile_supplements'/'queensberry_reach_additions.jsonl',
 ROOT/'profile_supplements'/'sofascore_reach_additions.jsonl',
 ROOT/'profile_supplements'/'foxsports_reach_additions.jsonl',
]

def readjsonl(path):
    out=[]
    if not path.exists():return out
    for line in path.read_text().splitlines():
        if not line.strip():continue
        try:out.append(json.loads(line))
        except Exception:pass
    return out

def main():
    existing={}
    for x in readjsonl(SUP):
        sid=x.get('target_source_id')
        if sid:existing[sid]=x

    groups=defaultdict(list)
    source_counts={}
    for path in FILES:
        rows=readjsonl(path);source_counts[path.name]=len(rows)
        for x in rows:
            sid=x.get('target_source_id');reach=(x.get('fields') or {}).get('reach_cm')
            try:r=float(reach)
            except Exception:continue
            if sid and 120<=r<=270:groups[sid].append((path.name,x,r))

    merged=[];skipped_existing=[];conflicts=[]
    for sid,items in groups.items():
        old=existing.get(sid)
        oldreach=((old or {}).get('fields') or {}).get('reach_cm')
        if oldreach not in (None,''):
            skipped_existing.append({'target_source_id':sid,'name':(old or {}).get('name'),'reach_cm':oldreach})
            continue
        vals=[r for _,_,r in items]
        if max(vals)-min(vals)>1:
            conflicts.append({'target_source_id':sid,'name':items[0][1].get('name'),
                              'candidate_reaches':vals,'sources':[a for a,_,_ in items]})
            continue
        reach=round(statistics.median(vals),2)
        if float(reach).is_integer():reach=int(reach)
        strongest=items[0][1]
        if old is None:
            old={'target_source_id':sid,'name':strongest.get('name'),'career_source':strongest.get('career_source'),
                 'fields':{},'evidence':[],'conflicts':{},'quality':''}
        fields=dict(old.get('fields') or {});fields['reach_cm']=reach;old['fields']=fields
        evidence=list(old.get('evidence') or [])
        for fname,x,r in items:
            evidence.extend(x.get('evidence') or [])
        old['evidence']=evidence
        q=str(old.get('quality') or '')
        marker='strict_multi_source_reach_consensus'
        if marker not in q:q=(q+';'+marker).strip(';')
        old['quality']=q;old['collected_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        existing[sid]=old
        merged.append({'target_source_id':sid,'name':old.get('name'),'reach_cm':reach,
                       'addition_files':[a for a,_,_ in items]})

    SUP.parent.mkdir(parents=True,exist_ok=True)
    with SUP.open('w') as f:
        for x in sorted(existing.values(),key=lambda z:(z.get('name',''),z.get('target_source_id',''))):
            f.write(json.dumps(x,ensure_ascii=False)+'\n')
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'source_counts':source_counts,
            'candidate_fighters':len(groups),'merged':len(merged),'merged_profiles':merged,
            'skipped_existing_reach':len(skipped_existing),'conflicts_quarantined':len(conflicts),
            'conflicts':conflicts,
            'policy':'Whitelisted strict corroboration files only; never overwrite existing reach; candidate sources must agree within 1 cm or fighter is quarantined.'}
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('source_counts','candidate_fighters','merged','skipped_existing_reach','conflicts_quarantined')},indent=2))

if __name__=='__main__':main()
