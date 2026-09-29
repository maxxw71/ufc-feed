#!/usr/bin/env python3
"""Audit all strict boxer reaches against current official WBA profiles.

Read-only correctness pass. No profile values are changed here.
- candidate WBA IDs come only from the existing official WBA profile index;
- fetched WBA profile title must exactly match the strict fighter identity;
- explicit REACH adjacent to the WBA label is parsed in either DOM direction;
- current strict reach is compared to the official value;
- differences >1 cm are surfaced for correction review.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,statistics,unicodedata,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
INDEX=ROOT/'profile_supplements'/'wba_profile_index.json'
OUT=ROOT/'profile_supplements'/'wba_existing_reach_audit.json'
BASE='https://www.wbaboxing.com'
UA='Mozilla/5.0 AppwizaWBAReachAudit/1.0'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(2_500_001)
        if len(raw)>2_500_000:raise ValueError('response too large')
        return r.geturl(),raw

def profile_name(soup):
    t=soup.title.get_text(' ',strip=True) if soup.title else ''
    m=re.match(r'\s*Boxer:\s*(.+?)\s*$',t,re.I)
    return re.sub(r'\s+',' ',m.group(1)).strip() if m else None

def cm_measure(s):
    if not s:return None
    txt=str(s).replace('’',"'").replace('′',"'").replace('“','"').replace('”','"').replace('″','"')
    txt=txt.replace('½','.5').replace('¼','.25').replace('¾','.75')
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',txt,re.I)
    if m:return round(float(m.group(1)),2)
    m=re.search(r'(\d+(?:\.\d+)?)\s*"',txt)
    if m:return round(float(m.group(1))*2.54,2)
    return None

def reach_near_label(soup):
    ss=[re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip()]
    vals=[]
    for i,s in enumerate(ss):
        if s.upper().replace(' :','').rstrip(':')!='REACH':continue
        for j in (i-1,i+1):
            if 0<=j<len(ss):
                v=cm_measure(ss[j])
                if v is not None and 120<=v<=270:vals.append(v)
    vals=sorted(set(round(v,2) for v in vals))
    return vals[0] if len(vals)==1 else None,vals

def main():
    audit=json.loads(AUDIT.read_text())
    idx=(json.loads(INDEX.read_text()).get('index') or {}) if INDEX.exists() else {}
    fighters=[]
    for x in audit.get('fighters') or []:
        key=nk(x.get('name'))
        entries=idx.get(key) or []
        if not entries:continue
        current=(x.get('present') or {}).get('reach_cm')
        fighters.append((x,key,entries,current))

    jobs=[]
    for x,key,entries,current in fighters:
        seen=set()
        for e in entries:
            try:pid=int(e.get('profile_id'))
            except Exception:continue
            if pid in seen:continue
            seen.add(pid)
            jobs.append((x,key,pid,current))

    def one(job):
        x,key,pid,current=job
        url=f'{BASE}/wba-boxer-profile?id={pid}'
        rec={'target_source_id':x.get('id'),'name':x.get('name'),'profile_id':pid,'url':url,
             'strict_bout_appearances':x.get('strict_bout_appearances'),'current_reach_cm':current}
        try:
            final,raw=fetch(url);soup=BeautifulSoup(raw,'lxml');name=profile_name(soup)
            reach,vals=reach_near_label(soup)
            rec.update({'url':final,'profile_name':name,'wba_reach_cm':reach,'reach_values':vals})
            if nk(name)!=key:rec['status']='identity_mismatch'
            elif reach is None:rec['status']='no_reach'
            elif current in (None,''):rec['status']='official_missing_fill_candidate'
            else:
                delta=round(float(reach)-float(current),2);rec['delta_cm']=delta
                rec['status']='agreement' if abs(delta)<=1 else 'conflict'
        except Exception as e:
            rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        rows=list(ex.map(one,jobs))

    # Collapse exact-identity WBA duplicates per strict fighter.
    grouped={}
    for r in rows:
        if r.get('status') in {'identity_mismatch','fetch_error'}:continue
        grouped.setdefault(r['target_source_id'],[]).append(r)
    fighter_rows=[]
    for sid,items in grouped.items():
        numeric=[x for x in items if x.get('wba_reach_cm') is not None and x.get('profile_name')]
        vals=sorted({round(float(x['wba_reach_cm']),2) for x in numeric})
        base=items[0]
        if not vals:
            fighter_rows.append({'target_source_id':sid,'name':base['name'],'status':'no_official_reach',
                                 'current_reach_cm':base.get('current_reach_cm')})
        elif max(vals)-min(vals)>1:
            fighter_rows.append({'target_source_id':sid,'name':base['name'],'status':'wba_internal_conflict',
                                 'current_reach_cm':base.get('current_reach_cm'),'wba_values_cm':vals,
                                 'profile_ids':[x['profile_id'] for x in numeric]})
        else:
            wba=round(statistics.median(vals),2);cur=base.get('current_reach_cm')
            if cur in (None,''):
                status='official_missing_fill_candidate';delta=None
            else:
                delta=round(wba-float(cur),2);status='agreement' if abs(delta)<=1 else 'conflict'
            fighter_rows.append({'target_source_id':sid,'name':base['name'],'status':status,
                                 'strict_bout_appearances':base.get('strict_bout_appearances'),
                                 'current_reach_cm':cur,'wba_reach_cm':wba,'delta_cm':delta,
                                 'profile_ids':[x['profile_id'] for x in numeric],
                                 'urls':[x['url'] for x in numeric]})

    counts={}
    for x in fighter_rows:counts[x['status']]=counts.get(x['status'],0)+1
    conflicts=sorted([x for x in fighter_rows if x['status']=='conflict'],
                     key=lambda x:(-int(x.get('strict_bout_appearances') or 0),-abs(float(x.get('delta_cm') or 0)),x['name']))
    fills=sorted([x for x in fighter_rows if x['status']=='official_missing_fill_candidate'],
                 key=lambda x:(-int(x.get('strict_bout_appearances') or 0),x['name']))
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'strict_fighters_with_wba_index_key':len(fighters),'candidate_profile_pages':len(jobs),
      'fighter_status_counts':counts,'conflict_count':len(conflicts),'missing_fill_candidates':len(fills),
      'conflicts':conflicts,'fill_candidates':fills,'rows':fighter_rows,
      'policy':'Read-only. Official WBA indexed ID only; exact official profile-title identity; explicit adjacent REACH; >1 cm difference is review conflict, never silently overwritten.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('strict_fighters_with_wba_index_key','candidate_profile_pages','fighter_status_counts','conflict_count','missing_fill_candidates')},indent=2))

if __name__=='__main__':main()
