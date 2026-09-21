#!/usr/bin/env python3
"""Collect official WBO male monthly ranking explanations with safe timing.

Discovery is limited to the official WBO /explanations/ archive. A document is
usable only when:
- its WBO article has an explicit publication date;
- an official wboboxing.com PDF is linked/embedded;
- the PDF contains a plausible multi-division ranking structure.

The article publication date is the safe effective date. Rankings are never
backdated to the nominal ranking period.
"""
from __future__ import annotations
import datetime as dt,hashlib,io,json,re,time,urllib.parse,urllib.request,urllib.error
from pathlib import Path
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'rankings';OUT.mkdir(parents=True,exist_ok=True)
UA='Mozilla/5.0 AppwizaBoxingWBOArchive/1.0'
BASE='https://wboboxing.com'
DIVISIONS=[
 'heavyweight','cruiserweight','light heavyweight','super middleweight','middleweight',
 'junior middleweight','welterweight','junior welterweight','lightweight',
 'junior lightweight','featherweight','junior featherweight','bantamweight',
 'junior bantamweight','flyweight','junior flyweight','minimumweight'
]
ALIASES={
 'super welterweight':'junior middleweight','super lightweight':'junior welterweight',
 'super featherweight':'junior lightweight','super bantamweight':'junior featherweight',
 'super flyweight':'junior bantamweight','light flyweight':'junior flyweight',
 'strawweight':'minimumweight'
}
DIVMAP={re.sub(r'[^a-z]','',x):x for x in DIVISIONS}
DIVMAP.update({re.sub(r'[^a-z]','',k):v for k,v in ALIASES.items()})

def get(url,limit=15_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=45) as r:
        data=r.read(limit+1);final=r.geturl()
    if len(data)>limit:raise ValueError('response too large')
    return final,data

def article_links():
    links=set()
    for page in range(1,6):
        url=BASE+'/explanations/' if page==1 else f'{BASE}/explanations/page/{page}/'
        try:_,raw=get(url,4_000_000)
        except Exception:continue
        soup=BeautifulSoup(raw,'lxml')
        for a in soup.find_all('a',href=True):
            href=urllib.parse.urljoin(BASE,a['href'])
            p=urllib.parse.urlsplit(href).path.rstrip('/')
            if not p.startswith('/explanations/') or p in {'/explanations'}:continue
            if '/page/' in p:continue
            links.add(href.split('#')[0])
    return sorted(links)

def parse_post_date(soup,raw_text):
    vals=[]
    for sel,attr in [
        ('meta[property="article:published_time"]','content'),
        ('meta[name="date"]','content'),
        ('time[datetime]','datetime')
    ]:
        for n in soup.select(sel):
            if n.get(attr):vals.append(n.get(attr))
    for v in vals:
        try:return dt.datetime.fromisoformat(v.replace('Z','+00:00')).date()
        except Exception:pass
    # WBO posts render human date, e.g. "February 5, 2024".
    m=re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(20\d{2})\b',raw_text)
    if m:
        try:return dt.datetime.strptime(m.group(0),'%B %d, %Y').date()
        except Exception:pass
    return None

def pdf_links(article_url,soup):
    out=[]
    for tag in soup.find_all(['a','iframe','embed'],href=True):
        u=urllib.parse.urljoin(article_url,tag.get('href'))
        if 'wboboxing.com' in urllib.parse.urlsplit(u).hostname.lower() and '.pdf' in u.lower():out.append(u)
    for tag in soup.find_all(['iframe','embed'],src=True):
        u=urllib.parse.urljoin(article_url,tag.get('src'))
        host=(urllib.parse.urlsplit(u).hostname or '').lower()
        if 'wboboxing.com' in host and '.pdf' in u.lower():out.append(u)
    # URLs can also be present in scripts/data attributes.
    html=str(soup)
    for u in re.findall(r'https?://[^"\'<>\s]+?\.pdf(?:\?[^"\'<>\s]*)?',html,re.I):
        host=(urllib.parse.urlsplit(u).hostname or '').lower()
        if host.endswith('wboboxing.com'):out.append(u.replace('&amp;','&'))
    return list(dict.fromkeys(out))

def clean_name(s):
    s=re.sub(r'\s+',' ',s).strip(' .–-')
    # Name ends before record like (27-3), then country/notes.
    m=re.match(r'(.+?)(?:\s*\(\s*\d+\s*[-–]\s*\d+[^)]*\))',s)
    if m:s=m.group(1)
    return re.sub(r'\s+',' ',s).strip(' .–-')

def division(line):
    key=re.sub(r'[^a-z]','',line.lower())
    return DIVMAP.get(key)

