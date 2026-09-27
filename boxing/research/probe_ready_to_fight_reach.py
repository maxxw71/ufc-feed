#!/usr/bin/env python3
"""Probe Ready To Fight public boxer profiles for missing reach.

Uses public profile sitemaps and exact normalized profile identity. Automatic
addition requires independent MartialBot agreement within 1 cm.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
OUT=ROOT/'profile_supplements'/'ready_to_fight_reach_probe.json'
ADD=ROOT/'profile_supplements'/'ready_to_fight_reach_additions.jsonl'
UA='Mozilla/5.0 AppwizaRTFReach/1.0'
SITEMAPS=[f'https://rtfight.com/sitemaps/en-profiles-{i}.xml' for i in range(1,11)]

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def get(url,limit=12_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=40) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def locs(raw):
    root=ET.fromstring(raw)
    return [x.text.strip() for x in root.iter() if x.tag.endswith('loc') and x.text]

def key_from_url(url):
    last=urllib.parse.urlsplit(url).path.rstrip('/').split('/')[-1]
    m=re.match(r'boxer-professional-(.+)-[a-z0-9]{5}$',last,re.I)
    return nk(m.group(1)) if m else None

def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1');name=' '.join(h.stripped_strings) if h else ''
    text=' '.join(soup.stripped_strings)
    vals=[]
    for m in re.finditer(r'\bReach\s*:?\s*(\d+(?:\.\d+)?)\s*cm\b',text,re.I):
        v=float(m.group(1))
        if 120<=v<=270:vals.append(v)
    vals=sorted(set(round(v,2) for v in vals))
    return name,(vals[0] if len(vals)==1 else None),vals

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={nk(x['name']):x for x in mbobj.get('leads',[])}

    idx={};diag=[]
    for u in SITEMAPS:
        try:
            final,raw=get(u)
            ls=locs(raw);n=0
            for x in ls:
                k=key_from_url(x)
                if k and k in targets:
                    idx.setdefault(k,[]).append(x);n+=1
            diag.append({'url':u,'status':'ok','locs':len(ls),'matched_target_urls':n})
        except Exception as e:
            diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})

    jobs=[]
    for k,t in targets.items():
        urls=list(dict.fromkeys(idx.get(k,[])))
        if len(urls)==1:jobs.append((k,t,urls[0]))
    def one(job):
        k,t,u=job
        rec={'id':t['id'],'name':t['name'],'url':u,'strict_bout_appearances':t.get('strict_bout_appearances')}
        try:
            final,raw=get(u,2_000_000);h,reach,vals=parse(raw)
            rec.update({'url':final,'h1':h,'reach_cm':reach,'reach_values':vals})
            if nk(h)!=k:rec['status']='identity_mismatch'
            elif reach is None:rec['status']='no_reach'
            else:
                rec['status']='lead'
                m=mb.get(k)
                if m and m.get('reach_cm') is not None:
                    rec['martialbot_reach_cm']=m['reach_cm']
                    rec['agrees_martialbot']=abs(float(m['reach_cm'])-float(reach))<=1
        except Exception as e:rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=8) as ex:rows=list(ex.map(one,jobs))
    leads=[x for x in rows if x['status']=='lead']
    corr=[x for x in leads if x.get('agrees_martialbot') is True]
    conflicts=[x for x in leads if x.get('agrees_martialbot') is False]
    additions=[]
    for x in corr:
        t=targets[nk(x['name'])];m=mb[nk(x['name'])]
        reach=round((float(x['reach_cm'])+float(m['reach_cm']))/2,2)
        if float(reach).is_integer():reach=int(reach)
        additions.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'ready_to_fight_martialbot_reach_consensus','fields':{'reach_cm':reach},
            'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':[{'source':'ready_to_fight','url':x['url'],'reported_reach_cm':x['reach_cm']},
                       {'source':'martialbot','url':m['url'],'reported_reach_cm':m['reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_rtf_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'targets_with_unique_rtf_profile':len(jobs),'status_counts':counts,'lead_count':len(leads),
      'martialbot_corroborated':len(corr),'martialbot_conflicts':len(conflicts),
      'corroborated_profiles':[{'name':x['name'],'rtf_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x['martialbot_reach_cm'],'url':x['url']} for x in corr],
      'conflicting_profiles':[{'name':x['name'],'rtf_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x.get('martialbot_reach_cm'),'url':x['url']} for x in conflicts],
      'sitemap_diagnostics':diag,'rows':rows,
      'policy':'Public Ready To Fight sitemap exact profile slug + exact H1 + plausible reach. Automatic addition only when independent exact-identity MartialBot agrees within 1 cm.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_targets','targets_with_unique_rtf_profile','status_counts','lead_count','martialbot_corroborated','martialbot_conflicts')},indent=2))

if __name__=='__main__':main()
