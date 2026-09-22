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
SEEDS=ROOT/'research'/'boxingscene_compubox_seed_urls.json'
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
    if SEEDS.exists():
        try:
            for url in json.loads(SEEDS.read_text()).get('urls',[]):
                if url not in seen_articles:
                    seen_articles.add(url);articles.append(url)
        except Exception as e:
            print('SEED_READ_FAIL',str(e),flush=True)
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

def article_title(soup,url=None):
    h=soup.find('h1')
    title=re.sub(r'\s+',' ',h.get_text(' ',strip=True)).strip() if h else ''
    if (not title or title.casefold() in {'boxing news','boxingscene','boxing scene'}) and soup.title:
        title=re.sub(r'\s+',' ',soup.title.get_text(' ',strip=True)).strip()
    if (not title or title.casefold().startswith('boxing news')) and url:
        slug=urllib.parse.unquote(urllib.parse.urlsplit(url).path.rstrip('/').split('/')[-1])
        title=slug.replace('-',' ')
    return title

def resolve_bouts(title,date,bydate,allitems,text_hint=''):
    """Resolve one or more exact local bouts mentioned in a CompuBox article.

    Priority:
    1) both surnames in title + exact publication-date window;
    2) when publication date exists, both surnames occur close together in the
       article body and the date-window pair is unique;
    3) title-implied historical pair only when that pair occurred once ever.

    Body matching is never used without a publication date.
    """
    tnorm=norm(title)
    body_ascii=unicodedata.normalize('NFKD',str(text_hint or '')).encode('ascii','ignore').decode().casefold()
    body_ascii=re.sub(r'\s+',' ',body_ascii)[:7000]

    def title_matches(item):
        sa,sb=surname(item['fighter_a']),surname(item['fighter_b'])
        return bool(sa and sb and sa!=sb and sa in tnorm and sb in tnorm)

    def body_pair_near(item):
        sa,sb=surname(item['fighter_a']),surname(item['fighter_b'])
        if not sa or not sb or sa==sb:return False
        # Require literal surname tokens, not normalized substring matches.
        pa=re.compile(r'(?<![a-z0-9])'+re.escape(sa)+r'(?![a-z0-9])',re.I)
        pb=re.compile(r'(?<![a-z0-9])'+re.escape(sb)+r'(?![a-z0-9])',re.I)
        apos=[m.start() for m in pa.finditer(body_ascii)]
        bpos=[m.start() for m in pb.finditer(body_ascii)]
        return any(abs(a-b)<=260 for a in apos for b in bpos)

    if date:
        window=[]
        for offset in range(-3,4):
            d=(date+dt.timedelta(days=offset)).isoformat()
            window.extend(bydate.get(d,{}).values())

        def unique_by_pair(matches):
            bypair=defaultdict(dict)
            for x in matches:
                pair=tuple(sorted([norm(x['fighter_a']),norm(x['fighter_b'])]))
                bypair[pair][x['date']]=x
            return [next(iter(d.values())) for d in bypair.values() if len(d)==1]

        title_resolved=unique_by_pair([x for x in window if title_matches(x)])
        if title_resolved:
            return sorted(title_resolved,key=lambda x:(x['date'],norm(x['fighter_a']),norm(x['fighter_b']))),'publication_date_window'

        body_resolved=unique_by_pair([x for x in window if body_pair_near(x)])
        if body_resolved:
            return sorted(body_resolved,key=lambda x:(x['date'],norm(x['fighter_a']),norm(x['fighter_b']))),'publication_date_body_pair'

    matches=[x for x in allitems if title_matches(x)]
    bypair=defaultdict(dict)
    for x in matches:
        pair=tuple(sorted([norm(x['fighter_a']),norm(x['fighter_b'])]))
        bypair[pair][x['date']]=x
    resolved=[next(iter(d.values())) for d in bypair.values() if len(d)==1]
    if resolved:
        return sorted(resolved,key=lambda x:(x['date'],norm(x['fighter_a']),norm(x['fighter_b']))),'unique_historical_pair_from_title'
    return [],None

