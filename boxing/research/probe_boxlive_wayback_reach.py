#!/usr/bin/env python3
"""Probe archived Box.Live boxer profiles for missing reach.

Live Box.Live blocks GitHub runners, so this uses only preserved Wayback
snapshots of deterministic public /boxers/<slug>/ pages. A reach is eligible
only with exact page identity and agreement within 1 cm with MartialBot.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
OUT=ROOT/'profile_supplements'/'boxlive_wayback_reach_probe.json'
ADD=ROOT/'profile_supplements'/'boxlive_wayback_reach_additions.jsonl'
UA='Mozilla/5.0 AppwizaBoxLiveWaybackReach/1.0'

def ascii_text(s):
    return unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode()
def nk(s): return re.sub(r'[^a-z0-9]+','',ascii_text(s).lower())
def slug(s): return re.sub(r'[^a-z0-9]+','-',ascii_text(s).lower()).strip('-')
def get(url,limit=3_000_000,timeout=45):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read(limit+1)
        if len(raw)>limit: raise ValueError('response too large')
        return r.geturl(),raw
def cdx(url):
    q='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode([
      ('url',url),('output','json'),('filter','statuscode:200'),
      ('fl','timestamp,original,statuscode,mimetype,digest'),('limit','20')
    ])
    _,raw=get(q,2_000_000,45)
    x=json.loads(raw.decode('utf-8','replace'))
    return x[1:] if isinstance(x,list) and x else []
def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1');name=' '.join(h.stripped_strings) if h else ''
    text=' '.join(soup.stripped_strings)
    vals=[]
    for m in re.finditer(r'\bReach\s*:?\s*(\d+(?:\.\d+)?)\s*(?:inches|inch|in|["″])',text,re.I):
        cm=round(float(m.group(1))*2.54,2)
        if 120<=cm<=270: vals.append(cm)
    for m in re.finditer(r'\bReach\s*:?\s*(\d+(?:\.\d+)?)\s*cm\b',text,re.I):
        cm=float(m.group(1))
        if 120<=cm<=270: vals.append(cm)
    vals=sorted(set(round(v,2) for v in vals))
    return name,(vals[0] if len(vals)==1 else None),vals

def main():
    audit=json.loads(AUDIT.read_text())
    targets=[x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])]
    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={nk(x['name']):x for x in mbobj.get('leads',[])}
    def one(t):
        key=nk(t['name'])
        base=f'https://box.live/boxers/{slug(t["name"])}/'
        rec={'id':t['id'],'name':t['name'],'strict_bout_appearances':t.get('strict_bout_appearances'),'live_url':base}
        caps=[]
        for u in (base,base.replace('https://','http://')):
            try:caps.extend(cdx(u))
            except Exception:pass
        uniq={}
        for r in caps:
            if len(r)>=5:uniq[(str(r[0]),str(r[4]))]=r
        caps=sorted(uniq.values(),key=lambda r:str(r[0]),reverse=True)
        rec['capture_count']=len(caps)
        errs=[]
        for row in caps[:5]:
            ts,orig=map(str,row[:2]);snap=f'https://web.archive.org/web/{ts}id_/{orig}'
            try:
                final,raw=get(snap,2_000_000,35);name,reach,vals=parse(raw)
                clean=re.sub(r'\s+boxer\s*$','',name,flags=re.I).strip()
                if nk(clean)!=key:
                    errs.append('identity:'+repr(name));continue
                if reach is None:
                    errs.append('reach:'+repr(vals));continue
                rec.update({'status':'lead','snapshot_url':final,'h1':name,'reach_cm':reach})
                m=mb.get(key)
                if m and m.get('reach_cm') is not None:
                    rec['martialbot_reach_cm']=m['reach_cm']
                    rec['agrees_martialbot']=abs(float(m['reach_cm'])-float(reach))<=1
                return rec
            except Exception as e:errs.append(type(e).__name__+': '+str(e)[:120])
        rec['status']='no_usable_archive';rec['errors']=errs[:10];return rec
    with cf.ThreadPoolExecutor(max_workers=6) as ex:rows=list(ex.map(one,targets))
    leads=[x for x in rows if x['status']=='lead']
    corr=[x for x in leads if x.get('agrees_martialbot') is True]
    additions=[]
    for x in corr:
        t=next(t for t in targets if t['id']==x['id']);m=mb[nk(x['name'])]
        reach=round((float(x['reach_cm'])+float(m['reach_cm']))/2,2)
        if float(reach).is_integer(): reach=int(reach)
        additions.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'boxlive_wayback_martialbot_reach_consensus','fields':{'reach_cm':reach},
            'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':[{'source':'boxlive_wayback','url':x['snapshot_url'],'reported_reach_cm':x['reach_cm']},
                       {'source':'martialbot','url':m['url'],'reported_reach_cm':m['reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_boxlive_archive_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'targets':len(targets),
      'status_counts':counts,'lead_count':len(leads),'martialbot_corroborated':len(corr),
      'corroborated_profiles':[{'name':x['name'],'boxlive_reach_cm':x['reach_cm'],'martialbot_reach_cm':x['martialbot_reach_cm'],'snapshot_url':x['snapshot_url']} for x in corr],
      'rows':rows,
      'policy':'Wayback-preserved Box.Live exact profile identity + plausible Reach; automatic addition only when exact-identity MartialBot agrees within 1 cm.'}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({k:out[k] for k in ('targets','status_counts','lead_count','martialbot_corroborated')},indent=2))
if __name__=='__main__': main()
