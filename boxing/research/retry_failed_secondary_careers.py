#!/usr/bin/env python3
"""Retry only failed/incomplete verified Champinon career supplements.

Intended for a network path different from GitHub-hosted runners. Acceptance
rules are identical to the normal strict refresh: exact identity, complete
career table, stated-record reconciliation, and full re-match of the already
stored priced evidence. Existing complete rows are never touched.
"""
from __future__ import annotations
import argparse,datetime as dt,json,os
from pathlib import Path
from backfill_priced_careers import OUT,match_evidence
from backfill_secondary_careers import parse_champinon

REPORT=OUT.parent/'selfhosted_retry_report.json'

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--limit',type=int,default=50)
    args=ap.parse_args()
    if not OUT.exists():
        raise SystemExit(f'missing supplements: {OUT}')
    rows=[json.loads(line) for line in OUT.read_text().splitlines() if line.strip()]
    targets=[i for i,x in enumerate(rows)
             if (x.get('source') or 'wikipedia')=='champinon'
             and (x.get('career_complete') is not True or x.get('refresh_error'))][:args.limit]
    recovered=[];failed=[]
    for i in targets:
        x=rows[i];name=x.get('requested_name') or x.get('verified_title')
        try:
            page=parse_champinon(name)
            evidence=x.get('matched_price_evidence') or []
            matches=match_evidence(page,evidence)
            if evidence and len(matches)!=len(evidence):
                raise ValueError(f'priced evidence reconciliation {len(matches)}/{len(evidence)}')
            y=dict(x)
            y.update({
                'verified_title':page['title'],'source_url':page['url'],
                'born':page['born'] or x.get('born'),
                'profile':page.get('profile') or x.get('profile') or {},
                'career_rows':page['rows'],'career_complete':page['career_complete'],
                'stated_total':page['stated_total'],
                'stated_record':list(page['stated_record']) if page['stated_record'] is not None else None,
                'quality':'secondary_public_complete_career_exact_priced_match_verified',
                'verification':'exact identity heading + complete dated career table + reconciled stated record + exact priced full-date/opponent matchup',
                'refreshed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                'network_retry':'selfhosted_appwiza'
            })
            y.pop('refresh_error',None)
            rows[i]=y
            recovered.append({'name':name,'career_rows':len(page['rows']),'stated_total':page['stated_total']})
            print('RECOVERED',name,len(page['rows']),flush=True)
        except Exception as e:
            y=dict(x);y['career_complete']=False;y['refresh_error']=str(e)[:500]
            rows[i]=y
            failed.append({'name':name,'error':str(e)[:500]})
            print('FAILED',name,str(e)[:220],flush=True)
    tmp=OUT.with_suffix('.jsonl.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        for x in rows:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    os.replace(tmp,OUT)
    report={
        'attempted':len(targets),'recovered':len(recovered),'failed':len(failed),
        'recovered_items':recovered,'failed_items':failed,
        'complete_after':sum((x.get('source') or 'wikipedia')!='champinon' or x.get('career_complete') is True for x in rows),
        'generated_at':dt.datetime.now(dt.timezone.utc).isoformat()
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':
    main()
