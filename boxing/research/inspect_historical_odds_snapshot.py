#!/usr/bin/env python3
"""Inspect DOM association for the positive historical ProBoxingOdds Wayback pilot.

Read-only. It determines whether selection, bookmaker, and stored price occur in
the same compact DOM row/card. No quote is promoted by this script.
"""
from __future__ import annotations
import datetime as dt,json,re,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup,Tag

PILOT=Path('boxing/public_reports/HISTORICAL_ODDS_WAYBACK_PILOT.json')
OUT=Path('boxing/public_reports/HISTORICAL_ODDS_WAYBACK_STRUCTURE_AUDIT.json')
UA='Mozilla/5.0 AppwizaHistoricalOddsStructure/1.0'

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=60) as r:
        raw=r.read(4_000_001)
        if len(raw)>4_000_000:raise ValueError('snapshot too large')
        return r.geturl(),raw

def clean(x):return re.sub(r'\s+',' ',str(x or '')).strip()

def tokens(price):
    p=float(price)
    vals={f'{p:g}',f'{p:.2f}',f'{p:.3f}'}
    return sorted({v.rstrip('0').rstrip('.') if '.' in v else v for v in vals if v})

def candidate_ancestors(soup,selection,book,price):
    want=tokens(price)
    hits=[]
    nodes=soup.find_all(string=lambda s:s and selection.casefold() in clean(s).casefold())
    for node in nodes[:40]:
        cur=node.parent
        for depth in range(0,9):
            if not isinstance(cur,Tag):break
            txt=clean(cur.get_text(' ',strip=True))
            low=txt.casefold()
            has_sel=selection.casefold() in low
            has_book=book.casefold() in low
            found=[t for t in want if re.search(r'(?<![0-9.])'+re.escape(t)+r'(?![0-9.])',txt)]
            if has_sel and has_book and found:
                hits.append({
                  'depth':depth,'tag':cur.name,'id':cur.get('id'),
                  'class':cur.get('class'),'matched_price_tokens':found,
                  'text_len':len(txt),'text_sample':txt[:1400],
                  'html_sample':str(cur)[:2500]
                })
                break
            cur=cur.parent
    # Prefer the smallest textual container.
    hits.sort(key=lambda x:(x['text_len'],x['depth']))
    return hits[:10]

def main():
    pilot=json.loads(PILOT.read_text())
    events=[x for x in pilot.get('events',[]) if x.get('candidate_exact_visible_quote_rows')]
    out=[]
    for event in events:
        item={k:event.get(k) for k in ('event_date','event_url','snapshot','latest_pre_event_timestamp')}
        try:
            final,raw=fetch(event['snapshot']);soup=BeautifulSoup(raw,'lxml')
            item['final_url']=final;item['page_bytes']=len(raw)
            checks=[]
            for q in event['candidate_exact_visible_quote_rows']:
                anc=candidate_ancestors(soup,q['selection'],q['bookmaker'],q['decimal_price'])
                compact=[a for a in anc if a['text_len']<=1200]
                checks.append({
                  'quote_rowid':q['quote_rowid'],'bout_id':q['bout_id'],'bookmaker':q['bookmaker'],
                  'selection':q['selection'],'decimal_price':q['decimal_price'],
                  'candidate_containers':anc,
                  'compact_container_count':len(compact),
                  'smallest_container':anc[0] if anc else None
                })
            item['quotes']=checks
        except Exception as e:item['error']=type(e).__name__+': '+str(e)[:240]
        out.append(item)
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'events':out,
      'policy':'Inspection only. Promotion requires a compact row/card that structurally associates the exact selection, bookmaker, and stored price on a snapshot captured before event date.'
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({
      'events':len(out),
      'quotes':sum(len(x.get('quotes',[])) for x in out),
      'quotes_with_compact_container':sum(any(a.get('text_len',999999)<=1200 for a in q.get('candidate_containers',[])) for x in out for q in x.get('quotes',[]))
    },indent=2))

if __name__=='__main__':main()
