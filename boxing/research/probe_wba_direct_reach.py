#!/usr/bin/env python3
"""Recover missing reach from official WBA boxer profiles.

Two provenance-safe discovery routes are used:
1) explicit WBA boxer-profile links on current official WBA pages;
2) the repository's existing WBA profile index, whose IDs were discovered from
   official WBA profile/link traversal.

No numeric profile ID is guessed. Every fetched candidate must have an exact
official WBA profile-title identity match to the target fighter. The WBA card
layout may render the numeric value before the label (for example "69″ REACH"),
so measurements are read on either side of the REACH label. Multiple exact
profiles for one target must agree within 1 cm or the fighter is quarantined.
Existing reach is never overwritten downstream.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,statistics,unicodedata,urllib.parse,urllib.request
from collections import defaultdict
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
INDEX=ROOT/'profile_supplements'/'wba_profile_index.json'
OUT=ROOT/'profile_supplements'/'wba_direct_reach_probe.json'
ADD=ROOT/'profile_supplements'/'wba_direct_reach_additions.jsonl'
BASE='https://www.wbaboxing.com'
UA='Mozilla/5.0 AppwizaWBAIndexedReach/2.0'
PAGES=[
 BASE+'/wba-ranking',BASE+'/current-wba-champions',BASE+'/boxing-results',
 BASE+'/boxing-schedule',BASE+'/wba-female-ranking',BASE+'/wba-europe-ranking',
 BASE+'/wba-africa-ranking'
]
RX=re.compile(r'/wba-boxer-profile/?\?id=(\d+)',re.I)

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,timeout=35):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read(3_000_001)
        if len(raw)>3_000_000:raise ValueError('response too large')
        return r.geturl(),raw

def profile_name(soup):
    t=soup.title.get_text(' ',strip=True) if soup.title else ''
    m=re.match(r'\s*Boxer:\s*(.+?)\s*$',t,re.I)
    if m:return re.sub(r'\s+',' ',m.group(1)).strip()
    # Defensive fallback for layout changes; exact identity is still required.
    for h in soup.find_all(['h1','h2']):
        s=re.sub(r'\s+',' ',h.get_text(' ',strip=True)).strip()
        if s and len(s)<=100:return s
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

def measure_near_label(soup,label='REACH'):
    """WBA cards can render VALUE then LABEL; support both directions."""
    want=label.upper()
    strings=[re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip()]
    candidates=[]
    for i,x in enumerate(strings):
        if x.upper().replace(' :','').rstrip(':')!=want:continue
        # Current WBA profile cards commonly emit "69″" immediately before
        # "REACH". Prefer previous text, but inspect the next text as fallback.
        for side,j in (('previous',i-1),('next',i+1)):
            if 0<=j<len(strings):
                val=cm_measure(strings[j])
                if val is not None:candidates.append((side,val,strings[j]))
    if not candidates:return None,None
    vals=[x[1] for x in candidates]
    if max(vals)-min(vals)>1:return None,{'status':'local_label_conflict','candidates':candidates}
    return round(statistics.median(vals),2),{'candidates':candidates}

def current_direct_links(targets):
    direct=defaultdict(list);page_diag=[]
    for page in PAGES:
        try:
            final,raw=fetch(page);soup=BeautifulSoup(raw,'lxml');n=0
            for a in soup.find_all('a',href=True):
                href=urllib.parse.urljoin(BASE,a['href'])
                m=RX.search(href)
                name=re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()
                key=nk(name)
                if not m or key not in targets:continue
                direct[key].append({'profile_id':int(m.group(1)),'url':f'{BASE}/wba-boxer-profile?id={m.group(1)}',
                                    'source_page':final,'discovery':'current_direct_link'})
                n+=1
            page_diag.append({'page':page,'status':'ok','target_links':n})
        except Exception as e:
            page_diag.append({'page':page,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    return direct,page_diag

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}

    direct,page_diag=current_direct_links(targets)
    indexed=json.loads(INDEX.read_text()).get('index',{}) if INDEX.exists() else {}

    candidates=defaultdict(dict)
    for key,entries in direct.items():
        for e in entries:candidates[key][int(e['profile_id'])]=dict(e)
    indexed_targets=0
    for key,t in targets.items():
        entries=indexed.get(key) or []
        if entries:indexed_targets+=1
        for e in entries:
            try:pid=int(e.get('profile_id'))
            except Exception:continue
            rec=candidates[key].setdefault(pid,{
                'profile_id':pid,
                'url':f'{BASE}/wba-boxer-profile?id={pid}',
                'discovery':'wba_profile_index_exact_name_key'
            })
            if rec.get('discovery')!='current_direct_link':
                rec['discovery']='wba_profile_index_exact_name_key'

    jobs=[]
    for key,byid in candidates.items():
        for pid,meta in byid.items():jobs.append((key,pid,meta))

    def one(job):
        key,pid,meta=job;t=targets[key]
        rec={'target_key':key,'target_name':t['name'],'profile_id':pid,'url':meta['url'],
             'discovery':meta.get('discovery'),'source_page':meta.get('source_page')}
        try:
            final,raw=fetch(meta['url']);soup=BeautifulSoup(raw,'lxml');name=profile_name(soup)
            reach,label_diag=measure_near_label(soup,'REACH')
            rec.update({'url':final,'profile_name':name,'reach_cm':reach,'label_diagnostics':label_diag})
            if nk(name)!=key:rec['status']='identity_mismatch'
            elif reach is None:rec['status']='no_reach'
            elif not 120<=reach<=270:rec['status']='implausible_reach'
            else:rec['status']='accepted'
        except Exception as e:
            rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        rows=list(ex.map(one,jobs))

    exact=defaultdict(list)
    for r in rows:
        if r.get('status')=='accepted':exact[r['target_key']].append(r)

    accepted=[];conflicts=[]
    for key,items in sorted(exact.items()):
        vals=[float(x['reach_cm']) for x in items]
        if max(vals)-min(vals)>1:
            conflicts.append({'name':targets[key]['name'],'target_key':key,
                              'values':vals,'profile_ids':[x['profile_id'] for x in items],
                              'urls':[x['url'] for x in items]})
            continue
        reach=round(statistics.median(vals),2)
        if float(reach).is_integer():reach=int(reach)
        t=targets[key]
        accepted.append({
          'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{
              'source':'wba_official_profile_indexed_exact_identity',
              'urls':sorted({x['url'] for x in items}),
              'profile_ids':sorted({x['profile_id'] for x in items}),
              'discovery_modes':sorted({x.get('discovery') for x in items if x.get('discovery')}),
              'fields':{'reach_cm':reach},
              'exact_identity':True,
              'layout_note':'WBA profile card reach value parsed adjacent to REACH label; value-before-label supported'
          }],
          'conflicts':{},'quality':'official_wba_profile_exact_identity_missing_reach_only',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
        })

    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in accepted:f.write(json.dumps(x,ensure_ascii=False)+'\n')

    counts={}
    for r in rows:counts[r.get('status')]=counts.get(r.get('status'),0)+1
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'missing_reach_targets':len(targets),
      'targets_with_current_direct_link':len(direct),
      'targets_with_exact_name_in_wba_index':indexed_targets,
      'candidate_profile_pages':len(jobs),
      'status_counts':counts,
      'accepted':len(accepted),
      'conflicts_quarantined':len(conflicts),
      'accepted_profiles':[{'name':x['name'],'reach_cm':x['fields']['reach_cm'],
                            'profile_ids':x['evidence'][0]['profile_ids']} for x in accepted],
      'conflicts':conflicts,'page_diagnostics':page_diag,'rows':rows,
      'policy':'Official WBA profile IDs discovered from current direct links or existing official WBA profile index only; no guessed IDs; fetched official profile title must exactly match target; value-before-label and label-before-value REACH layouts supported; multiple exact profiles must agree within 1 cm; missing reach only.'
    }
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in (
      'missing_reach_targets','targets_with_current_direct_link','targets_with_exact_name_in_wba_index',
      'candidate_profile_pages','status_counts','accepted','conflicts_quarantined')},indent=2))

if __name__=='__main__':main()
