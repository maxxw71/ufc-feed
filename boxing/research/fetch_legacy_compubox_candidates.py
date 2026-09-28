#!/usr/bin/env python3
"""Fetch legacy CompuBox CDX candidates and stage strict external-evidence payloads.

Candidates come only from the discovered Wayback CDX index. This does not
directly write normalized punch rows; it captures page tables/paragraphs into
a review file so the existing strict archived CompuBox importer can validate
identity/date/full-round completeness before any use.
"""
from __future__ import annotations
import datetime as dt,io,json,re,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
INDEX=ROOT/'public_punch_audit'/'legacy_compubox_cdx_index.json'
OUT=ROOT/'public_punch_audit'/'legacy_compubox_fetched_candidates.json'
UA='Mozilla/5.0 AppwizaLegacyCompuBoxFetcher/1.0'

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=45) as r:
        raw=r.read(3_000_001)
    if len(raw)>3_000_000:raise ValueError('too large')
    return raw

def tables(soup):
    out=[]
    for t in soup.find_all('table'):
        rows=[]
        for tr in t.find_all('tr'):
            cells=[' '.join(c.stripped_strings) for c in tr.find_all(['th','td'])]
            if cells:rows.append(cells)
        if rows:out.append(rows)
    return out

def main():
    idx=json.loads(INDEX.read_text())
    items=idx.get('items') or []
    rows=[]
    for item in items:
        caps=item.get('captures') or []
        if not caps:continue
        # Try the newest indexed captures first, but fall back through older
        # distinct snapshots. Legacy Wayback pages are often degraded in the
        # newest capture while an earlier snapshot still contains the tables.
        rec={'canonical_key':item.get('canonical_key')}
        attempts=[];best=None
        for cap in reversed(caps):
            url=cap.get('snapshot_url')
            try:
                raw=fetch(url)
                is_pdf=raw.lstrip().startswith(b'%PDF')
                if is_pdf:
                    pdf=PdfReader(io.BytesIO(raw))
                    text=' '.join((p.extract_text() or '') for p in pdf.pages)
                    text=re.sub(r'\s+',' ',text).strip()
                    title='PDF: '+str(item.get('canonical_key') or '')
                    tabs=[]
                    pages=len(pdf.pages)
                else:
                    soup=BeautifulSoup(raw,'lxml')
                    title=' '.join((soup.title.stripped_strings if soup.title else []))
                    text=' '.join(soup.stripped_strings)
                    tabs=tables(soup)
                    pages=None
                pair_density=len(re.findall(r'\b\d{1,3}\s*/\s*\d{1,3}\b',text))
                candidate={
                  'status':'fetched','snapshot_url':url,'timestamp':cap.get('timestamp'),
                  'title':title,'tables':tabs,'table_count':len(tabs),'pair_density':pair_density,
                  'is_pdf':is_pdf,'pdf_pages':pages,
                  'has_round_sequence':bool(re.search(r'\bRound\s+1\s+2(?:\s+3)?',text,re.I)),
                  'has_total':bool(re.search(r'Total\s+Punch',text,re.I)),
                  'has_jab':bool(re.search(r'\bJabs?\b',text,re.I)),
                  'has_power':bool(re.search(r'Power\s+Punch',text,re.I)),
                  'text_sample':text[:30000 if is_pdf else 8000]
                }
                attempts.append({'timestamp':cap.get('timestamp'),'status':'fetched','pair_density':pair_density})
                if best is None or candidate['pair_density']>best['pair_density']:
                    best=candidate
                if pair_density>=12 and candidate['has_total'] and candidate['has_jab'] and candidate['has_power']:
                    best=candidate;break
            except Exception as e:
                attempts.append({'timestamp':cap.get('timestamp'),'status':'fetch_error',
                                 'error':type(e).__name__+': '+str(e)[:160]})
        if best:
            rec.update(best);rec['capture_attempts']=attempts
        else:
            rec.update({'status':'fetch_error','capture_attempts':attempts,
                        'error':'all indexed captures failed'})
        rows.append(rec)
    likely=[r for r in rows if r.get('status')=='fetched' and r.get('pair_density',0)>=12 and r.get('has_total') and r.get('has_jab') and r.get('has_power')]
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'candidates':len(items),
         'fetched':sum(r.get('status')=='fetched' for r in rows),'likely_full_round_pages':len(likely),
         'likely_keys':[r.get('canonical_key') for r in likely],'rows':rows,
         'policy':'Discovery/staging only; no punch rows accepted until strict identity/date and round arithmetic checks pass.'}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({k:out[k] for k in ('candidates','fetched','likely_full_round_pages','likely_keys')},indent=2))
if __name__=='__main__':main()
