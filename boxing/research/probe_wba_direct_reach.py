#!/usr/bin/env python3
"""Strict official-WBA direct profile reach probe for currently missing fighters.

Unlike the broad historical WBA identity graph, this uses only explicit boxer
profile links appearing directly on current official WBA ranking/champion/
results/schedule pages. No opponent-link propagation and no numeric ID guessing.
"""
from __future__ import annotations
import datetime as dt,json,re,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'wba_direct_reach_probe.json'
ADD=ROOT/'profile_supplements'/'wba_direct_reach_additions.jsonl'
BASE='https://www.wbaboxing.com'
UA='Mozilla/5.0 AppwizaWBADirectReach/1.0'
PAGES=[
 BASE+'/wba-ranking',BASE+'/current-wba-champions',BASE+'/boxing-results',
 BASE+'/boxing-schedule',BASE+'/wba-female-ranking',BASE+'/wba-europe-ranking',
 BASE+'/wba-africa-ranking'
]
RX=re.compile(r'/wba-boxer-profile/?\?id=(\d+)',re.I)

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(3_000_001)
        if len(raw)>3_000_000:raise ValueError('response too large')
        return r.geturl(),raw

def profile_name(soup):
    t=soup.title.get_text(' ',strip=True) if soup.title else ''
    m=re.match(r'\s*Boxer:\s*(.+?)\s*$',t,re.I)
    return re.sub(r'\s+',' ',m.group(1)).strip() if m else None

def label_value(soup,label):
    want=label.upper()
    strings=[re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip()]
    for i,x in enumerate(strings[:-1]):
        if x.upper().replace(' :','').rstrip(':')==want:
            return strings[i+1]
    return None

def cm_measure(s):
    if not s:return None
    txt=str(s).replace('’',"'").replace('′',"'").replace('“','"').replace('”','"').replace('″','"')
    txt=txt.replace('½','.5').replace('¼','.25').replace('¾','.75')
    m=re.search(r"(\d+)\s*'\s*(\d+(?:\.\d+)?)?\s*\"?",txt)
    if m:return round((float(m.group(1))*12+float(m.group(2) or 0))*2.54,2)
    m=re.search(r'(\d+(?:\.\d+)?)\s*"',txt)
    if m:return round(float(m.group(1))*2.54,2)
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',txt,re.I)
    return round(float(m.group(1)),2) if m else None

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    direct={};page_diag=[]
    for page in PAGES:
        try:
            final,raw=fetch(page);soup=BeautifulSoup(raw,'lxml');n=0
            for a in soup.find_all('a',href=True):
                href=urllib.parse.urljoin(BASE,a['href'])
                m=RX.search(href)
                name=re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()
                key=nk(name)
                if not m or key not in targets:continue
                direct.setdefault(key,[]).append({'name':name,'profile_id':int(m.group(1)),
                  'url':f'{BASE}/wba-boxer-profile?id={m.group(1)}','source_page':final});n+=1
            page_diag.append({'page':page,'status':'ok','target_links':n})
        except Exception as e:page_diag.append({'page':page,'status':'error','error':type(e).__name__+': '+str(e)[:180]})

    rows=[];accepted=[]
    for key,entries in sorted(direct.items()):
        # one profile id per exact target; duplicated appearances on pages are fine
        ids=sorted({int(e['profile_id']) for e in entries})
        t=targets[key]
        if len(ids)!=1:
            rows.append({'name':t['name'],'status':'ambiguous_direct_profile_ids','profile_ids':ids});continue
        pid=ids[0];url=f'{BASE}/wba-boxer-profile?id={pid}'
        try:
            final,raw=fetch(url);soup=BeautifulSoup(raw,'lxml');name=profile_name(soup)
            reach=cm_measure(label_value(soup,'REACH'))
            rec={'name':t['name'],'profile_id':pid,'url':final,'profile_name':name,'reach_cm':reach}
            if nk(name)!=key:rec['status']='identity_mismatch'
            elif reach is None:rec['status']='no_reach'
            elif not 120<=reach<=270:rec['status']='implausible_reach'
            else:
                rec['status']='accepted'
                accepted.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
                  'fields':{'reach_cm':reach},
                  'evidence':[{'source':'wba_official_profile_direct_link','url':final,'profile_id':pid,
                    'source_pages':sorted({e['source_page'] for e in entries}),'fields':{'reach_cm':reach}}],
                  'conflicts':{},'quality':'official_wba_direct_profile_exact_identity_missing_reach_only',
                  'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
            rows.append(rec)
        except Exception as e:rows.append({'name':t['name'],'profile_id':pid,'url':url,'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})

    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in accepted:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for r in rows:counts[r['status']]=counts.get(r['status'],0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_reach_targets':len(targets),
      'targets_with_direct_wba_profile_link':len(direct),'status_counts':counts,'accepted':len(accepted),
      'accepted_profiles':[{'name':x['name'],'reach_cm':x['fields']['reach_cm']} for x in accepted],
      'page_diagnostics':page_diag,'rows':rows,
      'policy':'Official WBA direct profile links from current ranking/champion/results/schedule pages only; exact profile-title identity; plausible numeric reach; no guessed IDs or opponent-link propagation.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_reach_targets','targets_with_direct_wba_profile_link','status_counts','accepted')},indent=2))

if __name__=='__main__':main()
