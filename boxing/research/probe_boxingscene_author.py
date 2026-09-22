#!/usr/bin/env python3
"""Probe BoxingScene CompuBox author-page pagination/load-more structure."""
from __future__ import annotations
import json,re,urllib.parse,urllib.request
from bs4 import BeautifulSoup

URL='https://www.boxingscene.com/author/COMPUBOX'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/153.0 Safari/537.36'

def get(url,limit=6_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=40) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('too large')
        return r.geturl(),raw.decode('utf-8','replace'),dict(r.headers)

final,html,headers=get(URL)
soup=BeautifulSoup(html,'lxml')
out={
 'final':final,'bytes':len(html),'title':soup.title.get_text(' ',strip=True) if soup.title else None,
 'article_links':[],'load_more':[],'scripts':[],'payloads':[],'endpoint_candidates':[]
}
seen=set()
for a in soup.find_all('a',href=True):
    href=urllib.parse.urljoin(final,a['href']).split('#')[0]
    label=re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()
    if '/articles/' in urllib.parse.urlsplit(href).path and href not in seen:
        seen.add(href);out['article_links'].append({'url':href,'label':label[:200]})
    if re.search(r'load\s*more|next|older',label,re.I) or a.get('data-page') or a.get('data-url'):
        out['load_more'].append({'label':label,'href':href,'attrs':{k:v for k,v in a.attrs.items() if k.startswith('data-') or k in {'id','class','rel'}}})
for s in soup.find_all('script',src=True):
    out['scripts'].append(urllib.parse.urljoin(final,s['src']))
for s in soup.find_all('script'):
    txt=s.string or s.get_text() or ''
    if not txt.strip():continue
    if any(k in txt.lower() for k in ('load more','loadmore','author','compubox','api/','graphql','page=')):
        out['payloads'].append({'id':s.get('id'),'type':s.get('type'),'text':txt[:60000]})
pats=re.findall(r"""["']((?:https?://[^"']+|/[^"']+)(?:api|graphql|author|article|load|page)[^"']*)["']""",html,re.I)
out['endpoint_candidates']=list(dict.fromkeys(pats))[:300]
print(json.dumps(out,indent=2,ensure_ascii=False))
