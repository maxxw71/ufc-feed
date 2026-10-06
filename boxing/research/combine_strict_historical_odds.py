#!/usr/bin/env python3
"""Reconcile published archive evidence; do not infer settlement or closing odds."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPORTS=ROOT/'public_reports'
EVENT=REPORTS/'HISTORICAL_ODDS_VALIDATED_ROWS.json'
HOME=REPORTS/'HISTORICAL_ODDS_HOME_VALIDATED_ROWS.json'
OUT=REPORTS/'HISTORICAL_ODDS_STRICT_UNION_ROWS.json'
REPORT=REPORTS/'HISTORICAL_ODDS_STRICT_UNION_REPORT.json'
HELD=REPORTS/'HISTORICAL_ODDS_STRICT_UNION_HELD.json'

def load(path, optional=False):
    if optional and not path.exists(): return {}
    return json.loads(path.read_text())

def norm(s):
    import unicodedata
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def sig(x):
    try:p=round(float(x.get('stored_decimal_price')),6)
    except (TypeError,ValueError):return None
    d=str(x.get('event_date') or '')
    b=str(x.get('bout_id') or '')
    bk=norm(x.get('bookmaker'));sel=norm(x.get('selection'))
    return (d,b,bk,sel,p) if d and b and bk and sel and math.isfinite(p) and p>1 else None

def evidence_check(x):
    """Recheck every route's actual American price and archive-time metadata."""
    reasons=[]
    try:
        a=float(x['archived_american_price'])
        if not math.isfinite(a) or not a or not a.is_integer():raise ValueError()
        decimal=1+a/100 if a>0 else 1+100/abs(a)
        price=float(x['stored_decimal_price'])
        if not math.isfinite(price) or price<=1 or abs(decimal-price)>1e-6:
            reasons.append('archived_price_not_equivalent')
    except (KeyError,TypeError,ValueError,OverflowError):
        decimal=None;reasons.append('invalid_price')
    try:
        stamp=str(x['snapshot_timestamp'])
        captured=dt.datetime.strptime(stamp,'%Y%m%d%H%M%S')
        event=dt.date.fromisoformat(str(x['event_date']))
        match=re.match(r'^https://web\.archive\.org/web/(\d{14})(?:[a-z_]+)?/https?://',str(x['snapshot_url']))
        if len(stamp)!=14 or not match or match[1]!=stamp or captured.date()>=event:
            reasons.append('invalid_pre_event_archive_metadata')
    except (KeyError,TypeError,ValueError):
        reasons.append('invalid_pre_event_archive_metadata')
    return {'accepted':not reasons,'reasons':reasons,'archived_decimal_recomputed':decimal}

def combine(sources, previous_rows=(), previous_absent=()):
    groups=defaultdict(list);invalid=[]
    for route,obj in sources:
        for x in obj.get('rows') or []:
            key=sig(x)
            evidence={'route':route,**x,'reconciliation':evidence_check(x)}
            if key:groups[key].append(evidence)
            else:invalid.append({'reason':'invalid_signature','evidence':evidence})
    merged=[];held=list(invalid);overlap=0;route_only=defaultdict(int)
    for key,items in sorted(groups.items()):
        accepted=[e for e in items if e['reconciliation']['accepted']]
        if not accepted:
            held.append({'signature':list(key),'reason':'no_equivalent_pre_event_route','evidence':items})
            continue
        accepted.sort(key=lambda e:(e['route']!='event_page',e.get('snapshot_timestamp') or ''))
        chosen=accepted[0]
        base={k:v for k,v in chosen.items() if k not in ('route','reconciliation')}
        routes=sorted({e['route'] for e in accepted})
        if len(routes)>1:overlap+=1
        else:route_only[routes[0]]+=1
        base['strict_validation_routes']=routes
        # Include actual per-route prices, not only timestamps; rejected routes
        # remain visible but are never labelled valid routes.
        base['strict_validation_evidence']=accepted
        base['rejected_validation_evidence']=[e for e in items if not e['reconciliation']['accepted']]
        base['settlement_verified']=False
        base['closing_price_verified']=False
        merged.append(base)
    absent={}
    for x in [*previous_absent,*previous_rows]:
        key=sig(x)
        if key and key not in groups:absent[key]=x
    return merged,held,[absent[k] for k in sorted(absent)],{
        'input_unique_signatures':len(groups),
        'quote_signatures_validated_by_both_routes':overlap,
        'event_page_only_quote_rows':route_only.get('event_page',0),
        'homepage_only_quote_rows':route_only.get('homepage',0),
    }

def main():
    sources=[('event_page',load(EVENT)),('homepage',load(HOME))]
    previous=load(OUT,optional=True);oldheld=load(HELD,optional=True)
    merged,held,absent,counts=combine(sources,previous.get('rows') or [],oldheld.get('old_signatures_absent') or [])
    stamp=dt.datetime.now(dt.timezone.utc).isoformat()
    policy=('Reconciliation of published pre-event archive evidence; American-to-decimal price equivalence <=1e-6. '
            'Each retained route must pass its own price/time metadata checks. This step does not replay archives. '
            'Closing price and bookmaker settlement are unverified; no production or ROI eligibility is conferred.')
    report={'generated_at':stamp,**counts,'input_rows':{r:len(o.get('rows') or []) for r,o in sources},
      'input_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (EVENT,HOME)},
      'stable_unique_quote_rows':len(merged),'held_current_signatures':len(held),
      'old_signatures_absent':len(absent),
      'distinct_validated_bouts':len({(x['event_date'],str(x['bout_id'])) for x in merged}),
      'distinct_validated_events':len({x['event_url'] for x in merged if x.get('event_url')}),
      'date_min':min((x['event_date'] for x in merged),default=None),
      'date_max':max((x['event_date'] for x in merged),default=None),
      'validated_bookmakers':sorted({x['bookmaker'] for x in merged}),
      'fresh_archive_replay':False,'settlement_verified':False,'policy':policy}
    # Save held evidence before replacing the current output.
    HELD.write_text(json.dumps({'generated_at':stamp,'held_current':held,'old_signatures_absent':absent,
       'policy':'Retained evidence requiring review; excluded from the current accepted union.'},indent=2,ensure_ascii=False))
    OUT.write_text(json.dumps({'generated_at':stamp,'rows':merged,'policy':policy},indent=2,ensure_ascii=False))
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
