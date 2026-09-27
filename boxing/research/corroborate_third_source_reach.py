#!/usr/bin/env python3
"""Corroborate BoxingUndefeated reach leads against MartialBot or Ready To Fight.

Accept only exact identity and <=1 cm agreement with at least one independent
second source. Missing reach only; never overwrite an existing verified reach.
"""
from __future__ import annotations
import datetime as dt,json,re,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
PROBE=ROOT/'profile_supplements'/'boxingundefeated_reach_probe.json'
GAP=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
SUP=ROOT/'profile_supplements'/'verified_profiles.jsonl'
ADD=ROOT/'profile_supplements'/'third_source_reach_additions.jsonl'
REPORT=ROOT/'profile_supplements'/'third_source_reach_corroboration.json'
UA='Mozilla/5.0 AppwizaReachCorroboration/1.0'
MB='https://www.martialbot.com/boxing/sitemap.xml'
RTF=[f'https://rtfight.com/sitemaps/en-profiles-{i}.xml' for i in range(1,7)]

def nk(s):
    s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',s)

def get(url,limit=12_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=40) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def locs(raw):
    root=ET.fromstring(raw)
    return [x.text.strip() for x in root.iter() if x.tag.endswith('loc') and x.text]

def core(url,source):
    last=urllib.parse.urlsplit(url).path.rstrip('/').split('/')[-1]
    m=re.match(r'(.+)-[0-9a-f]{32}$',last,re.I) if source=='mb' else re.match(r'boxer-professional-(.+)-[a-z0-9]{5}$',last,re.I)
    return m.group(1) if m else None

def urlmap(urls,source):
    out={}
    for u in urls:
        c=core(u,source)
        if c:out.setdefault(nk(c),[]).append(u)
    return {k:list(dict.fromkeys(v)) for k,v in out.items()}

def profile(raw,source):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1');name=' '.join(h.stripped_strings) if h else ''
    text=' '.join(soup.stripped_strings)
    pat=r'\bReach\s+(\d+(?:\.\d+)?\s*cm)\b' if source=='mb' else r'\bReach\s*:\s*(\d+(?:\.\d+)?\s*cm)\b'
    m=re.search(pat,text,re.I)
    return name,float(re.search(r'\d+(?:\.\d+)?',m.group(1)).group()) if m else None

def readjsonl(path):
    out={}
    if not path.exists():return out
    for line in path.read_text().splitlines():
        if line.strip():
            x=json.loads(line);sid=x.get('target_source_id')
            if sid:out[sid]=x
    return out

def main():
    probe=json.loads(PROBE.read_text())
    leads=[x for x in probe.get('rows',[]) if x.get('reach_cm') is not None]
    gap=json.loads(GAP.read_text())
    fmap={nk(x.get('name')):x for x in gap.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    existing=readjsonl(SUP)

    _,raw=get(MB);mb=urlmap(locs(raw),'mb')
    rurls=[]
    for u in RTF:
        try:
            _,raw=get(u);rurls.extend(locs(raw))
        except Exception:pass
    rtf=urlmap(rurls,'rtf')

    accepted=[];rows=[]
    for lead in leads:
        name=lead['name'];key=nk(name);target=fmap.get(key)
        if not target:continue
        sid=target.get('id')
        if ((existing.get(sid) or {}).get('fields') or {}).get('reach_cm') not in (None,''):
            rows.append({'name':name,'status':'already_filled'});continue
        third=float(lead['reach_cm'])
        evidence=[{'source':'boxingundefeated','url':lead.get('url'),'reported_reach_cm':third}]
        agreements=[]
        for source,index in [('martialbot',mb),('ready_to_fight',rtf)]:
            urls=index.get(key,[])
            if len(urls)!=1:continue
            try:
                final,raw=get(urls[0]);h,v=profile(raw,'mb' if source=='martialbot' else 'rtf')
                if nk(h)!=key or v is None:continue
                evidence.append({'source':source,'url':final,'reported_reach_cm':v})
                if abs(v-third)<=1:agreements.append(v)
            except Exception:continue
        if not agreements:
            rows.append({'name':name,'status':'no_independent_agreement','third_reach_cm':third,'evidence':evidence});continue
        vals=[third]+agreements
        reach=round(sum(vals)/len(vals),2)
        if float(reach).is_integer():reach=int(reach)
        now=dt.datetime.now(dt.timezone.utc).isoformat()
        item={'target_source_id':sid,'name':name,'career_source':target.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'multi_source_reach_consensus','fields':{'reach_cm':reach},
                       'exact_identity':True,'agreement_tolerance_cm':1,'sources':evidence}],
          'conflicts':{},'quality':'public_profile_multi_source_reach_consensus_exact_identity','collected_at':now}
        accepted.append(item)
        rows.append({'name':name,'status':'accepted','reach_cm':reach,'evidence':evidence})

    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in accepted:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'lead_count':len(leads),
      'accepted':len(accepted),'accepted_profiles':[{'name':x['name'],'reach_cm':x['fields']['reach_cm']} for x in accepted],
      'rows':rows,'policy':'BoxingUndefeated exact-name lead plus at least one exact-name MartialBot/Ready To Fight source within 1 cm; missing reach only; no overwrite.'}
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
