#!/usr/bin/env python3
"""Audit and settle prospectively captured boxing moneylines.

Only quotes originally timestamped before the listed event are eligible.
Completed events are linked by exact event date + normalized participant pair
against the live verified bout graph. A result is accepted only when all
matching finished source rows agree on the winner/draw identity.
"""
from __future__ import annotations
import datetime as dt,json,re,sqlite3,unicodedata
from pathlib import Path
from collections import defaultdict,Counter

ROOT=Path(__file__).resolve().parents[1]
ODDS=ROOT/'prospective_odds'
DB=Path('/home/anestishkurti92/boxing-research/boxing.sqlite3')

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def snapshots():
    rows=[]
    for p in sorted(ODDS.glob('20??-??-??.jsonl')):
        for line_no,line in enumerate(p.read_text().splitlines(),1):
            if not line.strip():continue
            try:x=json.loads(line)
            except Exception:continue
            rows.append((p.name,line_no,x))
    return rows

def participant_names(q):
    vals=list((q.get('participants') or {}).values())
    vals=[str(x).strip() for x in vals if str(x).strip()]
    return vals if len(set(vals))==2 else []

def result_for_pair(d,date,names):
    target=sorted(nk(x) for x in names)
    if len(target)!=2 or target[0]==target[1]:return None
    vals=[];sources=[]
    for r in d.execute("select source,source_id,boxer_a,boxer_b,winner from bouts where date=? and status='FINISHED'",(date,)):
        if sorted([nk(r['boxer_a']),nk(r['boxer_b'])])!=target:continue
        w=str(r['winner'] or '').upper()
        if w=='BOXER A':winner=r['boxer_a']
        elif w=='BOXER B':winner=r['boxer_b']
        elif w=='DRAW':winner='DRAW'
        else:continue
        vals.append(nk(winner) if winner!='DRAW' else 'DRAW')
        sources.append([r['source'],r['source_id']])
    if not vals or len(set(vals))!=1:return None
    key=vals[0]
    if key=='DRAW':winner='DRAW'
    else:
        matches=[x for x in names if nk(x)==key]
        if len(matches)!=1:return None
        winner=matches[0]
    return {'winner':winner,'matching_source_rows':len(sources),'result_sources':sources[:20]}

def main():
    snaps=snapshots()
    now=dt.datetime.now(dt.timezone.utc)
    quotes=[];snap_summary=[]
    for fname,line_no,s in snaps:
        qs=s.get('quotes') or []
        snap_summary.append({
            'file':fname,'line':line_no,'fetched_at':s.get('fetched_at'),
            'quote_rows':len(qs),'verified_pre_event_rows':sum(q.get('timing_quality') in {'verified_pre_event_time','verified_pre_event_date'} for q in qs)
        })
        for q in qs:
            x=dict(q);x['_fetched_at']=s.get('fetched_at');x['_file']=fname;x['_line']=line_no
            quotes.append(x)
    eligible=[q for q in quotes if q.get('timing_quality') in {'verified_pre_event_time','verified_pre_event_date'}]
    groups=defaultdict(list)
    for q in eligible:
        names=participant_names(q);date=q.get('event_date');bid=str(q.get('bout_id') or '')
        if date and len(names)==2:
            groups[(date,bid,tuple(sorted(names,key=nk)))].append(q)

    d=sqlite3.connect(f'file:{DB}?mode=ro',uri=True,timeout=30);d.row_factory=sqlite3.Row
    settled=[];pending=[];unresolved=[]
    for (date,bid,names),qs in sorted(groups.items()):
        try:event_date=dt.date.fromisoformat(date)
        except Exception:continue
        record={'event_date':date,'bout_id':bid,'participants':list(names),
                'first_snapshot':min(q['_fetched_at'] for q in qs if q.get('_fetched_at')),
                'last_snapshot':max(q['_fetched_at'] for q in qs if q.get('_fetched_at')),
                'verified_quote_rows':len(qs),
                'sportsbooks':sorted({q.get('bookmaker') for q in qs if q.get('market_class')=='sportsbook' and q.get('bookmaker')}),
                'selections':sorted({q.get('selection') for q in qs if q.get('selection')})}
        if event_date>=now.date():
            pending.append(record);continue
        result=result_for_pair(d,date,names)
        if result:
            record.update(result);settled.append(record)
        else:
            unresolved.append(record)

    coverage={
        'generated_at':now.isoformat(),
        'snapshot_records':len(snaps),
        'raw_quote_observations':len(quotes),
        'verified_pre_event_quote_observations':len(eligible),
        'verification_pct':round(100*len(eligible)/len(quotes),2) if quotes else None,
        'unique_verified_bouts':len(groups),
        'unique_sportsbooks':sorted({q.get('bookmaker') for q in eligible if q.get('market_class')=='sportsbook' and q.get('bookmaker')}),
        'settled_verified_bouts':len(settled),
        'past_unresolved_bouts':len(unresolved),
        'future_or_today_bouts':len(pending),
        'date_min':min((q.get('event_date') for q in eligible if q.get('event_date')),default=None),
        'date_max':max((q.get('event_date') for q in eligible if q.get('event_date')),default=None),
        'policy':'Only originally timestamped pre-event quotes are eligible; results require exact date+pair and unanimous matching finished source rows.'
    }
    (ODDS/'coverage.json').write_text(json.dumps(coverage,indent=2,ensure_ascii=False))
    (ODDS/'settled_bouts.json').write_text(json.dumps({'generated_at':now.isoformat(),'settled':settled,'past_unresolved':unresolved},indent=2,ensure_ascii=False))
    print(json.dumps({**coverage,'settled_sample':settled[:10],'unresolved_sample':unresolved[:10]},indent=2,ensure_ascii=False))

if __name__=='__main__':main()
