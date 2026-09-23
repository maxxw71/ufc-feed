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
PREFIGHT_OUT=OUTDIR/'boxingscene_compubox_prefight_baselines.jsonl'
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
    candidates=[]
    for sel in selectors:
        root=soup.select_one(sel)
        if not root:continue
        body=BeautifulSoup(str(root),'lxml')
        for tag in body(['script','style','nav','footer','header','aside']):tag.decompose()
        txt=re.sub(r'\s+',' ',' '.join(body.stripped_strings)).strip()
        if txt:candidates.append(txt)

    # Migrated legacy BoxingScene articles often retain their original article
    # synopsis/stat prose only in description metadata / Next.js metadata.
    for sel,attr in [
      ('meta[name="description"]','content'),
      ('meta[property="og:description"]','content'),
      ('meta[name="twitter:description"]','content')
    ]:
        tag=soup.select_one(sel)
        if tag and tag.get(attr):
            txt=re.sub(r'\s+',' ',str(tag.get(attr))).strip()
            if txt:candidates.append(txt)

    if not candidates:
        root=soup.find('main')
        if root:
            body=BeautifulSoup(str(root),'lxml')
            for tag in body(['script','style','nav','footer','header','aside']):tag.decompose()
            txt=re.sub(r'\s+',' ',' '.join(body.stripped_strings)).strip()
            if txt:candidates.append(txt)
    if not candidates:return ''

    # Prefer candidate containing explicit punch/stat language and avoid a huge
    # migrated page shell when a concise legacy article description exists.
    def score(txt):
        stat=sum(k in txt.casefold() for k in ('punch','landed','threw','power','jab','compubox'))
        shell_penalty=2 if len(txt)>12000 else 0
        return (stat-shell_penalty, -abs(len(txt)-1200))
    return max(candidates,key=score)

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

    # Modern CompuBox prose:
    # "Walker was 192 of 513, 37% in total punches while Taylor was 225 of 655"
    # "Essuman landed 140 of 363 ... and Taylor landed 125 of 493"
    for f1,f2,p1,p2 in [(a,b,pa,pb),(b,a,pb,pa)]:
        rx=re.compile(
            p1+r'[^!?]{0,110}?\b(?:was|landed)\s+(\d+)\s+of\s+(\d+)'
            r'[^!?]{0,110}?(?:total\s+punches|in\s+total\s+punches)'
            r'[^!?]{0,150}?(?:while|and|compared\s+to)\s+'+p2+
            r'[^!?]{0,80}?\b(?:was|landed)\s+(\d+)\s+of\s+(\d+)',re.I)
        for m in rx.finditer(txt):
            setv(f1,'total_landed',m.group(1));setv(f1,'total_thrown',m.group(2))
            setv(f2,'total_landed',m.group(3));setv(f2,'total_thrown',m.group(4))

        # Variant with "in total punches" after the second fighter's values.
        rx=re.compile(
            p1+r'[^!?]{0,100}?\b(?:was|landed)\s+(\d+)\s+of\s+(\d+)'
            r'[^!?]{0,140}?(?:while|and)\s+'+p2+
            r'[^!?]{0,80}?\b(?:was|landed)\s+(\d+)\s+of\s+(\d+)'
            r'[^!?]{0,70}?(?:total\s+punches|in\s+total\s+punches)',re.I)
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
        rx=re.compile(p+r'[^.!?]{0,80}?landed\s+(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)\s+(?:total\s+)?(?:punches?\s+)?per\s+round',re.I)
        for m in rx.finditer(txt):
            setv(f,'total_landed_per_round',m.group(1));setv(f,'total_thrown_per_round',m.group(2))

    # "Macklin averaged 92 punches thrown per round" (no landed count stated).
    for f,p in [(a,pa),(b,pb)]:
        rx=re.compile(p+r'[^.!?]{0,100}?(?:averaged|averaging|avg\.?d?)\s+(?:just\s+)?(\d+(?:\.\d+)?)\s+(?:total\s+)?punches?\s+(?:thrown\s+)?per\s+round',re.I)
        for m in rx.finditer(txt):
            setv(f,'total_thrown_per_round',m.group(1))

        rx=re.compile(p+r'[^.!?]{0,100}?(?:averaged|averaging|avg\.?d?)\s+(?:just\s+)?(\d+(?:\.\d+)?)\s+(?:total\s+)?(?:punches?\s+)?(?:thrown\s+)?per\s+round\s*\(\s*(\d+(?:\.\d+)?)\s+landed\s*\)',re.I)
        for m in rx.finditer(txt):
            setv(f,'total_thrown_per_round',m.group(1));setv(f,'total_landed_per_round',m.group(2))

    # "Hopkins averaged 41 punches thrown per round, landing 9 per round"
    for f,p in [(a,pa),(b,pb)]:
        rx=re.compile(p+r'[^.!?]{0,100}?(?:averaged|avg[.]?d?)\s+(\d+(?:\.\d+)?)\s+(?:total\s+)?punches?\s+thrown\s+per\s+round[^.!?]{0,80}?(?:landing|landed)\s+(\d+(?:\.\d+)?)\s+per\s+round',re.I)
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
        rx=re.compile(p+r'[^.!?]{0,110}?landed\s+(\d+(?:\.\d+)?)%\s+of\s+(?:his|her|the)\s+(\d+)\s+(?:total\s+)?punches',re.I)
        for m in rx.finditer(txt):
            setv(f,'total_accuracy_pct',m.group(1));setv(f,'total_thrown',m.group(2))
        rx=re.compile(p+r'[^.!?]{0,100}?(?:landed|landing)\s+(\d+(?:\.\d+)?)%\s+of\s+(?:his|her)\s+power\s+(?:punches|shots)',re.I)
        for m in rx.finditer(txt):setv(f,'power_accuracy_pct',m.group(1))
        rx=re.compile(p+r'[^.!?]{0,180}?\band\s+(\d+(?:\.\d+)?)%\s+of\s+(?:his|her)\s+power\s+(?:punches|shots)',re.I)
        for m in rx.finditer(txt):setv(f,'power_accuracy_pct',m.group(1))
        rx=re.compile(p+r'.{0,100}?landed\s+half\s+(?:his|her)\s+power\s+(?:punches|shots)',re.I)
        for _ in rx.finditer(txt):setv(f,'power_accuracy_pct',50.0)

    # "Linares landed an average of 10 of 36 jabs per round"
    for f,p in [(a,pa),(b,pb)]:
        rx=re.compile(p+r'[^.!?]{0,100}?landed\s+(?:an?\s+)?(?:average|avg\.?)\s+of\s+(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)\s+jabs?\s+per\s+round',re.I)
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


    # "Sturm landed an avg. of 8 punches per round."
    for f,p in [(a,pa),(b,pb)]:
        rx=re.compile(p+r'[^.!?]{0,90}?landed\s+(?:an?\s+)?(?:avg\.?|average)\s+(?:of\s+)?(\d+(?:\.\d+)?)\s+(?:total\s+)?punches?\s+per\s+round',re.I)
        for m in rx.finditer(txt):setv(f,'total_landed_per_round',m.group(1))

        # "Zab landed 59% of his power shots - but averaged just 6 landed/11 thrown per round."
        rx=re.compile(
            p+r'[^.!?]{0,90}?landed\s+(\d+(?:\.\d+)?)%\s+of\s+(?:his|her)\s+power\s+(?:punches|shots)'
            r'[^.!?]{0,100}?averaged\s+(?:just\s+)?(\d+(?:\.\d+)?)\s+landed\s*/\s*(\d+(?:\.\d+)?)\s+thrown\s+per\s+round',re.I)
        for m in rx.finditer(txt):
            setv(f,'power_accuracy_pct',m.group(1));setv(f,'power_landed_per_round',m.group(2));setv(f,'power_thrown_per_round',m.group(3))

        # "Khan ... with his jab (5 of 28 per round)"
        rx=re.compile(p+r'[^.!?]{0,120}?\bjab\b[^.!?]{0,60}?\(\s*(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)\s+per\s+round\s*\)',re.I)
        for m in rx.finditer(txt):
            setv(f,'jab_landed_per_round',m.group(1));setv(f,'jab_thrown_per_round',m.group(2))

        # "... averaging just 33 punches thrown per frame"
        rx=re.compile(p+r'[^.!?]{0,120}?(?:averaging|averaged)\s+(?:just\s+)?(\d+(?:\.\d+)?)\s+punches?\s+thrown\s+per\s+(?:frame|round)',re.I)
        for m in rx.finditer(txt):setv(f,'total_thrown_per_round',m.group(1))

        # "Crawford landed 50% of his non-jabs"
        rx=re.compile(p+r'[^.!?]{0,100}?landed\s+(\d+(?:\.\d+)?)%\s+of\s+(?:his|her)\s+non[- ]jabs',re.I)
        for m in rx.finditer(txt):setv(f,'power_accuracy_pct',m.group(1))

        # "Wilder averaged 8.6 power shots thrown per round (4 landed) vs. Washington"
        rx=re.compile(
            p+r'[^.!?]{0,120}?(?:averaged|avg\.?d?)\s+(?:just\s+)?(\d+(?:\.\d+)?)\s+power\s+(?:shots|punches)\s+thrown\s+per\s+round'
            r'\s*\(\s*(\d+(?:\.\d+)?)\s+landed\s*\)',re.I)
        for m in rx.finditer(txt):
            setv(f,'power_thrown_per_round',m.group(1));setv(f,'power_landed_per_round',m.group(2))

        # "Ramirez's pressure (67 thrown per round) and power punching (19 of 45 per round)"
        rx=re.compile(p+r"['’]s\s+pressure\s*\(\s*(\d+(?:\.\d+)?)\s+thrown\s+per\s+round\s*\)",re.I)
        for m in rx.finditer(txt):setv(f,'total_thrown_per_round',m.group(1))
        rx=re.compile(p+r"[^.!?]{0,120}?power\s+punching\s*\(\s*(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)\s+per\s+round\s*\)",re.I)
        for m in rx.finditer(txt):
            setv(f,'power_landed_per_round',m.group(1));setv(f,'power_thrown_per_round',m.group(2))

        # "controlled the fight with his jab, landing an average of 9 of 29 thrown per round"
        rx=re.compile(
            p+r'[^.!?]{0,120}?\bjab\b[^.!?]{0,100}?landing\s+(?:an?\s+)?average\s+of\s+'
            r'(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)\s+thrown\s+per\s+round',re.I)
        for m in rx.finditer(txt):
            setv(f,'jab_landed_per_round',m.group(1));setv(f,'jab_thrown_per_round',m.group(2))

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


