#!/usr/bin/env python3
"""Collect published secondary boxing punch evidence without promoting it to primary data.

Discovery follows only links published by the source pages themselves. No numeric
endpoint guessing. Numeric rows remain explicitly secondary/unverified until a
separate identity/date review promotes them for research use.
"""
from __future__ import annotations
import argparse,datetime as dt,gzip,hashlib,json,re,sqlite3,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
DB=ROOT/'boxing.sqlite3'
c=sqlite3.connect(DB,timeout=120);c.row_factory=sqlite3.Row
c.executescript('''
CREATE TABLE IF NOT EXISTS round_evidence_pages(
 url TEXT PRIMARY KEY,fetched_at TEXT,sha256 TEXT,raw_path TEXT,status TEXT,
 title TEXT,tables_json TEXT,links_json TEXT,error TEXT);
CREATE TABLE IF NOT EXISTS secondary_round_punches(
 source_url TEXT,table_index INTEGER,fighter_label TEXT,round INTEGER,
 category TEXT,landed INTEGER,thrown INTEGER,quality TEXT,
 PRIMARY KEY(source_url,table_index,fighter_label,round,category));
CREATE TABLE IF NOT EXISTS secondary_fight_punch_totals(
 source_url TEXT,table_index INTEGER,fighter_label TEXT,category TEXT,
 landed INTEGER,thrown INTEGER,quality TEXT,
 PRIMARY KEY(source_url,table_index,fighter_label,category));
''')
OUT=ROOT/'round_archive';OUT.mkdir(exist_ok=True)
UA='BoxingHistoryResearch/1.3; public personal research'
ROOT_PAGES=['https://boxingblotter.com/','https://boxingblotter.com/programs/']

