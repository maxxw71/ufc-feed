#!/usr/bin/env python3
"""Audit and settle prospectively captured boxing moneylines.

Only quotes originally timestamped before the listed event are eligible.
Completed events are linked by exact event date + normalized participant pair
against the live verified bout graph. A result is accepted only when all
matching finished source rows agree on the winner/draw identity.
"""
from __future__ import annotations
import datetime as dt,json,re,sqlite3,unicodedata,os
from urllib.parse import urlparse
from pathlib import Path
from collections import defaultdict,Counter

ROOT=Path(__file__).resolve().parents[1]
ODDS=Path(os.environ.get('APPWIZA_BOXING_ODDS_ROOT',str(ROOT/'prospective_odds')))
DB=Path(os.environ.get('APPWIZA_BOXING_DB','/home/anestishkurti92/boxing-research/boxing.sqlite3'))
RESULT_SUPPLEMENTS=ODDS/'result_supplements.json'
PRIVATE_RESULT_SUPPLEMENTS=Path(os.environ.get('APPWIZA_BOXING_RESULT_SUPPLEMENTS','/srv/appwiza-sports/boxing-maintenance/private-supplements/result_supplements.json'))

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

def result_diagnostics(d,date,names):
    """Keep disagreement and nearby-date leads visible; never silently settle them."""
    target=sorted(nk(x) for x in names)
    exact=[]; nearby=[]; outcomes=set()
    for row in d.execute("select source,source_id,date,boxer_a,boxer_b,winner,status,url from bouts where date between date(?,'-7 days') and date(?,'+7 days')",(date,date)):
        r=dict(row)
        if sorted([nk(r['boxer_a']),nk(r['boxer_b'])])!=target:continue
        if r['date']!=date:
            nearby.append(r);continue
        exact.append(r)
        if r['status']!='FINISHED':continue
        w=str(r['winner'] or '').upper()
        outcomes.add(nk(r['boxer_a']) if w=='BOXER A' else nk(r['boxer_b']) if w=='BOXER B' else w or 'UNKNOWN')
    if len(outcomes)>1:reason='conflicting_exact_pair_results'
    elif outcomes and not outcomes <= set(target)|{'DRAW'}:reason='nondecisive_or_unknown_result_requires_rules'
    elif not exact and nearby:reason='possible_date_change_requires_review'
    elif not exact:reason='missing_exact_date_pair_result'
    elif not outcomes:reason='event_not_confirmed_finished'
    else:reason=None
    return {'review_reason':reason,'exact_evidence':exact,'nearby_date_leads':nearby,
            'automatic_settlement_blocked':reason in {'conflicting_exact_pair_results','nondecisive_or_unknown_result_requires_rules'}}

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
        domains={urlparse(x).hostname.removeprefix('www.') for x in sources if urlparse(x).scheme in {'http','https'} and urlparse(x).hostname}
        if len(domains)<2 or nk(winner) not in target:continue
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
    records=[]
    for path in dict.fromkeys([RESULT_SUPPLEMENTS,PRIVATE_RESULT_SUPPLEMENTS]):
        if not path.exists():continue
        obj=json.loads(path.read_text())
        records.extend(obj.get('results') or [])
    # Keep conflicting records: result_from_supplements must block disagreement.
    return records

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
        diagnostic=result_diagnostics(d,date,names)
        result=None if diagnostic['automatic_settlement_blocked'] else result_for_pair(d,date,names)
        supplement=result_from_supplements(result_supplements,date,names)
        if result and supplement and nk(result['winner'])!=nk(supplement['winner']):
            diagnostic.update(review_reason='database_supplement_result_conflict',automatic_settlement_blocked=True)
            result=None
        if not result and not diagnostic['automatic_settlement_blocked']:
            result=supplement
        record['evidence_review']=diagnostic
        record['wager_settlement_status']='unverified_bookmaker_rules'
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
    coverage['unresolved_reasons']=dict(Counter(r['evidence_review']['review_reason'] for r in unresolved))
    coverage['bout_level_results_location']='private_appwiza_server' if PRIVATE_RESULT_SUPPLEMENTS.exists() else 'settled_bouts.json'
    coverage['settled_count_meaning']='Verified fight outcomes; bookmaker-specific wager settlement remains separately unverified.'
    # Keep detailed repair evidence off the repository/public report path.
    review_root=Path(os.environ.get('APPWIZA_BOXING_REVIEW_ROOT','/srv/appwiza-sports/boxing-releases/outcome-review'))
    review_root.mkdir(parents=True,exist_ok=True)
    review_path=review_root/(now.strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    review_path.write_text(json.dumps({'generated_at':now.isoformat(),'settled':settled,'past_unresolved':unresolved},indent=2,ensure_ascii=False))
    public_settled=[{k:v for k,v in r.items() if k!='evidence_review'} for r in settled]
    public_unresolved=[{k:v for k,v in r.items() if k!='evidence_review'} for r in unresolved]
    (ODDS/'coverage.json').write_text(json.dumps(coverage,indent=2,ensure_ascii=False))
    # Private overlays must never flow into public bout-level exports.
    if not PRIVATE_RESULT_SUPPLEMENTS.exists():
        (ODDS/'settled_bouts.json').write_text(json.dumps({'generated_at':now.isoformat(),'settled':public_settled,'past_unresolved':public_unresolved},indent=2,ensure_ascii=False))
    print(json.dumps(coverage,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
