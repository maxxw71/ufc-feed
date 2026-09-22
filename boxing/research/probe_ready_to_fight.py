#!/usr/bin/env python3
"""Read-only probe of Ready To Fight public fight-page structure.

Purpose: find a stable structured payload/API and explicit fight-link graph
before writing any historical stats collector. No IDs are guessed.
"""
from __future__ import annotations
import json,re,urllib.parse,urllib.request
from bs4 import BeautifulSoup

UA='Mozilla/5.0 AppwizaBoxingRTFProbe/1.0'
SEEDS=[
 'https://rtfight.com/fights/leo-vs-fulton',
 'https://rtfight.com/fights/garcia-vs-tagoe',
 'https://rtfight.com/fights/2110',
 'https://rtfight.com/fights/tszyu-vs-mendoza',
 'https://rtfight.com/fights/rodriguez-vs-edwards',
 'https://rtfight.com/fights/romero-vs-cruz',
]

def get(url,limit=8_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=40) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw.decode('utf-8','replace'),dict(r.headers)

def main():
    out=[]
    for url in SEEDS:
        item={'url':url}
        try:
            final,html,headers=get(url);soup=BeautifulSoup(html,'lxml')
            item['final']=final;item['bytes']=len(html);item['title']=soup.title.get_text(' ',strip=True) if soup.title else None
            item['fight_links']=[]
            seen=set()
            for a in soup.find_all('a',href=True):
                href=urllib.parse.urljoin(final,a['href']).split('#')[0]
                p=urllib.parse.urlsplit(href)
                if p.hostname in {'rtfight.com','www.rtfight.com'} and p.path.startswith('/fights/') and href not in seen:
                    seen.add(href);item['fight_links'].append({'url':href,'label':re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()[:160]})
            item['scripts']=[urllib.parse.urljoin(final,s.get('src')) for s in soup.find_all('script',src=True)]
            payloads=[]
            for s in soup.find_all('script'):
                typ=str(s.get('type') or '')
                txt=s.string or s.get_text() or ''
                if not txt.strip():continue
                if 'json' in typ.lower() or '__NEXT_DATA__' in str(s.get('id')) or '__NUXT__' in txt[:200] or '"punch' in txt.lower():
                    payloads.append({'id':s.get('id'),'type':typ,'text':txt[:50000]})
            item['embedded_payloads']=payloads[:20]
            pats=re.findall(r"""["']((?:https?://[^"']+|/[^"']+)(?:api|graphql|fight|stat|analytics)[^"']*)["']""",html,re.I)
            item['endpoint_candidates']=list(dict.fromkeys(pats))[:200]
            text=re.sub(r'\s+',' ',' '.join(soup.stripped_strings)).strip()
            marker='Statistics of punches'
            pos=text.find(marker)
            item['stats_text_sample']=text[pos:pos+9000] if pos>=0 else text[:3000]
        except Exception as e:item['error']=repr(e)
        out.append(item)
    print(json.dumps({'seeds':len(SEEDS),'items':out},indent=2,ensure_ascii=False))

if __name__=='__main__':main()