def is_historical_review(title,text):
    head=(str(title or '')+' '+str(text or '')[:2200]).casefold()
    return bool(
        re.search(r'\bhistorical\s+(?:review|look|punch\s+stats?)\b',head)
        or re.search(r'\blast\s+\d+\s+fights?\b',head)
        or re.search(r'\bprevious\s+\d+\s+fights?\b',head)
        or re.search(r'\bin\s+(?:his|her)\s+\d+\s+fights?\b',head)
    )

def parse_prefight_baselines(text,a,b):
    """Parse only explicitly historical aggregate windows.

    These metrics describe prior fights and are attached directly to the target
    bout as pre-fight-known context; they are never inserted as current-fight
    punch observations.
    """
    txt=unicodedata.normalize('NFKD',str(text or '')).encode('ascii','ignore').decode()
    out={a:{},b:{}}

    def setv(f,key,val):
        if val is None:return
        v=float(val)
        if key in out[f] and abs(float(out[f][key])-v)>1e-9:
            out[f]['_conflict']=True
        else:out[f][key]=v

    for f in (a,b):
        p=fighter_patterns(f)
        # "Charlo (last 5 fights) averaged just 13.6 landed/43.1 thrown"
        rx=re.compile(
            p+r'\s*\(\s*last\s+(\d+)\s+fights?\s*\)[^.!?]{0,90}?'
            r'(?:averaged|avg\.?d?)\s+(?:just\s+)?(\d+(?:\.\d+)?)\s+landed\s*/\s*(\d+(?:\.\d+)?)\s+thrown',re.I)
        for m in rx.finditer(txt):
            setv(f,'history_window_fights',m.group(1))
            setv(f,'total_landed_per_round',m.group(2))
            setv(f,'total_thrown_per_round',m.group(3))

        # Historical-window sentence-local jabs / power rates and accuracy.
        for m in re.finditer(p+r'\s*\(\s*last\s+(\d+)\s+fights?\s*\)([^.!?]{0,360})',txt,re.I):
            setv(f,'history_window_fights',m.group(1));seg=m.group(2)
            jm=re.search(r'landed\s+(\d+(?:\.\d+)?)\s+jabs?\s+per\s+round',seg,re.I)
            if jm:setv(f,'jab_landed_per_round',jm.group(1))
            pm=re.search(r'(?:just\s+)?(\d+(?:\.\d+)?)\s+power\s+(?:punches|shots)\s+per\s+round(?:\s*\((\d+(?:\.\d+)?)%\))?',seg,re.I)
            if pm:
                setv(f,'power_landed_per_round',pm.group(1))
                if pm.group(2):setv(f,'power_accuracy_pct',pm.group(2))

        # "Garcia (last 12 fights) ... landed 40.3% of his power punches"
        rx=re.compile(p+r'\s*\(\s*last\s+(\d+)\s+fights?\s*\)([^.!?]{0,300})',re.I)
        for m in rx.finditer(txt):
            setv(f,'history_window_fights',m.group(1));seg=m.group(2)
            am=re.search(r'landed\s+(\d+(?:\.\d+)?)%\s+of\s+(?:his|her)\s+power\s+(?:punches|shots)',seg,re.I)
            if am:setv(f,'power_accuracy_pct',am.group(1))

        # In an explicitly historical-review article, the numeric history
        # window can be declared in one sentence and the fighter's historical
        # accuracy in the immediately following sentence. Only allow this after
        # an explicit history_window_fights value has already been established
        # for that fighter.
        if out[f].get('history_window_fights') is not None:
            rx=re.compile(
                p+r'[^.!?]{0,520}?landed\s+(\d+(?:\.\d+)?)%\s+of\s+'
                r'(?:his|her)\s+power\s+(?:punches|shots)',re.I)
            for m in rx.finditer(txt):
                setv(f,'power_accuracy_pct',m.group(1))

        # "Hopkins ... avg'd just 37.8 thrown and 12.9 landed in his 14 fights..."
        rx=re.compile(
            p+r'[^.!?]{0,160}?(?:avg\.?d?|averaged)\s+(?:just\s+)?(\d+(?:\.\d+)?)\s+thrown'
            r'\s+and\s+(\d+(?:\.\d+)?)\s+landed\s+in\s+(?:his|her)\s+(\d+)\s+fights?',re.I)
        for m in rx.finditer(txt):
            setv(f,'total_thrown_per_round',m.group(1))
            setv(f,'total_landed_per_round',m.group(2))
            setv(f,'history_window_fights',m.group(3))

        # "Rios' last 7 opponents landed 40.8% ... while Rios landed 38.5%"
        rx=re.compile(
            p+r"['’](?:s)?\s+last\s+(\d+)\s+opponents?[^.!?]{0,120}?landed\s+(\d+(?:\.\d+)?)%\s+of\s+(?:their\s+)?power\s+(?:punches|shots)"
            r'[^.!?]{0,100}?'+p+r'\s+landed\s+(\d+(?:\.\d+)?)%',re.I)
        for m in rx.finditer(txt):
            setv(f,'history_window_fights',m.group(1))
            setv(f,'opponent_power_accuracy_pct',m.group(2))
            setv(f,'power_accuracy_pct',m.group(3))

        # "Diaz landed 19 of 38 power shots per round in his last 5 fights (50%)."
        rx=re.compile(
            p+r'[^.!?]{0,140}?landed\s+(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)\s+'
            r'power\s+(?:punches|shots)\s+per\s+round[^.!?]{0,100}?(?:in\s+)?(?:his|her)\s+last\s+'
            r'(\d+)\s+fights?(?:\s*\(\s*(\d+(?:\.\d+)?)%\s*\))?',re.I)
        for m in rx.finditer(txt):
            setv(f,'power_landed_per_round',m.group(1))
            setv(f,'power_thrown_per_round',m.group(2))
            setv(f,'history_window_fights',m.group(3))
            if m.group(4):setv(f,'power_accuracy_pct',m.group(4))

        # "Russell averaged 32.7 jabs per round, but landed just 15%."
        rx=re.compile(
            p+r'[^.!?]{0,120}?(?:averaged|averaging|avg\.?d?)\s+(\d+(?:\.\d+)?)\s+jabs?\s+per\s+round'
            r'[^.!?]{0,80}?landed\s+(?:just\s+)?(\d+(?:\.\d+)?)%',re.I)
        for m in rx.finditer(txt):
            setv(f,'jab_thrown_per_round',m.group(1))
            setv(f,'jab_accuracy_pct',m.group(2))

        # "Russell opponents landed just 7.8 power shots per round and just 28%."
        rx=re.compile(
            p+r"(?:['’]s)?\s+opponents?[^.!?]{0,100}?landed\s+(?:just\s+)?(\d+(?:\.\d+)?)\s+"
            r'power\s+(?:punches|shots)\s+per\s+round[^.!?]{0,60}?(?:and\s+)?(?:just\s+)?(\d+(?:\.\d+)?)%',re.I)
        for m in rx.finditer(txt):
            setv(f,'opponent_power_landed_per_round',m.group(1))
            setv(f,'opponent_power_accuracy_pct',m.group(2))

        # Historical-review accuracy phrasing: "Broner ... landing 47% of his
        # power punches and 41% overall vs. ..."
        rx=re.compile(
            p+r'[^.!?]{0,160}?landing\s+(\d+(?:\.\d+)?)%\s+of\s+(?:his|her)\s+power\s+'
            r'(?:punches|shots)[^.!?]{0,80}?(\d+(?:\.\d+)?)%\s+overall',re.I)
        for m in rx.finditer(txt):
            setv(f,'power_accuracy_pct',m.group(1))
            setv(f,'total_accuracy_pct',m.group(2))

        # "14 of Porter's 17 landed punches per round were power shots."
        rx=re.compile(
            r'(\d+(?:\.\d+)?)\s+of\s+'+p+r"['’]s\s+(\d+(?:\.\d+)?)\s+landed\s+punches?\s+"
            r'per\s+round\s+were\s+power\s+(?:punches|shots)',re.I)
        for m in rx.finditer(txt):
            setv(f,'power_landed_per_round',m.group(1))
            setv(f,'total_landed_per_round',m.group(2))

        # Simple explicit historical rate, e.g. "Rios landed 21 power shots per round".
        rx=re.compile(p+r'[^.!?]{0,100}?landed\s+(\d+(?:\.\d+)?)\s+power\s+(?:punches|shots)\s+per\s+round',re.I)
        for m in rx.finditer(txt):setv(f,'power_landed_per_round',m.group(1))

        # Historical-review articles sometimes give explicit career/recent
        # aggregates without an N-fight window. Because this function is called
        # only for verified historical-review mode, retain the stated metric,
        # but it becomes usable only after article publication.

        # "Crawford's +14.4 plus/minus rating" / "Lomachenko +20.9 rating".
        rx=re.compile(
            p+r"(?:['’]s)?[^.!?]{0,100}?\+\s*(\d+(?:\.\d+)?)\s+"
            r"(?:compubox\s+)?plus\s*/?\s*minus(?:\s+rating)?",re.I)
        for m in rx.finditer(txt):setv(f,'plus_minus_rating',m.group(1))
        rx=re.compile(
            p+r"(?:['’]s)?[^.!?]{0,100}?\+\s*(\d+(?:\.\d+)?)\s+rating",re.I)
        for m in rx.finditer(txt):setv(f,'plus_minus_rating',m.group(1))

        # "Crawford landed 47.9% of his power shots" / "Canelo ... 47.8%".
        rx=re.compile(
            p+r"[^!?]{0,180}?landed\s+(\d+(?:\.\d+)?)%\s+of\s+"
            r"(?:his|her)\s+power\s+(?:punches|shots)",re.I)
        for m in rx.finditer(txt):setv(f,'power_accuracy_pct',m.group(1))

        # "landed 31.4% of his punches" / "total connect pct 40.1%".
        rx=re.compile(
            p+r"[^!?]{0,180}?landed\s+(\d+(?:\.\d+)?)%\s+of\s+"
            r"(?:his|her)\s+(?:total\s+)?punches",re.I)
        for m in rx.finditer(txt):setv(f,'total_accuracy_pct',m.group(1))
        rx=re.compile(
            p+r"[^.!?]{0,220}?(?:total\s+(?:conn\.?|connect)\s*(?:pct\.?|percentage)?|"
            r"total\s+connect\s*%)\s*[:=-]?\s*(\d+(?:\.\d+)?)%",re.I)
        for m in rx.finditer(txt):setv(f,'total_accuracy_pct',m.group(1))

        # Explicit historical per-round production.
        rx=re.compile(
            p+r"[^!?]{0,180}?landed\s+(\d+(?:\.\d+)?)\s+jabs?\s+per\s+round",re.I)
        for m in rx.finditer(txt):setv(f,'jab_landed_per_round',m.group(1))
        rx=re.compile(
            p+r"[^.!?]{0,220}?(?:punches\s+landed\s+per\s+round|"
            r"total\s+(?:connects?|landed)\s+per\s+round)\s*[:=-]?\s*(\d+(?:\.\d+)?)",re.I)
        for m in rx.finditer(txt):setv(f,'total_landed_per_round',m.group(1))
        rx=re.compile(
            p+r"[^.!?]{0,160}?(?:avg\.?d?|averaged)\s+(?:just\s+)?"
            r"(\d+(?:\.\d+)?)\s+(?:punches\s+)?(?:thrown\s+)?per\s+round",re.I)
        for m in rx.finditer(txt):setv(f,'total_thrown_per_round',m.group(1))

        # Explicit opponent-history defense rates.
        rx=re.compile(
            p+r"(?:['’]s)?\s+opponents?[^!?]{0,120}?(?:landed|land)\s+"
            r"(?:just\s+)?(\d+(?:\.\d+)?)\s+(?:total\s+)?punches?\s+per\s+round",re.I)
        for m in rx.finditer(txt):setv(f,'opponent_total_landed_per_round',m.group(1))
        rx=re.compile(
            p+r"(?:['’]s)?\s+opponents?[^!?]{0,120}?(?:landed|land)\s+"
            r"(?:just\s+)?(\d+(?:\.\d+)?)\s+power\s+(?:punches|shots)\s+per\s+round",re.I)
        for m in rx.finditer(txt):setv(f,'opponent_power_landed_per_round',m.group(1))
        rx=re.compile(
            p+r"(?:['’]s)?\s+opponents?[^!?]{0,140}?landed\s+"
            r"(\d+(?:\.\d+)?)%\s+of\s+(?:their\s+)?(?:total\s+)?punches",re.I)
        for m in rx.finditer(txt):setv(f,'opponent_total_accuracy_pct',m.group(1))

        # "Crawford's opponents land just 7.1 punches per round and just
        # 5 power shots per round." Keep both explicitly stated rates.
        rx=re.compile(
            p+r"(?:['’]s)?\s+opponents?[^!?]{0,120}?(?:landed|land)\s+"
            r"(?:just\s+)?(\d+(?:\.\d+)?)\s+(?:total\s+)?punches?\s+per\s+round"
            r"[^!?]{0,100}?(?:and\s+)?(?:just\s+)?(\d+(?:\.\d+)?)\s+"
            r"power\s+(?:punches|shots)\s+per\s+round",re.I)
        for m in rx.finditer(txt):
            setv(f,'opponent_total_landed_per_round',m.group(1))
            setv(f,'opponent_power_landed_per_round',m.group(2))

        # "Pacquiao's opponents landed 33% of their power shots."
        rx=re.compile(
            p+r"['’]s\s+opponents?[^.!?]{0,100}?landed\s+(\d+(?:\.\d+)?)%\s+of\s+"
            r'(?:their\s+)?power\s+(?:punches|shots)',re.I)
        for m in rx.finditer(txt):setv(f,'opponent_power_accuracy_pct',m.group(1))

        if out[f].pop('_conflict',False):
            out[f]={'_invalid_conflict':True}
    return out

