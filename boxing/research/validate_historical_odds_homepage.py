#!/usr/bin/env python3
"""Validate historical ProBoxingOdds quotes against archived homepage snapshots.

This complements event-page validation. A row is accepted only when an exact
Wayback homepage capture is strictly before the bout date and contains the exact
bout id, bookmaker, selection, and equivalent displayed price.
"""
from __future__ import annotations
import datetime as dt, json, os, sqlite3, urllib.parse, urllib.request
from collections import defaultdict
from pathlib import Path
from validate_historical_odds_snapshot import parse_snapshot, fetch_exact_snapshot

DB=Path(os.environ.get('BOXING_DB','/tmp/boxing_research.sqlite3'))
OUT=Path('boxing/public_reports/HISTORICAL_ODDS_HOME_VALIDATION_REPORT.json')
ROWS=Path('boxing/public_reports/HISTORICAL_ODDS_HOME_VALIDATED_ROWS.json')
UA='Mozilla/5.0 AppwizaHistoricalOddsHome/1.0'
ROOTS=['https://www.proboxingodds.com/','http://www.proboxingodds.com/']

def cdx(root):
    q='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode([
      ('url',root),('output','json'),('filter','statuscode:200'),
      ('fl','timestamp,original,statuscode,mimetype,digest'),('limit','10000')
    ])
    req=urllib.request.Request(q,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=90) as r:
        x=json.loads(r.read().decode('utf-8','replace'))
    return x[1:] if isinstance(x,list) and x else []

def nk_date(x):
    try:return dt.date.fromisoformat(str(x)[:10])
    except Exception:return None

def main():
    db=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);db.row_factory=sqlite3.Row
    quotes=[dict(r) for r in db.execute(
      "select rowid as quote_rowid,* from odds where source='proboxingodds' order by rowid"
    )]
    db.close()
    bydate=defaultdict(list)
    import re
    for q in quotes:
        m=re.search(r'/events/(\d{4}-\d{2}-\d{2})-',str(q.get('url') or ''))
        d=nk_date(m.group(1)) if m else None
        if d:
            q['derived_event_date']=d.isoformat()
            bydate[d].append(q)

    raw=[]
    errors=[]
    for root in ROOTS:
        try:raw.extend(cdx(root))
        except Exception as e:errors.append({'root':root,'error':type(e).__name__+': '+str(e)[:200]})
    caps={}
    for r in raw:
        if len(r)<5:continue
        ts,orig,status,mime,digest=map(str,r[:5])
        if len(ts)<14:continue
        caps[(ts,digest)]={'timestamp':ts,'original':orig,'digest':digest,
          'snapshot_url':f'https://web.archive.org/web/{ts}id_/{orig}'}
    caps=sorted(caps.values(),key=lambda x:x['timestamp'])

    validated=[]
    audits=[]
    for event_day,qrows in sorted(bydate.items()):
        ymd=event_day.strftime('%Y%m%d')
        # Homepage odds change frequently. Use up to the five newest captures in
        # the 14 days strictly preceding the event.
        eligible=[]
        for c in caps:
            try:cd=dt.datetime.strptime(c['timestamp'][:8],'%Y%m%d').date()
            except Exception:continue
            if cd<event_day and (event_day-cd).days<=14:
                eligible.append(c)
        eligible=eligible[-5:][::-1]
        if not eligible:continue
        parsed=[]
        attempts=[]
        for c in eligible:
            try:
                final,raw_html,used,prior=fetch_exact_snapshot(c['snapshot_url'],c['timestamp'])
                cells=parse_snapshot(raw_html)
                attempts.append({'timestamp':c['timestamp'],'cells':len(cells),'used':used})
                if cells:parsed.append((c,cells,used))
            except Exception as e:
                attempts.append({'timestamp':c['timestamp'],'error':type(e).__name__+': '+str(e)[:240]})
        if not parsed:
            audits.append({'event_date':event_day.isoformat(),'quotes':len(qrows),'validated':0,'attempts':attempts})
            continue
        count=0
        for q in qrows:
            key=(str(q.get('bout_id') or ''),__import__('validate_historical_odds_snapshot').nk(q.get('bookmaker')),__import__('validate_historical_odds_snapshot').nk(q.get('selection')))
            for c,cells,used in parsed:
                arc=cells.get(key)
                if not arc or arc.get('decimal_from_archive') is None or q.get('decimal_price') is None:continue
                diff=abs(float(q['decimal_price'])-float(arc['decimal_from_archive']))
                if diff<=0.005:
                    validated.append({
                      'quote_rowid':q['quote_rowid'],'bout_id':str(q.get('bout_id') or ''),
                      'bookmaker':q.get('bookmaker'),'selection':q.get('selection'),
                      'stored_decimal_price':float(q['decimal_price']),
                      'archived_american_price':arc['american_price'],
                      'archived_decimal_price':round(float(arc['decimal_from_archive']),6),
                      'event_date':event_day.isoformat(),'event_url':q.get('url'),
                      'snapshot_timestamp':c['timestamp'],'snapshot_url':used,
                      'verification':'exact_pre_event_homepage_bookmaker_bout_selection_price_match'
                    });count+=1;break
        audits.append({'event_date':event_day.isoformat(),'quotes':len(qrows),'validated':count,'attempts':attempts})

    uniq={str(x['quote_rowid']):x for x in validated}
    validated=list(uniq.values())
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'cdx_rows':len(raw),'cdx_errors':errors,'stored_quote_dates':len(bydate),
      'validated_price_rows':len(validated),
      'distinct_validated_bouts':len({x['bout_id'] for x in validated}),
      'distinct_validated_events':len({x['event_url'] for x in validated if x.get('event_url')}),
      'validated_rows':validated,'audits':audits,
      'policy':'Exact ProBoxingOdds homepage Wayback capture strictly before event date, within 14 days; exact bookmaker, bout id, selection and displayed price; decimal equivalence <=0.005.'
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    ROWS.write_text(json.dumps({'generated_at':report['generated_at'],'rows':validated,'policy':report['policy']},indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('cdx_rows','stored_quote_dates','validated_price_rows','distinct_validated_bouts','distinct_validated_events')},indent=2))

if __name__=='__main__':main()
