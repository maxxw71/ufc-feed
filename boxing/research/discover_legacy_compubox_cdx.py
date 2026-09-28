#!/usr/bin/env python3
"""Discover archived legacy CompuBox full-stat pages through Wayback CDX.

Discovery only. Finds archived stat_files / featured_stats URLs and records
candidate snapshots for later strict identity/date parsing.
"""
from __future__ import annotations
import datetime as dt,json,re,urllib.parse,urllib.request
from collections import defaultdict
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
]


def query(prefix):
    q='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode([
      ('url',prefix+'*'),('output','json'),('filter','statuscode:200'),
      ('fl','timestamp,original,statuscode,mimetype,digest'),('limit','10000')
    ])
    req=urllib.request.Request(q,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=90) as r:
        x=json.loads(r.read().decode('utf-8','replace'))
    return q,(x[1:] if isinstance(x,list) and x else [])

def query_exact(url):
    q='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode([
      ('url',url),('output','json'),('filter','statuscode:200'),
      ('fl','timestamp,original,statuscode,mimetype,digest'),('limit','1000')
    ])
    req=urllib.request.Request(q,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=90) as r:
        x=json.loads(r.read().decode('utf-8','replace'))
    return q,(x[1:] if isinstance(x,list) and x else [])


def main():
    rows=[];queries=[]
    for p in PREFIXES:
        try:
            q,x=query(p);queries.append({'prefix':p,'query':q,'rows':len(x),'error':None});rows.extend(x)
        except Exception as e:
            queries.append({'prefix':p,'rows':0,'error':type(e).__name__+': '+str(e)[:200]})
    for url in KNOWN_URLS:
        try:
            q,x=query_exact(url);queries.append({'exact_url':url,'query':q,'rows':len(x),'error':None});rows.extend(x)
        except Exception as e:
            queries.append({'exact_url':url,'rows':0,'error':type(e).__name__+': '+str(e)[:200]})
    grouped=defaultdict(list)
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
        for c in caps:uniq[(c['timestamp'],c['digest'],c['original'])]=c
        caps=sorted(uniq.values(),key=lambda x:x['timestamp'])
        items.append({'canonical_key':key,'capture_count':len(caps),'first_timestamp':caps[0]['timestamp'],
          'last_timestamp':caps[-1]['timestamp'],'captures':caps[-5:]})
    items.sort(key=lambda x:x['canonical_key'])
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'queries':queries,
      'raw_cdx_rows':len(rows),'unique_candidate_urls':len(items),
      'html_candidates':sum('.htm' in x['canonical_key'] for x in items),
      'pdf_candidates':sum('.pdf' in x['canonical_key'] for x in items),
      'items':items,
      'policy':'Discovery only. No punch values imported until exact archived snapshot, fight identity/date, both fighters, round sequence, and arithmetic checks pass.'}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('raw_cdx_rows','unique_candidate_urls','html_candidates','pdf_candidates')},indent=2))
if __name__=='__main__':main()
