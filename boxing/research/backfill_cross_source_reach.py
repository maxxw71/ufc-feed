#!/usr/bin/env python3
"""Strict two-source reach backfill: MartialBot + Ready To Fight."""
from __future__ import annotations
import argparse,datetime as dt,json,re,time,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
SUP=ROOT/'profile_supplements'/'verified_profiles.jsonl'
ADD=ROOT/'profile_supplements'/'cross_source_reach_additions.jsonl'
REPORT=ROOT/'profile_supplements'/'cross_source_reach_report.json'
UA='Mozilla/5.0 AppwizaBoxingReachConsensus/1.0'
MB='https://www.martialbot.com/boxing/sitemap.xml'
RTF=[f'https://rtfight.com/sitemaps/en-profiles-{i}.xml' for i in range(1,7)]

def nk(s):
    s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',s)

def clean(s): return re.sub(r'\s+',' ',str(s or '')).strip()

def get(url,limit=12_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=40) as r:
        raw=r.read(limit+1)
        if len(raw)>limit: raise ValueError('response too large')
        return r.geturl(),raw

def locs(raw):
    root=ET.fromstring(raw)
    return [x.text.strip() for x in root.iter() if x.tag.endswith('loc') and x.text]

def cm(v):
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',clean(v),re.I)
    return float(m.group(1)) if m else None

def profile(raw,source):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1'); name=clean(h.get_text(' ',strip=True)) if h else ''
    text=clean(soup.get_text(' ',strip=True))
    pat=r'\bReach\s+(\d+(?:\.\d+)?\s*cm)\b' if source=='mb' else r'\bReach\s*:\s*(\d+(?:\.\d+)?\s*cm)\b'
    m=re.search(pat,text,re.I)
    return name,cm(m.group(1)) if m else None

def core(url,source):
    last=urllib.parse.urlsplit(url).path.rstrip('/').split('/')[-1]
    m=re.match(r'(.+)-[0-9a-f]{32}$',last,re.I) if source=='mb' else re.match(r'boxer-professional-(.+)-[a-z0-9]{5}$',last,re.I)
    return m.group(1) if m else None

def urlmap(urls,source):
    out={}
    for u in urls:
        c=core(u,source)
        if c: out.setdefault(nk(c),[]).append(u)
    return {k:list(dict.fromkeys(v)) for k,v in out.items()}

def readjsonl(path):
    out={}
    if not path.exists(): return out
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            x=json.loads(line); sid=x.get('target_source_id')
            if sid: out[sid]=x
    return out

def merge(path):
    cur=readjsonl(SUP); adds=readjsonl(path); merged=skipped=0
    for sid,a in adds.items():
        old=cur.get(sid)
        if old is None:
            cur[sid]=a; merged+=1; continue
        fields=dict(old.get('fields') or {})
        if fields.get('reach_cm') not in (None,''):
            skipped+=1; continue
        reach=(a.get('fields') or {}).get('reach_cm')
        if reach in (None,''): continue
        fields['reach_cm']=reach; old['fields']=fields
        ev=list(old.get('evidence') or [])
        marker='public_profile_cross_source_reach_consensus_exact_identity'
        ev.append((a.get('evidence') or [])[0]); old['evidence']=ev
        q=str(old.get('quality') or '')
        if marker not in q: old['quality']=(q+';'+marker).strip(';')
        old['collected_at']=a.get('collected_at') or old.get('collected_at')
        cur[sid]=old; merged+=1
    SUP.parent.mkdir(parents=True,exist_ok=True)
    with SUP.open('w',encoding='utf-8') as f:
        for x in sorted(cur.values(),key=lambda z:(z.get('name',''),z.get('target_source_id',''))):
            f.write(json.dumps(x,ensure_ascii=False)+'\n')
    print(json.dumps({'additions':len(adds),'merged':merged,'skipped_existing_reach':skipped,'total':len(cur)},indent=2))

