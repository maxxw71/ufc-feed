#!/usr/bin/env python3
"""Strict multi-source height recovery for current boxing profile gaps.

Sources:
- MartialBot public profile (live)
- Ready To Fight public profile (live)
- BoxingMetrics cached exact-sitemap profile probe
- BoxerList cached exact-profile probe

Promotion rule:
- currently missing strict height only;
- exact normalized identity for live profiles;
- plausible 120-250 cm;
- at least two independent source families;
- all retained source-family values agree within 1.01 cm;
- within-source ambiguity/conflict is excluded;
- no overwrite of an existing height.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,statistics,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
BM=ROOT/'profile_supplements'/'boxingmetrics_reach_probe.json'
BL=ROOT/'profile_supplements'/'boxerlist_reach_probe.json'
OUT=ROOT/'profile_supplements'/'multi_source_height_probe.json'
ADD=ROOT/'profile_supplements'/'strict_height_additions.jsonl'
UA='Mozilla/5.0 AppwizaHeightConsensus/1.0'
MB_SITE='https://www.martialbot.com/boxing/sitemap.xml'
RTF_SITEMAPS=[f'https://rtfight.com/sitemaps/en-profiles-{i}.xml' for i in range(1,11)]

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,limit=12_000_000,timeout=35):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def xml_locs(raw):
    root=ET.fromstring(raw)
    return [(x.text or '').strip() for x in root.iter() if x.tag.endswith('loc') and (x.text or '').strip()]

def mb_map(raw):
    out={}
    for u in xml_locs(raw):
        last=urllib.parse.urlsplit(u).path.rstrip('/').split('/')[-1]
        m=re.match(r'(.+)-[0-9a-f]{32}$',last,re.I)
        if m:out.setdefault(nk(m.group(1)),[]).append(u)
    return {k:list(dict.fromkeys(v)) for k,v in out.items()}

def rtf_map():
    out={};diag=[]
    for u in RTF_SITEMAPS:
        try:
            final,raw=fetch(u);ls=xml_locs(raw);n=0
            for x in ls:
                last=urllib.parse.urlsplit(x).path.rstrip('/').split('/')[-1]
                m=re.match(r'boxer-professional-(.+)-[a-z0-9]{5}$',last,re.I)
                if not m:continue
                out.setdefault(nk(m.group(1)),[]).append(x);n+=1
            diag.append({'url':u,'status':'ok','locs':len(ls),'profile_urls':n})
        except Exception as e:
            diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    return {k:list(dict.fromkeys(v)) for k,v in out.items()},diag

def parse_height(raw,source):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1');name=' '.join(h.stripped_strings).strip() if h else ''
    text=' '.join(soup.stripped_strings)
    pats=[
      r'\bHeight\s*:?[ ]*(\d+(?:\.\d+)?)\s*cm\b',
      r'\bHeight\s+(\d+(?:\.\d+)?)\s*cm\b'
    ]
    vals=[]
    for pat in pats:
        for m in re.finditer(pat,text,re.I):
            v=float(m.group(1))
            if 120<=v<=250:vals.append(v)
    vals=sorted({round(x,2) for x in vals})
    return name,(vals[0] if len(vals)==1 else None),vals

def cached_values(path,targets,source):
    if not path.exists():return {}
    obj=json.loads(path.read_text())
    fam={}
    for r in obj.get('rows') or []:
        key=r.get('target_key') or r.get('url_key') or nk(r.get('h1'))
        if key not in targets:continue
        status=str(r.get('status') or '')
        if status.startswith('identity_mismatch'):continue
        v=r.get('page_height_cm')
        try:v=float(v)
        except Exception:continue
        if not 120<=v<=250:continue
        fam.setdefault(key,[]).append({'source':source,'url':r.get('url'),'height_cm':round(v,2)})
    out={}
    for k,items in fam.items():
        vals=sorted({x['height_cm'] for x in items})
        if vals and max(vals)-min(vals)<=1.01:
            out[k]={'height_cm':round(statistics.median(vals),2),'items':items}
    return out

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'height_cm' in set(x.get('missing') or [])}
    _,raw=fetch(MB_SITE);mb=mb_map(raw)
    rtf,rtf_diag=rtf_map()
    cached_bm=cached_values(BM,targets,'boxingmetrics')
    cached_bl=cached_values(BL,targets,'boxerlist')

    jobs=[]
    for k,t in targets.items():
        mus=mb.get(k,[])
        rus=rtf.get(k,[])
        jobs.append((k,t,mus[0] if len(mus)==1 else None,rus[0] if len(rus)==1 else None))

    def one(job):
        k,t,mu,ru=job
        rec={'target_source_id':t['id'],'name':t['name'],'strict_bout_appearances':t.get('strict_bout_appearances'),'sources':[]}
        for source,u in [('martialbot',mu),('ready_to_fight',ru)]:
            if not u:continue
            try:
                final,raw=fetch(u,2_000_000);h,v,vals=parse_height(raw,source)
                item={'source':source,'url':final,'h1':h,'height_cm':v,'height_values':vals}
                if nk(h)!=k:item['status']='identity_mismatch'
                elif v is None:item['status']='no_unique_height'
                else:item['status']='lead'
                rec['sources'].append(item)
            except Exception as e:
                rec['sources'].append({'source':source,'url':u,'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        for cache in (cached_bm,cached_bl):
            x=cache.get(k)
            if x:
                rec['sources'].append({'source':x['items'][0]['source'],'url':x['items'][0].get('url'),
                                      'height_cm':x['height_cm'],'status':'lead','cached_items':x['items']})
        leads=[x for x in rec['sources'] if x.get('status')=='lead' and x.get('height_cm') is not None]
        byfam={}
        for x in leads:byfam[x['source']]=float(x['height_cm'])
        vals=list(byfam.values())
        rec['source_family_values']=byfam
        if len(byfam)<2:rec['status']='insufficient_independent_sources'
        elif max(vals)-min(vals)>1.01:rec['status']='source_conflict'
        else:
            rec['status']='accepted';rec['height_cm']=round(statistics.median(vals),2)
            if float(rec['height_cm']).is_integer():rec['height_cm']=int(rec['height_cm'])
        return rec

    with cf.ThreadPoolExecutor(max_workers=8) as ex:rows=list(ex.map(one,jobs))
    accepted=[r for r in rows if r.get('status')=='accepted']
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for r in accepted:
            t=targets[nk(r['name'])]
            srcs=[]
            for s in r['sources']:
                if s.get('status')!='lead' or s.get('height_cm') is None:continue
                srcs.append({'source':s['source'],'url':s.get('url'),'reported_height_cm':s['height_cm']})
            item={'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
              'fields':{'height_cm':r['height_cm']},
              'evidence':[{'source':'strict_multi_source_height_consensus','fields':{'height_cm':r['height_cm']},
                'exact_identity':True,'agreement_tolerance_cm':1.01,'sources':srcs}],
              'conflicts':{},'quality':'strict_multi_source_height_consensus_exact_identity',
              'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()}
            f.write(json.dumps(item,ensure_ascii=False)+'\n')
    counts={}
    for r in rows:counts[r['status']]=counts.get(r['status'],0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_height_targets':len(targets),
      'status_counts':counts,'accepted':len(accepted),
      'accepted_profiles':[{'name':r['name'],'height_cm':r['height_cm'],'source_family_values':r['source_family_values']} for r in accepted],
      'rtf_sitemap_diagnostics':rtf_diag,'rows':rows,
      'policy':'Missing height only; exact identity; plausible height; >=2 independent source families; all retained source-family values within 1.01 cm; within-source conflicts excluded; never overwrite.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_height_targets','status_counts','accepted','accepted_profiles')},indent=2))

if __name__=='__main__':main()