def fetch(url):
    prev=c.execute('SELECT raw_path FROM round_evidence_pages WHERE url=?',(url,)).fetchone()
    if prev and prev[0] and (ROOT/prev[0]).exists():
        return gzip.decompress((ROOT/prev[0]).read_bytes())
    time.sleep(.6)
    with urlopen(Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'}),timeout=25) as r:
        raw=r.read(12_000_001)
    if len(raw)>12_000_000:raise ValueError('Page exceeds size limit')
    return raw

def clean(s):return re.sub(r'\s+',' ',str(s or '')).strip()
def pair(cell):
    m=re.search(r'(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)',clean(cell))
    if not m:return None
    a,b=map(int,m.groups())
    if not 0<=a<=b:raise ValueError('Landed exceeds thrown')
    return a,b

def category(label):
    x=clean(label).casefold()
    if 'power' in x:return 'power'
    if 'jab' in x:return 'jab'
    if 'total' in x or 'punch' in x:return 'total'
    return 'unspecified'

def extract_tables(url,soup):
    all_tables=[];round_rows=[];fight_totals=[]
    for ti,t in enumerate(soup.find_all('table')):
        rows=[[clean(x.get_text(' ',strip=True)) for x in r.find_all(['th','td'],recursive=False)]
              for r in t.find_all('tr') if r.find_parent('table') is t]
        all_tables.append(rows)
        if not rows:continue
        header=rows[0]
        if header and re.fullmatch(r'round|rd\.?|rnd\.?',header[0],re.I):
            for row in rows[1:]:
                if len(row)!=len(header):continue
                rn=re.match(r'^(\d{1,2})(?:\s|$)',row[0])
                if not rn or not 1<=int(rn[1])<=15:continue
                for i,cell in enumerate(row[1:],1):
                    p=pair(cell)
                    if not p:continue
                    label=re.sub(r'\([^)]*\)','',header[i]).strip()
                    round_rows.append((url,ti,label,int(rn[1]),category(header[i]),p[0],p[1],
                                       'secondary_published_counts_unverified'))
            continue
        lower=[h.casefold() for h in header]
        fighter_i=next((i for i,h in enumerate(lower) if h in {'fighter','boxer','name'} or 'fighter' in h),None)
        stat_cols=[(i,category(h)) for i,h in enumerate(header)
                   if category(h) in {'total','jab','power'} and ('landed' in h.casefold() or 'thrown' in h.casefold() or '/' in h)]
        if fighter_i is not None and stat_cols:
            for row in rows[1:]:
                if len(row)!=len(header):continue
                fighter=clean(row[fighter_i])
                if not fighter:continue
                for i,cat in stat_cols:
                    p=pair(row[i])
                    if p:fight_totals.append((url,ti,fighter,cat,p[0],p[1],
                                              'secondary_published_fight_total_unverified'))
    return all_tables,round_rows,fight_totals

def discover_programs(limit):
    urls=[];seen_urls=set();visited_pages=set();queued=set(ROOT_PAGES);queue=list(ROOT_PAGES)
    while queue and len(urls)<limit:
        page=queue.pop(0);queued.discard(page)
        if page in visited_pages:continue
        visited_pages.add(page)
        try:raw=fetch(page);s=BeautifulSoup(raw,'lxml')
        except Exception as e:
            print('DISCOVERY_FAIL',page,str(e),flush=True);continue
        for a in s.find_all('a',href=True):
            u=urljoin(page,a['href']);p=urlsplit(u)
            if p.hostname!='boxingblotter.com':continue
            path=p.path.rstrip('/')
            if path.startswith('/programs/') and path!='/programs':
                label=(a.get_text(' ',strip=True)+' '+path).casefold()
                if 'recap' in label and u not in seen_urls:
                    seen_urls.add(u);urls.append(u)
                    if len(urls)>=limit:break
            if path in {'/programs','/archive','/programs/archive'} and u not in visited_pages and u not in queued:
                queue.append(u);queued.add(u)
    print('DISCOVERY_INDEX_PAGES',len(visited_pages),sorted(visited_pages),flush=True)
    return urls[:limit]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--pages',type=int,default=120);args=ap.parse_args()
    urls=discover_programs(args.pages)
    print('DISCOVERED_RECAP_PAGES',len(urls),flush=True)
    attempted=0
    for url in urls:
        try:
            raw=fetch(url);sha=hashlib.sha256(raw).hexdigest();path='round_archive/'+sha+'.html.gz'
            (ROOT/path).write_bytes(gzip.compress(raw));soup=BeautifulSoup(raw,'lxml')
            tables,round_rows,totals=extract_tables(url,soup)
            links=[urljoin(url,a['href']) for a in soup.find_all('a',href=True)
                   if 'compubox' in a['href'].casefold() or 'round-stats' in a['href'].casefold()]
            title=soup.find('h1') or soup.title
            payload={'tables':tables,
                     'images':[dict(im.attrs) for im in soup.find_all('img') if any(w in str(im).casefold() for w in ['compubox','punch','stats'])],
                     'date_claims':[m.get('content') for m in soup.select('meta[property="article:published_time"],meta[name="date"]')],
                     'note':'Publication/page claims only; bout date and fighter identity require separate review.'}
            c.execute('DELETE FROM secondary_round_punches WHERE source_url=?',(url,))
            c.execute('DELETE FROM secondary_fight_punch_totals WHERE source_url=?',(url,))
            c.executemany('INSERT OR REPLACE INTO secondary_round_punches VALUES(?,?,?,?,?,?,?,?)',round_rows)
            c.executemany('INSERT OR REPLACE INTO secondary_fight_punch_totals VALUES(?,?,?,?,?,?,?)',totals)
            status='tables_extracted' if round_rows or totals else 'needs_review'
            c.execute('INSERT OR REPLACE INTO round_evidence_pages VALUES(?,?,?,?,?,?,?,?,?)',
                      (url,dt.datetime.now(dt.timezone.utc).isoformat(),sha,path,status,
                       title.get_text(' ',strip=True) if title else '',json.dumps(payload),json.dumps(links),None))
            c.commit();attempted+=1
            print(json.dumps({'url':url,'round_rows':len(round_rows),'fight_total_rows':len(totals),'tables':len(tables)}),flush=True)
        except Exception as e:
            c.rollback();c.execute('INSERT OR REPLACE INTO round_evidence_pages(url,status,error) VALUES(?,?,?)',(url,'failed',str(e)));c.commit()
            print('FAILED',url,str(e),flush=True)
            if getattr(e,'code',None) in [403,429]:break
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'discovered_recap_pages':len(urls),'attempted':attempted,
            'secondary_pages':c.execute('SELECT count(*) FROM round_evidence_pages').fetchone()[0],
            'secondary_round_rows':c.execute('SELECT count(*) FROM secondary_round_punches').fetchone()[0],
            'secondary_fight_total_rows':c.execute('SELECT count(*) FROM secondary_fight_punch_totals').fetchone()[0],
            'secondary_pages_with_numeric_rows':c.execute('''SELECT count(DISTINCT source_url) FROM (
                SELECT source_url FROM secondary_round_punches UNION ALL
                SELECT source_url FROM secondary_fight_punch_totals)''').fetchone()[0],
            'limitations':['Secondary counts are not independently verified or promoted into betting features.',
                           'Publication dates are not automatically treated as bout dates.',
                           'No missing counts or punch categories are inferred.',
                           'No round winners are inferred from punch counts.']}
    (OUT/'secondary_coverage.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)

if __name__=='__main__':main()
