#!/usr/bin/env python3
"""Read-only probe of official IBF boxer and bout APIs."""
from __future__ import annotations
import json,urllib.parse,urllib.request

BASE='https://www.ibf-usba-boxing.com'
UA='Mozilla/5.0 AppwizaIBFBoxerBoutProbe/1.0'

def get(url,limit=8_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json','Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=40) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('too large')
        return r.geturl(),raw.decode('utf-8','replace'),r.headers.get('content-type')

urls=[
 BASE+'/wp-json/boxers/v1/filter?ppp=5',
 BASE+'/wp-json/bouts/v1/filter?ppp=5',
 BASE+'/wp-json/wp/v2/types',
 BASE+'/wp-json/wp/v2/bouts?per_page=3&orderby=date&order=desc',
 BASE+'/wp-json/wp/v2/bouts?per_page=3&orderby=date&order=asc',
]
out={}
for u in urls:
    try:
        final,body,ct=get(u)
        out[u]={'final':final,'content_type':ct,'body':body[:50000]}
    except Exception as e:out[u]={'error':repr(e)}
print(json.dumps(out,indent=2,ensure_ascii=False))
