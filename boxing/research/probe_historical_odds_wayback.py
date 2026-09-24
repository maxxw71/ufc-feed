#!/usr/bin/env python3
"""Pilot pre-event Wayback validation for historical ProBoxingOdds event pages.

This does not promote historical prices. It asks whether an archived snapshot
exists before the event and whether the archived page visibly contains the
stored participants/bookmakers/prices needed for exact quote verification.
"""
from __future__ import annotations
import datetime as dt,json,re,sqlite3,urllib.parse,urllib.request
from collections import defaultdict
from pathlib import Path

DB=Path('/home/anestishkurti92/boxing-research/boxing.sqlite3')
OUT=Path('boxing/public_reports/HISTORICAL_ODDS_WAYBACK_PILOT.json')
UA='Mozilla/5.0 AppwizaHistoricalOddsWayback/1.0'

def get_json(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=45) as r:
        return json.loads(r.read().decode('utf-8','replace'))

def get_text(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=60) as r:
        raw=r.read(3_000_001)
        return r.geturl(),raw.decode('utf-8','replace') if len(raw)<=3_000_000 else ''

def cdx_before(url,event_date):
    to=event_date.replace('-','')
    params=[
      ('url',url),('output','json'),('filter','statuscode:200'),
      ('fl','timestamp,original,statuscode,mimetype,digest'),
      ('to',to),('collapse','digest'),('limit','20')
    ]
    q='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode(params)
    try:
        rows=get_json(q)
    except Exception as e:
        return q,[],type(e).__name__+': '+str(e)[:180]
    vals=rows[1:] if isinstance(rows,list) and rows else []
    vals=[r for r in vals if len(r)>=5 and str(r[0])[:8] < to]
    vals.sort(key=lambda r:r[0],reverse=True)
    return q,vals,None

def num_tokens(price):
    try:p=float(price)
    except Exception:return []
    vals={f'{p:g}',f'{p:.2f}',f'{p:.3f}'}
    # Stored historical prices are decimal. Common page representations may
    # also show American odds; keep this pilot conservative and test decimal
    # strings only rather than reverse-converting without source-format proof.
    return sorted(v.rstrip('0').rstrip('.') if '.' in v else v for v in vals if v)

def main():
    d=sqlite3.connect(f'file:{DB}?mode=ro',uri=True,timeout=120);d.row_factory=sqlite3.Row
    matchups={}
    for r in d.execute("select source_id,data from source_rows where source='proboxingodds' and kind='matchup'"):
        try:matchups[str(r['source_id'])]=json.loads(r['data'])
        except Exception:pass

    byevent=defaultdict(list)
    for r in d.execute("select rowid as quote_rowid,* from odds where source='proboxingodds' and url is not null order by rowid"):
        x=dict(r);meta=matchups.get(str(x.get('bout_id') or '')) or {}
        date=str(meta.get('event_date') or '')
        url=str(x.get('url') or meta.get('event_url') or '')
        if not re.match(r'^\d{4}-\d{2}-\d{2}$',date) or 'proboxingodds.com/events/' not in url:continue
        byevent[(date,url)].append(x)

    # Prioritize older events with multiple bookmaker/selection rows; these are
    # most valuable for converting exploratory historical backtests.
    candidates=sorted(byevent.items(),key=lambda kv:(kv[0][0],-len(kv[1])))[:40]
    out=[]
    for (event_date,url),quotes in candidates:
        q,rows,error=cdx_before(url,event_date)
        item={
          'event_date':event_date,'event_url':url,'stored_quote_rows':len(quotes),
          'cdx_query':q,'pre_event_captures':len(rows),'cdx_error':error
        }
        if rows:
            ts,orig,status,mime,digest=rows[0][:5]
            snap=f'https://web.archive.org/web/{ts}id_/{orig}'
            item.update({'latest_pre_event_timestamp':ts,'snapshot':snap,'digest':digest,'mimetype':mime})
            try:
                final,text=get_text(snap)
                low=text.casefold()
                item['snapshot_final_url']=final
                item['snapshot_text_chars']=len(text)
                participants=sorted({str(x.get('selection') or '') for x in quotes if x.get('selection')})
                books=sorted({str(x.get('bookmaker') or '') for x in quotes if x.get('bookmaker')})
                item['participant_hits']={p:(p.casefold() in low) for p in participants}
                item['bookmaker_hits']={b:(b.casefold() in low) for b in books}
                matched=[]
                for x in quotes:
                    sel=str(x.get('selection') or '');book=str(x.get('bookmaker') or '')
                    tokens=num_tokens(x.get('decimal_price'))
                    if sel.casefold() in low and book.casefold() in low and any(tok in text for tok in tokens):
                        matched.append({'quote_rowid':x.get('quote_rowid'),'bout_id':x.get('bout_id'),'bookmaker':book,
                                        'selection':sel,'decimal_price':x.get('decimal_price'),'matched_decimal_tokens':tokens})
                item['candidate_exact_visible_quote_rows']=matched
                item['candidate_exact_visible_quote_count']=len(matched)
            except Exception as e:
                item['snapshot_error']=type(e).__name__+': '+str(e)[:200]
        out.append(item)

    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'events_probed':len(out),
      'events_with_pre_event_capture':sum(bool(x.get('pre_event_captures')) for x in out),
      'events_with_visible_candidate_quotes':sum(bool(x.get('candidate_exact_visible_quote_count')) for x in out),
      'candidate_visible_quote_rows':sum(int(x.get('candidate_exact_visible_quote_count') or 0) for x in out),
      'events':out,
      'policy':'Pilot only. A row is not validated merely because strings co-occur on a snapshot; exact page structure/bookmaker/selection/price association must be parsed and tested before promotion.'
    }
    d.close()
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('generated_at','events_probed','events_with_pre_event_capture','events_with_visible_candidate_quotes','candidate_visible_quote_rows')},indent=2))

if __name__=='__main__':main()
