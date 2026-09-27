#!/usr/bin/env python3
"""Probe public BoxRec Wiki pages for missing reach and corroborate MartialBot.

This uses human-readable BoxRec Wiki pages (not login-gated pro profile pages).
A lead requires exact normalized article identity and a plausible Reach field.
Automatic additions require independent MartialBot agreement within 1 cm.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
OUT=ROOT/'profile_supplements'/'boxrec_wiki_reach_probe.json'
ADD=ROOT/'profile_supplements'/'boxrec_wiki_reach_additions.jsonl'
UA='Mozilla/5.0 AppwizaBoxRecWikiReach/1.0'
BASE='https://boxrec.com/wiki/index.php/'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def url_for(name):
    title=str(name).replace(' ','_')
    return BASE+urllib.parse.quote(title,safe="_-'()")

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('response too large')
        return r.geturl(),raw

def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1');heading=' '.join(h.stripped_strings) if h else ''
    text=' '.join(soup.stripped_strings)
    # BoxRec wiki format commonly: Name: X ... Height: 165cm Reach: 159cm
    mname=re.search(r'\bName:\s*(.+?)(?=\s+(?:Alias|Hometown|Birthplace|Stance|Height|Reach|Pro Boxer):)',text,re.I)
    name=mname.group(1).strip() if mname else heading
    vals=[]
    for m in re.finditer(r'\bReach:\s*(\d+(?:\.\d+)?)\s*cm\b',text,re.I):
        v=float(m.group(1))
        if 120<=v<=270:vals.append(v)
    for m in re.finditer(r'\bReach:\s*(\d+(?:\.\d+)?)\s*(?:in|inch|inches|["″])\b',text,re.I):
        v=round(float(m.group(1))*2.54,2)
        if 120<=v<=270:vals.append(v)
    vals=sorted(set(round(v,2) for v in vals))
    return heading,name,(vals[0] if len(vals)==1 else None),vals

def main():
    audit=json.loads(AUDIT.read_text())
    targets=[x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])]
    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={nk(x['name']):x for x in mbobj.get('leads',[])}

    def one(t):
        name=t['name'];key=nk(name);u=url_for(name)
        rec={'id':t['id'],'name':name,'url':u,'strict_bout_appearances':t.get('strict_bout_appearances')}
        try:
            final,raw=fetch(u);heading,article_name,reach,vals=parse(raw)
            rec.update({'url':final,'h1':heading,'article_name':article_name,'reach_cm':reach,'reach_values':vals})
            # Require exact Name field or exact page heading after removing a common
            # " - BoxRec" suffix; redirects are accepted only if identity remains exact.
            hclean=re.sub(r'\s*-\s*BoxRec.*$','',heading,flags=re.I).strip()
            if nk(article_name)!=key and nk(hclean)!=key:rec['status']='identity_mismatch'
            elif reach is None:rec['status']='no_reach'
            else:
                rec['status']='lead'
                m=mb.get(key)
                if m and m.get('reach_cm') is not None:
                    rec['martialbot_reach_cm']=m['reach_cm']
                    rec['agrees_martialbot']=abs(float(m['reach_cm'])-float(reach))<=1
        except urllib.error.HTTPError as e:
            rec.update({'status':f'http_{e.code}'})
        except Exception as e:
            rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=6) as ex:rows=list(ex.map(one,targets))
    leads=[x for x in rows if x['status']=='lead']
    corr=[x for x in leads if x.get('agrees_martialbot') is True]
    conflicts=[x for x in leads if x.get('agrees_martialbot') is False]
    additions=[]
    for x in corr:
        t=next(t for t in targets if t['id']==x['id']);m=mb[nk(x['name'])]
        reach=round((float(x['reach_cm'])+float(m['reach_cm']))/2,2)
        if float(reach).is_integer():reach=int(reach)
        additions.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'boxrec_wiki_martialbot_reach_consensus','fields':{'reach_cm':reach},
            'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':[{'source':'boxrec_wiki','url':x['url'],'reported_reach_cm':x['reach_cm']},
                       {'source':'martialbot','url':m['url'],'reported_reach_cm':m['reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_boxrec_wiki_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'targets':len(targets),
      'status_counts':counts,'lead_count':len(leads),'martialbot_corroborated':len(corr),
      'martialbot_conflicts':len(conflicts),
      'corroborated_profiles':[{'name':x['name'],'boxrec_wiki_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x['martialbot_reach_cm'],'url':x['url']} for x in corr],
      'conflicting_profiles':[{'name':x['name'],'boxrec_wiki_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x.get('martialbot_reach_cm'),'url':x['url']} for x in conflicts],
      'rows':rows,
      'policy':'Exact public BoxRec Wiki identity + plausible Reach. Automatic addition only when independent exact-identity MartialBot agrees within 1 cm; disagreements are quarantined.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('targets','status_counts','lead_count','martialbot_corroborated','martialbot_conflicts')},indent=2))
if __name__=='__main__':main()
