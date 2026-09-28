#!/usr/bin/env python3
"""Discover archived legacy CompuBox full-stat pages through Wayback CDX.

Discovery only. Finds archived stat_files / featured_stats URLs and records
candidate snapshots for later strict identity/date parsing.
"""
from __future__ import annotations
import datetime as dt,json,os,re,urllib.parse,urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
OUT=Path('boxing/public_punch_audit/legacy_compubox_cdx_index.json')
UA='Mozilla/5.0 AppwizaLegacyCompuBoxDiscovery/1.0'
PREFIXES=[
 'http://www.compuboxonline.com:80/stat_files/',
 'https://www.compuboxonline.com:80/stat_files/',
 'http://compuboxonline.com:80/stat_files/',
 'https://compuboxonline.com:80/stat_files/',
 'http://www.compuboxonline.com:80/featured_stats/',
 'https://www.compuboxonline.com:80/featured_stats/',
 'http://compuboxonline.com:80/featured_stats/',
 'https://compuboxonline.com:80/featured_stats/',
 'http://www.compuboxonline.com/stat_files/',
 'https://www.compuboxonline.com/stat_files/',
 'http://compuboxonline.com/stat_files/',
 'https://compuboxonline.com/stat_files/',
 'http://www.compuboxonline.com/featured_stats/',
 'https://www.compuboxonline.com/featured_stats/',
 'http://compuboxonline.com/featured_stats/',
 'https://compuboxonline.com/featured_stats/',
]

KNOWN_URLS=[
 # Exact historical CompuBox URLs documented by contemporaneous external
 # references. These are discovery seeds only; no stats are trusted from the
 # referring page. Every capture still has to pass the normal strict parser.
 'http://compuboxonline.com/stat_files/ARR-ADA.htm',
 'http://compuboxonline.com/stat_files/DIA-MAL3.htm',
 'http://compuboxonline.com/stat_files/KLI-JOH.htm',
 'http://www.compuboxonline.com/featured_stats/V_Klitschko_KO_10_Arreola.pdf',
 'http://www.compuboxonline.com/stat_files/COT-MOS.htm',
 'http://compuboxonline.com/stat_files/MOS-MOR.htm',
]


def query(prefix):
    q='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode([
      ('url',prefix+'*'),('output','json'),('filter','statuscode:200'),
      ('fl','timestamp,original,statuscode,mimetype,digest'),('limit','10000')
    ])
    req=urllib.request.Request(q,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=25) as r:
        x=json.loads(r.read().decode('utf-8','replace'))
    return q,(x[1:] if isinstance(x,list) and x else [])

def query_exact(url):
    q='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode([
      ('url',url),('output','json'),('filter','statuscode:200'),
      ('fl','timestamp,original,statuscode,mimetype,digest'),('limit','1000')
    ])
    req=urllib.request.Request(q,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=8) as r:
        x=json.loads(r.read().decode('utf-8','replace'))
    return q,(x[1:] if isinstance(x,list) and x else [])

def exact_variants(url):
    p=urllib.parse.urlsplit(url)
    host=(p.hostname or 'compuboxonline.com').lower()
    path=p.path
    variants=[]
    for scheme in ('http','https'):
        for www in (False,True):
            for port in (False,True):
                h=('www.' if www else '')+'compuboxonline.com'+(':80' if port else '')
                v=urllib.parse.urlunsplit((scheme,h,path,'',''))
                if v not in variants:variants.append(v)
    return variants


def main():
    queries=[]
    grouped=defaultdict(list)

    # Preserve already discovered candidates. Automatic exact-seed passes
    # should not re-run every expensive wildcard query just to rediscover the
    # same 50 legacy URLs. Set COMPUBOX_REFRESH_WILDCARDS=1 for a full sweep.
    preserved=0
    if OUT.exists():
        try:
            old=json.loads(OUT.read_text())
            for item in old.get('items') or []:
                key=item.get('canonical_key')
                if not key:continue
                caps=item.get('captures') or []
                grouped[key].extend(caps)
                preserved+=1
        except Exception:
            pass

    rows=[]
    if os.environ.get('COMPUBOX_REFRESH_WILDCARDS')=='1' or not preserved:
        for p in PREFIXES:
            try:
                q,x=query(p)
                queries.append({'prefix':p,'query':q,'rows':len(x),'error':None})
                rows.extend(x)
            except Exception as e:
                queries.append({'prefix':p,'rows':0,'error':type(e).__name__+': '+str(e)[:200]})

    # Exact documented seeds are high-value but Wayback host variants can
    # individually stall. Probe variants concurrently with bounded workers,
    # then keep captures from every successful variant (dedupe happens below).
    jobs=[]
    with ThreadPoolExecutor(max_workers=12) as pool:
        for seed in KNOWN_URLS:
            for url in exact_variants(seed):
                jobs.append((seed,url,pool.submit(query_exact,url)))
        found_seeds=set()
        for seed,url,fut in jobs:
            try:
                q,x=fut.result()
                queries.append({'seed_url':seed,'exact_url':url,'query':q,'rows':len(x),'error':None})
                if x:
                    rows.extend(x);found_seeds.add(seed)
            except Exception as e:
                queries.append({'seed_url':seed,'exact_url':url,'rows':0,'error':type(e).__name__+': '+str(e)[:200]})
    for seed in KNOWN_URLS:
        if seed not in found_seeds:
            queries.append({'seed_url':seed,'status':'no_capture_found_across_variants'})

    for r in rows:
        if len(r)<5:continue
        ts,orig,status,mime,digest=map(str,r[:5])
        low=orig.lower()
        if not any(x in low for x in ('/stat_files/','/featured_stats/')):continue
        if not re.search(r'\.(?:html?|pdf)(?:$|[?#])',orig,re.I):continue
        key=re.sub(r'^https?://(?:www\.)?','',orig,flags=re.I).lower()
        grouped[key].append({'timestamp':ts,'original':orig,'mimetype':mime,'digest':digest,
          'snapshot_url':f'https://web.archive.org/web/{ts}id_/{orig}'})

    items=[]
    for key,caps in grouped.items():
        uniq={}
        for cap in caps:
            if not cap.get('timestamp') or not cap.get('original'):continue
            uniq[(cap.get('timestamp'),cap.get('digest'),cap.get('original'))]=cap
        caps=sorted(uniq.values(),key=lambda x:x['timestamp'])
        if not caps:continue
        items.append({'canonical_key':key,'capture_count':len(caps),'first_timestamp':caps[0]['timestamp'],
          'last_timestamp':caps[-1]['timestamp'],'captures':caps[-5:]})
    items.sort(key=lambda x:x['canonical_key'])
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'queries':queries,
      'preserved_candidate_urls':preserved,'raw_cdx_rows':len(rows),'unique_candidate_urls':len(items),
      'html_candidates':sum('.htm' in x['canonical_key'] for x in items),
      'pdf_candidates':sum('.pdf' in x['canonical_key'] for x in items),
      'items':items,
      'policy':'Discovery only. Existing candidates are preserved; exact documented URLs are probed by host/scheme variant. No punch values imported until exact archived snapshot, fight identity/date, both fighters, round sequence, and arithmetic checks pass.'}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('preserved_candidate_urls','raw_cdx_rows','unique_candidate_urls','html_candidates','pdf_candidates')},indent=2))

if __name__=='__main__':main()