def parse_pdf(raw,post_date,source_url,article_url):
    pdf=PdfReader(io.BytesIO(raw))
    lines=[]
    for p in pdf.pages:
        lines.extend(re.sub(r'\s+',' ',x).strip() for x in (p.extract_text() or '').splitlines() if re.sub(r'\s+',' ',x).strip())
    div=None;ranks=[];champs=[]
    for line in lines:
        d=division(line)
        if d:
            div=d;continue
        if not div:continue
        cm=re.match(r'^(?:(Interim|Super)\s+)?Champion\s*:\s*(.+)$',line,re.I)
        if cm:
            name=clean_name(cm.group(2))
            if name and len(name)<=90:
                champs.append({'division':div,'status':((cm.group(1)+' ') if cm.group(1) else '')+'Champion',
                               'name':name,'safe_effective_date':post_date.isoformat(),
                               'source_url':source_url,'article_url':article_url})
            continue
        rm=re.match(r'^(\d{1,2})\s*[-–.]\s*(.+)$',line)
        if rm and 1<=int(rm.group(1))<=15:
            name=clean_name(rm.group(2))
            if name and not re.match(r'^(removed|vacant|not rated)\b',name,re.I):
                ranks.append({'division':div,'rank':int(rm.group(1)),'name':name,
                              'safe_effective_date':post_date.isoformat(),
                              'source_url':source_url,'article_url':article_url})
    distinct=len({x['division'] for x in ranks})
    # WBO has 17 male divisions; allow older docs with a few missing but reject
    # explanations that are not a full rankings document.
    if len(ranks)<180 or distinct<13:
        raise ValueError(f'incomplete WBO parse rows={len(ranks)} divisions={distinct}')
    return ranks,champs,len(pdf.pages)

def main():
    today=dt.date.today();docs=[];allr=[];allc=[]
    for article in article_links():
        try:
            _,html=get(article,4_000_000);soup=BeautifulSoup(html,'lxml')
            text=' '.join(soup.stripped_strings)
            posted=parse_post_date(soup,text)
            if not posted or posted.year<2021 or posted>today:
                continue
            pdfs=pdf_links(article,soup)
            if not pdfs:
                docs.append({'article_url':article,'published_date':posted.isoformat(),'status':'no_official_pdf'})
                continue
            best=None
            for pdfurl in pdfs:
                try:
                    final,raw=get(pdfurl)
                    if not raw.lstrip().startswith(b'%PDF') or len(raw)<20_000:continue
                    ranks,champs,pages=parse_pdf(raw,posted,final,article)
                    cand=(len(ranks),ranks,champs,pages,final,raw)
                    if best is None or cand[0]>best[0]:best=cand
                except Exception as e:
                    continue
            if not best:
                docs.append({'article_url':article,'published_date':posted.isoformat(),'status':'parse_review'})
                continue
            _,ranks,champs,pages,final,raw=best
            allr.extend(ranks);allc.extend(champs)
            docs.append({'article_url':article,'published_date':posted.isoformat(),'status':'parsed',
                         'source_url':final,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
                         'pages':pages,'ranking_rows':len(ranks),'champions':len(champs)})
        except Exception as e:
            docs.append({'article_url':article,'status':'fetch_review','error':str(e)[:300]})
        time.sleep(.05)
    # Deduplicate same publication+division+rank/name in case archive pages link twice.
    uniqr={}
    for r in allr:uniqr[(r['safe_effective_date'],r['division'],r['rank'],r['name'])]=r
    uniqc={}
    for r in allc:uniqc[(r['safe_effective_date'],r['division'],r['status'],r['name'])]=r
    ranks=list(uniqr.values());champs=list(uniqc.values())
    ranks.sort(key=lambda x:(x['safe_effective_date'],x['division'],x['rank']))
    champs.sort(key=lambda x:(x['safe_effective_date'],x['division'],x['status']))
    (OUT/'wbo_monthly_rankings.json').write_text(json.dumps(ranks,indent=2,ensure_ascii=False))
    (OUT/'wbo_monthly_champions.json').write_text(json.dumps(champs,indent=2,ensure_ascii=False))
    meta={'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'documents':docs,
          'parsed_documents':sum(d.get('status')=='parsed' for d in docs),
          'ranking_rows':len(ranks),'champion_rows':len(champs),
          'safe_date_policy':'Official WBO article publication date; never backdated to the nominal ranking period.',
          'archive_url':BASE+'/explanations/'}
    (OUT/'wbo_monthly_rankings_meta.json').write_text(json.dumps(meta,indent=2,ensure_ascii=False))
    print(json.dumps({k:meta[k] for k in ['parsed_documents','ranking_rows','champion_rows','archive_url']},indent=2))

if __name__=='__main__':main()
