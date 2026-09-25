#!/usr/bin/env python3
"""Strictly validate historical ProBoxingOdds rows against pre-event Wayback HTML.

A validated row requires:
- snapshot timestamp strictly before event date;
- exact event URL;
- exact bookmaker column in the archived odds table;
- exact bout id embedded in ProBoxingOdds data-li;
- exact fighter/selection row text;
- archived displayed American odds convert back to the stored decimal price.
"""
from __future__ import annotations
import datetime as dt,json,os,re,sqlite3,unicodedata,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

DB=Path(os.environ.get('BOXING_DB','/home/anestishkurti92/boxing-research/boxing.sqlite3'))
PILOT=Path('boxing/public_reports/HISTORICAL_ODDS_WAYBACK_PILOT.json')
INDEX=Path('boxing/public_reports/HISTORICAL_ODDS_WAYBACK_INDEX.json')
OUT=Path('boxing/public_reports/HISTORICAL_ODDS_VALIDATION_REPORT.json')
VALID=Path('boxing/public_reports/HISTORICAL_ODDS_VALIDATED_ROWS.json')
UA='Mozilla/5.0 AppwizaHistoricalOddsValidator/1.0'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def american_to_decimal(s):
    m=re.search(r'([+-]\d+)',str(s or '').replace(',',''))
    if not m:return None
    a=int(m.group(1))
    if a>0:return 1+a/100
    if a<0:return 1+100/abs(a)
    return None

def wayback_timestamp(url):
    m=re.search(r'/web/(\d{14})',str(url or ''))
    return m.group(1) if m else None

def snapshot_variants(url):
    m=re.match(r'^(https://web\.archive\.org/web/)(\d{14})(?:[a-z_]+)?/(https?://)(www\.)?(.+)$',str(url or ''),re.I)
    if not m:return [url]
    base,stamp,scheme,www,rest=m.groups()
    originals=[]
    for sch in ('https://','http://'):
        for host in ('www.',''):
            originals.append(sch+host+rest)
    out=[]
    for orig in originals:
        for mod in ('id_',''):
            cand=f'{base}{stamp}{mod}/{orig}'
            if cand not in out:out.append(cand)
    return out

def fetch_exact_snapshot(url,requested_stamp):
    errors=[]
    for cand in snapshot_variants(url):
        try:
            req=urllib.request.Request(cand,headers={
              'User-Agent':'Mozilla/5.0 AppwizaHistoricalOddsStructure/1.0',
              'Accept-Language':'en-US,en;q=0.8'
            })
            with urllib.request.urlopen(req,timeout=15) as r:
                raw=r.read(4_000_001);final=r.geturl()
            if len(raw)>4_000_000:
                errors.append({'url':cand,'reason':'snapshot_too_large'});continue
            if wayback_timestamp(final)!=requested_stamp:
                errors.append({'url':cand,'final_url':final,'reason':'wayback_timestamp_drift'});continue
            if b'odds-table' not in raw.lower():
                errors.append({'url':cand,'final_url':final,'reason':'no_odds_table'});continue
            return final,raw,cand,errors
        except Exception as e:
            errors.append({'url':cand,'reason':type(e).__name__+': '+str(e)[:160]})
    raise RuntimeError('no exact Wayback replay: '+json.dumps(errors[:12],ensure_ascii=False))

def parse_snapshot(raw):
    soup=BeautifulSoup(raw,'lxml')
    out={}
    for table in soup.select('table.odds-table'):
        headers={}
        head=table.find('thead')
        if head:
            for th in head.find_all('th'):
                bid=th.get('data-b')
                if bid:
                    headers[str(bid)]=re.sub(r'\s+',' ',th.get_text(' ',strip=True)).strip()
        if not headers:continue
        for tr in table.find_all('tr'):
            namecell=tr.find('th',attrs={'scope':'row'})
            if not namecell:continue
            selection=re.sub(r'\s+',' ',namecell.get_text(' ',strip=True)).strip()
            if not selection:continue
            for td in tr.find_all('td'):
                rawli=str(td.get('data-li') or '')
                m=re.fullmatch(r'\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]',rawli)
                if not m:continue
                book_id,side,bout_id=m.groups()
                book=headers.get(book_id)
                if not book:continue
                odds=None
                for span in td.find_all('span'):
                    txt=span.get_text(' ',strip=True)
                    if re.fullmatch(r'[+-]\d+',txt):
                        odds=txt;break
                if odds is None:
                    txt=td.get_text(' ',strip=True)
                    mm=re.search(r'(?<!\d)([+-]\d+)(?!\d)',txt)
                    odds=mm.group(1) if mm else None
                if odds:
                    out[(bout_id,nk(book),nk(selection))]={
                      'bout_id':bout_id,'bookmaker':book,'selection':selection,
                      'side':int(side),'american_price':odds,
                      'decimal_from_archive':american_to_decimal(odds),
                      'data_li':rawli
                    }
    return out

def candidate_events():
    if INDEX.exists():
        obj=json.loads(INDEX.read_text())
        out=[]
        for e in obj.get('events',[]):
            caps=[]
            for c in e.get('captures',[]):
                stamp=str(c.get('timestamp') or '')
                snap=str(c.get('snapshot_url') or '')
                if len(stamp)>=14 and snap:
                    caps.append({'timestamp':stamp,'snapshot_url':snap,'original':c.get('original'),'digest':c.get('digest')})
            if caps:
                out.append({'event_date':e.get('event_date'),'event_url':e.get('event_url'),'captures':caps,'source':'wayback_index'})
        return out
    pilot=json.loads(PILOT.read_text())
    out=[]
    for e in pilot.get('events',[]):
        stamp=str(e.get('latest_pre_event_timestamp') or '')
        snap=str(e.get('snapshot') or '')
        if e.get('pre_event_captures') and stamp and snap:
            out.append({
              'event_date':e.get('event_date'),'event_url':e.get('event_url'),
              'captures':[{'timestamp':stamp,'snapshot_url':snap,'digest':e.get('digest')}],
              'source':'wayback_pilot'
            })
    return out

