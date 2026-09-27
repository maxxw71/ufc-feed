#!/usr/bin/env python3
"""Probe BoxRec public profile pages for reach on strict missing-reach fighters.

Read-only diagnostic. Uses BoxRec IDs already present in source profile metadata
when available; it does not search or guess IDs and never writes verified data.
"""
from __future__ import annotations
import json,os,re,sqlite3,urllib.request,urllib.error
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
DB=Path(os.environ.get('BOXING_DB','/tmp/boxing_research.sqlite3'))
GAP=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'boxrec_reach_probe.json'
UA='Mozilla/5.0 AppwizaBoxingReachProbe/1.0'

def norm(s):
    import unicodedata
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def boxrec_id(attrs):
    if not isinstance(attrs,dict):return None
    for k,v in attrs.items():
        if norm(k) in {'boxrec','boxrecid','boxrecno','boxrecnumber'}:
            m=re.search(r'\d{3,9}',str(v or ''))
            if m:return m.group(0)
    return None

def parse_reach(name,raw):
    soup=BeautifulSoup(raw,'lxml')
    text=' '.join(soup.stripped_strings)
    # Exact public profile heading if present.
    h=soup.find('h1')
    if h and norm(name) not in norm(h.get_text(' ',strip=True)):
        return None,{'reason':'identity','h1':h.get_text(' ',strip=True)}
    pats=[
      r'\breach\b[^0-9]{0,50}(\d{2}(?:\.\d+)?)\s*[″\"”]',
      r'\breach\b[^0-9]{0,50}(\d{3})\s*cm\b',
    ]
    vals=[]
    for i,p in enumerate(pats):
        for m in re.finditer(p,text,re.I):
            x=float(m.group(1))
            cm=x*2.54 if i==0 else x
            if 120<=cm<=230:vals.append(round(cm))
    vals=sorted(set(vals))
    if len(vals)==1:return vals[0],{}
    return None,{'reason':'missing_or_conflict','values':vals}

def main():
    gap=json.loads(GAP.read_text())
    targets=(gap.get('missing_ranked') or {}).get('reach_cm') or []
    want={norm(x.get('name')):x for x in targets}
    db=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);db.row_factory=sqlite3.Row
    tabs={r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    rows=[]
    if 'fighters' in tabs:
        cols={r[1] for r in db.execute('pragma table_info(fighters)')}
        if {'name','snapshot'}<=cols:
            for r in db.execute('select name,snapshot from fighters'):
                key=norm(r['name'])
                if key not in want:continue
                try:s=json.loads(r['snapshot'] or '{}')
                except Exception:continue
                attrs=s.get('attributes') or s
                bid=boxrec_id(attrs)
                if bid:rows.append((want[key],bid))
    db.close()
    seen=set();out=[]
    for t,bid in rows:
        if (norm(t.get('name')),bid) in seen:continue
        seen.add((norm(t.get('name')),bid))
        name=t.get('name');url=f'https://boxrec.com/en/box-pro/{bid}'
        item={'name':name,'boxrec_id':bid,'url':url,'strict_bout_appearances':t.get('strict_bout_appearances')}
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
            with urllib.request.urlopen(req,timeout=20) as r:raw=r.read(2_000_001)
            reach,meta=parse_reach(name,raw);item.update(meta);item['reach_cm']=reach
        except urllib.error.HTTPError as e:item.update({'reach_cm':None,'reason':f'http_{e.code}'})
        except Exception as e:item.update({'reach_cm':None,'reason':type(e).__name__+': '+str(e)[:140]})
        out.append(item)
    report={'targets_with_embedded_boxrec_id':len(out),'profiles_with_reach':sum(x.get('reach_cm') is not None for x in out),'rows':out,
      'policy':'Read-only exact BoxRec-ID probe from already-held profile metadata; never guesses IDs; no verified profile writes.'}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
