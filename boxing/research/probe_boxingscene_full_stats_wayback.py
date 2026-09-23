#!/usr/bin/env python3
"""Probe explicitly linked BoxingScene full-punch-stat pages via exact Wayback snapshots.

Input targets come only from boxingscene_compubox_summary_report.json where the
CompuBox-authored article explicitly linked "Full Punch Stats". No target URLs
or snapshot IDs are guessed.

This is read-only discovery: it records available snapshots and table/text
structure so a strict importer can be built against verified layouts.
"""
from __future__ import annotations
import json,re,time,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'punch_supplements'/'boxingscene_compubox_summary_report.json'
UA='Mozilla/5.0 AppwizaBoxingSceneFullStatsProbe/1.0'

def fetch(url,limit=8_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=45) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw,dict(r.headers)

def cdx(url):
    q=urllib.parse.urlencode({
      'url':url,'output':'json','fl':'timestamp,original,statuscode,mimetype,digest',
      'filter':['statuscode:200','mimetype:text/html'],'collapse':'digest','limit':'8',
      'from':'2008','to':'2026'
    },doseq=True)
    api='https://web.archive.org/cdx/search/cdx?'+q
    _,raw,_=fetch(api,2_000_000)
    data=json.loads(raw.decode('utf-8','replace'))
    if not isinstance(data,list) or len(data)<2:return []
    head=data[0]
    return [dict(zip(head,row)) for row in data[1:]]

def table_shape(table):
    rows=[]
    for tr in table.find_all('tr'):
        cells=[re.sub(r'\s+',' ',x.get_text(' ',strip=True)).strip() for x in tr.find_all(['th','td'])]
        if any(cells):rows.append(cells)
    return rows

def main():
    data=json.loads(REPORT.read_text())
    targets=data.get('explicit_full_stat_targets') or []
    out=[]
    for i,t in enumerate(targets,1):
        item={'target_url':t['url'],'label':t.get('label'),'article_urls':t.get('article_urls') or []}
        try:
            snaps=cdx(t['url'])
            item['snapshots_found']=len(snaps);item['snapshots']=snaps[:8]
            probes=[]
            for s in snaps[:3]:
                ts=s['timestamp'];original=s['original']
                snap=f'https://web.archive.org/web/{ts}id_/{original}'
                p={'timestamp':ts,'snapshot_url':snap}
                try:
                    final,raw,headers=fetch(snap)
                    soup=BeautifulSoup(raw,'lxml')
                    tables=[table_shape(x) for x in soup.find_all('table')]
                    tables=[x for x in tables if x]
                    text=re.sub(r'\s+',' ',' '.join(soup.stripped_strings)).strip()
                    stat_words={k:(k in text.casefold()) for k in ['total punches','jabs','power punches','round 1','compubox']}
                    p.update({
                      'final':final,'bytes':len(raw),'title':soup.title.get_text(' ',strip=True) if soup.title else None,
                      'table_count':len(tables),
                      'table_shapes':[[len(x),max((len(r) for r in x),default=0)] for x in tables[:20]],
                      'table_samples':[x[:12] for x in tables[:8]],
                      'text_sample':text[:12000],
                      'stat_words':stat_words
                    })
                except Exception as e:p['error']=repr(e)
                probes.append(p)
                time.sleep(.15)
            item['probes']=probes
        except Exception as e:item['error']=repr(e)
        out.append(item);print(i,t['url'],'SNAPS',item.get('snapshots_found'),flush=True)
        time.sleep(.15)
    print(json.dumps({'targets':len(targets),'items':out},indent=2,ensure_ascii=False))

if __name__=='__main__':main()