def main():
    events=candidate_events()
    d=sqlite3.connect(f'file:{DB}?mode=ro',uri=True,timeout=120);d.row_factory=sqlite3.Row
    validated=[];audits=[];events_with_stored_quotes=0
    for event in events:
        event_date=str(event.get('event_date') or '')
        event_url=str(event.get('event_url') or '')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',event_date) or not event_url:
            continue
        qrows=[dict(r) for r in d.execute(
          "select rowid as quote_rowid,* from odds where source='proboxingodds' and url=? order by rowid",
          (event_url,))]
        if not qrows:
            continue
        events_with_stored_quotes+=1

        parsed=None;chosen=None;replay_log=[]
        for cap in event.get('captures',[]):
            stamp=str(cap.get('timestamp') or '')
            snapshot=str(cap.get('snapshot_url') or '')
            if len(stamp)<14 or stamp[:8]>=event_date.replace('-',''):
                replay_log.append({'timestamp':stamp,'snapshot_url':snapshot,'status':'rejected_non_pre_event'})
                continue
            try:
                final,raw,used_snapshot,prior=fetch_exact_snapshot(snapshot,stamp)
                cells=parse_snapshot(raw)
                attempt={
                  'timestamp':stamp,'snapshot_url':snapshot,'snapshot_used_url':used_snapshot,
                  'snapshot_final_url':final,'archive_market_cells':len(cells),
                  'replay_attempts_before_success':prior
                }
                replay_log.append(attempt)
                if cells:
                    parsed=cells
                    chosen=attempt
                    break
            except Exception as e:
                replay_log.append({
                  'timestamp':stamp,'snapshot_url':snapshot,'status':'fetch_or_parse_error',
                  'error':type(e).__name__+': '+str(e)[:500]
                })
        if not parsed or not chosen:
            audits.append({
              'event_date':event_date,'event_url':event_url,'stored_quote_rows':len(qrows),
              'status':'no_exact_pre_event_market_replay','capture_attempts':replay_log
            })
            continue

        checks=[]
        for q in qrows:
            key=(str(q.get('bout_id') or ''),nk(q.get('bookmaker')),nk(q.get('selection')))
            arc=parsed.get(key)
            check={
              'quote_rowid':q.get('quote_rowid'),'bout_id':q.get('bout_id'),
              'bookmaker':q.get('bookmaker'),'selection':q.get('selection'),
              'stored_decimal_price':q.get('decimal_price'),'archive_match':arc
            }
            if arc and arc.get('decimal_from_archive') is not None and q.get('decimal_price') is not None:
                diff=abs(float(q['decimal_price'])-float(arc['decimal_from_archive']))
                check['decimal_abs_diff']=round(diff,8)
                if diff<=0.005:
                    v={
                      'quote_rowid':q['quote_rowid'],'bout_id':str(q.get('bout_id') or ''),
                      'bookmaker':q.get('bookmaker'),'selection':q.get('selection'),
                      'stored_decimal_price':float(q['decimal_price']),
                      'archived_american_price':arc['american_price'],
                      'archived_decimal_price':round(float(arc['decimal_from_archive']),6),
                      'event_date':event_date,'event_url':event_url,
                      'snapshot_timestamp':chosen['timestamp'],
                      'snapshot_url':chosen['snapshot_used_url'],
                      'verification':'exact_pre_event_archive_event_bookmaker_bout_selection_price_match'
                    }
                    validated.append(v);check['validated']=True
                else:
                    check['validated']=False
            else:
                check['validated']=False
            checks.append(check)
        audits.append({
          'event_date':event_date,'event_url':event_url,
          'snapshot_timestamp':chosen['timestamp'],
          'snapshot_url':chosen['snapshot_used_url'],
          'snapshot_final_url':chosen['snapshot_final_url'],
          'archive_market_cells':len(parsed),'stored_quote_rows':len(qrows),
          'validated_rows':sum(bool(x.get('validated')) for x in checks),
          'capture_attempts':replay_log,'checks':checks
        })
    d.close()

    uniq={str(x['quote_rowid']):x for x in validated}
    validated=list(uniq.values())
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'candidate_events':len(events),
      'events_with_stored_quotes':events_with_stored_quotes,
      'validated_price_rows':len(validated),
      'distinct_validated_bouts':len({x['bout_id'] for x in validated}),
      'distinct_validated_events':len({x['event_url'] for x in validated}),
      'validated_rows':validated,'event_audits':audits,
      'policy':'Validation requires an exact pre-event Wayback snapshot plus exact archived event/bookmaker/bout/selection cell and price equivalence after American-to-decimal conversion (<=0.005 absolute difference). Newest eligible capture is attempted first; older captures may be used only if their exact Wayback timestamp is preserved.'
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    VALID.write_text(json.dumps({'generated_at':report['generated_at'],'rows':validated,'policy':report['policy']},indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:report[k] for k in (
      'candidate_events','events_with_stored_quotes','validated_price_rows','distinct_validated_bouts','distinct_validated_events'
    )},indent=2))

if __name__=='__main__':main()
