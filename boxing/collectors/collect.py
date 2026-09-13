"""Resumable public boxing archive, with source observations kept separate.

Run on appwiza: python collect.py --pbc-pages 20
No betting, email, or existing scanner changes. Prices never inferred from outcomes.
"""
import argparse, csv, datetime as dt, gzip, hashlib, io, json, re, sqlite3, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import public_feed_ipv6
public_feed_ipv6.HOSTS.update({'www.premierboxingchampions.com','raw.githubusercontent.com'})
public_feed_ipv6.enable()
ROOT=Path(__file__).resolve().parent
(ROOT/'raw').mkdir(exist_ok=True)
db=sqlite3.connect(ROOT/'boxing.sqlite3',timeout=120)
db.executescript('''
CREATE TABLE IF NOT EXISTS captures(url TEXT PRIMARY KEY, fetched_at TEXT, sha256 TEXT, path TEXT);
CREATE TABLE IF NOT EXISTS failures(url TEXT PRIMARY KEY, error TEXT, attempted_at TEXT);
CREATE TABLE IF NOT EXISTS source_rows(source TEXT, kind TEXT, source_id TEXT, data TEXT, PRIMARY KEY(source,kind,source_id));
CREATE TABLE IF NOT EXISTS fighters(source TEXT, source_id TEXT, name TEXT, born TEXT, height_cm REAL, snapshot TEXT, PRIMARY KEY(source,source_id));
CREATE TABLE IF NOT EXISTS bouts(source TEXT, source_id TEXT, date TEXT, boxer_a TEXT, boxer_b TEXT, winner TEXT, method TEXT, rounds TEXT, scheduled_rounds TEXT, division TEXT, venue TEXT, status TEXT, url TEXT, data TEXT, PRIMARY KEY(source,source_id));
CREATE TABLE IF NOT EXISTS odds(source TEXT, bout_id TEXT, bookmaker TEXT, selection TEXT, decimal_price REAL CHECK(decimal_price>1), quoted_at TEXT, timestamp_quality TEXT, url TEXT);
''')
def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def fetch(url):
    cached=db.execute('SELECT path FROM captures WHERE url=?',(url,)).fetchone()
    if cached and (ROOT/cached[0]).exists(): return gzip.decompress((ROOT/cached[0]).read_bytes()).decode('utf-8-sig')
    time.sleep(0.6)
    req=Request(url,headers={'User-Agent':'BoxingHistoryResearch/1.0 (public archive; conservative rate)','Accept-Encoding':'identity'})
    with urlopen(req,timeout=25) as response:
        raw=response.read(15_000_001)
    if len(raw)>15_000_000: raise ValueError('response too large')
    if raw[:2]==b'\x1f\x8b': raw=gzip.decompress(raw)
    digest=hashlib.sha256(raw).hexdigest(); path='raw/'+digest+'.gz'
    (ROOT/path).write_bytes(gzip.compress(raw))
    db.execute('INSERT OR REPLACE INTO captures VALUES(?,?,?,?)',(url,now(),digest,path));db.commit()
    return raw.decode('utf-8-sig')
def fail(url,e):
    db.execute('INSERT OR REPLACE INTO failures VALUES(?,?,?)',(url,str(e),now()));db.commit();print('FAILED',url,str(e),flush=True)
def putbout(values):
    db.execute('INSERT OR REPLACE INTO bouts VALUES('+','.join('?'*14)+')',values)
def openboxing():
    base='https://raw.githubusercontent.com/edhwright/open-boxing/main/src/db/data/'
    for filename in ['champions.csv','bouts.csv','locations.csv','references.csv','reigns.csv','titles.csv']:
        url=base+filename
        try:
            rows=list(csv.DictReader(io.StringIO(fetch(url))))
            for i,r in enumerate(rows):
                db.execute('INSERT OR REPLACE INTO source_rows VALUES(?,?,?,?)',('openboxing',filename,str(i),json.dumps(r)))
                if filename=='champions.csv':
                    db.execute('INSERT OR REPLACE INTO fighters VALUES(?,?,?,?,?,?)',('openboxing',r['champion_id'],r['first_name']+' '+r['last_name'],r['born'],None,json.dumps(r)))
                if filename=='bouts.csv':
                    putbout(('openboxing',r['bout_id'],r['date'],r['boxer_a_name'],r['boxer_b_name'],r['winner'],r['method_of_victory'],r['total_rounds'],r['scheduled_rounds'],r['weight_class'],r['location_id'],r['status'],url,json.dumps(r)))
            db.commit(); print(filename,len(rows),flush=True)
        except Exception as e: fail(url,e)
