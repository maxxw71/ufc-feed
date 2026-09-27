#!/usr/bin/env python3
"""Probe archived BoxRec profiles for missing reach using already-known BoxRec IDs.

No BoxRec ID guessing. For each strict missing-reach fighter with an embedded
BoxRec ID, query Wayback for exact historical captures of that public profile.
Automatic additions require exact fighter identity plus agreement with an
independent MartialBot reach within 1 cm.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,os,re,sqlite3,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
DB=Path(os.environ.get('BOXING_DB','/tmp/boxing_research.sqlite3'))
GAP=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
OUT=ROOT/'profile_supplements'/'boxrec_wayback_reach_probe.json'
ADD=ROOT/'profile_supplements'/'boxrec_wayback_reach_additions.jsonl'
UA='Mozilla/5.0 AppwizaBoxRecWaybackReach/1.0'

def norm(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def boxrec_id(attrs):
    if not isinstance(attrs,dict):return None
    for k,v in attrs.items():
        if norm(k) in {'boxrec','boxrecid','boxrecno','boxrecnumber'}:
            m=re.search(r'\d{3,9}',str(v or ''))
            if m:return m.group(0)
    return None

def get(url,limit=3_000_000,timeout=45):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def cdx(url):
    q='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode([
      ('url',url),('output','json'),('filter','statuscode:200'),
      ('fl','timestamp,original,statuscode,mimetype,digest'),('limit','20')
    ])
    final,raw=get(q,2_000_000,45)
    x=json.loads(raw.decode('utf-8','replace'))
    rows=x[1:] if isinstance(x,list) and x else []
    return rows

def parse(name,raw):
    soup=BeautifulSoup(raw,'lxml');text=' '.join(soup.stripped_strings)
    h=soup.find('h1');htext=' '.join(h.stripped_strings) if h else ''
    # BoxRec archived profiles may label header as "Name ..."; require target
    # normalized name to appear in h1 or page title/text near the top.
    title=' '.join(soup.title.stripped_strings) if soup.title else ''
    identity_ok=norm(name) in norm(htext) or norm(name) in norm(title) or norm(name) in norm(text[:1200])
    vals=[]
    for m in re.finditer(r'\breach\b[^0-9]{0,50}(\d{2}(?:\.\d+)?)\s*[″"”]',text,re.I):
        cm=round(float(m.group(1))*2.54,2)
        if 120<=cm<=270:vals.append(cm)
    for m in re.finditer(r'\breach\b[^0-9]{0,50}(\d{3}(?:\.\d+)?)\s*cm\b',text,re.I):
        cm=float(m.group(1))
        if 120<=cm<=270:vals.append(cm)
    vals=sorted(set(round(v,2) for v in vals))
    return identity_ok,htext,title,(vals[0] if len(vals)==1 else None),vals

def main():
    gap=json.loads(GAP.read_text())
    targets={norm(x['name']):x for x in gap.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={norm(x['name']):x for x in mbobj.get('leads',[])}

    db=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);db.row_factory=sqlite3.Row
    found={}
    tabs={r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    if 'fighters' in tabs:
        cols={r[1] for r in db.execute('pragma table_info(fighters)')}
        if {'name','snapshot'}<=cols:
            for r in db.execute('select name,snapshot from fighters'):
                key=norm(r['name'])
                if key not in targets:continue
                try:s=json.loads(r['snapshot'] or '{}')
                except Exception:continue
                bid=boxrec_id(s.get('attributes') or s)
                if bid:found.setdefault(key,set()).add(bid)
    db.close()

    jobs=[]
    for key,ids in found.items():
        if len(ids)==1:jobs.append((key,targets[key],next(iter(ids))))
    def one(job):
        key,t,bid=job
        urls=[f'https://boxrec.com/en/box-pro/{bid}',f'http://boxrec.com/en/box-pro/{bid}']
        caps=[]
        for u in urls:
            try:caps.extend(cdx(u))
            except Exception:pass
        # newest distinct capture first
        uniq={}
        for r in caps:
            if len(r)>=5:uniq[(str(r[0]),str(r[4]))]=r
        caps=sorted(uniq.values(),key=lambda r:str(r[0]),reverse=True)
        rec={'id':t['id'],'name':t['name'],'boxrec_id':bid,'capture_count':len(caps)}
        errs=[]
        for row in caps[:5]:
            ts,orig=map(str,row[:2])
            snap=f'https://web.archive.org/web/{ts}id_/{orig}'
            try:
                final,raw=get(snap,2_000_000,35);ok,h,title,reach,vals=parse(t['name'],raw)
                if not ok:errs.append('identity_mismatch');continue
                if reach is None:errs.append('reach_missing_or_conflict:'+repr(vals));continue
                rec.update({'status':'lead','snapshot_url':final,'reach_cm':reach,'h1':h,'title':title})
                m=mb.get(key)
                if m and m.get('reach_cm') is not None:
                    rec['martialbot_reach_cm']=m['reach_cm'];rec['agrees_martialbot']=abs(float(m['reach_cm'])-reach)<=1
                return rec
            except Exception as e:errs.append(type(e).__name__+': '+str(e)[:120])
        rec['status']='no_usable_archive';rec['errors']=errs[:12];return rec

    with cf.ThreadPoolExecutor(max_workers=6) as ex:rows=list(ex.map(one,jobs))
    leads=[x for x in rows if x['status']=='lead'];corr=[x for x in leads if x.get('agrees_martialbot') is True]
    additions=[]
    for x in corr:
        t=targets[norm(x['name'])];m=mb[norm(x['name'])]
        reach=round((float(x['reach_cm'])+float(m['reach_cm']))/2,2)
        if float(reach).is_integer():reach=int(reach)
        additions.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'boxrec_wayback_martialbot_reach_consensus','fields':{'reach_cm':reach},
            'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':[{'source':'boxrec_wayback','url':x['snapshot_url'],'reported_reach_cm':x['reach_cm'],'boxrec_id':x['boxrec_id']},
                       {'source':'martialbot','url':m['url'],'reported_reach_cm':m['reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_boxrec_archive_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'targets_with_known_unique_boxrec_id':len(jobs),
      'status_counts':counts,'lead_count':len(leads),'martialbot_corroborated':len(corr),
      'corroborated_profiles':[{'name':x['name'],'boxrec_reach_cm':x['reach_cm'],'martialbot_reach_cm':x['martialbot_reach_cm'],
                                'snapshot_url':x['snapshot_url']} for x in corr],
      'rows':rows,
      'policy':'Known embedded BoxRec IDs only; exact archived public profile capture and identity. Automatic addition only with exact-identity MartialBot agreement <=1 cm.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('targets_with_known_unique_boxrec_id','status_counts','lead_count','martialbot_corroborated')},indent=2))
if __name__=='__main__':main()
