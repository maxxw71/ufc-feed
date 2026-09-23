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
RESULT_SUPPLEMENTS=ODDS/'result_supplements.json'

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

def sportsbook_market_summary(qs):
    by=defaultdict(list)
    for q in qs:
        if q.get('market_class')!='sportsbook':continue
        book=str(q.get('bookmaker') or '').strip()
        sel=str(q.get('selection') or '').strip()
        fetched=str(q.get('_fetched_at') or '')
        try:price=float(q.get('decimal_price'))
        except Exception:continue
        if not book or not sel or not fetched or price<=1:continue
        by[(book,sel)].append((fetched,price,int(q.get('american_price')) if q.get('american_price') is not None else None))
    per_book={}
    for (book,sel),vals in sorted(by.items()):
        vals=sorted(vals,key=lambda x:x[0])
        per_book.setdefault(book,{})[sel]={
          'first_verified_pre_event':{'fetched_at':vals[0][0],'decimal_price':vals[0][1],'american_price':vals[0][2]},
          'latest_verified_pre_event':{'fetched_at':vals[-1][0],'decimal_price':vals[-1][1],'american_price':vals[-1][2]},
          'observations':len(vals),
          'decimal_move':round(vals[-1][1]-vals[0][1],6)
        }
    selections={}
    for book,data in per_book.items():
        for sel,x in data.items():
            selections.setdefault(sel,[]).append((book,x['latest_verified_pre_event']['decimal_price']))
    consensus={}
    for sel,vals in selections.items():
        prices=sorted(v for _,v in vals)
        n=len(prices)
        median=prices[n//2] if n%2 else (prices[n//2-1]+prices[n//2])/2
        consensus[sel]={'books':len(vals),'median_latest_verified_decimal':round(median,6),
                        'min_latest_verified_decimal':min(prices),'max_latest_verified_decimal':max(prices)}
    complete_books=sum(len(v)>=2 for v in per_book.values())
    return {'per_book':per_book,'consensus_latest_verified':consensus,
            'books_with_both_sides_latest':complete_books,'book_selection_pairs':len(by)}

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

def result_from_supplements(records,date,names):
    target=sorted(nk(x) for x in names)
    if len(target)!=2 or target[0]==target[1]:return None
    matches=[]
    for r in records or []:
        if str(r.get('event_date') or '')!=date:continue
        participants=r.get('participants') or []
        if len(participants)!=2 or sorted(nk(x) for x in participants)!=target:continue
        sources=sorted({str(x).strip() for x in (r.get('sources') or []) if str(x).strip()})
        winner=str(r.get('winner') or '').strip()
        if len(sources)<2 or nk(winner) not in target:continue
        matches.append((nk(winner),winner,sources,r))
    if not matches or len({x[0] for x in matches})!=1:return None
    key=matches[0][0]
    display=next((x for x in names if nk(x)==key),None)
    if not display:return None
    sources=sorted({src for x in matches for src in x[2]})
    return {
      'winner':display,
      'matching_source_rows':0,
      'result_sources':[['external_verified_result',src] for src in sources],
      'result_quality':'two_plus_independent_published_result_sources_exact_date_pair',
      'result_method':matches[0][3].get('method'),
      'result_round':matches[0][3].get('round'),
      'result_time':matches[0][3].get('time')
    }

def load_result_supplements():
    try:
        obj=json.loads(RESULT_SUPPLEMENTS.read_text())
        return obj.get('results') or []
    except Exception:
        return []

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
    result_supplements=load_result_supplements()
    settled=[];pending=[];unresolved=[]
    for (date,bid,names),qs in sorted(groups.items()):
        try:event_date=dt.date.fromisoformat(date)
        except Exception:continue
        sportsbook_qs=[q for q in qs if q.get('market_class')=='sportsbook']
        prediction_qs=[q for q in qs if q.get('market_class')=='prediction_market']
        market_summary=sportsbook_market_summary(qs)
        record={'event_date':date,'bout_id':bid,'participants':list(names),
                'first_snapshot':min(q['_fetched_at'] for q in qs if q.get('_fetched_at')),
                'last_snapshot':max(q['_fetched_at'] for q in qs if q.get('_fetched_at')),
                'verified_quote_rows':len(qs),
                'verified_sportsbook_quote_rows':len(sportsbook_qs),
                'verified_prediction_market_quote_rows':len(prediction_qs),
                'market_classes':sorted({q.get('market_class') for q in qs if q.get('market_class')}),
                'sportsbooks':sorted({q.get('bookmaker') for q in sportsbook_qs if q.get('bookmaker')}),
                'sportsbook_market_summary':market_summary,
                'selections':sorted({q.get('selection') for q in qs if q.get('selection')})}
        if event_date>=now.date():
            pending.append(record);continue
        result=result_for_pair(d,date,names) or result_from_supplements(result_supplements,date,names)
        if result:
            record.update(result);settled.append(record)
        else:
            unresolved.append(record)

    coverage={
        'generated_at':now.isoformat(),
        'snapshot_records':len(snaps),
        'raw_quote_observations':len(quotes),
        'verified_pre_event_quote_observations':len(eligible),
        'verified_pre_event_sportsbook_quote_observations':sum(q.get('market_class')=='sportsbook' for q in eligible),
        'verified_pre_event_prediction_market_quote_observations':sum(q.get('market_class')=='prediction_market' for q in eligible),
        'verification_pct':round(100*len(eligible)/len(quotes),2) if quotes else None,
        'unique_verified_bouts':len(groups),
        'unique_verified_sportsbook_bouts':sum(any(q.get('market_class')=='sportsbook' for q in qs) for qs in groups.values()),
        'unique_prediction_market_only_bouts':sum(not any(q.get('market_class')=='sportsbook' for q in qs) for qs in groups.values()),
        'unique_sportsbooks':sorted({q.get('bookmaker') for q in eligible if q.get('market_class')=='sportsbook' and q.get('bookmaker')}),
        'sportsbook_bouts_with_two_sided_latest_quotes':sum((sportsbook_market_summary(qs).get('books_with_both_sides_latest') or 0)>0 for qs in groups.values()),
        'settled_verified_bouts':len(settled),
        'settled_verified_sportsbook_bouts':sum(x.get('verified_sportsbook_quote_rows',0)>0 for x in settled),
        'past_unresolved_bouts':len(unresolved),
        'past_unresolved_sportsbook_bouts':sum(x.get('verified_sportsbook_quote_rows',0)>0 for x in unresolved),
        'future_or_today_bouts':len(pending),
        'future_or_today_sportsbook_bouts':sum(x.get('verified_sportsbook_quote_rows',0)>0 for x in pending),
        'date_min':min((q.get('event_date') for q in eligible if q.get('event_date')),default=None),
        'date_max':max((q.get('event_date') for q in eligible if q.get('event_date')),default=None),
        'policy':'Only originally timestamped pre-event quotes are eligible; results require exact date+pair and either unanimous matching finished source rows or a strict manual supplement with at least two independent published result sources. Sportsbook and prediction-market observations are reported separately. For sportsbook validation, opening=first verified observation and latest=last verified observation before listed event start/date; consensus latest is the median of each sportsbook latest observation, never a retrospectively chosen price.'
    }
    (ODDS/'coverage.json').write_text(json.dumps(coverage,indent=2,ensure_ascii=False))
    (ODDS/'settled_bouts.json').write_text(json.dumps({'generated_at':now.isoformat(),'settled':settled,'past_unresolved':unresolved},indent=2,ensure_ascii=False))
    print(json.dumps({**coverage,'settled_sample':settled[:10],'unresolved_sample':unresolved[:10]},indent=2,ensure_ascii=False))

if __name__=='__main__':main()