def objects(value):
    if isinstance(value,list):
        for x in value: yield from objects(x)
    elif isinstance(value,dict):
        yield value
        if '@graph' in value: yield from objects(value['@graph'])
def pbc(maxpages):
    for year in range(2015,dt.date.today().year+1):
        url='https://www.premierboxingchampions.com/past-boxing-fights/'+str(year)
        seen=set()
        for page in range(maxpages):
            if url in seen: break
            seen.add(url)
            try:
                soup=BeautifulSoup(fetch(url),'html.parser'); events={}
                for script in soup.select('script[type="application/ld+json"]'):
                    try:
                        for obj in objects(json.loads(script.get_text())):
                            if obj.get('@type')=='SportsEvent' and len(obj.get('performer',[]))==2: events[obj['url']]=obj
                    except (ValueError,KeyError): pass
                for link,event in events.items():
                    a,b=event['performer']; names=[x.get('givenName','')+' '+x.get('familyName','') for x in [a,b]]
                    if not all(x.strip() for x in names): continue
                    for person,name in zip([a,b],names):
                        db.execute('INSERT OR REPLACE INTO fighters VALUES(?,?,?,?,?,?)',('pbc',person.get('url',name),name,person.get('birthDate','')[:10],person.get('height',{}).get('value'),json.dumps(person)))
                    db.commit()
                    winner=method=''; status='UNVERIFIED'; result=''
                    try:
                        detail=BeautifulSoup(fetch(link),'html.parser')
                        for span in detail.find_all('span'):
                            text=span.get_text(' ',strip=True)
                            if re.fullmatch(r'.{1,80} wins by .{1,80}',text,re.I):
                                result=text; who,method=re.split(' wins by ',text,flags=re.I,maxsplit=1)
                                matches=[i for i,p in enumerate([a,b]) if who.casefold() in [names[i].casefold(),p.get('familyName','').casefold()]]
                                if len(matches)==1: winner=['BOXER A','BOXER B'][matches[0]];status='FINISHED'
                                break
                    except Exception as e: fail(link,e)
                    event['parsed_result_text']=result
                    putbout(('pbc',link,event.get('startDate','')[:10],names[0].strip(),names[1].strip(),winner,method,'','','',event.get('location',{}).get('name',''),status,link,json.dumps(event)))
                db.commit();report(); print('PBC',year,page,len(events),flush=True)
                nextlink=soup.select_one('li.pager-next a, a[rel="next"]')
                if not nextlink: break
                url=urljoin(url,nextlink['href'])
            except Exception as e: fail(url,e);break
def report():
    sources=[]
    for source,n,lo,hi,finished in db.execute("SELECT source,count(*),min(date),max(date),sum(status='FINISHED') FROM bouts GROUP BY source"):
        sources.append(dict(source=source,bout_observations=n,first_date=lo,last_date=hi,verified_result_rows=finished))
    report={'updated_at':now(),'sources':sources,'fighter_source_profiles':db.execute('SELECT count(*) FROM fighters').fetchone()[0], 'odds_rows':db.execute('SELECT count(*) FROM odds').fetchone()[0], 'by_year':list(db.execute('SELECT source,substr(date,1,4),count(*) FROM bouts GROUP BY 1,2 ORDER BY 2,1')), 'failures':list(db.execute('SELECT * FROM failures')),'limitations':['Source observations may overlap; counts are not globally deduplicated bouts.','Open Boxing is title-focused and ends in 2023, not complete careers.','PBC biographies are current snapshots; only stable identity attributes may inform historical features after validation.','PBC results parsed conservatively; unresolved rows are not treated as wins/losses.','No historical ROI until genuine bookmaker prices and market settlement rules are joined.','Past-page schema eventStatus is unreliable; result text is required.']}
    (ROOT/'coverage.json').write_text(json.dumps(report,indent=2))
    lines=['# Boxing archive coverage',report['updated_at'],'','| Source | Observations | Earliest | Latest | Result rows |','|---|---:|---|---|---:|']
    for s in sources: lines.append('| {source} | {bout_observations} | {first_date} | {last_date} | {verified_result_rows} |'.format(**s))
    lines+=['',f"Fighter source profiles: {report['fighter_source_profiles']}. Historical odds rows: {report['odds_rows']}.",'']+['- '+x for x in report['limitations']]
    (ROOT/'COVERAGE.md').write_text('\n'.join(lines))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--pbc-pages',type=int,default=0);args=parser.parse_args()
    openboxing();report()
    if args.pbc_pages:pbc(args.pbc_pages)
    report()
