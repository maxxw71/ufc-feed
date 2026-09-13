"""Personal research: source-linked round counts from public CompuBox reports.

No commercial feed or redistribution. Raw reports retained for provenance.
Only observed linked reports are fetched; there is no numeric endpoint enumeration.
CompuBox currently states that website data is for personal use and commercial
use requires approval, so these rows stay in the private research database.
"""
import datetime as dt,hashlib,io,json,re,time
from urllib.parse import urljoin,urlsplit
from urllib.request import Request,urlopen
import pdfplumber
from bs4 import BeautifulSoup
from collect import db,fetch,fail,ROOT,now,public_feed_ipv6
BASES=['https://app2.compuboxdata.com/','https://beta.compuboxdata.com/']
for host in ['app2.compuboxdata.com','beta.compuboxdata.com','api2.compuboxdata.com']:
    public_feed_ipv6.HOSTS.add(host)
db.executescript('''CREATE TABLE IF NOT EXISTS punch_reports(url TEXT PRIMARY KEY,title TEXT,bout_date TEXT,fetched_at TEXT,sha256 TEXT,raw_path TEXT,status TEXT);
CREATE TABLE IF NOT EXISTS round_punches(report_url TEXT,fighter_label TEXT,round INTEGER,category TEXT,landed INTEGER,thrown INTEGER,CHECK(landed>=0 AND thrown>=landed),PRIMARY KEY(report_url,fighter_label,round,category));''')
db.execute('CREATE TABLE IF NOT EXISTS fight_punch_totals(report_url TEXT,fighter_label TEXT,category TEXT,landed INTEGER,body_landed INTEGER,thrown INTEGER,PRIMARY KEY(report_url,fighter_label,category),CHECK(body_landed>=0 AND body_landed<=landed AND landed<=thrown))')

def soup_for(url):
    return BeautifulSoup(fetch(url),'html.parser')

links=set();report_pages=set()
for base in BASES:
    try:
        soup=soup_for(base)
        links.update(urljoin(base,a['href']) for a in soup.find_all('a',href=True) if 'round-stats/' in a['href'])
        report_pages.update(urljoin(base,a['href']) for a in soup.find_all('a',href=True) if '/reports/' in a['href'])
    except Exception as e:fail(base,e)
# Follow only report/category pages actually linked by the public site. These pages
# can expose additional current round-stat reports without guessing numeric IDs.
for report in sorted(report_pages):
    try:
        other=soup_for(report)
        links.update(urljoin(report,a['href']) for a in other.find_all('a',href=True) if 'round-stats/' in a['href'])
    except Exception as e:fail(report,e)
# Historical articles/evidence collected elsewhere can contain exact report URLs.
for (payload,) in db.execute("SELECT data FROM source_rows WHERE source='external_evidence'"):
    try:obj=json.loads(payload)
    except Exception:continue
    for link in obj.get('stat_links',[]):
        parsed=urlsplit(link)
        if parsed.scheme=='https' and (parsed.hostname or '').endswith('.compuboxdata.com') and '/round-stats/' in parsed.path:
            links.add(link)
