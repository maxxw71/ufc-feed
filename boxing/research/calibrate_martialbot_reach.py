#!/usr/bin/env python3
"""Calibrate MartialBot reach reliability against existing strict boxing reaches.

This does NOT merge data. It measures exact-identity MartialBot reach values
against already-verified strict reaches across the current 723-fighter sample.
The result is used to decide whether MartialBot can qualify as a validated
single-source reach provider for otherwise-missing fighters.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,statistics,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'martialbot_reach_calibration.json'
UA='Mozilla/5.0 AppwizaMartialBotReachCalibration/1.0'
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
    mr=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    mh=re.search(r'\bHeight\s+(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    return name,(float(mr.group(1)) if mr else None),(float(mh.group(1)) if mh else None)

def main():
    audit=json.loads(AUDIT.read_text())
    _,raw=fetch(SITE);idx=sitemap_map(raw)
    fighters=audit.get('fighters') or []

    jobs=[]
    for x in fighters:
        strict=(x.get('present') or {}).get('reach_cm')
        if strict is None:continue
        key=nk(x['name']);urls=idx.get(key,[])
        if len(urls)==1:jobs.append((x,urls[0]))

    def one(job):
        x,u=job
        rec={'id':x['id'],'name':x['name'],'strict_reach_cm':float((x.get('present') or {}).get('reach_cm')),
             'strict_height_cm':(x.get('present') or {}).get('height_cm'),'url':u}
        try:
            final,raw=fetch(u,1_500_000);h,r,height=parse(raw)
            rec.update({'final_url':final,'h1':h,'martialbot_reach_cm':r,'martialbot_height_cm':height})
            if nk(h)!=nk(x['name']):rec['status']='identity_mismatch'
            elif r is None:rec['status']='no_reach'
            elif not 120<=r<=270:rec['status']='implausible_reach'
            else:
                rec['status']='usable'
                rec['abs_diff_cm']=round(abs(float(r)-rec['strict_reach_cm']),2)
                rec['signed_diff_cm']=round(float(r)-rec['strict_reach_cm'],2)
                rec['martialbot_reach_equals_height']=height is not None and abs(float(r)-float(height))<=0.5
                sh=rec['strict_height_cm']
                rec['strict_reach_equals_height']=sh is not None and abs(rec['strict_reach_cm']-float(sh))<=0.5
        except Exception as e:rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:160]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=8) as ex:rows=list(ex.map(one,jobs))
    usable=[x for x in rows if x.get('status')=='usable']
    diffs=[x['abs_diff_cm'] for x in usable]
    signed=[x['signed_diff_cm'] for x in usable]
    def countle(v):return sum(d<=v for d in diffs)
    suspicious=[x for x in usable if x.get('martialbot_reach_equals_height') and not x.get('strict_reach_equals_height') and x['abs_diff_cm']>1]
    out={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'strict_reach_fighters':sum((x.get('present') or {}).get('reach_cm') is not None for x in fighters),
      'exact_sitemap_overlap_jobs':len(jobs),'usable_overlap':len(usable),
      'exact_match_count':sum(d==0 for d in diffs),
      'within_1cm_count':countle(1),'within_2cm_count':countle(2),'within_3cm_count':countle(3),
      'within_1cm_pct':round(100*countle(1)/len(usable),2) if usable else None,
      'within_2cm_pct':round(100*countle(2)/len(usable),2) if usable else None,
      'median_abs_diff_cm':round(statistics.median(diffs),2) if diffs else None,
      'mean_abs_diff_cm':round(statistics.mean(diffs),2) if diffs else None,
      'median_signed_diff_cm':round(statistics.median(signed),2) if signed else None,
      'large_error_gt5cm':sum(d>5 for d in diffs),
      'suspicious_reach_equals_height_errors':len(suspicious),
      'suspicious_samples':suspicious[:40],
      'status_counts':{k:sum(x.get('status')==k for x in rows) for k in sorted({x.get('status') for x in rows})},
      'rows':rows,
      'policy':'Calibration only; exact MartialBot sitemap slug + exact H1 identity. Compare against pre-existing strict reach values; no missing reach is merged by this audit.'
    }
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({k:out[k] for k in (
      'strict_reach_fighters','exact_sitemap_overlap_jobs','usable_overlap','exact_match_count',
      'within_1cm_count','within_1cm_pct','within_2cm_count','within_2cm_pct',
      'median_abs_diff_cm','mean_abs_diff_cm','large_error_gt5cm','suspicious_reach_equals_height_errors'
    )},indent=2))
if __name__=='__main__':main()
