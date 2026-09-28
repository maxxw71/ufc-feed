#!/usr/bin/env python3
"""Corroborate missing boxing reaches from RingSide24 public fighter profiles.

RingSide24 is treated as a third-party corroboration source, never a solo
authority. A target is auto-added only when:
- deterministic /en/persons/<slug>/ page resolves to the exact fighter name;
- RingSide24 exposes an explicit numeric Reach;
- an existing exact-identity MartialBot lead agrees within 1 cm;
- any Ready To Fight numeric lead does not contradict by more than 1 cm.

This deliberately avoids promoting RingSide24 + another newly discovered
single lead without an established independent profile source.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
RTF=ROOT/'profile_supplements'/'ready_to_fight_reach_probe.json'
OUT=ROOT/'profile_supplements'/'ringside24_reach_probe.json'
ADD=ROOT/'profile_supplements'/'ringside24_reach_additions.jsonl'
BASE='https://ringside24.com'
UA='Mozilla/5.0 AppwizaRingSide24Reach/1.0'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def slug(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    x=re.sub(r"\b(?:jr|sr|ii|iii|iv)\.?\b",' ',x)
    return re.sub(r'[^a-z0-9]+','-',x).strip('-')

def fetch(url,limit=3_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def cm_measure(s):
    if not s:return None
    txt=str(s).replace('’',"'").replace('′',"'").replace('“','"').replace('”','"').replace('″','"')
    txt=txt.replace('½','.5').replace('¼','.25').replace('¾','.75')
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',txt,re.I)
    if m:
        v=float(m.group(1))
        if 120<=v<=270:return round(v,2)
    m=re.search(r'(\d+(?:\.\d+)?)\s*(?:\"|in(?:ches)?)\b',txt,re.I)
    if m:
        v=round(float(m.group(1))*2.54,2)
        if 120<=v<=270:return v
    return None

def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1')
    name=re.sub(r'\s+',' ',h.get_text(' ',strip=True)).strip() if h else ''
    strings=[re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip()]
    text=' '.join(strings)
    vals=[]
    # Common profile line: Reach 71″ / 182 cm
    for m in re.finditer(r'\bReach\s*:?[ ]*([^|;]{0,45})',text,re.I):
        chunk=m.group(1)
        # stop before obvious next profile field
        chunk=re.split(r'\b(?:Nationality|Division|Stance|Next fight|Age)\b',chunk,1,flags=re.I)[0]
        v=cm_measure(chunk)
        if v is not None:vals.append(v)
    # FAQ form: "His reach is 72 inches ... 182 cm"
    for m in re.finditer(r'\breach\s+is\s+([^.!?]{0,100})',text,re.I):
        v=cm_measure(m.group(1))
        if v is not None:vals.append(v)
    vals=sorted(set(round(v,2) for v in vals))
    return name,(vals[0] if len(vals)==1 else None),vals

def load_leads():
    mb={};rtf={}
    if MB.exists():
        j=json.loads(MB.read_text())
        for x in j.get('leads') or []:
            if x.get('reach_cm') is not None:
                mb[nk(x.get('name'))]={'reach_cm':float(x['reach_cm']),'url':x.get('url')}
    if RTF.exists():
        j=json.loads(RTF.read_text())
        for x in j.get('rows') or []:
            if x.get('status')=='lead' and x.get('reach_cm') is not None:
                rtf[nk(x.get('name'))]={'reach_cm':float(x['reach_cm']),'url':x.get('url')}
    return mb,rtf

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    mb,rtf=load_leads()

    # Only spend requests on targets with an established MartialBot lead.
    jobs=[(key,t,f"{BASE}/en/persons/{slug(t['name'])}/") for key,t in targets.items() if key in mb]

    def one(job):
        key,t,url=job
        rec={'target_key':key,'name':t['name'],'url':url,
             'strict_bout_appearances':t.get('strict_bout_appearances'),
             'martialbot_reach_cm':mb[key]['reach_cm'],'martialbot_url':mb[key]['url']}
        try:
            final,raw=fetch(url);name,reach,vals=parse(raw)
            rec.update({'url':final,'h1':name,'reach_cm':reach,'reach_values':vals})
            if nk(name)!=key:rec['status']='identity_mismatch'
            elif reach is None:rec['status']='no_reach'
            elif not 120<=reach<=270:rec['status']='implausible_reach'
            else:
                rec['agrees_martialbot']=abs(float(reach)-float(mb[key]['reach_cm']))<=1
                other=rtf.get(key)
                if other:
                    rec['ready_to_fight_reach_cm']=other['reach_cm']
                    rec['ready_to_fight_url']=other['url']
                    rec['rtf_conflict']=abs(float(reach)-float(other['reach_cm']))>1
                else:rec['rtf_conflict']=False
                rec['status']='corroborated' if rec['agrees_martialbot'] and not rec['rtf_conflict'] else 'lead_or_conflict'
        except Exception as e:
            rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        rows=list(ex.map(one,jobs))

    additions=[]
    for x in rows:
        if x.get('status')!='corroborated':continue
        t=targets[x['target_key']]
        reach=round((float(x['reach_cm'])+float(x['martialbot_reach_cm']))/2,2)
        if float(reach).is_integer():reach=int(reach)
        additions.append({
          'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'ringside24_martialbot_reach_consensus',
            'exact_identity':True,'agreement_tolerance_cm':1,'fields':{'reach_cm':reach},
            'sources':[
              {'source':'ringside24','url':x['url'],'reported_reach_cm':x['reach_cm']},
              {'source':'martialbot','url':x['martialbot_url'],'reported_reach_cm':x['martialbot_reach_cm']}
            ]}],
          'conflicts':{},'quality':'two_source_reach_consensus_ringside24_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
        })

    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'missing_targets':len(targets),'martialbot_target_jobs':len(jobs),
      'status_counts':counts,'corroborated_additions':len(additions),
      'additions':[{'name':x['name'],'reach_cm':x['fields']['reach_cm']} for x in additions],
      'conflicts':[x for x in rows if x.get('status')=='lead_or_conflict'],
      'rows':rows,
      'policy':'RingSide24 is corroboration-only. Exact deterministic profile identity + explicit reach + MartialBot agreement within 1 cm required; any Ready To Fight disagreement >1 cm blocks automatic addition.'
    }
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_targets','martialbot_target_jobs','status_counts','corroborated_additions')},indent=2))

if __name__=='__main__':main()
