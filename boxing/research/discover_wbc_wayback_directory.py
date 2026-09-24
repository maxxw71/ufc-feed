#!/usr/bin/env python3
"""Discover older official WBC ratings PDFs through Wayback CDX directory indexes.

Read-only discovery: it records official wbcboxing.com PDF captures and does not
add rankings until the PDF itself is fetched, parsed, and passes the existing
WBC integrity checks.
"""
from __future__ import annotations
import datetime as dt,json,re,urllib.parse,urllib.request
from pathlib import Path

OUT=Path('boxing/rankings/wbc_wayback_directory_discovery.json')
UA='Mozilla/5.0 AppwizaWBCArchiveDiscovery/1.0'
YEARS=range(2018,2025)

def get_json(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=60) as r:
        return json.loads(r.read().decode('utf-8','replace'))

def query(pattern):
    params=[
      ('url',pattern),('output','json'),('filter','statuscode:200'),
      ('filter','mimetype:application/pdf'),('fl','timestamp,original,statuscode,mimetype,digest'),
      ('collapse','urlkey'),('limit','500')
    ]
    url='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode(params)
    try:
        rows=get_json(url)
        return url,(rows[1:] if isinstance(rows,list) and rows else []),None
    except Exception as e:
        return url,[],type(e).__name__+': '+str(e)[:240]

def main():
    out=[];seen=set()
    for year in YEARS:
        patterns=[
          f'https://wbcboxing.com/mailing/{year}/ratings_pdf/*',
          f'http://wbcboxing.com/mailing/{year}/ratings_pdf/*',
          f'https://www.wbcboxing.com/mailing/{year}/ratings_pdf/*',
          f'http://www.wbcboxing.com/mailing/{year}/ratings_pdf/*',
          f'https://wbcboxing.com/mailing/{year}/*',
        ]
        for pattern in patterns:
            q,rows,error=query(pattern)
            rec={'year':year,'pattern':pattern,'query':q,'error':error,'captures':[]}
            for row in rows:
                if len(row)<5:continue
                ts,orig,status,mime,digest=row[:5]
                low=str(orig).lower()
                if '.pdf' not in low:continue
                if not re.search(r'(rating|ratings|clasific)',low,re.I):continue
                key=(ts,orig,digest)
                if key in seen:continue
                seen.add(key)
                rec['captures'].append({'timestamp':ts,'original':orig,'digest':digest,
                                        'snapshot':f'https://web.archive.org/web/{ts}id_/{orig}'})
            out.append(rec)
    flat=[x for rec in out for x in rec['captures']]
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'years':[min(YEARS),max(YEARS)],
      'query_count':len(out),
      'unique_candidate_captures':len(flat),
      'unique_original_urls':len({x['original'] for x in flat}),
      'captures':sorted(flat,key=lambda x:(x['timestamp'],x['original'])),
      'queries':out,
      'policy':'Discovery only; official wbcboxing.com PDF URLs from Wayback index. No ranking data accepted until PDF bytes parse and existing integrity checks pass.'
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('generated_at','years','query_count','unique_candidate_captures','unique_original_urls')},indent=2))

if __name__=='__main__':main()
