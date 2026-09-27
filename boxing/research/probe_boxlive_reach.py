#!/usr/bin/env python3
"""Probe Box.Live public boxer profiles for missing reach.

Uses deterministic public /boxers/<slug>/ URLs. A value is accepted only when
the page identity matches exactly and an explicit plausible Reach value is
present. Existing reach is never overwritten.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'boxlive_reach_probe.json'
ADD=ROOT/'profile_supplements'/'boxlive_reach_additions.jsonl'
BASE='https://box.live/boxers/'
UA='Mozilla/5.0 AppwizaBoxLiveReach/1.0'

def ascii_text(s):
    return unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode()

def nk(s):
    return re.sub(r'[^a-z0-9]+','',ascii_text(s).lower())

def slug(s):
    return re.sub(r'[^a-z0-9]+','-',ascii_text(s).lower()).strip('-')

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=25) as r:
        raw=r.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('response too large')
        return r.geturl(),raw

def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1')
    name=' '.join(h.stripped_strings) if h else ''
    text=' '.join(soup.stripped_strings)
    vals=[]
    for m in re.finditer(r'\bReach\s*:?\s*(\d+(?:\.\d+)?)\s*(?:inches|inch|in|["″])',text,re.I):
        cm=round(float(m.group(1))*2.54,2)
        if 120<=cm<=270:vals.append(cm)
    for m in re.finditer(r'\bReach\s*:?\s*(\d+(?:\.\d+)?)\s*cm\b',text,re.I):
        cm=float(m.group(1))
        if 120<=cm<=270:vals.append(cm)
    vals=sorted(set(round(v,2) for v in vals))
    return name,(vals[0] if len(vals)==1 else None),vals,text[:1200]

def main():
    audit=json.loads(AUDIT.read_text())
    targets=[x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])]
    def one(t):
        u=BASE+slug(t['name'])+'/'
        rec={'id':t['id'],'name':t['name'],'strict_bout_appearances':t.get('strict_bout_appearances'),'url':u}
        try:
            final,raw=fetch(u);name,reach,vals,sample=parse(raw)
            rec.update({'url':final,'h1':name,'reach_cm':reach,'reach_values':vals,'text_sample':sample})
            clean=re.sub(r'\s+boxer\s*$','',name,flags=re.I).strip()
            if nk(clean)!=nk(t['name']):rec['status']='identity_mismatch'
            elif reach is None:rec['status']='no_reach'
            else:rec['status']='accepted'
        except urllib.error.HTTPError as e:rec['status']=f'http_{e.code}'
        except Exception as e:rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:160]})
        return rec
    with cf.ThreadPoolExecutor(max_workers=8) as ex:rows=list(ex.map(one,targets))
    accepted=[x for x in rows if x['status']=='accepted']
    additions=[]
    for x in accepted:
        t=next(t for t in targets if t['id']==x['id']);reach=x['reach_cm']
        if float(reach).is_integer():reach=int(reach)
        additions.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'boxlive_public_profile','url':x['url'],'fields':{'reach_cm':reach},'exact_identity':True}],
          'conflicts':{},'quality':'public_boxing_profile_exact_identity_missing_reach_only',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'targets':len(targets),
      'status_counts':counts,'accepted':len(additions),
      'accepted_profiles':[{'name':x['name'],'reach_cm':x['fields']['reach_cm'],'url':x['evidence'][0]['url']} for x in additions],
      'rows':rows,
      'policy':'Box.Live public boxer profile exact identity + explicit plausible Reach; missing reach only; never overwrite.'}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({k:out[k] for k in ('targets','status_counts','accepted')},indent=2))
if __name__=='__main__':main()
