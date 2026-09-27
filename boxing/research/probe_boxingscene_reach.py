#!/usr/bin/env python3
"""Probe BoxingScene fighter profiles for strict missing-reach targets.

Discovery only. A lead is recorded only when the requested fighter maps to a
BoxingScene fighter page whose H1 normalizes exactly to the target name and the
page exposes a numeric Reach value. Leads are not merged automatically.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'boxingscene_reach_probe.json'
UA='Mozilla/5.0 AppwizaBoxingSceneReach/1.0'

def ascii_text(s):
    return unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode()

def nk(s):
    return re.sub(r'[^a-z0-9]+','',ascii_text(s).lower())

def slug(s):
    return re.sub(r'[^a-z0-9]+','-',ascii_text(s).lower()).strip('-')

def get(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=18) as r:
        raw=r.read(1_500_001)
        if len(raw)>1_500_000:raise ValueError('response too large')
        return r.geturl(),raw

def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1')
    name=' '.join(h.stripped_strings) if h else ''
    text=' '.join(soup.stripped_strings)
    # Current fighter pages render e.g. Reach 70" / 179cm.
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*[″"]?\s*/\s*(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    if m:return name,float(m.group(2))
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    if m:return name,float(m.group(1))
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*(?:in|inch|inches|[″"])\b',text,re.I)
    if m:return name,round(float(m.group(1))*2.54,2)
    return name,None

def worker(x):
    name=x['name'];u='https://www.boxingscene.com/fighters/'+slug(name)
    rec={'id':x.get('id'),'name':name,'strict_bout_appearances':x.get('strict_bout_appearances'),'candidate_url':u}
    try:
        final,raw=get(u);h,reach=parse(raw)
        rec.update({'status':'fetched','final_url':final,'h1':h,'reach_cm':reach,
                    'exact_identity':nk(h)==nk(name)})
        if not rec['exact_identity']:rec['status']='identity_mismatch'
        elif reach is None:rec['status']='no_reach'
        elif not 120<=reach<=270:rec['status']='implausible_reach'
        else:rec['status']='lead'
    except Exception as e:
        rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
    return rec

def main():
    audit=json.loads(AUDIT.read_text())
    targets=[x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])]
    targets.sort(key=lambda x:(-int(x.get('strict_bout_appearances') or 0),x.get('name') or ''))
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        rows=list(ex.map(worker,targets))
    leads=[r for r in rows if r['status']=='lead']
    counts={}
    for r in rows:counts[r['status']]=counts.get(r['status'],0)+1
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'targets':len(targets),
         'status_counts':counts,'lead_count':len(leads),
         'leads':[{'id':r['id'],'name':r['name'],'reach_cm':r['reach_cm'],'url':r.get('final_url'),
                   'strict_bout_appearances':r.get('strict_bout_appearances')} for r in leads],
         'rows':rows,
         'policy':'Discovery only; exact normalized H1 identity + numeric plausible reach required. No automatic merge without independent corroboration.'}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({'targets':len(targets),'lead_count':len(leads),'status_counts':counts},indent=2))
if __name__=='__main__':main()
