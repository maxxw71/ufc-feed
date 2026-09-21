#!/usr/bin/env python3
"""Probe explicit archived CompuBox PDF links already captured in evidence pages."""
from __future__ import annotations
import io,json,re,sqlite3,urllib.request
from pypdf import PdfReader

DB='/home/anestishkurti92/boxing-research/boxing.sqlite3'
UA='Mozilla/5.0 AppwizaCompuBoxPDFProbe/1.0'

def get(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=45) as r:
        raw=r.read(12_000_001)
        if len(raw)>12_000_000:raise ValueError('pdf too large')
        return r.geturl(),raw

d=sqlite3.connect(DB);d.row_factory=sqlite3.Row
links={}
for row in d.execute("select source_id,data from source_rows where source='external_evidence' and kind='punch'"):
    try:p=json.loads(row['data'])
    except Exception:continue
    for u in p.get('stat_links') or []:
        low=str(u).lower()
        if 'web.archive.org' in low and 'compubox' in low and re.search(r'\.pdf(?:$|[?#])',low):
            links.setdefault(u,[]).append(row['source_id'])

out=[]
for url,parents in list(links.items())[:40]:
    item={'url':url,'parents':parents[:8]}
    try:
        final,raw=get(url);item['final']=final;item['bytes']=len(raw);item['magic']=raw[:8].decode('latin1','replace')
        if raw.lstrip().startswith(b'%PDF'):
            pdf=PdfReader(io.BytesIO(raw))
            text='\n'.join((p.extract_text() or '') for p in pdf.pages)
            item['pages']=len(pdf.pages);item['text_sample']=text[:12000]
        else:item['error']='not PDF response'
    except Exception as e:item['error']=str(e)[:300]
    out.append(item)
print(json.dumps({'linked_pdf_urls':len(links),'probed':len(out),'items':out},indent=2,ensure_ascii=False))
