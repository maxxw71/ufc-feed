#!/usr/bin/env python3
"""Prospectively snapshot upcoming ProBoxingOdds moneylines.

Unlike the historical archive collector, every row here receives an actual UTC
fetch timestamp. Future-date snapshots are therefore auditable pre-event price
observations. Same-day observations remain separately flagged unless event time
is independently verified.
"""
from __future__ import annotations
import datetime as dt,hashlib,json,re,subprocess,time,urllib.request,unicodedata
from pathlib import Path
from bs4 import BeautifulSoup
BASE='https://www.proboxingodds.com/'
ROOT=Path(__file__).resolve().parents[1]/'prospective_odds'
MONTHS={m:i for i,m in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'],1)}

def norm(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+',' ',x).strip()

def american_to_decimal(v):
    n=int(v)
    return 1+n/100 if n>0 else 1+100/abs(n)

def infer_event_date(label,now):
    m=re.search(r'\b('+'|'.join(MONTHS)+r')\s+(\d{1,2})(?:st|nd|rd|th)?\b',label,re.I)
    if not m:return None
    month=MONTHS[m.group(1).title()];day=int(m.group(2));cand=dt.date(now.year,month,day)
    if cand < now.date()-dt.timedelta(days=180):cand=dt.date(now.year+1,month,day)
    elif cand > now.date()+dt.timedelta(days=180):cand=dt.date(now.year-1,month,day)
    return cand.isoformat()

def heading_for(table):
    node=table
    for _ in range(30):
        node=node.find_previous()
        if node is None:break
        if getattr(node,'name',None) in ('h1','h2','h3','h4','h5'):
            text=node.get_text(' ',strip=True)
            if re.search(r'Boxing Odds|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b',text,re.I):
                return text
    return ''

def parse_home(html,now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    soup=BeautifulSoup(html,'lxml')
    rows=[]
    for table in soup.select('table.odds-table'):
        label=heading_for(table);event_date=infer_event_date(label,now)
        books={int(c['data-b']):c.get_text(' ',strip=True) for c in table.select('thead th[data-b]')}
        if not books:continue
        participants={}
        quotes=[]
        for tr in table.select('tbody tr'):
            if 'pr' in (tr.get('class') or []):continue
            a=tr.select_one('th a[href^="/fighters/"]')
            if not a:continue
            fighter=a.get_text(' ',strip=True)
            for td in tr.select('td.but-sg[data-li]'):
                try:ids=json.loads(td['data-li'])
                except Exception:continue
                if len(ids)!=3:continue
                book_id,side,bout=ids;book=books.get(book_id)
                participants.setdefault(str(bout),{})[str(side)]=fighter
                val=td.select_one('span[id^="oID"]')
                if not book or not val:continue
                raw=val.get_text(strip=True).replace('−','-')
                if not re.fullmatch(r'[+-]\d+',raw):continue
                american=int(raw)
                if abs(american)<100:continue
                quotes.append((str(bout),str(side),fighter,book,american,american_to_decimal(american)))
        for bout,side,fighter,book,american,decimal in quotes:
            names=participants.get(bout,{})
            if len(set(names.values()))!=2:continue
            market_class='prediction_market' if book.casefold() in {'polymarket','kalshi'} else 'sportsbook'
            timing='verified_pre_event_date' if event_date and event_date>now.date().isoformat() else 'same_day_or_date_unresolved'
            rows.append({'bout_id':bout,'event_date':event_date,'event_label':label,'selection':fighter,
                         'participants':names,'bookmaker':book,'market_class':market_class,
                         'american_price':american,'decimal_price':decimal,'timing_quality':timing})
    return rows

def fetch():
    ua='Mozilla/5.0 AppwizaProspectiveBoxing/1.1'
    last=None
    for attempt in range(3):
        try:
            # curl has proved more reliable than urllib against this host from cloud runners.
            p=subprocess.run(['curl','-4','--http1.1','-L','--compressed','--fail','--silent','--show-error',
                              '--connect-timeout','20','--max-time','180','--retry','2','--retry-delay','3',
                              '-A',ua,BASE],capture_output=True,timeout=200)
            if p.returncode==0 and p.stdout:return p.stdout
            last=RuntimeError(p.stderr.decode('utf-8','replace') or f'curl exit {p.returncode}')
        except Exception as e:last=e
        time.sleep(4*(attempt+1))
    # Final urllib fallback for non-curl environments.
    try:
        req=urllib.request.Request(BASE,headers={'User-Agent':ua,'Connection':'close'})
        with urllib.request.urlopen(req,timeout=180) as r:return r.read()
    except Exception as e:
        raise RuntimeError(f'PBO fetch failed after retries: {last!r}; urllib: {e!r}')

def main():
    now=dt.datetime.now(dt.timezone.utc);raw=fetch();sha=hashlib.sha256(raw).hexdigest()
    quotes=parse_home(raw,now)
    snap={'fetched_at':now.isoformat(),'source_url':BASE,'raw_sha256':sha,
          'quote_rows':len(quotes),'future_date_rows':sum(q['timing_quality']=='verified_pre_event_date' for q in quotes),
          'sportsbook_rows':sum(q['market_class']=='sportsbook' for q in quotes),'quotes':quotes}
    ROOT.mkdir(parents=True,exist_ok=True)
    day=ROOT/(now.date().isoformat()+'.jsonl')
    with day.open('a',encoding='utf-8') as f:f.write(json.dumps(snap,separators=(',',':'),ensure_ascii=False)+'\n')
    (ROOT/'latest.json').write_text(json.dumps(snap,indent=2,ensure_ascii=False))
    print(json.dumps({k:snap[k] for k in ['fetched_at','raw_sha256','quote_rows','future_date_rows','sportsbook_rows']},indent=2))
if __name__=='__main__':main()