def text_content(soup):
    # Extract the article body only. Never let sidebar/top-story/current-feed
    # text participate in historical fighter identity or stat parsing.
    selectors=[
      '[itemprop="articleBody"]','article .article-body','article .content',
      '.article-body','.article-content','.entry-content','article'
    ]
    root=None
    for sel in selectors:
        root=soup.select_one(sel)
        if root:break
    if root is None:
        root=soup.find('main')
    if root is None:
        return ''
    # Work on a detached parse so caller's soup is not mutated.
    body=BeautifulSoup(str(root),'lxml')
    for tag in body(['script','style','nav','footer','header','aside']):tag.decompose()
    return re.sub(r'\s+',' ',' '.join(body.stripped_strings)).strip()

def is_compubox_article(soup,url,title):
    # Explicit authorship/source gate. Discovery pages contain unrelated
    # current-story links, so URL/title alone are insufficient.
    signals=[]
    for sel in [
      'meta[name="author"]','meta[property="article:author"]',
      '[rel="author"]','.author','.byline','a[href*="/author/"]'
    ]:
        for tag in soup.select(sel):
            val=(tag.get('content') or tag.get_text(' ',strip=True) or '')
            if val:signals.append(val)
    # Historical migrated CompuBox articles often preserve "By CompuBox" in
    # their article body even when author metadata is absent.
    body=text_content(soup)
    lead=(title+' '+body[:700]).casefold()
    if 'compubox' in str(title).casefold():return True
    if any('compubox' in str(x).casefold() for x in signals):return True
    if re.search(r'\bby\s+compubox\b',lead,re.I):return True
    return False

