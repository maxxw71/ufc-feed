#!/usr/bin/env python3
"""Discover pre-event Wayback captures for ProBoxingOdds event pages.

Discovery only. This indexes archive timestamps; it does not validate prices.
Strict quote validation is handled separately by validate_historical_odds_snapshot.py.
"""
from __future__ import annotations
import datetime as dt,json,re,time,urllib.parse,urllib.request
from collections import defaultdict
from pathlib import Path

OUT=Path('boxing/public_reports/HISTORICAL_ODDS_WAYBACK_INDEX.json')
UA='Mozilla/5.0 AppwizaHistoricalOddsIndex/1.0'
PREFIXES=[
  'https://www.proboxingodds.com/events/',
  'http://www.proboxingodds.com/events/',
  'https://proboxingodds.com/events/',
  'http://proboxingodds.com/events/',
]
EVENT_RE=re.compile(r'/events/(\d{4}-\d{2}-\d{2})-(\d+)(?:$|[/?#])',re.I)

def get_json(url,timeout=75):
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8','replace'))

def cdx_prefix(prefix):
    params=[
      ('url',prefix),('matchType','prefix'),('output','json'),
      ('filter','statuscode:200'),
      ('fl','timestamp,original,statuscode,mimetype,digest'),
      ('collapse','urlkey,digest'),('limit','10000')
    ]
    q='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode(params)
    try:
        rows=get_json(q)
        vals=rows[1:] if isinstance(rows,list) and rows else []
        return q,vals,None
    except Exception as e:
        return q,[],type(e).__name__+': '+str(e)[:220]

def canonical(original):
    try:
        p=urllib.parse.urlsplit(original)
    except Exception:
        return None,None
    m=EVENT_RE.search(p.path)
    if not m:return None,None
    date=m.group(1)
    path=p.path.rstrip('/')
    return date,'https://www.proboxingodds.com'+path

def main():
    raw=[];queries=[]
    for prefix in PREFIXES:
        q,rows,error=cdx_prefix(prefix)
        queries.append({'prefix':prefix,'query':q,'rows':len(rows),'error':error})
        raw.extend(rows)
        time.sleep(.25)

    grouped=defaultdict(list)
    rejected=0
    for row in raw:
        if len(row)<5:
            rejected+=1;continue
        stamp,original,status,mime,digest=map(str,row[:5])
        date,url=canonical(original)
        if not date or len(stamp)<8:
            rejected+=1;continue
        # Strictly before event calendar date. Same-day captures are not accepted
        # because fight start time is not proven here.
        if stamp[:8] >= date.replace('-',''):
            continue
        grouped[(date,url)].append({
          'timestamp':stamp,'original':original,'statuscode':status,
          'mimetype':mime,'digest':digest,
          'snapshot_url':f'https://web.archive.org/web/{stamp}id_/{original}'
        })

    events=[]
    for (date,url),caps in grouped.items():
        uniq={}
        for c in caps:
            uniq[(c['timestamp'],c['digest'],c['original'])]=c
        caps=sorted(uniq.values(),key=lambda x:x['timestamp'],reverse=True)[:20]
        events.append({
          'event_date':date,'event_url':url,
          'pre_event_capture_count':len(caps),
          'captures':caps
        })
    events.sort(key=lambda x:(x['event_date'],x['event_url']))

    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'queries':queries,
      'raw_cdx_rows':len(raw),
      'rejected_unparsed_rows':rejected,
      'events_with_pre_event_captures':len(events),
      'pre_event_captures_indexed':sum(len(x['captures']) for x in events),
      'date_min':min((x['event_date'] for x in events),default=None),
      'date_max':max((x['event_date'] for x in events),default=None),
      'events':events,
      'policy':'Discovery only. Capture timestamp must be strictly before the event calendar date; same-day captures are excluded. No quote is validated by this index.'
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:report[k] for k in (
      'raw_cdx_rows','events_with_pre_event_captures','pre_event_captures_indexed','date_min','date_max'
    )},indent=2))

if __name__=='__main__':main()
