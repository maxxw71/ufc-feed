#!/usr/bin/env python3
"""Read-only probe of MartialBot public profile discovery paths."""
import json,re,urllib.parse,urllib.request
from bs4 import BeautifulSoup

BASE='https://www.martialbot.com'
UA='Mozilla/5.0 AppwizaReachDiscovery/1.0'

def get(url,limit=2_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('too large')
        return r.geturl(),raw.decode('utf-8','replace'),r.headers.get('content-type')

out={}
for label,url in [
 ('robots',BASE+'/robots.txt'),
 ('home',BASE+'/'),
 ('search_q',BASE+'/search?q=Tyler+Denny'),
 ('search_query',BASE+'/search?query=Tyler+Denny'),
 ('boxing_search',BASE+'/boxing/search?q=Tyler+Denny'),
 ('site_search',BASE+'/?s=Tyler+Denny'),
]:
    try:
        final,html,ct=get(url)
        item={'final':final,'content_type':ct,'bytes':len(html)}
        if label=='robots':
            item['body']=html[:4000]
        else:
            soup=BeautifulSoup(html,'lxml')
            item['title']=soup.title.get_text(' ',strip=True) if soup.title else None
            item['forms']=[{'action':f.get('action'),'method':f.get('method'),
                            'inputs':[{'name':x.get('name'),'type':x.get('type'),'placeholder':x.get('placeholder')} for x in f.find_all('input')]}
                           for f in soup.find_all('form')][:20]
            links=[]
            for a in soup.find_all('a',href=True):
                href=urllib.parse.urljoin(final,a['href'])
                txt=re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()
                if 'tyler' in (href+' '+txt).lower() or '/boxing/boxers/' in href:
                    links.append({'text':txt,'url':href})
            item['profile_links']=links[:50]
            item['script_sources']=[urllib.parse.urljoin(final,s['src']) for s in soup.find_all('script',src=True)][-30:]
            item['fragments']=re.findall(r'.{0,100}(?:search|autocomplete|boxers).{0,220}',html,re.I)[:30]
        out[label]=item
    except Exception as e:
        out[label]={'error':repr(e)}
print(json.dumps(out,indent=2,ensure_ascii=False))
