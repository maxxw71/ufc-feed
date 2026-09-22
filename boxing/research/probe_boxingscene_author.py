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

out['author_chunk_probe']=[]
for src in out['scripts']:
    if '/authors/' not in urllib.parse.unquote(src):continue
    try:
        _,js,_=get(src,3_000_000)
        strings=[]
        for m in re.finditer(r'.{0,260}(?:fetch\(|axios|/api/|graphql|author|loadMore|load_more|cursor|offset|pageSize|page=).{0,700}',js,re.I):
            val=m.group(0)
            if val not in strings:strings.append(val)
        literals=re.findall(r'''["']([^"']{1,500})["']''',js)
        interesting=[x for x in literals if re.search(r'api|author|article|cursor|offset|page|limit|load',x,re.I)]
        out['author_chunk_probe'].append({
          'url':src,'bytes':len(js),'context_matches':strings[:120],
          'search_action_context':[js[max(0,m.start()-1800):min(len(js),m.end()+2800)] for m in re.finditer(r'searchArticlesAction|PostgresQueryReadonlyFrontendServerFunc|createServerReference',js,re.I)][:30],
          'interesting_literals':list(dict.fromkeys(interesting))[:250]
        })
    except Exception as e:
        out['author_chunk_probe'].append({'url':src,'error':repr(e)})
print(json.dumps(out,indent=2,ensure_ascii=False))