# Canonicalize modern beta/app2 duplicates by preserving the actually observed URL,
# but skip already parsed reports. A bounded batch keeps source load polite.
links=[url for url in sorted(links) if not db.execute("SELECT 1 FROM punch_reports WHERE url=? AND status='parsed'",(url,)).fetchone()][:160]
(ROOT/'punch_raw').mkdir(exist_ok=True)
for url in links:
    try:
        cached=db.execute('SELECT raw_path,fetched_at FROM punch_reports WHERE url=?',(url,)).fetchone()
        if cached and (ROOT/cached[0]).exists():raw=(ROOT/cached[0]).read_bytes()
        else:
            time.sleep(1)
            with urlopen(Request(url,headers={'User-Agent':'BoxingHistoryResearch/1.1','Accept-Encoding':'identity'}),timeout=30) as response:raw=response.read(8_000_001)
        if len(raw)>8_000_000:raise ValueError('report too large')
        sha=hashlib.sha256(raw).hexdigest();path='punch_raw/'+sha+('.pdf' if raw.startswith(b'%PDF') else '.html');(ROOT/path).write_bytes(raw)
        if raw.startswith(b'%PDF'):
            with pdfplumber.open(io.BytesIO(raw)) as pdf:text='\n'.join(p.extract_text() or '' for p in pdf.pages)
        else:text=BeautifulSoup(raw,'html.parser').get_text('\n',strip=True)
        lines=[x.strip() for x in text.splitlines() if x.strip()];mode=None;parsed=[];totals=[];date=None
        for line in lines:
            date_match=re.search(r'\b(\d{2}/\d{2}/\d{2,4})\b',line)
            if date_match and date is None:
                value=date_match[1];date=dt.datetime.strptime(value,'%m/%d/%Y' if len(value)==10 else '%m/%d/%y').date().isoformat()
            lower=line.lower()
            if 'final punch' in lower:mode='final'
            elif mode=='final':
                m=re.fullmatch(r'(.+?)\s+(\d+)\s*\((\d+)\)/(\d+)\s+(\d+)\s*\((\d+)\)/(\d+)\s+(\d+)\s*\((\d+)\)/(\d+)\s*',line)
                if m:
                    for i,category in enumerate(['total','jab','power']):
                        landed,body,thrown=map(int,m.groups()[1+i*3:4+i*3]);totals.append((url,m[1].strip(),category,landed,body,thrown))
            elif 'landed' in lower and 'thrown' in lower:
                mode='jab' if 'jab' in lower else 'power' if 'power' in lower else 'total' if 'total' in lower else None
            elif mode:
                match=re.fullmatch(r'(.+?)\s+((?:\d+/\d+|-/-)(?:\s+(?:\d+/\d+|-/-))*)\s*',line)
                if match:
                    for i,pair in enumerate(match[2].split(),1):
                        if pair=='-/-':continue
                        landed,thrown=map(int,pair.split('/'))
                        if not 0<=landed<=thrown:raise ValueError('invalid count')
                        parsed.append((url,match[1].strip(),i,mode,landed,thrown))
        counts={(x[1],x[2],x[3]):(x[4],x[5]) for x in parsed}
        for fighter,roundno,category in counts:
            if category=='total' and (fighter,roundno,'jab') in counts and (fighter,roundno,'power') in counts:
                t=counts[(fighter,roundno,'total')];j=counts[(fighter,roundno,'jab')];p=counts[(fighter,roundno,'power')]
                if t!=(j[0]+p[0],j[1]+p[1]):raise ValueError('total does not equal jab plus power')
        db.executemany('INSERT OR REPLACE INTO round_punches VALUES(?,?,?,?,?,?)',parsed)
        for _,fighter,category,landed,body,thrown in totals:
            matching=[r for r in parsed if r[1]==fighter and r[3]==category]
            if matching and (sum(r[4] for r in matching),sum(r[5] for r in matching))!=(landed,thrown):raise ValueError('round sums differ from report totals')
        db.executemany('INSERT OR REPLACE INTO fight_punch_totals VALUES(?,?,?,?,?,?)',totals)
        status='parsed' if parsed else 'needs_parser_review'
        db.execute('INSERT OR REPLACE INTO punch_reports VALUES(?,?,?,?,?,?,?)',(url,lines[0] if lines else '',date,cached[1] if cached else now(),sha,path,status));db.commit();print(url,len(parsed),len(totals),date,status,flush=True)
    except Exception as e:
        db.rollback();fail(url,e)
        if getattr(e,'code',None) in [403,429]:break
print('punch_counts',list(db.execute('SELECT category,count(*) FROM round_punches GROUP BY category')),flush=True)
print('punch_report_status',list(db.execute('SELECT status,count(*) FROM punch_reports GROUP BY status')),flush=True)
