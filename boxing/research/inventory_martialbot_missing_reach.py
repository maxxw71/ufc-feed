#!/usr/bin/env python3
"""Inventory MartialBot reach values for every currently missing strict boxer.

Discovery only. Exact sitemap slug + exact H1 identity + plausible numeric reach.
No automatic merge because this is a single-source inventory used to prioritize
independent corroboration.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
UA='Mozilla/5.0 AppwizaMartialBotReachInventory/1.0'
SITE='https://www.martialbot.com/boxing/sitemap.xml'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,limit=12_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def sitemap_map(raw):
    root=ET.fromstring(raw);out={}
    for x in root.iter():
        if not x.tag.endswith('loc') or not x.text:continue
        u=x.text.strip();last=urllib.parse.urlsplit(u).path.rstrip('/').split('/')[-1]
        m=re.match(r'(.+)-[0-9a-f]{32}$',last,re.I)
        if m:out.setdefault(nk(m.group(1)),[]).append(u)
    return {k:list(dict.fromkeys(v)) for k,v in out.items()}

def parse(raw):
    soup=BeautifulSoup(raw,'lxml');h=soup.find('h1')
    name=' '.join(h.stripped_strings) if h else ''
    text=' '.join(soup.stripped_strings)
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    return name,(float(m.group(1)) if m else None)

def main():
    audit=json.loads(AUDIT.read_text())
    targets=[x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])]
    _,raw=fetch(SITE);idx=sitemap_map(raw)
    def one(t):
        key=nk(t['name']);urls=idx.get(key,[])
        rec={'id':t['id'],'name':t['name'],'strict_bout_appearances':t.get('strict_bout_appearances'),'urls':urls}
        if len(urls)!=1:
            rec['status']='missing_url' if not urls else 'ambiguous_url';return rec
        try:
            final,raw=fetch(urls[0],1_500_000);h,v=parse(raw)
            rec.update({'url':final,'h1':h,'reach_cm':v})
            if nk(h)!=key:rec['status']='identity_mismatch'
            elif v is None:rec['status']='no_reach'
            elif not 120<=v<=270:rec['status']='implausible_reach'
            else:rec['status']='lead'
        except Exception as e:rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec
    with cf.ThreadPoolExecutor(max_workers=8) as ex:rows=list(ex.map(one,targets))
    counts={}
    for r in rows:counts[r['status']]=counts.get(r['status'],0)+1
    leads=[r for r in rows if r['status']=='lead']
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'targets':len(targets),
      'sitemap_exact_keys':len(idx),'status_counts':counts,'lead_count':len(leads),
      'leads':[{'id':r['id'],'name':r['name'],'reach_cm':r['reach_cm'],'url':r['url'],
                'strict_bout_appearances':r.get('strict_bout_appearances')} for r in leads],
      'policy':'Inventory only; unique exact sitemap slug, exact H1 identity, plausible numeric reach. Requires independent corroboration before merge.'}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({'targets':len(targets),'status_counts':counts,'lead_count':len(leads)},indent=2))
if __name__=='__main__':main()
