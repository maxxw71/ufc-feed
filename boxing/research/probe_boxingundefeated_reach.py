#!/usr/bin/env python3
"""Probe BoxingUndefeated as a third reach source for strict high-value gaps.

Read-only: produces diagnostics only and never updates verified_profiles.
"""
from __future__ import annotations
import json,re,unicodedata,urllib.request,urllib.error
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
GAP=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'boxingundefeated_reach_probe.json'
UA='Mozilla/5.0 AppwizaBoxingReachProbe/1.0'

def norm(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def slug(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    x=re.sub(r"['’]",'',x)
    return re.sub(r'[^a-z0-9]+','-',x).strip('-')

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=25) as r:
        raw=r.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('too large')
        return r.geturl(),raw

def parse(name,raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1')
    title=' '.join(h.stripped_strings) if h else ''
    # The site appends nickname punctuation to H1. Require the requested full
    # name at the start after normalization, and reject obvious other identities.
    if not norm(title).startswith(norm(name)):
        return None,{'reason':'identity','h1':title}
    text=' '.join(soup.stripped_strings)
    vals=[]
    # Structured card often renders "Reach 173"" while prose says 173cm.
    for m in re.finditer(r'\bReach\s+(\d{2,3})(?:\s*cm|["”])',text,re.I):
        v=int(m.group(1))
        if 120<=v<=230:vals.append(v)
    for m in re.finditer(r'\breach\s+(?:of\s+)?(?:\d{2}(?:\.\d+)?\s+inches?\s*\()?\s*(\d{3})\s*cm\b',text,re.I):
        v=int(m.group(1))
        if 120<=v<=230:vals.append(v)
    vals=sorted(set(vals))
    if len(vals)!=1:
        return None,{'reason':'reach_missing_or_conflict','values':vals,'h1':title}
    # Height is retained only as an identity sanity check/diagnostic.
    heights=[]
    for m in re.finditer(r'\bHeight\s+(\d{3})\s*cm\b',text,re.I):
        v=int(m.group(1))
        if 130<=v<=220:heights.append(v)
    return vals[0],{'h1':title,'height_cm':heights[0] if heights else None}

def main():
    gap=json.loads(GAP.read_text())
    targets=(gap.get('missing_ranked') or {}).get('reach_cm') or []
    targets=targets[:80]
    rows=[]
    for t in targets:
        name=t.get('name')
        url=f'https://boxingundefeated.com/boxers/{slug(name)}/'
        try:
            final,raw=fetch(url)
            reach,meta=parse(name,raw)
            rows.append({'name':name,'strict_bout_appearances':t.get('strict_bout_appearances'),
                         'url':final,'reach_cm':reach,**meta})
        except urllib.error.HTTPError as e:
            rows.append({'name':name,'strict_bout_appearances':t.get('strict_bout_appearances'),
                         'url':url,'reach_cm':None,'reason':f'http_{e.code}'})
        except Exception as e:
            rows.append({'name':name,'strict_bout_appearances':t.get('strict_bout_appearances'),
                         'url':url,'reach_cm':None,'reason':type(e).__name__+': '+str(e)[:120]})
    report={
      'targets':len(targets),
      'profiles_with_reach':sum(r.get('reach_cm') is not None for r in rows),
      'rows':rows,
      'policy':'Read-only exact-name third-source probe; no profile writes.'
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
