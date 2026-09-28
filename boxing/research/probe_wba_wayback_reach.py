#!/usr/bin/env python3
"""Recover missing reach from archived official WBA fighter profiles.

Targets come only from current WBA probe rows whose fetched official profile
title exactly matched the missing fighter but whose current profile had no
reach. No WBA profile IDs are guessed.

For each exact official profile URL:
- query Wayback CDX for successful captures;
- inspect bounded newest/oldest snapshots;
- require archived title "Boxer: NAME" to exactly match the target;
- parse WBA's value-before-REACH or label-before-value layouts;
- require all recovered archived reach values to agree within 1 cm.

Archived official WBA evidence is first-party and can merge by itself under the
strict reach evidence gate. Existing reach is never overwritten downstream.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,statistics,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
SUP=ROOT/'profile_supplements'
CURRENT=SUP/'wba_direct_reach_probe.json'
OUT=SUP/'wba_wayback_reach_probe.json'
ADD=SUP/'wba_wayback_reach_additions.jsonl'
BASE='https://www.wbaboxing.com'
UA='Mozilla/5.0 AppwizaWBAWaybackReach/1.0'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def get(url,limit=3_000_000,timeout=35):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def cdx(url):
    q='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode([
      ('url',url),('output','json'),('filter','statuscode:200'),
      ('fl','timestamp,original,statuscode,mimetype,digest'),('limit','100')
    ])
    _,raw=get(q,2_000_000,35)
    obj=json.loads(raw.decode('utf-8','replace'))
    return obj[1:] if isinstance(obj,list) and obj else []

def profile_name(soup):
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    m=re.match(r'\s*Boxer:\s*(.+?)\s*$',title,re.I)
    if m:return re.sub(r'\s+',' ',m.group(1)).strip()
    for h in soup.find_all(['h1','h2']):
        v=re.sub(r'\s+',' ',h.get_text(' ',strip=True)).strip()
        if v and len(v)<=100:return v
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

def reach_near_label(soup):
    strings=[re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip()]
    vals=[]
    for i,x in enumerate(strings):
        if x.upper().replace(' :','').rstrip(':')!='REACH':continue
        # WBA has used both VALUE -> REACH and REACH -> VALUE layouts.
        for j in (i-1,i+1):
            if 0<=j<len(strings):
                v=cm_measure(strings[j])
                if v is not None and 120<=v<=270:vals.append(v)
    text=' '.join(strings)
    for m in re.finditer(r'\bREACH\s*:?\s*([^A-Za-z]{0,10}\d[^A-Za-z]{0,20}(?:cm|[\"″]))',text,re.I):
        v=cm_measure(m.group(1))
        if v is not None and 120<=v<=270:vals.append(v)
    vals=sorted(set(round(v,2) for v in vals))
    if not vals:return None,vals
    if max(vals)-min(vals)>1.01:return None,vals
    return round(statistics.median(vals),2),vals

def snapshots(rows):
    # De-duplicate identical archived bodies, then sample both ends of history.
    uniq={}
    for r in rows:
        if len(r)<5:continue
        ts,orig,status,mime,digest=map(str,r[:5])
        uniq[(ts,digest)]={'timestamp':ts,'original':orig,'digest':digest}
    vals=sorted(uniq.values(),key=lambda x:x['timestamp'])
    if len(vals)<=12:return vals
    return vals[:6]+vals[-6:]

def main():
    cur=json.loads(CURRENT.read_text()) if CURRENT.exists() else {}
    targets=[]
    seen=set()
    for x in cur.get('rows') or []:
        if x.get('status')!='no_reach':continue
        name=x.get('target_name') or x.get('name')
        pid=x.get('profile_id');url=x.get('url')
        if not name or pid is None:continue
        key=(nk(name),int(pid))
        if key in seen:continue
        seen.add(key)
        targets.append({'name':name,'target_key':nk(name),'profile_id':int(pid),
                        'url':url or f'{BASE}/wba-boxer-profile?id={pid}',
                        'target_source_id':x.get('id')})

    # Recover target source ids from current audit when old report row omits it.
    audit_path=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
    audit=json.loads(audit_path.read_text()) if audit_path.exists() else {}
    idmap={nk(x.get('name')):x for x in audit.get('fighters') or []}
    for t in targets:
        a=idmap.get(t['target_key']) or {}
        t['target_source_id']=t.get('target_source_id') or a.get('id')
        t['career_source']=a.get('career_source')
        t['strict_bout_appearances']=a.get('strict_bout_appearances')

    def one(t):
        rec={k:t.get(k) for k in ('name','target_source_id','career_source','strict_bout_appearances','profile_id','url')}
        caps=[]
        errors=[]
        variants=[
          t['url'],
          t['url'].replace('https://','http://'),
          t['url'].replace('www.wbaboxing.com','wbaboxing.com'),
          t['url'].replace('https://www.wbaboxing.com','http://wbaboxing.com')
        ]
        for u in dict.fromkeys(variants):
            try:caps.extend(cdx(u))
            except Exception as e:errors.append('cdx '+type(e).__name__+': '+str(e)[:100])
        ss=snapshots(caps);rec['capture_count']=len(caps);rec['sampled_snapshots']=len(ss)
        hits=[]
        for cap in ss:
            snap=f"https://web.archive.org/web/{cap['timestamp']}id_/{cap['original']}"
            try:
                final,raw=get(snap,2_500_000,30);soup=BeautifulSoup(raw,'lxml')
                pname=profile_name(soup)
                if nk(pname)!=t['target_key']:
                    errors.append('identity '+cap['timestamp']+': '+repr(pname));continue
                reach,vals=reach_near_label(soup)
                if reach is None:
                    if vals:errors.append('local reach conflict '+cap['timestamp']+': '+repr(vals))
                    continue
                hits.append({'timestamp':cap['timestamp'],'snapshot_url':final,'reach_cm':reach,'raw_values_cm':vals})
            except Exception as e:errors.append('snapshot '+cap['timestamp']+' '+type(e).__name__+': '+str(e)[:100])
        rec['hits']=hits
        uniqvals=sorted({round(float(x['reach_cm']),2) for x in hits})
        if not hits:rec['status']='no_archived_reach'
        elif max(uniqvals)-min(uniqvals)>1.01:
            rec['status']='archived_reach_conflict';rec['reach_values_cm']=uniqvals
        else:
            rec['status']='accepted';rec['reach_cm']=round(statistics.median(uniqvals),2)
            rec['reach_values_cm']=uniqvals
        rec['errors']=errors[:20]
        return rec

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        rows=list(ex.map(one,targets))

    additions=[]
    for x in rows:
        if x.get('status')!='accepted' or not x.get('target_source_id'):continue
        reach=x['reach_cm']
        if float(reach).is_integer():reach=int(reach)
        additions.append({
          'target_source_id':x['target_source_id'],'name':x['name'],'career_source':x.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'wba_official_archived_profile','exact_identity':True,
            'fields':{'reach_cm':reach},'profile_id':x['profile_id'],
            'urls':[h['snapshot_url'] for h in x['hits']],
            'snapshot_values_cm':[h['reach_cm'] for h in x['hits']]}],
          'conflicts':{},'quality':'official_wba_archived_profile_exact_identity_missing_reach_only',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
        })

    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'current_exact_wba_no_reach_targets':len(targets),
      'status_counts':counts,'accepted':len(additions),
      'accepted_profiles':[{'name':x['name'],'reach_cm':x['fields']['reach_cm'],
                            'profile_id':x['evidence'][0]['profile_id']} for x in additions],
      'rows':rows,
      'policy':'Archived official WBA profiles only, using profile IDs already resolved by current exact-title WBA probes. Archived profile title must exactly match target. No guessed IDs. All recovered snapshot reaches must agree within 1.01 cm. Existing reach is never overwritten.'
    }
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('current_exact_wba_no_reach_targets','status_counts','accepted')},indent=2))

if __name__=='__main__':main()
