#!/usr/bin/env python3
"""Read-only probe of WBA boxer profile/search backend."""
import json,re,urllib.parse,urllib.request
from bs4 import BeautifulSoup

BASE='https://www.wbaboxing.com'
UA='Mozilla/5.0 AppwizaWBAProfileProbe/1.0'

def get(url,limit=5_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('too large')
        return r.geturl(),raw.decode('utf-8','replace'),r.headers.get('content-type')

out={}
for label,url in [
 ('profile',BASE+'/wba-boxer-profile?id=20810'),
 ('home',BASE+'/'),
 ('rankings',BASE+'/wba-ranking'),
 ('wp_root',BASE+'/wp-json/'),
]:
    try:
        final,html,ct=get(url)
        item={'final':final,'content_type':ct,'bytes':len(html)}
        if 'json' in str(ct).lower():
            item['body']=html[:40000]
        else:
            soup=BeautifulSoup(html,'lxml')
            item['forms']=[{'action':f.get('action'),'method':f.get('method'),
                            'inputs':[{'name':x.get('name'),'id':x.get('id'),'type':x.get('type'),'value':x.get('value')} for x in f.find_all('input')]}
                           for f in soup.find_all('form')]
            item['scripts']=[urllib.parse.urljoin(final,s.get('src')) for s in soup.find_all('script',src=True)]
            item['boxer_fragments']=re.findall(r'.{0,160}(?:boxer|fighter|autocomplete|ajaxurl|wba-boxer-profile).{0,260}',html,re.I)[:100]
        out[label]=item
    except Exception as e:out[label]={'error':repr(e)}

# Fetch only same-site JS files that look potentially relevant, bounded.
scripts=[]
for item in out.values():
    for s in item.get('scripts',[]) if isinstance(item,dict) else []:
        if urllib.parse.urlsplit(s).hostname in {'www.wbaboxing.com','wbaboxing.com'} and s not in scripts:scripts.append(s)
out['script_probes']={}
for s in scripts[:40]:
    try:
        _,body,ct=get(s,2_000_000)
        if re.search(r'boxer|fighter|autocomplete|wba-boxer-profile|admin-ajax|wp-json',body,re.I):
            out['script_probes'][s]=re.findall(r'.{0,240}(?:boxer|fighter|autocomplete|wba-boxer-profile|admin-ajax|wp-json).{0,500}',body,re.I)[:80]
    except Exception as e:pass
print(json.dumps(out,indent=2,ensure_ascii=False))
