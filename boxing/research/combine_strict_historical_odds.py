#!/usr/bin/env python3
"""Combine all strict independently verified historical boxing price evidence.

Inputs:
- exact archived event-page validation
- exact archived ProBoxingOdds homepage validation

Rows are deduplicated by stable quote signature rather than transient SQLite
rowid. Every retained row preserves its validation route(s). No unverified
historical prices are introduced.
"""
from __future__ import annotations
import datetime as dt,json
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPORTS=ROOT/'public_reports'
EVENT=REPORTS/'HISTORICAL_ODDS_VALIDATED_ROWS.json'
HOME=REPORTS/'HISTORICAL_ODDS_HOME_VALIDATED_ROWS.json'
OUT=REPORTS/'HISTORICAL_ODDS_STRICT_UNION_ROWS.json'
REPORT=REPORTS/'HISTORICAL_ODDS_STRICT_UNION_REPORT.json'

def load(path):
    try:return json.loads(path.read_text())
    except Exception:return {}

def norm(s):
    import re,unicodedata
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def sig(x):
    try:p=round(float(x.get('stored_decimal_price')),6)
    except Exception:return None
    d=str(x.get('event_date') or '')
    b=str(x.get('bout_id') or '')
    bk=norm(x.get('bookmaker'));sel=norm(x.get('selection'))
    return (d,b,bk,sel,p) if d and b and bk and sel else None

def main():
    sources=[('event_page',load(EVENT)),('homepage',load(HOME))]
    groups=defaultdict(list)
    raw_counts={}
    for route,obj in sources:
        rows=obj.get('rows') or []
        raw_counts[route]=len(rows)
        for x in rows:
            k=sig(x)
            if k:groups[k].append((route,x))
    merged=[]
    route_only=defaultdict(int);overlap=0
    for k,items in groups.items():
        routes=sorted({r for r,_ in items})
        if len(routes)>1:overlap+=1
        else:route_only[routes[0]]+=1
        # Prefer event-page evidence as the base record, then homepage.
        items.sort(key=lambda z:(0 if z[0]=='event_page' else 1,z[1].get('snapshot_timestamp') or ''))
        base=dict(items[0][1])
        evidence=[]
        for route,x in items:
            evidence.append({
              'route':route,'snapshot_timestamp':x.get('snapshot_timestamp'),
              'snapshot_url':x.get('snapshot_url'),'verification':x.get('verification')
            })
        base['strict_validation_routes']=routes
        base['strict_validation_evidence']=evidence
        merged.append(base)
    merged.sort(key=lambda x:(x.get('event_date') or '',str(x.get('bout_id') or ''),str(x.get('bookmaker') or ''),str(x.get('selection') or '')))
    bouts={(x.get('event_date'),str(x.get('bout_id') or '')) for x in merged}
    events={str(x.get('event_url') or '') for x in merged if str(x.get('event_url') or '')}
    dates=sorted({str(x.get('event_date') or '') for x in merged if str(x.get('event_date') or '')})
    books=sorted({str(x.get('bookmaker') or '') for x in merged if str(x.get('bookmaker') or '')})
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'input_rows':raw_counts,'stable_unique_quote_rows':len(merged),
      'quote_signatures_validated_by_both_routes':overlap,
      'event_page_only_quote_rows':route_only.get('event_page',0),
      'homepage_only_quote_rows':route_only.get('homepage',0),
      'distinct_validated_bouts':len(bouts),'distinct_validated_events':len(events),
      'date_min':dates[0] if dates else None,'date_max':dates[-1] if dates else None,
      'validated_bookmakers':books,
      'policy':'Union of strict exact pre-event Wayback event-page and homepage quote validation. Stable quote signature = event date + bout id + bookmaker + selection + stored decimal price. No unverified historical rows.'
    }
    OUT.write_text(json.dumps({'generated_at':report['generated_at'],'rows':merged,'policy':report['policy']},indent=2,ensure_ascii=False))
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
