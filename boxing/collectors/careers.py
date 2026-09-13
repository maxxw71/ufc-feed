"""Professional record observations; no exhibitions or current records backcast."""
import re,json,argparse,subprocess,sys
from urllib.parse import quote,urljoin,urlsplit,unquote
from bs4 import BeautifulSoup
from dateutil.parser import parse
from collect import db,fetch,fail,putbout,report,public_feed_ipv6
from wiki_record_tables import record_tables,record_date
public_feed_ipv6.HOSTS.add('en.wikipedia.org')
try:
    import lxml
    html_parser='lxml'
except ImportError:html_parser='html.parser'
SEEDS=['John L. Sullivan','James J. Corbett','Bob Fitzsimmons','Jack Johnson (boxer)','Jack Dempsey','Joe Louis','Sugar Ray Robinson','Rocky Marciano','Muhammad Ali','Joe Frazier','George Foreman','Larry Holmes','Roberto Durán','Sugar Ray Leonard','Marvin Hagler','Thomas Hearns','Mike Tyson','Evander Holyfield','Lennox Lewis','Oscar De La Hoya','Floyd Mayweather Jr.','Manny Pacquiao','Canelo Álvarez','Gennady Golovkin','Terence Crawford','Oleksandr Usyk','Anthony Joshua','Tyson Fury','Naoya Inoue','Dmitry Bivol']
SEEDS += ['Katie Taylor','Amanda Serrano','Claressa Shields','Cecilia Brækhus','Christy Martin','Laila Ali','Lucia Rijker','Mia St. John','Chantelle Cameron','Jessica McCaskill','Savannah Marshall','Alycia Baumgardner','Devin Haney','Ryan Garcia','Gervonta Davis','Shakur Stevenson','David Benavidez','Artur Beterbiev','Jesse Rodriguez','Junto Nakatani','Kenshiro Teraji','Kazuto Ioka','Román González','Juan Francisco Estrada','Srisaket Sor Rungvisai','Chris Eubank','Nigel Benn','Ricky Hatton','Joe Calzaghe','Carl Froch','Naseem Hamed','Wladimir Klitschko','Vitali Klitschko','Sergio Martínez','Miguel Cotto','Juan Manuel Márquez','Erik Morales','Marco Antonio Barrera','Bernard Hopkins','Roy Jones Jr.','James Toney','Hasim Rahman','David Tua','Micky Ward','Arturo Gatti','Emanuel Augustus','Darnell Boone','Gabe Rosado','Derek Chisora']
args=argparse.ArgumentParser();args.add_argument('--max-pages',type=int,default=600);limit=args.parse_args().max_pages
db.execute('PRAGMA busy_timeout=120000')
db.execute('CREATE TABLE IF NOT EXISTS career_queue(url TEXT PRIMARY KEY,name TEXT,status TEXT,rows INTEGER)')
db.execute('CREATE TABLE IF NOT EXISTS career_priority(url TEXT PRIMARY KEY,priority INTEGER,reason TEXT)')
db.execute('CREATE TABLE IF NOT EXISTS career_row_issues(url TEXT,row_key TEXT,raw_date TEXT,reason TEXT,PRIMARY KEY(url,row_key))')
for name in SEEDS:
    db.execute('INSERT OR IGNORE INTO career_queue VALUES(?,?,?,NULL)',('https://en.wikipedia.org/wiki/'+quote(name.replace(' ','_')),name,'pending'))
