#!/usr/bin/env python3
"""Inspect migrated BoxingScene article payload for recoverable historical CompuBox body."""
import json,re,urllib.request
from bs4 import BeautifulSoup

URL='https://www.boxingscene.com/compubox-stats-macklin-big-numbers-over-sturm--40893'
UA='Mozilla/5.0 AppwizaBoxingScenePayloadProbe/1.0'
req=urllib.request.Request(URL,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
with urllib.request.urlopen(req,timeout=40) as r:
    raw=r.read(6_000_000)
html=raw.decode('utf-8','replace')
needle='Matthew Macklin averaged 92 punches'
out={'bytes':len(raw),'needle_in_raw':needle in html,'needle_fragments':[],'scripts':[],'jsonld':[]}
for m in re.finditer(re.escape('Matthew Macklin'),html,re.I):
    out['needle_fragments'].append(html[max(0,m.start()-1200):m.start()+5000])
soup=BeautifulSoup(html,'lxml')
for i,s in enumerate(soup.find_all('script')):
    txt=s.string or s.get_text() or ''
    if any(k in txt for k in ['Matthew Macklin','articleBody','40893','Macklin Has Big Numbers']):
        out['scripts'].append({'index':i,'type':s.get('type'),'id':s.get('id'),'text':txt[:40000]})
    if s.get('type')=='application/ld+json':
        try:
            obj=json.loads(txt)
            if 'articleBody' in txt or 'Macklin' in txt:
                out['jsonld'].append(obj)
        except Exception:
            pass
for sel in ['[itemprop="articleBody"]','.article-body','.article-content','.entry-content','article','main']:
    tag=soup.select_one(sel)
    if tag:
        out.setdefault('selectors',{})[sel]=re.sub(r'\s+',' ',tag.get_text(' ',strip=True))[:12000]
print(json.dumps(out,indent=2,ensure_ascii=False))