def explicit_full_stat_links(soup,base):
    out=[];seen=set()
    for a in soup.find_all('a',href=True):
        label=re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()
        href=urllib.parse.urljoin(base,a.get('href')).split('#')[0]
        low=(label+' '+href).casefold()
        if not (
            ('full punch stat' in low)
            or ('round-by-round' in low)
            or ('round by round' in low)
            or re.search(r'featured_stats/.*\.(?:pdf|html?)',low)
            or re.search(r'stat_files/.*\.(?:pdf|html?)',low)
        ):
            continue
        if href in seen:continue
        seen.add(href)
        out.append({'label':label,'url':href})
    return out

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
        val=float(val) if ('_per_round' in key or '_pct' in key) else int(val)
        if key in out[f] and abs(float(out[f][key])-float(val))>1e-9:
            out[f]['_conflict']=True
            out[f].setdefault('_conflict_details',[]).append({'field':key,'old':out[f][key],'new':val})
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

    # "Thurman had a 168-100 edge in power punches/shots landed"
    for f1,f2,p1 in [(a,b,pa),(b,a,pb)]:
        for cat_pat,key in [
            (r'total\s+punches?', 'total_landed'),
            (r'jabs?', 'jab_landed'),
            (r'power\s+(?:punches|shots)', 'power_landed')
        ]:
            rx=re.compile(p1+r'.{0,120}?(\d+)\s*[-–]\s*(\d+)\s+(?:edge|advantage).{0,50}?'+cat_pat+r'.{0,25}?landed',re.I)
            for m in rx.finditer(txt):
                setv(f1,key,m.group(1));setv(f2,key,m.group(2))

    # "Figueroa landed an avg. of 40 of 79 punches per round- Arakawa landed 23 of 98 per round"
    for f1,f2,p1,p2 in [(a,b,pa,pb),(b,a,pb,pa)]:
        rx=re.compile(
            p1+r'.{0,80}?landed\s+(?:an?\s+)?(?:avg\.?|average)?\s*(?:of\s+)?'
            r'(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)\s+(?:total\s+)?punches?\s+per\s+round'
            r'.{0,120}?'+p2+r'.{0,50}?landed\s+(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)\s+per\s+round',re.I)
        for m in rx.finditer(txt):
            setv(f1,'total_landed_per_round',m.group(1));setv(f1,'total_thrown_per_round',m.group(2))
            setv(f2,'total_landed_per_round',m.group(3));setv(f2,'total_thrown_per_round',m.group(4))

    # "Jacobs landed 17 of 53 per round."
    for f,p in [(a,pa),(b,pb)]:
        rx=re.compile(p+r'.{0,80}?landed\s+(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)\s+(?:total\s+)?(?:punches?\s+)?per\s+round',re.I)
        for m in rx.finditer(txt):
            setv(f,'total_landed_per_round',m.group(1));setv(f,'total_thrown_per_round',m.group(2))

    # "Hopkins averaged 41 punches thrown per round, landing 9 per round"
    for f,p in [(a,pa),(b,pb)]:
        rx=re.compile(p+r'.{0,100}?(?:averaged|avg[.]?d?)\s+(\d+(?:\.\d+)?)\s+(?:total\s+)?punches?\s+thrown\s+per\s+round.{0,80}?(?:landing|landed)\s+(\d+(?:\.\d+)?)\s+per\s+round',re.I)
        for m in rx.finditer(txt):
            setv(f,'total_thrown_per_round',m.group(1));setv(f,'total_landed_per_round',m.group(2))

    # Single-fighter explicit category totals, e.g. "Golovkin ... landing 53 of 108 jabs and 52 of 97 power shots."
    for f,p in [(a,pa),(b,pb)]:
        rx=re.compile(p+r'.{0,160}?(?:landed|landing)\s+(\d+)\s+of\s+(\d+)\s+jabs?.{0,80}?(\d+)\s+of\s+(\d+)\s+power\s+(?:punches|shots)',re.I)
        for m in rx.finditer(txt):
            setv(f,'jab_landed',m.group(1));setv(f,'jab_thrown',m.group(2))
            setv(f,'power_landed',m.group(3));setv(f,'power_thrown',m.group(4))

    # "Khan threw 369 jabs and landed 151" / "Malignaggi only threw 280 jabs ... land ... 57"
    for f,p in [(a,pa),(b,pb)]:
        rx=re.compile(p+r'.{0,100}?(?:only\s+)?threw\s+(\d+)\s+jabs?.{0,120}?(?:landed|land)\s+(?:\d+(?:\.\d+)?%\s*(?:or\s*)?)?(\d+)\b',re.I)
        for m in rx.finditer(txt):
            setv(f,'jab_thrown',m.group(1));setv(f,'jab_landed',m.group(2))

    # Exact per-round percentage form:
    # "Concepcion landed 37% of the 39 total punches he threw per round"
    for f,p in [(a,pa),(b,pb)]:
        rx=re.compile(
            p+r'\s+(?:landed|landing)\s+(\d+(?:\.\d+)?)%\s+of\s+'
            r'(?:the\s+)?(\d+(?:\.\d+)?)\s+(?:total\s+)?punches?\s+'
            r'(?:he|she)\s+threw\s+per\s+round',re.I)
        for m in rx.finditer(txt):
            setv(f,'total_accuracy_pct',m.group(1))
            setv(f,'total_thrown_per_round',m.group(2))

    # Explicit accuracy statements. Percentages stay percentages; rounded
    # percentage statements are never inverted to invent landed totals.
    for f,p in [(a,pa),(b,pb)]:
        rx=re.compile(p+r'.{0,110}?landed\s+(\d+(?:\.\d+)?)%\s+of\s+(?:his|her|the)\s+(\d+)\s+(?:total\s+)?punches',re.I)
        for m in rx.finditer(txt):
            setv(f,'total_accuracy_pct',m.group(1));setv(f,'total_thrown',m.group(2))
        rx=re.compile(p+r'.{0,100}?landed\s+(\d+(?:\.\d+)?)%\s+of\s+(?:his|her)\s+power\s+(?:punches|shots)',re.I)
        for m in rx.finditer(txt):setv(f,'power_accuracy_pct',m.group(1))
        rx=re.compile(p+r'[^.!?]{0,180}?\band\s+(\d+(?:\.\d+)?)%\s+of\s+(?:his|her)\s+power\s+(?:punches|shots)',re.I)
        for m in rx.finditer(txt):setv(f,'power_accuracy_pct',m.group(1))
        rx=re.compile(p+r'.{0,100}?landed\s+half\s+(?:his|her)\s+power\s+(?:punches|shots)',re.I)
        for _ in rx.finditer(txt):setv(f,'power_accuracy_pct',50.0)

    # "Linares landed an average of 10 of 36 jabs per round"
    for f,p in [(a,pa),(b,pb)]:
        rx=re.compile(p+r'.{0,100}?landed\s+(?:an?\s+)?(?:average|avg\.?)\s+of\s+(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)\s+jabs?\s+per\s+round',re.I)
        for m in rx.finditer(txt):
            setv(f,'jab_landed_per_round',m.group(1));setv(f,'jab_thrown_per_round',m.group(2))

    # "Juarez averaged 47 punches thrown per round and landed 18%"
    # "Concepcion landed 37% of the 39 total punches he threw per round"
    for f,p in [(a,pa),(b,pb)]:
        rx=re.compile(p+r'[^.!?]{0,120}?(?:averag(?:ed|ing)|avg\.?d?)\s+(?:just\s+)?(\d+(?:\.\d+)?)\s+(?:total\s+)?punches?\s+thrown\s+per\s+round[^.!?]{0,90}?(?:landed|landing)\s+(\d+(?:\.\d+)?)%',re.I)
        for m in rx.finditer(txt):
            setv(f,'total_thrown_per_round',m.group(1));setv(f,'total_accuracy_pct',m.group(2))
        rx=re.compile(p+r'[^.!?]{0,100}?(?:landed|landing)\s+(\d+(?:\.\d+)?)%\s+of\s+(?:the\s+)?(\d+(?:\.\d+)?)\s+(?:total\s+)?punches?\s+(?:he|she)\s+threw\s+per\s+round',re.I)
        for m in rx.finditer(txt):
            setv(f,'total_accuracy_pct',m.group(1));setv(f,'total_thrown_per_round',m.group(2))

    # "Rios landed 21 power shots per round, Manny 20 per round"
    for f1,f2,p1,p2 in [(a,b,pa,pb),(b,a,pb,pa)]:
        rx=re.compile(p1+r'.{0,80}?landed\s+(\d+(?:\.\d+)?)\s+power\s+(?:punches|shots)\s+per\s+round.{0,80}?'+p2+r'\s+(\d+(?:\.\d+)?)\s+per\s+round',re.I)
        for m in rx.finditer(txt):
            setv(f1,'power_landed_per_round',m.group(1));setv(f2,'power_landed_per_round',m.group(2))

    # "Murata ... out-land N'Dam 95-80 overall and 71-56 power"
    for f1,f2,p1,p2 in [(a,b,pa,pb),(b,a,pb,pa)]:
        rx=re.compile(p1+r'.{0,160}?out[- ]?land(?:ed)?\s+'+p2+r'\s+(\d+)\s*[-–]\s*(\d+)\s+(?:overall|total).{0,70}?(\d+)\s*[-–]\s*(\d+)\s+power',re.I)
        for m in rx.finditer(txt):
            setv(f1,'total_landed',m.group(1));setv(f2,'total_landed',m.group(2))
            setv(f1,'power_landed',m.group(3));setv(f2,'power_landed',m.group(4))

    for f in (a,b):
        if out[f].pop('_conflict',False):
            details=out[f].get('_conflict_details',[])
            out[f]={'_invalid_conflict':True,'_conflict_details':details}
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
    rows=[];diag=defaultdict(int);fail=[];unresolved_sample=[];no_numeric_sample=[];full_stat_targets={}
    for i,url in enumerate(urls,1):
        try:
            final,raw=fetch(url);soup=BeautifulSoup(raw,'lxml')
            title=article_title(soup,final);pd=pub_date(soup)
            if not is_compubox_article(soup,final,title):
                diag['rejected_non_compubox_article']+=1
                continue
            for link in explicit_full_stat_links(soup,final):
                full_stat_targets.setdefault(link['url'],{'url':link['url'],'label':link['label'],'article_urls':[]})
                full_stat_targets[link['url']]['article_urls'].append(final)
            raw_text=text_content(BeautifulSoup(raw,'lxml'))
            if not raw_text:
                diag['article_body_missing']+=1
                continue
            bouts,resolution=resolve_bouts(title,pd,bydate,allitems,raw_text)
            if not bouts:
                diag['unresolved_article_bout']+=1
                if pd is None:diag['article_date_missing']+=1
                if len(unresolved_sample)<60:
                    unresolved_sample.append({'url':final,'title':title,'article_date':pd.isoformat() if pd else None})
                continue
            diag['resolved_'+resolution]+=len(bouts)
            if len(bouts)>1:diag['multi_bout_articles_resolved']+=1
            text=raw_text
            article_added=0
            article_bout_labels=[]
            for bout in bouts:
                article_bout_labels.append({'date':bout['date'],'fighter_a':bout['fighter_a'],'fighter_b':bout['fighter_b']})
                stats=parse_explicit_stats(text,bout['fighter_a'],bout['fighter_b'])
                bout_added=0
                for fighter,opponent in [(bout['fighter_a'],bout['fighter_b']),(bout['fighter_b'],bout['fighter_a'])]:
                    st=stats.get(fighter) or {}
                    if st.get('_invalid_conflict'):continue
                    numeric={k:v for k,v in st.items() if isinstance(v,(int,float)) and not isinstance(v,bool)}
                    if not numeric:continue
                    rec={'source_url':final,'article_title':title,'article_date':pd.isoformat() if pd else None,'identity_resolution':resolution,
                         'bout_date':bout['date'],'fighter':fighter,'opponent':opponent,
                         'rounds_observed':rounds_num(bout.get('rounds')),
                         **{k:numeric.get(k) for k in (
                           'total_landed','total_thrown','jab_landed','jab_thrown','power_landed','power_thrown',
                           'total_landed_per_round','total_thrown_per_round',
                           'jab_landed_per_round','jab_thrown_per_round',
                           'power_landed_per_round','power_thrown_per_round',
                           'total_accuracy_pct','jab_accuracy_pct','power_accuracy_pct')},
                         'quality':'compubox_authored_boxingscene_explicit_numeric_summary_exact_date_pair'}
                    rows.append(rec);bout_added+=1;article_added+=1
                if bout_added:diag['matched_bouts_with_numeric_rows']+=1
                else:diag['matched_bouts_no_safe_numeric_pattern']+=1
            diag['matched_articles_with_numeric_rows' if article_added else 'matched_articles_no_safe_numeric_pattern']+=1
            if not article_added and len(no_numeric_sample)<80:
                no_numeric_sample.append({
                  'url':final,'title':title,'article_date':pd.isoformat() if pd else None,
                  'bouts':article_bout_labels,'text_sample':text[:4500]
                })
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
      'diagnostics':dict(diag),'unresolved_sample':unresolved_sample,
      'explicit_full_stat_targets':list(full_stat_targets.values()),
      'explicit_full_stat_target_count':len(full_stat_targets),
      'matched_no_numeric_sample':no_numeric_sample,'failures_sample':fail[:80],
      'policy':'CompuBox-authored BoxingScene articles only; unique local date+pair; explicit numeric patterns only; separate summary tier from full round reports.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
