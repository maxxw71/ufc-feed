#!/usr/bin/env python3
"""Strictly parse staged legacy CompuBox archive candidates into round reports.

Uses the existing archived CompuBox identity/date/full-round/arithmetic parser.
Outputs only uniquely resolved fight reports. Duplicate URL variants for the same
date+pair are collapsed, preferring the earliest archived capture.
"""
from __future__ import annotations
import argparse,datetime as dt,json,sqlite3,re
from pathlib import Path
from import_archived_compubox_rounds import parse_candidate,norm

ROOT=Path(__file__).resolve().parents[1]
IN=ROOT/'public_punch_audit'/'legacy_compubox_fetched_candidates.json'
OUT=ROOT/'punch_supplements'/'legacy_compubox_round_reports.jsonl'
REPORT=ROOT/'punch_supplements'/'legacy_compubox_round_report.json'

def archive_date(url):
    m=re.search(r'/web/(\d{8})\d*',str(url or ''))
    if not m:return None
    try:return dt.datetime.strptime(m.group(1),'%Y%m%d').date().isoformat()
    except Exception:return None

def compact(parsed):
    cats={}
    for cat,byfighter in parsed['cats'].items():
        cats[cat]={}
        for fighter,vals in byfighter.items():
            cats[cat][fighter]=[[int(a),int(b)] for a,b in vals]
    return {
      'report_url':parsed['url'],'bout_date':parsed['date'],
      'available_from_date':archive_date(parsed['url']) or parsed['date'],
      'fighters':parsed['fighters'],'rounds':int(parsed['rounds']),
      'cats':cats,'header':parsed.get('header'),
      'date_identity_resolution':parsed.get('date_identity_resolution'),
      'source_quality':'archived_compubox_full_round_table_strict'
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);args=ap.parse_args()
    src=json.loads(IN.read_text())
    db=sqlite3.connect(args.db);db.row_factory=sqlite3.Row
    accepted=[];rejected=[]
    for row in src.get('rows') or []:
        if row.get('status')!='fetched':continue
        url=row.get('snapshot_url')
        payload={'title':row.get('title') or '',
                 'tables':row.get('tables') or [],
                 'relevant_paragraphs':[row.get('text_sample') or '']}
        parsed,err=parse_candidate(db,url,payload)
        if parsed:accepted.append(compact(parsed))
        else:rejected.append({'canonical_key':row.get('canonical_key'),'url':url,'reason':err})
    db.close()

    # Collapse duplicate domain/port/archive variants of the same exact fight.
    grouped={}
    for x in accepted:
        key=(x['bout_date'],tuple(sorted(norm(n) for n in x['fighters'])))
        cur=grouped.get(key)
        if cur is None or str(x.get('available_from_date') or '')<str(cur.get('available_from_date') or ''):
            grouped[key]=x
    unique=sorted(grouped.values(),key=lambda x:(x['bout_date'],x['fighters']))
    OUT.parent.mkdir(parents=True,exist_ok=True)
    with OUT.open('w') as f:
        for x in unique:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    reasons={}
    for x in rejected:reasons[x['reason']]=reasons.get(x['reason'],0)+1
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'staged_fetched_pages':sum(r.get('status')=='fetched' for r in (src.get('rows') or [])),
      'strict_parser_accepts_before_dedup':len(accepted),
      'unique_full_round_fights':len(unique),
      'duplicate_variants_collapsed':len(accepted)-len(unique),
      'date_min':min((x['bout_date'] for x in unique),default=None),
      'date_max':max((x['bout_date'] for x in unique),default=None),
      'accepted':[{'date':x['bout_date'],'fighters':x['fighters'],'rounds':x['rounds'],'available_from_date':x['available_from_date'],'url':x['report_url']} for x in unique],
      'rejection_reasons':reasons,'rejected_sample':rejected[:50],
      'policy':'Existing strict archived CompuBox resolver; exact fight identity/date; total+jab+power for both fighters every observed round; landed<=thrown; total==jab+power; duplicate date+pair variants collapsed.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('staged_fetched_pages','strict_parser_accepts_before_dedup','unique_full_round_fights','duplicate_variants_collapsed','date_min','date_max','rejection_reasons')},indent=2))

if __name__=='__main__':main()