def rounds_num(v):
    m=re.search(r'(\d{1,2})',str(v or ''))
    return int(m.group(1)) if m and 1<=int(m.group(1))<=15 else None

def dedupe_summary_rows(rows):
    """Collapse spelling/suffix aliases for the same dated fighter/opponent side.

    Grouping is date + source article + fighter surname + opponent surname.
    Metrics are merged only when duplicate variants agree. Any numeric conflict
    quarantines the entire side observation.
    """
    metric_fields=[
      'total_landed','total_thrown','jab_landed','jab_thrown','power_landed','power_thrown',
      'total_landed_per_round','total_thrown_per_round',
      'jab_landed_per_round','jab_thrown_per_round',
      'power_landed_per_round','power_thrown_per_round',
      'total_accuracy_pct','jab_accuracy_pct','power_accuracy_pct'
    ]
    groups=defaultdict(list)
    for r in rows:
        fs=surname(r.get('fighter'));os=surname(r.get('opponent'))
        if not fs or not os or fs==os:continue
        groups[(r.get('source_url'),r.get('bout_date'),fs,os)].append(r)
    out=[];conflicts=[]
    for key,items in groups.items():
        base=dict(items[0])
        aliases=sorted({str(x.get('fighter') or '').strip() for x in items if x.get('fighter')})
        opp_aliases=sorted({str(x.get('opponent') or '').strip() for x in items if x.get('opponent')})
        bad=[]
        for field in metric_fields:
            vals={float(x[field]) for x in items if x.get(field) is not None}
            if len(vals)>1:
                bad.append({'field':field,'values':sorted(vals)})
            elif len(vals)==1:
                v=next(iter(vals))
                base[field]=int(v) if field in {'total_landed','total_thrown','jab_landed','jab_thrown','power_landed','power_thrown'} and float(v).is_integer() else v
        if bad:
            conflicts.append({'key':key,'fighter_aliases':aliases,'opponent_aliases':opp_aliases,'conflicts':bad})
            continue
        # Prefer the most descriptive observed name, but retain every alias and
        # let the master index this row under each alias.
        base['fighter']=max(aliases,key=lambda x:(len(x),x))
        base['opponent']=max(opp_aliases,key=lambda x:(len(x),x))
        base['fighter_aliases']=aliases
        base['opponent_aliases']=opp_aliases
        base['alias_dedup_quality']='same_article_date_and_surname_side_numeric_agreement'
        out.append(base)
    out.sort(key=lambda r:(r.get('bout_date') or '',surname(r.get('fighter')),r.get('source_url') or ''))
    return out,conflicts

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--articles',type=int,default=400);args=ap.parse_args()
    if not DB.exists():raise SystemExit('missing boxing.sqlite3')
    bydate,allitems=local_bouts();urls=crawl_articles(max_articles=args.articles)
    rows=[];prefight_rows=[];diag=defaultdict(int);fail=[];unresolved_sample=[];no_numeric_sample=[];full_stat_targets={}
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
            article_prefight_added=0
            article_bout_labels=[]
            historical_mode=is_historical_review(title,text)
            article_date=pd.isoformat() if pd else None
            for bout in bouts:
                article_bout_labels.append({'date':bout['date'],'fighter_a':bout['fighter_a'],'fighter_b':bout['fighter_b']})
                published_before_target=bool(article_date and article_date < bout['date'])
                prefight_mode=historical_mode or published_before_target
                if prefight_mode:
                    histstats=parse_prefight_baselines(text,bout['fighter_a'],bout['fighter_b']) if historical_mode else {
                        bout['fighter_a']:{},bout['fighter_b']:{}
                    }
                    # When the source was published before the target bout, any
                    # explicit target-fighter numeric stats necessarily describe
                    # prior history and are safe pre-fight context. Keep them out
                    # of current-fight observations and merge them here instead.
                    if published_before_target:
                        explicit=parse_explicit_stats(text,bout['fighter_a'],bout['fighter_b'])
                        for fighter in (bout['fighter_a'],bout['fighter_b']):
                            src=explicit.get(fighter) or {}
                            dst=histstats.setdefault(fighter,{})
                            if src.get('_invalid_conflict'):
                                dst['_invalid_conflict']=True
                                continue
                            for k,v in src.items():
                                if not isinstance(v,(int,float)) or isinstance(v,bool):continue
                                if k in dst and abs(float(dst[k])-float(v))>1e-9:
                                    dst['_invalid_conflict']=True
                                else:
                                    dst[k]=v

                    hist_added=0
                    if not article_date:
                        diag['prefight_baseline_unknown_publication_date_rejected']+=1
                        continue
                    for fighter,opponent in [(bout['fighter_a'],bout['fighter_b']),(bout['fighter_b'],bout['fighter_a'])]:
                        st=histstats.get(fighter) or {}
                        if st.get('_invalid_conflict'):continue
                        numeric={k:v for k,v in st.items() if isinstance(v,(int,float)) and not isinstance(v,bool)}
                        if not numeric:continue
                        aliases=[fighter]
                        target_eligible=published_before_target
                        prefight_rows.append({
                          'source_url':final,'article_title':title,'article_date':article_date,
                          'available_from_date':article_date,'target_bout_date':bout['date'],
                          'target_bout_eligible':target_eligible,
                          'fighter':fighter,'fighter_aliases':aliases,'opponent':opponent,
                          **numeric,
                          'quality':'compubox_authored_boxingscene_explicit_historical_prefight_aggregate',
                          'timing_evidence':'article_published_before_target_bout' if target_eligible
                                            else 'historical_aggregate_available_only_after_article_publication'
                        })
                        hist_added+=1;article_prefight_added+=1
                    if hist_added:
                        diag['prefight_historical_baseline_rows']+=hist_added
                        diag['prefight_historical_baseline_bouts']+=1
                        diag['prefight_direct_target_rows']+=sum(
                            1 for x in prefight_rows[-hist_added:] if x.get('target_bout_eligible'))
                        diag['prefight_future_only_rows']+=sum(
                            1 for x in prefight_rows[-hist_added:] if not x.get('target_bout_eligible'))
                    continue
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
            if article_added:
                diag['matched_articles_with_numeric_rows']+=1
            elif article_prefight_added:
                diag['matched_articles_with_prefight_baselines_only']+=1
            else:
                diag['matched_articles_no_safe_numeric_pattern']+=1
            if not article_added and not article_prefight_added and len(no_numeric_sample)<80:
                no_numeric_sample.append({
                  'url':final,'title':title,'article_date':pd.isoformat() if pd else None,
                  'bouts':article_bout_labels,'text_sample':text[:4500]
                })
            if i%25==0:print('PROGRESS',i,'ROWS',len(rows),dict(diag),flush=True)
        except Exception as e:
            fail.append({'url':url,'error':str(e)[:220]});diag['fetch_or_parse_failure']+=1
        time.sleep(.12)

    rows,dedupe_conflicts=dedupe_summary_rows(rows)
    diag['alias_dedup_conflicts_quarantined']=len(dedupe_conflicts)
    with OUT.open('w',encoding='utf-8') as fh:
        for r in rows:fh.write(json.dumps(r,ensure_ascii=False)+'\n')
    with PREFIGHT_OUT.open('w',encoding='utf-8') as fh:
        for r in prefight_rows:fh.write(json.dumps(r,ensure_ascii=False)+'\n')
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'articles_discovered':len(urls),'summary_rows':len(rows),'prefight_historical_baseline_rows':len(prefight_rows),
      'prefight_historical_baseline_bouts':len({(r['target_bout_date'],tuple(sorted([norm(r['fighter']),norm(r['opponent'])]))) for r in prefight_rows}),
      'distinct_bouts':len({(r['bout_date'],tuple(sorted([norm(r['fighter']),norm(r['opponent'])]))) for r in rows}),
      'date_min':min((r['bout_date'] for r in rows),default=None),
      'date_max':max((r['bout_date'] for r in rows),default=None),
      'diagnostics':dict(diag),'unresolved_sample':unresolved_sample,
      'explicit_full_stat_targets':list(full_stat_targets.values()),
      'explicit_full_stat_target_count':len(full_stat_targets),
      'matched_no_numeric_sample':no_numeric_sample,'alias_dedup_conflicts_sample':dedupe_conflicts[:40],'failures_sample':fail[:80],
      'policy':'CompuBox-authored BoxingScene articles only; unique local date+pair; explicit numeric patterns only. Articles published before the resolved target bout are pre-fight context and never current-fight observations. Historical-review/last-N-fights aggregates published on/after the target bout are available only for later bouts after publication. Post-fight summaries remain separate from full round reports.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
