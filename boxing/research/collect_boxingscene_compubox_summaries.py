#!/usr/bin/env python3
"""Collect historical CompuBox-authored BoxingScene fight-stat summaries.

Purpose: deepen historical punch evidence without pretending summary articles are
full round-by-round reports.

Strict gates:
- discover only articles linked from BoxingScene's CompuBox author archive;
- map article to exactly one local finished bout using publication date window
  and both fighter surnames in the article title;
- store only explicit numeric punch statements that can be assigned to one
  fighter unambiguously;
- no missing stat, category, identity, date or number is inferred;
- summary rows remain a separate quality tier from full CompuBox round reports.
"""
from __future__ import annotations
import argparse,datetime as dt,json,re,time,unicodedata,urllib.parse,urllib.request
from collections import defaultdict,deque
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'research'/'boxing.sqlite3'
OUTDIR=ROOT/'punch_supplements';OUTDIR.mkdir(parents=True,exist_ok=True)
OUT=OUTDIR/'boxingscene_compubox_summaries.jsonl'
REPORT=OUTDIR/'boxingscene_compubox_summary_report.json'
START='https://www.boxingscene.com/author/COMPUBOX'
UA='Mozilla/5.0 AppwizaBoxingCompuBoxSummary/1.0'

def norm(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def surname(s):
    toks=re.findall(r"[A-Za-zÀ-ÿ0-9'-]+",str(s or ''))
    while toks and toks[-1].lower().rstrip('.') in {'jr','sr','ii','iii','iv','jnr'}:toks.pop()
    return norm(toks[-1]) if toks else ''

def fetch(url,limit=5_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def pub_date(soup):
    candidates=[]
    for sel,attr in [
      ('meta[property="article:published_time"]','content'),
      ('meta[name="date"]','content'),
      ('meta[name="publish-date"]','content'),
      ('time[datetime]','datetime')
    ]:
        for tag in soup.select(sel):
            if tag.get(attr):candidates.append(tag.get(attr))
    for value in candidates:
        s=str(value).strip()
        try:return dt.datetime.fromisoformat(s.replace('Z','+00:00')).date()
        except Exception:pass
        for fmt in ('%Y-%m-%d','%b %d, %Y','%B %d, %Y'):
            try:return dt.datetime.strptime(s[:30],fmt).date()
            except Exception:pass
    # visible legacy article dates
    txt=' '.join(soup.stripped_strings)
    for m in re.finditer(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+20\d{2}\b',txt,re.I):
        for fmt in ('%b %d, %Y','%B %d, %Y'):
            try:return dt.datetime.strptime(m.group(0).replace('Sept','Sep'),fmt).date()
            except Exception:pass
    return None

def crawl_articles(max_index_pages=25,max_articles=500):
    queue=deque([START]);seen_pages=set();articles=[];seen_articles=set()
    while queue and len(seen_pages)<max_index_pages and len(articles)<max_articles:
        url=queue.popleft()
        if url in seen_pages:continue
        seen_pages.add(url)
        try:
            final,raw=fetch(url);soup=BeautifulSoup(raw,'lxml')
        except Exception as e:
            print('INDEX_FAIL',url,str(e),flush=True);continue
        for a in soup.find_all('a',href=True):
            href=urllib.parse.urljoin(final,a['href']).split('#')[0]
            p=urllib.parse.urlsplit(href)
            if p.hostname not in {'boxingscene.com','www.boxingscene.com'}:continue
            if p.path.startswith('/articles/'):
                if href not in seen_articles:
                    seen_articles.add(href);articles.append(href)
            elif '/author/COMPUBOX' in p.path.upper() or ('author' in p.path.lower() and 'compubox' in p.path.lower()):
                if href not in seen_pages and href not in queue:queue.append(href)
            elif url==START and ('page=' in p.query or re.search(r'/page/\d+/?$',p.path)):
                if href not in seen_pages and href not in queue:queue.append(href)
        # Some sites expose rel=next.
        for a in soup.select('a[rel="next"]'):
            href=urllib.parse.urljoin(final,a.get('href','')).split('#')[0]
            if href and href not in seen_pages and href not in queue:queue.append(href)
        time.sleep(.12)
    print('INDEX_PAGES',len(seen_pages),'ARTICLES',len(articles),flush=True)
    return articles[:max_articles]

def local_bouts():
    import sqlite3
    con=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);con.row_factory=sqlite3.Row
    bydate=defaultdict(dict);allitems={}
    for r in con.execute("""select date,boxer_a,boxer_b,rounds,source,source_id
                            from bouts where status='FINISHED' and date is not null"""):
        a,b=str(r['boxer_a'] or '').strip(),str(r['boxer_b'] or '').strip()
        if not a or not b:continue
        pair=tuple(sorted([norm(a),norm(b)]))
        key=(r['date'],pair)
        bydate[r['date']].setdefault(pair,{'date':r['date'],'fighter_a':a,'fighter_b':b,'rounds':r['rounds'],'sources':[]})
        bydate[r['date']][pair]['sources'].append((r['source'],r['source_id']))
        allitems.setdefault(key,bydate[r['date']][pair])
    con.close()
    return bydate,list(allitems.values())

def article_title(soup):
    h=soup.find('h1')
    if h:return re.sub(r'\s+',' ',h.get_text(' ',strip=True)).strip()
    if soup.title:return re.sub(r'\s+',' ',soup.title.get_text(' ',strip=True)).strip()
    return ''

def resolve_bout(title,date,bydate,allitems):
    tnorm=norm(title)
    def title_matches(item):
        sa,sb=surname(item['fighter_a']),surname(item['fighter_b'])
        return bool(sa and sb and sa!=sb and sa in tnorm and sb in tnorm)

    # First choice: publication date window.
    if date:
        matches=[]
        for offset in range(-3,4):
            d=(date+dt.timedelta(days=offset)).isoformat()
            for item in bydate.get(d,{}).values():
                if title_matches(item):matches.append(item)
        uniq={(x['date'],tuple(sorted([norm(x['fighter_a']),norm(x['fighter_b'])]))):x for x in matches}
        if len(uniq)==1:
            return next(iter(uniq.values())),'publication_date_window'

    # Safe fallback for migrated pages with missing/incorrect article metadata:
    # accept only when the exact pair implied by the title occurs once in the
    # entire local finished-bout archive. Rematches and multi-fight titles fail.
    matches=[x for x in allitems if title_matches(x)]
    uniq={(x['date'],tuple(sorted([norm(x['fighter_a']),norm(x['fighter_b'])]))):x for x in matches}
    if len(uniq)==1:
        return next(iter(uniq.values())),'unique_historical_pair_from_title'
    return None,None

def text_content(soup):
    for tag in soup(['script','style','nav','footer','header']):tag.decompose()
    return re.sub(r'\s+',' ',' '.join(soup.stripped_strings)).strip()

def fighter_patterns(name):
    vals=[re.escape(name),re.escape(surname(name))]
    return '(?:'+'|'.join(x for x in vals if x)+')'

def parse_explicit_stats(text,a,b):
    """Return per-fighter dicts. Only high-specificity prose patterns."""
    out={a:{},b:{}}
    # ASCII-normalized text preserves digits/punctuation enough for regex and
    # improves matching diacritics.
    txt=unicodedata.normalize('NFKD',text).encode('ascii','ignore').decode()
    pa,pb=fighter_patterns(a),fighter_patterns(b)

    def setv(f,key,val):
        if val is None:return
        val=int(val)
        if key in out[f] and out[f][key]!=val:
            out[f]['_conflict']=True
        else:out[f][key]=val

    # "Martinez landed 226 of 593 total punches ... to 161 of 413 ... for Dzinziruk"
    for f1,f2,p1,p2 in [(a,b,pa,pb),(b,a,pb,pa)]:
        rx=re.compile(p1+r'.{0,120}?landed\s+(\d+)\s+of\s+(\d+)\s+(?:total\s+)?punches.{0,120}?(?:to|compared\s+to)\s+(\d+)\s+of\s+(\d+).{0,80}?'+p2,re.I)
        for m in rx.finditer(txt):
            setv(f1,'total_landed',m.group(1));setv(f1,'total_thrown',m.group(2))
            setv(f2,'total_landed',m.group(3));setv(f2,'total_thrown',m.group(4))

    # "Ajagba was 186 of 583 ... compared to 177 of 622 ... for Vianello in total punches"
    for f1,f2,p1,p2 in [(a,b,pa,pb),(b,a,pb,pa)]:
        rx=re.compile(p1+r'.{0,100}?\bwas\s+(\d+)\s+of\s+(\d+).{0,100}?compared\s+to\s+(\d+)\s+of\s+(\d+).{0,100}?'+p2+r'.{0,60}?total\s+punch',re.I)
        for m in rx.finditer(txt):
            setv(f1,'total_landed',m.group(1));setv(f1,'total_thrown',m.group(2))
            setv(f2,'total_landed',m.group(3));setv(f2,'total_thrown',m.group(4))

    # "Martinez landed 147 of 384 jabs ... to 80 of 242 for Dzinziruk"
    for cat in ('jabs','power punches'):
        key='jab' if cat=='jabs' else 'power'
        for f1,f2,p1,p2 in [(a,b,pa,pb),(b,a,pb,pa)]:
            rx=re.compile(p1+r'.{0,100}?landed\s+(\d+)\s+of\s+(\d+)\s+'+re.escape(cat)+r'.{0,100}?(?:to|compared\s+to)\s+(\d+)\s+of\s+(\d+).{0,80}?'+p2,re.I)
            for m in rx.finditer(txt):
                setv(f1,key+'_landed',m.group(1));setv(f1,key+'_thrown',m.group(2))
                setv(f2,key+'_landed',m.group(3));setv(f2,key+'_thrown',m.group(4))

    # "Fury ... connect leads of 107-37 overall, 7-1 jabs and 100-36 power."
    for f1,f2,p1 in [(a,b,pa),(b,a,pb)]:
        rx=re.compile(p1+r'.{0,220}?(?:connect\s+(?:leads?|gaps?)|edge)\s+of\s+(\d+)\s*[-–]\s*(\d+)\s+(?:overall|total).{0,60}?(\d+)\s*[-–]\s*(\d+)\s+jabs?.{0,60}?(\d+)\s*[-–]\s*(\d+)\s+power',re.I)
        for m in rx.finditer(txt):
            for key,v1,v2 in [('total_landed',m.group(1),m.group(2)),('jab_landed',m.group(3),m.group(4)),('power_landed',m.group(5),m.group(6))]:
                setv(f1,key,v1);setv(f2,key,v2)

    # "Thurman had a 168-100 edge in power punches landed"
    for f1,f2,p1 in [(a,b,pa),(b,a,pb)]:
        for cat,key in [('total punches','total_landed'),('jabs','jab_landed'),('power punches','power_landed')]:
            rx=re.compile(p1+r'.{0,100}?(\d+)\s*[-–]\s*(\d+)\s+(?:edge|advantage).{0,40}?'+re.escape(cat)+r'.{0,20}?landed',re.I)
            for m in rx.finditer(txt):
                setv(f1,key,m.group(1));setv(f2,key,m.group(2))

    for f in (a,b):
        if out[f].pop('_conflict',False):out[f]={'_invalid_conflict':True}
        # arithmetic sanity when enough fields exist
        if all(k in out[f] for k in ('total_landed','jab_landed','power_landed')):
            if out[f]['jab_landed']+out[f]['power_landed']!=out[f]['total_landed']:
                out[f]={'_invalid_conflict':True}
        for cat in ('total','jab','power'):
            l=out[f].get(cat+'_landed');t=out[f].get(cat+'_thrown')
            if l is not None and t is not None and not (0<=l<=t):out[f]={'_invalid_conflict':True}
    return out

def rounds_num(v):
    m=re.search(r'(\d{1,2})',str(v or ''))
    return int(m.group(1)) if m and 1<=int(m.group(1))<=15 else None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--articles',type=int,default=400);args=ap.parse_args()
    if not DB.exists():raise SystemExit('missing boxing.sqlite3')
    bydate,allitems=local_bouts();urls=crawl_articles(max_articles=args.articles)
    rows=[];diag=defaultdict(int);fail=[]
    for i,url in enumerate(urls,1):
        try:
            final,raw=fetch(url);soup=BeautifulSoup(raw,'lxml')
            title=article_title(soup);pd=pub_date(soup);bout,resolution=resolve_bout(title,pd,bydate,allitems)
            if not bout:
                diag['unresolved_article_bout']+=1
                if pd is None:diag['article_date_missing']+=1
                continue
            diag['resolved_'+resolution]+=1
            text=text_content(soup)
            stats=parse_explicit_stats(text,bout['fighter_a'],bout['fighter_b'])
            added=0
            for fighter,opponent in [(bout['fighter_a'],bout['fighter_b']),(bout['fighter_b'],bout['fighter_a'])]:
                st=stats.get(fighter) or {}
                if st.get('_invalid_conflict'):continue
                numeric={k:v for k,v in st.items() if isinstance(v,int)}
                if not numeric:continue
                rec={'source_url':final,'article_title':title,'article_date':pd.isoformat() if pd else None,'identity_resolution':resolution,
                     'bout_date':bout['date'],'fighter':fighter,'opponent':opponent,
                     'rounds_observed':rounds_num(bout.get('rounds')),
                     **{k:numeric.get(k) for k in (
                       'total_landed','total_thrown','jab_landed','jab_thrown','power_landed','power_thrown')},
                     'quality':'compubox_authored_boxingscene_explicit_numeric_summary_exact_date_pair'}
                rows.append(rec);added+=1
            diag['matched_articles_with_numeric_rows' if added else 'matched_articles_no_safe_numeric_pattern']+=1
            if i%25==0:print('PROGRESS',i,'ROWS',len(rows),dict(diag),flush=True)
        except Exception as e:
            fail.append({'url':url,'error':str(e)[:220]});diag['fetch_or_parse_failure']+=1
        time.sleep(.12)

    # exact duplicate observations collapse
    uniq={}
    for r in rows:
        key=(r['source_url'],r['bout_date'],norm(r['fighter']))
        uniq[key]=r
    rows=sorted(uniq.values(),key=lambda r:(r['bout_date'],norm(r['fighter']),r['source_url']))
    with OUT.open('w',encoding='utf-8') as fh:
        for r in rows:fh.write(json.dumps(r,ensure_ascii=False)+'\n')
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'articles_discovered':len(urls),'summary_rows':len(rows),
      'distinct_bouts':len({(r['bout_date'],tuple(sorted([norm(r['fighter']),norm(r['opponent'])]))) for r in rows}),
      'date_min':min((r['bout_date'] for r in rows),default=None),
      'date_max':max((r['bout_date'] for r in rows),default=None),
      'diagnostics':dict(diag),'failures_sample':fail[:80],
      'policy':'CompuBox-authored BoxingScene articles only; unique local date+pair; explicit numeric patterns only; separate summary tier from full round reports.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
