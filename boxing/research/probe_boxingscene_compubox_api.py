#!/usr/bin/env python3
"""Inspect BoxingScene's CompuBox author page for its own client-side data endpoint."""
import json,re,urllib.parse,urllib.request
from bs4 import BeautifulSoup

URL='https://www.boxingscene.com/author/COMPUBOX'
UA='Mozilla/5.0 AppwizaCompuBoxAuthorProbe/1.0'

def get(url,limit=4_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('too large')
        return r.geturl(),raw.decode('utf-8','replace'),r.headers.get('content-type')

final,html,ct=get(URL)
soup=BeautifulSoup(html,'lxml')
out={
 'final':final,'bytes':len(html),'content_type':ct,
 'scripts':[urllib.parse.urljoin(final,s.get('src')) for s in soup.find_all('script',src=True)],
 'inline_fragments':[],
}
patterns=[
 r'.{0,280}(?:compubox|author|graphql|api/|_next/data|loadMore|load-more|articles).{0,700}',
]
for pat in patterns:
    out['inline_fragments']+=re.findall(pat,html,re.I|re.S)[:120]

out['script_matches']={}
for src in out['scripts'][:60]:
    host=urllib.parse.urlsplit(src).hostname
    if host not in {'www.boxingscene.com','boxingscene.com'}:continue
    try:
        _,body,_=get(src,3_000_000)
    except Exception:
        continue
    hits=[]
    for pat in patterns:
        hits += re.findall(pat,body,re.I|re.S)[:80]
    if hits:out['script_matches'][src]=hits[:100]

print(json.dumps(out,indent=2,ensure_ascii=False))
