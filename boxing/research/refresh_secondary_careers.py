#!/usr/bin/env python3
"""Refresh stored secondary careers under the current strict completeness rules."""
from __future__ import annotations
import datetime as dt,json,os,tempfile
from pathlib import Path
from backfill_priced_careers import OUT,match_evidence
from backfill_secondary_careers import parse_champinon

REPORT=OUT.parent/'secondary_refresh_report.json'

def main():
    if not OUT.exists():
        REPORT.write_text(json.dumps({'rows':0,'refreshed_complete':0,'failed':0},indent=2));return
    rows=[]
    for line in OUT.read_text().splitlines():
        if line.strip():rows.append(json.loads(line))
    refreshed=0;failed=0;errors=[];out=[]
    for x in rows:
        if (x.get('source') or 'wikipedia')!='champinon':
            out.append(x);continue
        name=x.get('requested_name') or x.get('verified_title')
        try:
            page=parse_champinon(name)
            evidence=x.get('matched_price_evidence') or []
            matches=match_evidence(page,evidence)
            if evidence and len(matches)!=len(evidence):
                raise ValueError(f'priced evidence reconciliation {len(matches)}/{len(evidence)}')
            y=dict(x)
            y.update({'verified_title':page['title'],'source_url':page['url'],'born':page['born'] or x.get('born'),
                      'profile':page.get('profile') or x.get('profile') or {},
                      'career_rows':page['rows'],'career_complete':page['career_complete'],
                      'stated_total':page['stated_total'],
                      'stated_record':list(page['stated_record']) if page['stated_record'] is not None else None,
                      'quality':'secondary_public_complete_career_exact_priced_match_verified',
                      'verification':'exact identity heading + complete dated career table + reconciled stated record + exact priced full-date/opponent matchup',
                      'refreshed_at':dt.datetime.now(dt.timezone.utc).isoformat()})
            y.pop('refresh_error',None)
            out.append(y);refreshed+=1
            print('REFRESHED',name,len(page['rows']),page['stated_record'],flush=True)
        except Exception as e:
            y=dict(x);y['career_complete']=False;y['refresh_error']=str(e)[:500]
            out.append(y);failed+=1;errors.append({'name':name,'error':str(e)[:500]})
            print('FAILED',name,str(e)[:200],flush=True)
    tmp=OUT.with_suffix('.jsonl.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        for x in out:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    os.replace(tmp,OUT)
    report={'rows':len(rows),'champinon_rows':sum((x.get('source') or 'wikipedia')=='champinon' for x in rows),
            'refreshed_complete':refreshed,'failed':failed,'errors':errors,
            'completed_at':dt.datetime.now(dt.timezone.utc).isoformat()}
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
