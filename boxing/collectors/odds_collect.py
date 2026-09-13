"""Archived displayed moneylines. Quote time and settlement rules UNVERIFIED."""
import json,re,argparse,datetime
from urllib.parse import urljoin,quote
from bs4 import BeautifulSoup
from collect import db,fetch,fail,report,public_feed_ipv6
public_feed_ipv6.HOSTS.add('www.proboxingodds.com')
BASE='https://www.proboxingodds.com'
db.execute('CREATE UNIQUE INDEX IF NOT EXISTS odds_key ON odds(source,bout_id,bookmaker,selection,url)')
db.execute('CREATE TABLE IF NOT EXISTS odds_queue(url TEXT PRIMARY KEY,kind TEXT,status TEXT)')
def discover(s):
    for a in s.find_all('a',href=True):
        path=a['href']
        if path.startswith('/events/') or path.startswith('/fighters/'):
            kind='event' if path.startswith('/events/') else 'fighter'
            if kind=='event':
                m=re.search(r'/events/(\d{4}-\d{2}-\d{2})-',path)
                if not m or m[1]>datetime.date.today().isoformat():continue
            db.execute("INSERT OR IGNORE INTO odds_queue VALUES(?,?,'pending')",(urljoin(BASE,path),kind))
    db.commit()
def prices(s,url):
    total=0
    for table in s.select('table.odds-table'):
        books={int(x['data-b']):x.get_text(' ',strip=True) for x in table.select('thead th[data-b]')}
        if not books:continue
        matchups={}
        for row in table.select('tbody tr'):
            if 'pr' in row.get('class',[]):continue
            name=row.select_one('th a[href^="/fighters/"]')
            if not name:continue
            name=name.get_text(' ',strip=True)
            for td in row.select('td.but-sg[data-li]'):
                keys=json.loads(td['data-li'])
                if len(keys)!=3:continue
                book,side,bout=keys;bookname=books.get(book)
                matchups.setdefault(str(bout),{})[str(side)]=name
                if not bookname or bookname in ['Polymarket','Kalshi']:continue
                val=td.select_one('span[id^="oID"]')
                if not val:continue
                raw=val.get_text(strip=True).replace('−','-')
                if not re.fullmatch(r'[+-]\d+',raw):continue
                american=int(raw)
                if abs(american)<100:continue
                decimal=1+american/100 if american>0 else 1+100/abs(american)
                db.execute('INSERT OR REPLACE INTO odds VALUES(?,?,?,?,?,?,?,?)',('proboxingodds',str(bout),bookname,name,decimal,None,'archived_display_time_unknown_rules_unverified',url));total+=1
        for bout,participants in matchups.items():
            db.execute('INSERT OR REPLACE INTO source_rows VALUES(?,?,?,?)',('proboxingodds','matchup',bout,json.dumps({'participants':participants,'event_url':url,'event_date':re.search(r'/events/(\d{4}-\d{2}-\d{2})',url)[1]})))
    db.commit();return total
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--fighter-pages',type=int,default=60)
    parser.add_argument('--event-pages',type=int,default=250)
    args=parser.parse_args()
    discover(BeautifulSoup(fetch(BASE+'/archive'),'html.parser'))
    first=db.execute("SELECT url FROM odds_queue WHERE kind='event' ORDER BY url DESC LIMIT 1").fetchone()
    if first:
        print('initial_event_quotes',prices(BeautifulSoup(fetch(first[0]),'html.parser'),first[0]),flush=True);report()
    for name in ['Floyd Mayweather','Manny Pacquiao','Canelo Alvarez','Anthony Joshua','Gennady Golovkin','Tyson Fury','Terence Crawford','Katie Taylor','Naoya Inoue','Oleksandr Usyk']:
        u=BASE+'/search?query='+quote(name)
        try:discover(BeautifulSoup(fetch(u),'html.parser'))
        except Exception as e:fail(u,e)
    for kind,limit in [('fighter',args.fighter_pages),('event',args.event_pages)]:
        for _ in range(limit):
            if kind=='event':
                # Round-robin by previously processed event count per year, so old
                # archive years cannot indefinitely block more recent years.
                item=db.execute("""SELECT q.url FROM odds_queue q WHERE q.status='pending' AND q.kind='event'
                  ORDER BY (SELECT count(*) FROM odds_queue p WHERE p.kind='event' AND p.status!='pending'
                  AND substr(p.url,instr(p.url,'/events/')+8,4)=substr(q.url,instr(q.url,'/events/')+8,4)),q.url LIMIT 1""").fetchone()
            else:
                item=db.execute("SELECT url FROM odds_queue WHERE status='pending' AND kind=? ORDER BY url LIMIT 1",(kind,)).fetchone()
            if not item:break
            url=item[0]
            try:
                s=BeautifulSoup(fetch(url),'lxml');discover(s)
                n=prices(s,url) if kind=='event' else 0
                s.decompose()
                db.execute("UPDATE odds_queue SET status='parsed' WHERE url=?",(url,));db.commit();report();print(kind,url,n,flush=True)
            except Exception as e:
                db.rollback();fail(url,e);db.execute("UPDATE odds_queue SET status='failed' WHERE url=?",(url,));db.commit()
                if getattr(e,'code',None) in [403,429]:break

if __name__=='__main__':main()
