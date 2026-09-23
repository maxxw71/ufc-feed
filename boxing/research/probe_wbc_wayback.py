#!/usr/bin/env python3
"""Probe Wayback availability for known official WBC historical rating PDFs."""
from __future__ import annotations
import json,urllib.parse,urllib.request
from pathlib import Path

OUT=Path('boxing/rankings/wbc_wayback_probe.json')
UA='Mozilla/5.0 AppwizaWBCArchiveProbe/1.0'
URLS=[
 'https://wbcboxing.com/mailing/2023/ratings_pdf/WBC_RATINGS_APRIL_2023_.pdf',
 'https://wbcboxing.com/mailing/2023/ratings_pdf/WBC_RATINGS_JUNE_2023.pdf',
 'https://wbcboxing.com/mailing/2023/ratings_pdf/WBC_RATINGS_JULY_2023.pdf',
 'https://wbcboxing.com/mailing/2023/ratings_pdf/WBC_RATINGS_SEPTEMBER_2023.pdf',
 'https://wbcboxing.com/mailing/2023/ratings_pdf/WBC_RATINGS_NOVEMBER_2023__.pdf',
 'https://wbcboxing.com/mailing/2024/ratings_pdf/WBC_RATINGS_AUGUST_2024.pdf',
 'https://wbcboxing.com/mailing/2024/ratings_pdf/_WBC_RATINGS_JUNE_2024.pdf',
]
def get(url,limit=2_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read(limit+1)
        if len(raw)>limit: raise ValueError('too large')
        return r.geturl(),raw

def cdx(url):
    q='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode({
      'url':url,'output':'json','filter':'statuscode:200','filter':'mimetype:application/pdf',
      'fl':'timestamp,original,statuscode,mimetype,digest','collapse':'digest','limit':'5','from':'2023'
    })
    try:
        _,raw=get(q)
        data=json.loads(raw)
        return data[1:] if isinstance(data,list) and data else []
    except Exception as e:
        return {'error':type(e).__name__+': '+str(e)[:180]}

def main():
    rows=[]
    for url in URLS:
        rows.append({'url':url,'captures':cdx(url)})
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps({'rows':rows},indent=2))
    print(json.dumps({'rows':rows},indent=2))
if __name__=='__main__':main()
