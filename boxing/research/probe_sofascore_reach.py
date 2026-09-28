#!/usr/bin/env python3
"""Probe SofaScore public API for missing boxer reach and corroborate MartialBot.

Discovery uses SofaScore's public search endpoint; exact normalized name match is
required. A reach is only accepted from a returned entity detail payload with a
plausible numeric value. Automatic additions require independent MartialBot
agreement within 1 cm. No guessed IDs.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
OUT=ROOT/'profile_supplements'/'sofascore_reach_probe.json'
ADD=ROOT/'profile_supplements'/'sofascore_reach_additions.jsonl'
UA='Mozilla/5.0 AppwizaSofaScoreReach/1.0'
SEARCH_PATTERNS=[
 'https://www.sofascore.com/api/v1/search/all/?q={q}&page=0',
 'https://api.sofascore.com/api/v1/search/all/?q={q}&page=0',
 'https://www.sofascore.com/api/v1/search/player-team-persons/?q={q}&page=0',
 'https://api.sofascore.com/api/v1/search/player-team-persons/?q={q}&page=0',
]
DETAIL_PATTERNS=[
 'https://www.sofascore.com/api/v1/player/{id}',
 'https://www.sofascore.com/api/v1/fighter/{id}',
 'https://api.sofascore.com/api/v1/player/{id}',
 'https://api.sofascore.com/api/v1/fighter/{id}',
]

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch_json(url):
    req=urllib.request.Request(url,headers={
      'User-Agent':UA,'Accept':'application/json,text/plain,*/*','Accept-Language':'en-US,en;q=0.8'
    })
    with urllib.request.urlopen(req,timeout=25) as r:
        raw=r.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('response too large')
        return r.geturl(),json.loads(raw.decode('utf-8','replace'))

def walk(obj,path=''):
    if isinstance(obj,dict):
        yield path,obj
        for k,v in obj.items():
            yield from walk(v,path+'.'+str(k) if path else str(k))
    elif isinstance(obj,list):
        for i,v in enumerate(obj):
            yield from walk(v,path+f'[{i}]')

def entity_candidates(payload,target):
    out=[]
    want=nk(target)
    for path,d in walk(payload):
        # Standard search payloads wrap the actual person in {"type":...,"entity":{...}}.
        candidates=[d]
        if isinstance(d.get('entity'),dict):candidates.append(d['entity'])
        if isinstance(d.get('player'),dict):candidates.append(d['player'])
        if isinstance(d.get('fighter'),dict):candidates.append(d['fighter'])
        for obj in candidates:
            names=[]
            for k in ('name','fullName','slug','shortName'):
                if isinstance(obj.get(k),str):names.append(obj[k])
            if not any(nk(x)==want for x in names):continue
            ident=obj.get('id')
            try:ident=int(ident)
            except Exception:continue
            sport=''
            sp=obj.get('sport')
            if isinstance(sp,dict):sport=str(sp.get('name') or sp.get('slug') or '')
            team=obj.get('team')
            if not sport and isinstance(team,dict) and isinstance(team.get('sport'),dict):
                sport=str(team['sport'].get('name') or team['sport'].get('slug') or '')
            out.append({'id':ident,'name':next((x for x in names if nk(x)==want),target),
                        'sport':sport,'path':path,'raw':obj})
    # de-dup IDs
    uniq={}
    for x in out:uniq.setdefault(x['id'],x)
    return list(uniq.values())

def normalize_reach(v):
    try:x=float(v)
    except Exception:return None
    if x<=0:return None
    if 1.2<=x<=2.7:return round(x*100,2)  # meters
    if 48<=x<=110:return round(x*2.54,2)  # inches
    if 120<=x<=270:return round(x,2)       # centimeters
    return None

def reach_from_payload(obj):
    vals=[]
    for path,d in walk(obj):
        for k,v in d.items():
            key=str(k).casefold()
            if key in {'reach','reachcm','reach_cm','armreach','arm_reach'}:
                r=normalize_reach(v)
                if r is not None:vals.append((path+'.'+str(k),r,v))
    unique=sorted({round(r,2) for _,r,_ in vals})
    return (unique[0] if len(unique)==1 else None),vals

def search(name):
    errors=[]
    q=urllib.parse.quote(name)
    for pat in SEARCH_PATTERNS:
        u=pat.format(q=q)
        try:
            final,payload=fetch_json(u)
            c=entity_candidates(payload,name)
            if c:return final,c,payload,errors
            result_types=[]
            if isinstance(payload,dict):
                for row in payload.get('results') or []:
                    if isinstance(row,dict):result_types.append(str(row.get('type') or ''))
            errors.append({'url':u,'reason':'no_exact_name_candidate','result_types':result_types[:20]})
        except Exception as e:errors.append({'url':u,'reason':type(e).__name__+': '+str(e)[:160]})
    return None,[],None,errors

def detail(entity):
    errors=[]
    for pat in DETAIL_PATTERNS:
        u=pat.format(id=entity['id'])
        try:
            final,payload=fetch_json(u)
            reach,vals=reach_from_payload(payload)
            if reach is not None:
                return final,payload,reach,vals,errors
            errors.append({'url':u,'reason':'no_unique_reach','reach_hits':vals[:10]})
        except Exception as e:errors.append({'url':u,'reason':type(e).__name__+': '+str(e)[:160]})
    # Search payload itself can contain reach.
    reach,vals=reach_from_payload(entity.get('raw') or {})
    if reach is not None:return None,entity.get('raw'),reach,vals,errors
    return None,None,None,vals if 'vals' in locals() else [],errors

def main():
    audit=json.loads(AUDIT.read_text())
    targets=[x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])]
    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={nk(x['name']):x for x in mbobj.get('leads',[])}

    def one(t):
        name=t['name'];key=nk(name)
        rec={'id':t['id'],'name':name,'strict_bout_appearances':t.get('strict_bout_appearances')}
        final,cands,payload,errs=search(name)
        rec['search_url']=final;rec['search_candidates']=[{k:v for k,v in x.items() if k!='raw'} for x in cands]
        if not cands:
            rec['status']='no_exact_search_match';rec['errors']=errs[:8];return rec
        # Require one unique exact entity id. Multiple exact IDs are ambiguous.
        ids=sorted({x['id'] for x in cands})
        if len(ids)!=1:
            rec['status']='ambiguous_exact_ids';rec['candidate_ids']=ids;return rec
        entity=next(x for x in cands if x['id']==ids[0])
        du,dp,reach,hits,derr=detail(entity)
        rec.update({'sofascore_id':ids[0],'detail_url':du,'reach_cm':reach,'reach_hits':hits[:20]})
        if reach is None:
            rec['status']='no_unique_reach';rec['errors']=(errs+derr)[:12];return rec
        rec['status']='lead'
        m=mb.get(key)
        if m and m.get('reach_cm') is not None:
            rec['martialbot_reach_cm']=m['reach_cm']
            rec['agrees_martialbot']=abs(float(m['reach_cm'])-float(reach))<=1
        return rec

    # API-friendly concurrency.
    with cf.ThreadPoolExecutor(max_workers=5) as ex:rows=list(ex.map(one,targets))
    leads=[x for x in rows if x['status']=='lead']
    corr=[x for x in leads if x.get('agrees_martialbot') is True]
    conflicts=[x for x in leads if x.get('agrees_martialbot') is False]
    additions=[]
    target_by_id={x['id']:x for x in targets}
    for x in corr:
        t=target_by_id[x['id']];m=mb[nk(x['name'])]
        reach=round((float(x['reach_cm'])+float(m['reach_cm']))/2,2)
        if float(reach).is_integer():reach=int(reach)
        additions.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'sofascore_martialbot_reach_consensus','fields':{'reach_cm':reach},
             'exact_identity':True,'agreement_tolerance_cm':1,
             'sources':[{'source':'sofascore','url':x.get('detail_url') or x.get('search_url'),
                         'entity_id':x['sofascore_id'],'reported_reach_cm':x['reach_cm']},
                        {'source':'martialbot','url':m['url'],'reported_reach_cm':m['reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_sofascore_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'targets':len(targets),
      'status_counts':counts,'lead_count':len(leads),'martialbot_corroborated':len(corr),
      'martialbot_conflicts':len(conflicts),
      'corroborated_profiles':[{'name':x['name'],'sofascore_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x['martialbot_reach_cm'],'sofascore_id':x['sofascore_id']} for x in corr],
      'conflicting_profiles':[{'name':x['name'],'sofascore_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x.get('martialbot_reach_cm'),'sofascore_id':x['sofascore_id']} for x in conflicts],
      'rows':rows,
      'policy':'Public SofaScore search/detail API; exact normalized fighter name and one unique entity ID. Automatic addition only when independent exact-identity MartialBot agrees within 1 cm; disagreements quarantined.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('targets','status_counts','lead_count','martialbot_corroborated','martialbot_conflicts')},indent=2))
if __name__=='__main__':main()
