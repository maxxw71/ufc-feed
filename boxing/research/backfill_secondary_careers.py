#!/usr/bin/env python3
"""Backfill priced fighters absent from Wikipedia using a secondary public record.

Current source: Champinon fighter pages. A career is accepted only when:
1) the page identity normalizes to the requested fighter,
2) its main dated record table is substantially complete when a stated career
   total is available, and
3) at least one archived priced matchup matches exact full date + opponent.

Rows remain explicitly sourced as ``champinon`` and are never relabeled as
Wikipedia or treated as independently certified complete careers.
"""
from __future__ import annotations
import argparse,datetime as dt,json,re,sqlite3,time,unicodedata,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup
from dateutil.parser import parse as dateparse
from backfill_priced_careers import DB,OUT,nk,priced_missing,match_evidence

UA='Mozilla/5.0 AppwizaBoxingSecondaryCareer/1.0'

def slug(name):
    s=unicodedata.normalize('NFKD',str(name or '')).encode('ascii','ignore').decode().lower()
    s=re.sub(r"\bjr\.?$",'jr',s).strip()
    return re.sub(r'[^a-z0-9]+','-',s).strip('-')

def get(url,timeout=35):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=timeout) as r:return r.read()

def clean_opponent(text):
    s=re.sub(r'\s+',' ',str(text or '')).strip()
    # Record parentheticals on these pages follow the opponent name.
    s=re.sub(r'\s*\(\s*\d+\s*[-–−]\s*\d+(?:\s*[-–−]\s*\d+)?\s*\).*?$','',s).strip()
    return s

def parse_champinon(name):
    url=f'https://champinon.info/boxing/{slug(name)}/'
    soup=BeautifulSoup(get(url),'lxml')
    h1=soup.find('h1');title=h1.get_text(' ',strip=True) if h1 else ''
    if nk(title)!=nk(name):
        raise ValueError(f'identity heading mismatch: {title!r}')
    text=soup.get_text('\n',strip=True)
    total_match=re.search(r'has had\s+(\d+)\s+professional fights',text,re.I)
    stated_total=int(total_match.group(1)) if total_match else None
    born=None
    bm=re.search(r'Born on\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})',text)
    if bm:
        try:born=dateparse(bm.group(1)).date().isoformat()
        except Exception:born=None
    rows=[];seen=set()
    for table in soup.find_all('table'):
        trs=table.find_all('tr')
        if not trs:continue
        header=None;start=0
        for i,tr in enumerate(trs[:5]):
            hs=[re.sub(r'\s+',' ',c.get_text(' ',strip=True)).strip().casefold() for c in tr.find_all(['th','td'],recursive=False)]
            if all(x in hs for x in ('date','opponent','result')):
                header=hs;start=i+1;break
        if not header:continue
        di,oi,ri=header.index('date'),header.index('opponent'),header.index('result')
        for tr in trs[start:]:
            cells=tr.find_all(['th','td'],recursive=False)
            if max(di,oi,ri)>=len(cells):continue
            rawdate=cells[di].get_text(' ',strip=True)
            m=re.search(r'\b(20\d{2}|19\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b',rawdate)
            if m:date=f'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
            else:
                try:date=dateparse(rawdate,dayfirst=False,fuzzy=True).date().isoformat()
                except Exception:continue
            opponent=clean_opponent(cells[oi].get_text(' ',strip=True))
            result_text=re.sub(r'\s+',' ',cells[ri].get_text(' ',strip=True)).strip()
            low=result_text.casefold()
            if re.search(r'\bwin\b',low):result='win'
            elif re.search(r'\bloss\b|\blost\b',low):result='loss'
            elif re.search(r'\bdraw\b',low):result='draw'
            elif re.search(r'\bno contest\b|\bnc\b',low):result='nc'
            else:continue
            mm=re.search(r'\b(KO|TKO|UD|SD|MD|PTS|RTD|DQ|TD|NC)\b',result_text,re.I)
            method=mm.group(1).upper() if mm else ''
            key=(date,nk(opponent),result)
            if not opponent or key in seen:continue
            seen.add(key)
            rows.append({'date':date,'result':result,'opponent':opponent,'type':method,
                         'round_time':'','location':'','record':'',
                         'raw':{'date':rawdate,'opponent':opponent,'result':result_text,'source':'champinon'}})
    rows.sort(key=lambda r:r['date'])
    if stated_total is not None:
        # Allow a small discrepancy for very recent site updates, but reject
        # pages where only the losses/summary table was parsed.
        minimum=max(3,stated_total-2)
        if len(rows)<minimum:raise ValueError(f'partial career table {len(rows)}/{stated_total}')
    elif len(rows)<5:
        raise ValueError(f'insufficient career rows: {len(rows)}')
    return {'title':title,'url':url,'born':born,'profile':{},'rows':rows,'stated_total':stated_total}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--limit',type=int,default=60);args=ap.parse_args()
    con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
    OUT.parent.mkdir(parents=True,exist_ok=True)
    done={}
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            try:r=json.loads(line);done[nk(r['requested_name'])]=r
            except Exception:pass
    queue=[x for x in priced_missing(con) if nk(x['name']) not in done][:args.limit]
    accepted=[];failed=[]
    for i,item in enumerate(queue,1):
        try:
            page=parse_champinon(item['name']);matches=match_evidence(page,item['evidence'])
            if not matches:raise ValueError('no exact priced date+opponent evidence match')
            found={'requested_name':item['name'],'verified_title':page['title'],'source':'champinon','source_url':page['url'],
                   'born':page['born'],'profile':page['profile'],'career_rows':page['rows'],'matched_price_evidence':matches,
                   'priced_bouts':item['priced_bouts'],'bookmakers':item['bookmakers'],'discovery':'deterministic_champinon_slug',
                   'quality':'secondary_public_exact_priced_match_verified',
                   'verification':'exact identity heading + substantial dated career table + exact priced full-date/opponent matchup',
                   'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()}
            with OUT.open('a',encoding='utf-8') as f:f.write(json.dumps(found,ensure_ascii=False)+'\n')
            accepted.append({'name':item['name'],'rows':len(page['rows']),'matched':len(matches),'priced_bouts':item['priced_bouts']})
            print(i,item['name'],'OK',len(page['rows']),flush=True)
        except Exception as e:
            failed.append({'name':item['name'],'priced_bouts':item['priced_bouts'],'error':str(e)[:300]})
            print(i,item['name'],'NO_MATCH',str(e)[:160],flush=True)
        time.sleep(.35)
    report={'attempted':len(queue),'accepted':len(accepted),'failed':len(failed),'accepted_items':accepted,'failed_items':failed,
            'total_supplemental_fighters':len(done)+len(accepted),'source':'champinon'}
    (OUT.parent/'latest_secondary_backfill_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