db.commit()
for iteration in range(limit):
    entry=db.execute("SELECT q.url,q.name FROM career_queue q LEFT JOIN career_priority p ON p.url=q.url WHERE q.status='pending' ORDER BY coalesce(p.priority,0) DESC,q.rowid LIMIT 1").fetchone()
    if not entry:break
    url,name=entry
    try:
        soup=BeautifulSoup(fetch(url),html_parser); count=0
        info=soup.select_one('table.infobox');profile={}
        if info:
            for row in info.find_all('tr'):
                label=row.find('th');value=row.find('td')
                if label and value:profile[label.get_text(' ',strip=True)]=value.get_text(' ',strip=True)
        born=soup.select_one('.bday')
        db.execute('INSERT OR REPLACE INTO fighters VALUES(?,?,?,?,?,?)',('wikipedia',url,name,born.get_text(strip=True) if born else '',None,json.dumps({'attributes':profile,'historical_use':'Current snapshot; career totals must not be backdated.'})))
        db.commit()
        for headers, table_rows in record_tables(soup):
            for row in table_rows:
                cells=row.find_all(['th','td'],recursive=False)
                if len(cells)!=len(headers):continue
                r=dict(zip(headers,[c.get_text(' ',strip=True) for c in cells]))
                result=r['result'].casefold()
                if result not in ['win','loss','draw','nc','no contest']:continue
                rawdate=re.sub(r'\[.*?\]','',r['date'])
                if not re.search(r'\b(?:18|19|20)\d{2}\b',rawdate):continue
                try:date=record_date(rawdate)
                except (ValueError,OverflowError) as exc:
                    db.execute('INSERT OR REPLACE INTO career_row_issues VALUES(?,?,?,?)',(url,r.get('no.',r['opponent']+'|'+rawdate),rawdate,str(exc)))
                    continue
                if date<'1800-01-01':continue
                if date>__import__('datetime').date.today().isoformat():continue
                outcome={'win':'BOXER A','loss':'BOXER B','draw':'DRAW','nc':'NO CONTEST','no contest':'NO CONTEST'}[result]
                ident=url+'#'+r.get('no.',str(count))+'-'+date
                putbout(('wikipedia',ident,date,name,r['opponent'],outcome,r.get('type',''),r.get('round, time',''),'', '',r.get('location',''),'FINISHED',url,json.dumps(r)))
                count+=1
                # Opponent expansion discovers non-title fighters and undercard careers.
                # Earlier bouts remain useful history, but only 1990+ opponents expand the graph.
                if date>='1990-01-01':
                    for a in cells[headers.index('opponent')].find_all('a',href=True):
                        # Flag/country and citation links are not fighter identities.
                        label=re.sub(r'\[[^]]*\]','',a.get_text(' ',strip=True)).strip()
                        if not label or label.casefold()!=re.sub(r'\[[^]]*\]','',r['opponent']).strip().casefold():continue
                        link=quote(unquote(urljoin(url,a['href']).split('#')[0]),safe=':/?=&')
                        if urlsplit(link).hostname=='en.wikipedia.org' and '/wiki/' in link and ':' not in urlsplit(link).path and 'redlink' not in link:
                            db.execute('INSERT OR IGNORE INTO career_queue VALUES(?,?,?,NULL)',(link,r['opponent'],'pending'))
        if not count:
            for anchor in soup.find_all('a',href=True):
                if 'boxing career of ' in anchor.get_text(' ',strip=True).casefold() and '/wiki/' in urljoin(url,anchor['href']):
                    target=quote(unquote(urljoin(url,anchor['href']).split('#')[0]),safe=':/?=&')
                    if urlsplit(target).hostname=='en.wikipedia.org':
                        db.execute("INSERT OR IGNORE INTO career_queue VALUES(?,?,'pending',NULL)",(target,name))
        db.execute('UPDATE career_queue SET status=?,rows=? WHERE url=?',('parsed' if count else 'needs_parser_review',count,url))
        db.commit();soup.decompose();report();print(name,count,flush=True)
        # Keep result/price coverage current even during a long career batch.
        if (iteration+1)%100==0:
            subprocess.run([sys.executable,str(__import__('pathlib').Path(__file__).with_name('match_prices.py'))],check=True)
    except Exception as e:
        db.rollback();fail(url,e)
        db.execute("UPDATE career_queue SET status='failed' WHERE url=?",(url,));db.commit()