def collect(limit,sleep):
    audit=json.loads(AUDIT.read_text(encoding='utf-8')); old=readjsonl(SUP)
    targets=[x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])
             and ((old.get(x.get('id')) or {}).get('fields') or {}).get('reach_cm') in (None,'')]
    targets.sort(key=lambda x:(-int(x.get('strict_bout_appearances') or 0),x.get('name') or '')); targets=targets[:limit]
    _,raw=get(MB); mb=urlmap(locs(raw),'mb')
    rurls=[]; sdiag=[]
    for u in RTF:
        try:
            _,raw=get(u); xs=locs(raw); rurls.extend(xs); sdiag.append({'url':u,'loc_count':len(xs)})
        except Exception as e: sdiag.append({'url':u,'error':repr(e)})
    rtf=urlmap(rurls,'rtf')
    keys=['unique_pairs','fetched_pairs','accepted','missing_mb_url','missing_rtf_url','ambiguous_url','missing_mb_reach','missing_rtf_reach','identity_mismatch','reach_conflict','fetch_error']
    stats={k:0 for k in keys}; adds=[]; rejects=[]
    for x in targets:
        name=x['name']; key=nk(name); a=mb.get(key,[]); b=rtf.get(key,[])
        if not a: stats['missing_mb_url']+=1; continue
        if not b: stats['missing_rtf_url']+=1; continue
        if len(a)!=1 or len(b)!=1: stats['ambiguous_url']+=1; continue
        stats['unique_pairs']+=1
        try:
            af,araw=get(a[0]); bf,braw=get(b[0]); stats['fetched_pairs']+=1
            an,av=profile(araw,'mb'); bn,bv=profile(braw,'rtf')
            if nk(an)!=key or nk(bn)!=key:
                stats['identity_mismatch']+=1; rejects.append({'name':name,'reason':'identity','mb_h1':an,'rtf_h1':bn}); continue
            if av is None: stats['missing_mb_reach']+=1; continue
            if bv is None: stats['missing_rtf_reach']+=1; continue
            if not (120<=av<=270 and 120<=bv<=270) or abs(av-bv)>1:
                stats['reach_conflict']+=1; rejects.append({'name':name,'reason':'reach_conflict','mb':av,'rtf':bv}); continue
            reach=round((av+bv)/2,2); reach=int(reach) if float(reach).is_integer() else reach
            now=dt.datetime.now(dt.timezone.utc).isoformat()
            adds.append({'target_source_id':x['id'],'name':name,'career_source':x.get('career_source'),
              'fields':{'reach_cm':reach},
              'evidence':[{'source':'cross_source_reach_consensus','fields':{'reach_cm':reach},'exact_identity':True,
                'agreement_tolerance_cm':1,'sources':[{'source':'martialbot','url':af,'reported_reach_cm':av},
                {'source':'ready_to_fight','url':bf,'reported_reach_cm':bv}]}],
              'conflicts':{},'quality':'public_profile_cross_source_reach_consensus_exact_identity','collected_at':now})
            stats['accepted']+=1
        except Exception as e:
            stats['fetch_error']+=1; rejects.append({'name':name,'reason':'fetch_error','error':repr(e)[:220]})
        time.sleep(max(0,sleep))
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w',encoding='utf-8') as f:
        for x in sorted(adds,key=lambda z:z.get('name','')): f.write(json.dumps(x,ensure_ascii=False)+'\n')
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'targets':len(targets),**stats,
      'martialbot_exact_keys':len(mb),'rtf_english_urls':len(rurls),'rtf_exact_keys':len(rtf),'rtf_sitemaps':sdiag,
      'accepted_profiles':[{'name':x['name'],'reach_cm':x['fields']['reach_cm']} for x in adds],'rejects_sample':rejects[:100],
      'policy':'Unique exact-name URL on both sources; exact H1 on both; both numeric reaches; <=1 cm agreement; missing reach only; never overwrite.'}
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'accepted_profiles','rejects_sample','rtf_sitemaps'}},indent=2))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--limit',type=int,default=300); ap.add_argument('--sleep',type=float,default=.05)
    ap.add_argument('--merge-only',action='store_true'); ap.add_argument('--additions',default=str(ADD)); args=ap.parse_args()
    merge(Path(args.additions)) if args.merge_only else collect(args.limit,args.sleep)

if __name__=='__main__': main()
