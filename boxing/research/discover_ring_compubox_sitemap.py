#!/usr/bin/env python3
"""Discover Ring Magazine CompuBox article URLs from public sitemap surfaces.

Discovery only: no punch values or identities are accepted here. Candidate URLs
must still be curated/resolved and pass collect_ring_compubox_summaries.py's
exact DB date/pair gate before any numeric row is retained.
"""
from __future__ import annotations
import datetime as dt,json,re,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SEEDS=ROOT/'research'/'ring_compubox_seed_urls.json'
OUT=ROOT/'public_punch_audit'/'ring_compubox_sitemap_discovery.json'
UA='Mozilla/5.0 AppwizaRingSitemapDiscovery/1.0'
START=[
 'https://www.ringmagazine.com/sitemap.xml',
 'https://www.ringmagazine.com/sitemap_index.xml',
 'https://www.ringmagazine.com/sitemap-index.xml',
 'https://www.ringmagazine.com/robots.txt'
]
def fetch(url,limit=20_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw
def xml_locs(raw):
    try:root=ET.fromstring(raw)
    except Exception:return []
    return [(x.text or '').strip() for x in root.iter() if x.tag.endswith('loc') and (x.text or '').strip()]
def main():
    existing=set()
    if SEEDS.exists():
        try:existing={x.get('url') for x in json.loads(SEEDS.read_text()).get('pages') or [] if x.get('url')}
        except Exception:pass
    queue=list(START);seen=set();cands=set();diag=[]
    while queue and len(seen)<120:
        u=queue.pop(0)
        if u in seen:continue
        seen.add(u)
        try:
            final,raw=fetch(u)
            text=raw.decode('utf-8','replace')
            locs=xml_locs(raw)
            robots=re.findall(r'(?im)^\s*Sitemap:\s*(https?://\S+)\s*$',text)
            added=0
            for x in [*robots,*locs]:
                low=x.casefold()
                if 'sitemap' in low and x not in seen and x not in queue:
                    queue.append(x);continue
                if 'ringmagazine.com/news/' not in low:continue
                slug=low.rsplit('/',1)[-1]
                if 'compubox' in slug or 'compu-box' in slug or 'compu_box' in slug:
                    cands.add(x.split('#')[0]);added+=1
            diag.append({'url':u,'final_url':final,'status':'ok','bytes':len(raw),'locs':len(locs),'robots_sitemaps':len(robots),'candidate_hits':added})
        except Exception as e:
            diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:220]})
    candidates=sorted(cands)
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
         'existing_seed_urls':len(existing),'sitemap_surfaces_checked':len(seen),
         'candidate_urls':len(candidates),
         'new_candidate_urls':[x for x in candidates if x not in existing],
         'already_seeded_urls':[x for x in candidates if x in existing],
         'diagnostics':diag,
         'policy':'Discovery only. URL slug must explicitly contain CompuBox/Compu-Box. No data accepted until exact date/pair DB validation and explicit numeric parsing in the Ring collector.'}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({k:(len(v) if isinstance(v,list) else v) for k,v in out.items() if k in {'existing_seed_urls','sitemap_surfaces_checked','candidate_urls','new_candidate_urls'}},indent=2))
if __name__=='__main__':main()
