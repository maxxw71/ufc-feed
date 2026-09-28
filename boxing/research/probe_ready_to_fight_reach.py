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
UA='Mozilla/5.0 AppwizaRTFReach/2.0'
SITEMAPS=[f'https://rtfight.com/sitemaps/en-profiles-{i}.xml' for i in range(1,11)]
FIGHT_SITEMAPS=[f'https://rtfight.com/sitemaps/en-fights-{i}.xml' for i in range(1,21)] + [
 'https://rtfight.com/sitemaps/en-fights.xml','https://rtfight.com/sitemaps/fights.xml'
]

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def get(url,limit=12_000_000,timeout=40):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
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


def surname_key(name):
    toks=re.findall(r"[A-Za-zÀ-ÿ0-9'-]+",str(name or ''))
    while toks and toks[-1].lower().rstrip('.') in {'jr','sr','ii','iii','iv','jnr'}:toks.pop()
    return nk(toks[-1]) if toks else ''

def fight_sitemap_index(targets):
    """Return candidate RTF fight URLs narrowed by target surname.

    Surname matching is discovery-only. Exact full-name identity on the fetched
    fight page is mandatory before any reach is retained.
    """
    surname_to_keys={}
    for key,t in targets.items():
        s=surname_key(t.get('name'))
        if s and len(s)>=3:surname_to_keys.setdefault(s,[]).append(key)

    def probe(u):
        try:
            final,raw=get(u,12_000_000,18);ls=locs(raw)
            fights=[x for x in ls if '/fights/' in urllib.parse.urlsplit(x).path]
            return {'url':u,'status':'ok','locs':len(ls),'fight_urls':fights}
        except Exception as e:
            return {'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:160],'fight_urls':[]}

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        probes=list(ex.map(probe,FIGHT_SITEMAPS))
    idx={k:[] for k in targets}
    for p in probes:
        for url in p.get('fight_urls') or []:
            slug=nk(urllib.parse.urlsplit(url).path.rstrip('/').split('/')[-1])
            for surname,keys in surname_to_keys.items():
                if surname not in slug:continue
                for key in keys:
                    if len(idx[key])<12 and url not in idx[key]:idx[key].append(url)
    diag=[{k:v for k,v in p.items() if k!='fight_urls'}|{'fight_urls':len(p.get('fight_urls') or [])} for p in probes]
    return idx,diag

def parse_fight_reach(raw,target_name):
    """Parse target reach from RTF paired Parameters block.

    RTF renders paired rows such as "170 Height 178" and "175 Reach 178".
    The fighter whose full-name H2 appears before Parameters is the left value;
    the full-name H2 after Parameters is the right value.
    """
    soup=BeautifulSoup(raw,'lxml')
    tags=soup.find_all(True)
    order={id(tag):i for i,tag in enumerate(tags)}
    target_tags=[]
    for h in soup.find_all(['h1','h2','h3']):
        label=' '.join(h.stripped_strings).strip()
        if nk(label)==nk(target_name):target_tags.append(h)
    if not target_tags:return None,None,'target_full_name_heading_missing'

    param_tag=None
    for h in soup.find_all(['h2','h3','h4']):
        if ' '.join(h.stripped_strings).strip().casefold()=='parameters':
            param_tag=h;break
    if not param_tag:return None,None,'parameters_heading_missing'

    text=' '.join(soup.stripped_strings)
    m=re.search(
      r'\b(\d+(?:\.\d+)?)\s+Height\s+(\d+(?:\.\d+)?)'
      r'\s+(\d+(?:\.\d+)?)\s+Reach\s+(\d+(?:\.\d+)?)\b',
      text,re.I)
    if not m:return None,None,'paired_height_reach_pattern_missing'
    left_h,right_h,left_r,right_r=map(float,m.groups())

    pidx=order.get(id(param_tag),-1)
    # Prefer the target full-name heading nearest to the Parameters block.
    before=[h for h in target_tags if order.get(id(h),10**9)<pidx]
    after=[h for h in target_tags if order.get(id(h),-1)>pidx]
    if before and after:
        return None,None,'target_heading_both_sides_ambiguous'
    if before:
        reach=left_r;side='left'
    elif after:
        reach=right_r;side='right'
    else:
        return None,None,'target_heading_side_unresolved'
    if not 120<=reach<=270 or reach==0:return None,side,'reach_missing_or_implausible'
    return reach,side,None

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

    def one_profile(job):
        k,t,u=job
        rec={'id':t['id'],'name':t['name'],'url':u,'strict_bout_appearances':t.get('strict_bout_appearances')}
        try:
            final,raw=get(u,2_000_000);h,reach,vals=parse(raw)
            rec.update({'url':final,'h1':h,'reach_cm':reach,'reach_values':vals})
            if nk(h)!=k:rec['status']='identity_mismatch'
            elif reach is None:rec['status']='no_reach'
            else:rec['status']='lead'
        except Exception as e:rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        profile_rows=list(ex.map(one_profile,jobs))

    # Recover historical reach from RTF fight pages for targets whose profile
    # has no reach. Bout pages are a separate RTF evidence surface, but remain
    # the same source family for consensus purposes.
    unresolved={nk(x['name']):targets[nk(x['name'])] for x in profile_rows
                if x.get('status')=='no_reach' and nk(x.get('name')) in targets}
    fight_idx,fight_diag=fight_sitemap_index(unresolved) if unresolved else ({},[])
    fight_jobs=[]
    for key,t in unresolved.items():
        for u in (fight_idx.get(key) or [])[:12]:
            fight_jobs.append((key,t,u))

    def one_fight(job):
        key,t,u=job
        rec={'id':t['id'],'name':t['name'],'url':u,'target_key':key}
        try:
            final,raw=get(u,3_000_000,30)
            reach,side,error=parse_fight_reach(raw,t['name'])
            rec.update({'url':final,'reach_cm':reach,'side':side})
            rec['status']='bout_lead' if reach is not None else error
        except Exception as e:
            rec.update({'status':'fight_fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        fight_rows=list(ex.map(one_fight,fight_jobs))

    # Consolidate each RTF target across profile and fight pages. RTF internal
    # disagreements >1 cm are quarantined and never sent to cross-source merge.
    rtf_values={}
    internal_conflicts=[]
    for key,t in targets.items():
        items=[]
        for x in profile_rows:
            if nk(x.get('name'))==key and x.get('status')=='lead' and x.get('reach_cm') is not None:
                items.append({'surface':'profile','url':x['url'],'reach_cm':float(x['reach_cm'])})
        for x in fight_rows:
            if x.get('target_key')==key and x.get('status')=='bout_lead' and x.get('reach_cm') is not None:
                items.append({'surface':'fight_page','url':x['url'],'reach_cm':float(x['reach_cm'])})
        if not items:continue
        vals=sorted({round(float(x['reach_cm']),2) for x in items})
        if max(vals)-min(vals)>1:
            internal_conflicts.append({'name':t['name'],'target_source_id':t['id'],'values':vals,'sources':items})
            continue
        reach=round(sum(vals)/len(vals),2)
        rtf_values[key]={'reach_cm':reach,'sources':items}

    leads=[];corr=[];conflicts=[];additions=[]
    for key,item in sorted(rtf_values.items()):
        t=targets[key];reach=item['reach_cm']
        rec={'id':t['id'],'name':t['name'],'reach_cm':reach,'sources':item['sources'],
             'strict_bout_appearances':t.get('strict_bout_appearances')}
        m=mb.get(key)
        if m and m.get('reach_cm') is not None:
            rec['martialbot_reach_cm']=float(m['reach_cm'])
            rec['agrees_martialbot']=abs(float(m['reach_cm'])-float(reach))<=1
        leads.append(rec)
        if rec.get('agrees_martialbot') is True:corr.append(rec)
        elif rec.get('agrees_martialbot') is False:conflicts.append(rec)

    for x in corr:
        t=targets[nk(x['name'])];m=mb[nk(x['name'])]
        reach=round((float(x['reach_cm'])+float(m['reach_cm']))/2,2)
        if float(reach).is_integer():reach=int(reach)
        additions.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'ready_to_fight_martialbot_reach_consensus','fields':{'reach_cm':reach},
            'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':[{'source':'ready_to_fight','urls':[s['url'] for s in x['sources']],
                        'surfaces':[s['surface'] for s in x['sources']],
                        'reported_reach_cm':x['reach_cm']},
                       {'source':'martialbot','url':m['url'],'reported_reach_cm':m['reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_rtf_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})

    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')

    profile_counts={}
    for x in profile_rows:profile_counts[x['status']]=profile_counts.get(x['status'],0)+1
    fight_counts={}
    for x in fight_rows:fight_counts[x['status']]=fight_counts.get(x['status'],0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'targets_with_unique_rtf_profile':len(jobs),'profile_status_counts':profile_counts,
      'fight_sitemap_diagnostics':fight_diag,'fight_candidate_jobs':len(fight_jobs),
      'fight_status_counts':fight_counts,'rtf_lead_count':len(leads),
      'martialbot_corroborated':len(corr),'martialbot_conflicts':len(conflicts),
      'rtf_internal_conflicts':internal_conflicts,
      'corroborated_profiles':[{'name':x['name'],'rtf_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x['martialbot_reach_cm'],'sources':x['sources']} for x in corr],
      'conflicting_profiles':[{'name':x['name'],'rtf_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x.get('martialbot_reach_cm'),'sources':x['sources']} for x in conflicts],
      'rows':profile_rows,'fight_rows':fight_rows,
      'policy':'Public Ready To Fight profile/fight pages only. Profile sitemap identity and exact full-name heading on fight pages required. Paired fight Parameters are parsed by exact fighter side. RTF internal disagreement >1 cm quarantined. Automatic addition only when independent exact-identity MartialBot agrees within 1 cm.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_targets','targets_with_unique_rtf_profile','profile_status_counts','fight_candidate_jobs','fight_status_counts','rtf_lead_count','martialbot_corroborated','martialbot_conflicts')},indent=2))


if __name__=='__main__':main()
