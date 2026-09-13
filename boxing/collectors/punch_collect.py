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
BASES=['https://app2.compuboxdata.com/','https://beta.compuboxdata.com/','https://api2.compuboxdata.com/']
DISCOVERY_PAGES=[
 'https://app2.compuboxdata.com/reports/49',
 'https://app2.compuboxdata.com/reports/76',
 'https://app2.compuboxdata.com/reports/79',
 'https://app2.compuboxdata.com/reports/80',
 'https://app2.compuboxdata.com/reports/93',
 'https://app2.compuboxdata.com/reports/94',
 'https://beta.compuboxdata.com/reports/76',
 'https://beta.compuboxdata.com/reports/77',
 'https://beta.compuboxdata.com/reports/93',
 'https://beta.compuboxdata.com/reports/94',
 'https://app2.compuboxdata.com/round-stats/15677',
 'https://beta.compuboxdata.com/round-stats/15535',
]
WEB_HOSTS={'app2.compuboxdata.com','beta.compuboxdata.com'}
for host in ['app2.compuboxdata.com','beta.compuboxdata.com','api2.compuboxdata.com']:
    public_feed_ipv6.HOSTS.add(host)
db.executescript('''CREATE TABLE IF NOT EXISTS punch_reports(url TEXT PRIMARY KEY,title TEXT,bout_date TEXT,fetched_at TEXT,sha256 TEXT,raw_path TEXT,status TEXT);
CREATE TABLE IF NOT EXISTS round_punches(report_url TEXT,fighter_label TEXT,round INTEGER,category TEXT,landed INTEGER,thrown INTEGER,CHECK(landed>=0 AND thrown>=landed),PRIMARY KEY(report_url,fighter_label,round,category));''')
db.execute('CREATE TABLE IF NOT EXISTS fight_punch_totals(report_url TEXT,fighter_label TEXT,category TEXT,landed INTEGER,body_landed INTEGER,thrown INTEGER,PRIMARY KEY(report_url,fighter_label,category),CHECK(body_landed>=0 AND body_landed<=landed AND landed<=thrown))')

def soup_for(url):
    return BeautifulSoup(fetch(url),'html.parser')

def _web_host(host):
    # api2 pages sometimes publish web-report hrefs; fetch reports from the web
    # hosts rather than constructing unsupported API report URLs.
    return 'beta.compuboxdata.com' if host=='api2.compuboxdata.com' else host

def round_id(url):
    m=re.search(r'(?:^|/)round-stats/(\d+)(?:/|$)',urlsplit(str(url)).path)
    return m.group(1) if m else None

def canonical_round(page,href):
    raw=urljoin(page,href)
    p=urlsplit(raw);rid=round_id(raw);host=_web_host(p.hostname or '')
    if not rid or host not in WEB_HOSTS:return None
    return f'https://{host}/round-stats/{rid}'

def canonical_report(page,href):
    raw=urljoin(page,href);p=urlsplit(raw);host=_web_host(p.hostname or '')
    m=re.search(r'(?:^|/)reports/(\d+)(?:/|$)',p.path)
    if not m or host not in WEB_HOSTS:return None
    return f'https://{host}/reports/{m.group(1)}'

def discover_from(page,links,report_pages):
    try:
        soup=soup_for(page)
        for a in soup.find_all('a',href=True):
            href=a['href']
            if 'round-stats/' in href:
                u=canonical_round(page,href)
                if u:links.add(u)
            if '/reports/' in href or href.startswith('reports/'):
                u=canonical_report(page,href)
                if u:report_pages.add(u)
    except Exception as e:fail(page,e)

links=set();report_pages=set()
for base in BASES:discover_from(base,links,report_pages)
for page in DISCOVERY_PAGES:discover_from(page,links,report_pages)
# Follow only report/category pages actually linked by the public site. These pages
# can expose additional current round-stat reports without guessing numeric IDs.
for report in sorted(report_pages):
    try:
        other=soup_for(report)
        for a in other.find_all('a',href=True):
            if 'round-stats/' in a['href']:
                u=canonical_round(report,a['href'])
                if u:links.add(u)
    except Exception as e:fail(report,e)
# Historical articles/evidence collected elsewhere can contain exact report URLs.
for (payload,) in db.execute("SELECT data FROM source_rows WHERE source='external_evidence'"):
    try:obj=json.loads(payload)
    except Exception:continue
    for link in obj.get('stat_links',[]):
        parsed=urlsplit(link)
        if parsed.scheme=='https' and (parsed.hostname or '').endswith('.compuboxdata.com') and 'round-stats/' in parsed.path:
            u=canonical_round(link,link)
            if u:links.add(u)

# De-duplicate by report ID, not raw URL. This prevents equivalent beta/app2 links
# and previously malformed relative joins from being fetched repeatedly.
parsed_ids=set()
for (url,) in db.execute("SELECT url FROM punch_reports WHERE status='parsed'"):
    rid=round_id(url)
    if rid:parsed_ids.add(rid)
priority={'beta.compuboxdata.com':0,'app2.compuboxdata.com':1}
chosen={}
for url in sorted(links):
    rid=round_id(url)
    if not rid or rid in parsed_ids:continue
    cur=chosen.get(rid)
    if cur is None or priority.get(urlsplit(url).hostname,9)<priority.get(urlsplit(cur).hostname,9):chosen[rid]=url
links=[chosen[k] for k in sorted(chosen,key=lambda x:int(x))][:240]
print('discovered_unparsed_report_ids',len(links),[round_id(x) for x in links[:25]],flush=True)

(ROOT/'punch_raw').mkdir(exist_ok=True)
for url in links:
    try:
        cached=db.execute('SELECT raw_path,fetched_at FROM punch_reports WHERE url=?',(url,)).fetchone()
        if cached and (ROOT/cached[0]).exists():raw=(ROOT/cached[0]).read_bytes()
        else:
            time.sleep(1)
            with urlopen(Request(url,headers={'User-Agent':'BoxingHistoryResearch/1.3','Accept-Encoding':'identity'}),timeout=30) as response:raw=response.read(8_000_001)
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
