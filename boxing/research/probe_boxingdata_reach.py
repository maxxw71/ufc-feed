#!/usr/bin/env python3
"""Mine Boxing Data public fight-preview pages for missing reach leads.

Boxing Data is a third-party structured source, so it is never merged alone.
Discovery starts from the public /blog/ index. A candidate must appear in a
fighter-specific heading/section with an explicit Reach value. Automatic
additions require an independent exact-identity source (MartialBot or Ready To
Fight) to agree within 1 cm. Conflicts are retained for audit, never averaged.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,statistics,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
RTF=ROOT/'profile_supplements'/'ready_to_fight_reach_probe.json'
OUT=ROOT/'profile_supplements'/'boxingdata_reach_probe.json'
ADD=ROOT/'profile_supplements'/'boxingdata_reach_additions.jsonl'
BLOG='https://boxing-data.com/blog/'
UA='Mozilla/5.0 AppwizaBoxingDataReach/1.0'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,limit=4_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def cm_measure(s):
    if not s:return None
    txt=str(s).replace('’',"'").replace('′',"'").replace('“','"').replace('”','"').replace('″','"')
    txt=txt.replace('½','.5').replace('¼','.25').replace('¾','.75')
    # Prefer explicit metric when present.
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',txt,re.I)
    if m:
        v=float(m.group(1))
        return round(v,2) if 120<=v<=270 else None
    m=re.search(r'(\d+(?:\.\d+)?)\s*(?:\"|in(?:ches)?)\b',txt,re.I)
    if m:
        v=round(float(m.group(1))*2.54,2)
        return v if 120<=v<=270 else None
    return None

def independent_leads():
    out={}
    if MB.exists():
        try:
            data=json.loads(MB.read_text())
            for x in data.get('leads') or []:
                if x.get('reach_cm') is not None:
                    out.setdefault(nk(x.get('name')),[]).append({
                      'source':'martialbot','url':x.get('url'),'reach_cm':float(x['reach_cm'])})
        except Exception:pass
    if RTF.exists():
        try:
            data=json.loads(RTF.read_text())
            for x in data.get('rows') or []:
                if x.get('status')=='lead' and x.get('reach_cm') is not None:
                    out.setdefault(nk(x.get('name')),[]).append({
                      'source':'ready_to_fight','url':x.get('url'),'reach_cm':float(x['reach_cm'])})
        except Exception:pass
    return out

def discover(targets):
    final,raw=fetch(BLOG)
    soup=BeautifulSoup(raw,'lxml')
    jobs={}
    for a in soup.find_all('a',href=True):
        u=urllib.parse.urljoin(final,a['href']).split('#')[0]
        p=urllib.parse.urlsplit(u)
        if p.hostname not in {'boxing-data.com','www.boxing-data.com'} or not p.path.startswith('/blog/'):
            continue
        if p.path.rstrip('/')=='/blog':continue
        label=re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()
        lk=nk(label)
        hits=[]
        for key,t in targets.items():
            # Fight-preview titles generally contain full fighter names.
            if key and key in lk:hits.append(key)
        if hits:
            jobs[u]={'label':label,'target_keys':sorted(set(hits))}
    return jobs

def section_text(soup,target_name):
    """Return sections whose heading begins with the exact target identity."""
    key=nk(target_name)
    out=[]
    for h in soup.find_all(['h2','h3']):
        ht=re.sub(r'\s+',' ',h.get_text(' ',strip=True)).strip()
        hk=nk(ht)
        if not hk.startswith(key):continue
        parts=[]
        node=h.find_next_sibling()
        while node is not None and getattr(node,'name',None) not in {'h2','h3'}:
            if getattr(node,'get_text',None):
                txt=re.sub(r'\s+',' ',node.get_text(' ',strip=True)).strip()
                if txt:parts.append(txt)
            node=node.find_next_sibling()
        text=' '.join(parts)
        if text:out.append({'heading':ht,'text':text})
    return out

def parse_article(job,targets):
    url,meta=job
    rec={'url':url,'index_label':meta['label'],'target_keys':meta['target_keys'],'rows':[]}
    try:
        final,raw=fetch(url);soup=BeautifulSoup(raw,'lxml');rec['url']=final
        h1=soup.find('h1')
        rec['h1']=re.sub(r'\s+',' ',h1.get_text(' ',strip=True)).strip() if h1 else ''
        for key in meta['target_keys']:
            t=targets[key]
            secs=section_text(soup,t['name'])
            values=[]
            samples=[]
            for sec in secs:
                for m in re.finditer(r'\bReach\s*:\s*([^|;\n]{1,40})',sec['text'],re.I):
                    val=cm_measure(m.group(1))
                    if val is not None:
                        values.append(val);samples.append({'heading':sec['heading'],'raw':m.group(1)[:80]})
                # prose form: "with a 76-inch reach"
                for m in re.finditer(r'\b(?:with\s+)?(?:a\s+)?(\d+(?:\.\d+)?)\s*(?:-|\s*)inch\s+reach\b',sec['text'],re.I):
                    val=round(float(m.group(1))*2.54,2)
                    if 120<=val<=270:
                        values.append(val);samples.append({'heading':sec['heading'],'raw':m.group(0)[:80]})
            vals=sorted(set(round(v,2) for v in values))
            row={'target_key':key,'name':t['name'],'section_count':len(secs),
                 'reach_values_cm':vals,'samples':samples[:8]}
            if len(vals)==1:
                row['reach_cm']=vals[0];row['status']='lead'
            elif len(vals)>1:
                row['status']='article_conflict'
            else:row['status']='no_reach'
            rec['rows'].append(row)
    except Exception as e:
        rec['error']=type(e).__name__+': '+str(e)[:200]
    return rec

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    indep=independent_leads()
    jobs=discover(targets)
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        articles=list(ex.map(lambda x:parse_article(x,targets),jobs.items()))

    leads=[];conflicts=[];additions=[]
    for a in articles:
        for row in a.get('rows') or []:
            if row.get('status')!='lead':continue
            row={**row,'url':a['url'],'article_title':a.get('h1')}
            leads.append(row)
            others=indep.get(row['target_key']) or []
            agreeing=[x for x in others if abs(float(x['reach_cm'])-float(row['reach_cm']))<=1]
            disagreeing=[x for x in others if abs(float(x['reach_cm'])-float(row['reach_cm']))>1]
            if agreeing:
                t=targets[row['target_key']]
                vals=[float(row['reach_cm'])]+[float(x['reach_cm']) for x in agreeing]
                reach=round(statistics.median(vals),2)
                if float(reach).is_integer():reach=int(reach)
                additions.append({
                  'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
                  'fields':{'reach_cm':reach},
                  'evidence':[{'source':'boxingdata_cross_source_reach_consensus',
                    'exact_identity':True,'agreement_tolerance_cm':1,'fields':{'reach_cm':reach},
                    'sources':[{'source':'boxing_data_public_preview','url':a['url'],
                                'reported_reach_cm':row['reach_cm']},
                               *agreeing]}],
                  'conflicts':{},'quality':'public_fight_preview_cross_source_reach_consensus_exact_identity',
                  'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
                })
            elif disagreeing:
                conflicts.append({'name':row['name'],'boxingdata_reach_cm':row['reach_cm'],
                                  'boxingdata_url':a['url'],'other_sources':disagreeing})

    # one addition per fighter; agreement should make duplicates identical/near-identical.
    unique={}
    for x in additions:
        sid=x['target_source_id'];r=float(x['fields']['reach_cm'])
        if sid in unique and abs(float(unique[sid]['fields']['reach_cm'])-r)>1:
            conflicts.append({'name':x['name'],'reason':'multiple_corroborated_boxingdata_values',
                              'values':[unique[sid]['fields']['reach_cm'],x['fields']['reach_cm']]})
            unique[sid]=None
        elif sid not in unique:unique[sid]=x
    additions=[x for x in unique.values() if x is not None]

    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'indexed_matching_articles':len(jobs),'articles_scanned':len(articles),'lead_count':len(leads),
      'corroborated_additions':len(additions),'conflicts_quarantined':len(conflicts),
      'leads':leads,'conflicts':conflicts,
      'additions':[{'name':x['name'],'reach_cm':x['fields']['reach_cm']} for x in additions],
      'policy':'Public Boxing Data fight previews are lead-tier only. Exact fighter-specific section plus explicit Reach required. Automatic merge only with independent exact-identity MartialBot or Ready To Fight agreement within 1 cm; conflicts quarantined.'
    }
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_targets','indexed_matching_articles','articles_scanned','lead_count','corroborated_additions','conflicts_quarantined')},indent=2))

if __name__=='__main__':main()
