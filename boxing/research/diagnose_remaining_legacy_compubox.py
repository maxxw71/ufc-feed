#!/usr/bin/env python3
"""Diagnose remaining legacy CompuBox identity failures against research DB."""
from __future__ import annotations
import argparse,datetime as dt,json,re,sqlite3,unicodedata
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
IN=ROOT/'public_punch_audit'/'legacy_compubox_fetched_candidates.json'
REPORT=ROOT/'punch_supplements'/'legacy_compubox_round_report.json'
OUT=ROOT/'public_punch_audit'/'legacy_compubox_remaining_identity_diag.json'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def parse_header(text):
    m=re.search(r'PunchStat Report\s+(.{2,80}?)\s+vs\s+(.{2,80}?)\s+(\d{1,2}/\d{1,2}/\d{4})',text,re.I)
    if m:return m.group(1).strip(),m.group(2).strip(),m.group(3)
    m=re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*.{0,60}?([A-Za-zÀ-ÿ .\'’\-]{2,60}?)\s+(?:W|L|D|DRAW|UD|SD|MD|KO|TKO|RTD|TD)\s+\d{1,2}\s+([A-Za-zÀ-ÿ .\'’\-]{2,60}?)(?=\s+Total Punch)',text,re.I)
    if m:return m.group(2).strip(),m.group(3).strip(),m.group(1)
    return None

def iso(d):
    for f in ('%m/%d/%Y','%m/%d/%y'):
        try:return dt.datetime.strptime(d,f).date()
        except Exception:pass
    return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);args=ap.parse_args()
    fetched=json.loads(IN.read_text())
    accepted={x['url'] for x in json.loads(REPORT.read_text()).get('accepted',[])}
    db=sqlite3.connect(args.db);db.row_factory=sqlite3.Row
    rows=[]
    for x in fetched.get('rows') or []:
        if x.get('status')!='fetched' or x.get('snapshot_url') in accepted:continue
        text=str(x.get('text_sample') or '')
        h=parse_header(text)
        item={'canonical_key':x.get('canonical_key'),'url':x.get('snapshot_url'),'header':h,'title':x.get('title')}
        if not h:
            item['status']='no_parseable_header';rows.append(item);continue
        a,b,rawdate=h;day=iso(rawdate)
        item['normalized']={'a':nk(a),'b':nk(b),'date':day.isoformat() if day else None}
        nearby=[]
        if day:
            for delta in (-2,-1,0,1,2):
                d=(day+dt.timedelta(days=delta)).isoformat()
                for r in db.execute("select date,boxer_a,boxer_b,source,source_id,method,rounds,status from bouts where date=?",(d,)):
                    score=sum(k in (nk(r['boxer_a']),nk(r['boxer_b'])) for k in (nk(a),nk(b)))
                    # Also retain surname-ish partial candidates.
                    if score or nk(a) in nk(r['boxer_a'])+nk(r['boxer_b']) or nk(b) in nk(r['boxer_a'])+nk(r['boxer_b']):
                        nearby.append({**dict(r),'exact_full_name_hits':score})
        item['nearby_candidates']=nearby[:50]
        item['status']='candidates' if nearby else 'no_db_candidate'
        rows.append(item)
    db.close()
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'remaining_pages':len(rows),
         'with_candidates':sum(bool(x.get('nearby_candidates')) for x in rows),'rows':rows}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({'remaining_pages':out['remaining_pages'],'with_candidates':out['with_candidates']},indent=2))
if __name__=='__main__':main()
