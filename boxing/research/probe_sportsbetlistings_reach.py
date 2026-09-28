#!/usr/bin/env python3
"""Mine SportsBetListings boxing previews for explicit missing-reach leads.

This source is corroboration-only. Article URLs are discovered from public
WordPress/sitemap surfaces and narrowed by target name tokens. A lead requires
an exact target identity in the article and an explicit numeric reach in a
fighter-specific paragraph. "Unlisted"/N/A reach is recorded as absence, never
converted from height. No automatic strict merge happens in this collector;
the cross-probe consensus layer must find an independent agreeing source.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'sportsbetlistings_reach_probe.json'
BASE='https://www.sportsbetlistings.com'
UA='Mozilla/5.0 AppwizaSportsBetListingsReach/1.0'
SEEDS=[
 BASE+'/sitemap.xml',BASE+'/sitemap_index.xml',BASE+'/wp-sitemap.xml',
 BASE+'/wp-sitemap-posts-post-1.xml',BASE+'/wp-sitemap-posts-page-1.xml',
 BASE+'/boxing/'
]

def ascii_text(s):
    return unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode()
def nk(s):return re.sub(r'[^a-z0-9]+','',ascii_text(s).casefold())
def tokens(s):return [x for x in re.findall(r'[a-z0-9]+',ascii_text(s).casefold()) if len(x)>=3]

def fetch(url,limit=8_000_000,timeout=35):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw,r.headers.get('content-type','')

def xml_locs(raw):
    try:root=ET.fromstring(raw)
    except Exception:return []
    return [(x.text or '').strip() for x in root.iter() if x.tag.endswith('loc') and (x.text or '').strip()]

def discover(targets):
    # surname/token discovery only. Exact article-body identity is mandatory.
    target_tokens={}
    for key,t in targets.items():
        ts=tokens(t['name'])
        if ts:target_tokens[key]=ts

    queue=list(SEEDS);seen=set();urls={};diag=[]
    while queue and len(seen)<100:
        u=queue.pop(0)
        if u in seen:continue
        seen.add(u)
        try:
            final,raw,ct=fetch(u)
            locs=xml_locs(raw)
            robots=[]
            txt=raw.decode('utf-8','replace')
            robots=re.findall(r'(?im)^\s*Sitemap:\s*(https?://\S+)\s*$',txt)
            found=[]
            for x in [*robots,*locs]:
                low=x.casefold()
                if 'sitemap' in low and x not in seen and x not in queue:
                    queue.append(x);continue
                p=urllib.parse.urlsplit(x)
                if 'sportsbetlistings.com' not in (p.hostname or ''):continue
                if '/boxing/' not in p.path.casefold():continue
                slug=ascii_text(p.path).casefold()
                hits=[]
                for key,ts in target_tokens.items():
                    # Prefer surname plus at least one other name token where
                    # possible; single-token names use that token.
                    if ts[-1] in slug and (len(ts)==1 or any(z in slug for z in ts[:-1])):
                        hits.append(key)
                if hits:
                    urls.setdefault(x.split('#')[0],set()).update(hits);found.append(x)
            # HTML boxing/category fallback.
            if not locs:
                soup=BeautifulSoup(raw,'lxml')
                for a in soup.find_all('a',href=True):
                    x=urllib.parse.urljoin(final,a['href']).split('#')[0]
                    p=urllib.parse.urlsplit(x)
                    if 'sportsbetlistings.com' not in (p.hostname or '') or '/boxing/' not in p.path.casefold():continue
                    slug=ascii_text(p.path).casefold();hits=[]
                    for key,ts in target_tokens.items():
                        if ts[-1] in slug and (len(ts)==1 or any(z in slug for z in ts[:-1])):hits.append(key)
                    if hits:urls.setdefault(x,set()).update(hits)
            diag.append({'url':u,'status':'ok','locs':len(locs),'candidate_urls_found':len(found),'bytes':len(raw),'content_type':ct})
        except Exception as e:
            diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    return {u:sorted(v) for u,v in urls.items()},diag

def inches_to_cm(v):
    x=float(v);return round(x*2.54,2) if 48<=x<=110 else None

def paragraph_lead(text,name):
    """Extract explicit reach only from a paragraph tied to this fighter."""
    plain=re.sub(r'\s+',' ',text).strip()
    low=ascii_text(plain).casefold()
    ts=tokens(name)
    if not ts:return None,None
    # Require surname plus either full normalized name or another name token.
    if ts[-1] not in low:return None,None
    if len(ts)>1 and nk(name) not in nk(plain) and not any(z in low for z in ts[:-1]):
        return None,None
    if re.search(r'\breach\s+(?:is\s+)?(?:unlisted|unknown|n/?a|not\s+listed)\b',low):
        return None,'explicit_unlisted'
    pats=[
      r'\b(?:has|with|owns|possesses)\s+(?:an?\s+)?(\d+(?:\.\d+)?)\s*(?:-|\s*)inch(?:es)?\s+reach\b',
      r'\breach\s+(?:is|of|measures|at)\s+(\d+(?:\.\d+)?)\s*(?:-|\s*)inch(?:es)?\b',
      r'\b(\d+(?:\.\d+)?)\s*(?:-|\s*)inch(?:es)?\s+reach\b',
    ]
    vals=[]
    for pat in pats:
        for m in re.finditer(pat,plain,re.I):
            cm=inches_to_cm(m.group(1))
            if cm is not None:vals.append(cm)
    vals=sorted(set(vals))
    if not vals:return None,None
    if max(vals)-min(vals)>1.01:return None,'paragraph_numeric_conflict'
    return round(sum(vals)/len(vals),2),None

def parse_article(raw,target_keys,targets,url):
    soup=BeautifulSoup(raw,'lxml')
    title=re.sub(r'\s+',' ',soup.title.get_text(' ',strip=True)).strip() if soup.title else ''
    paras=[]
    for tag in soup.find_all(['p','li']):
        s=re.sub(r'\s+',' ',tag.get_text(' ',strip=True)).strip()
        if s:paras.append(s)
    rows=[]
    for key in target_keys:
        t=targets[key];hits=[];absence=False
        for p in paras:
            reach,reason=paragraph_lead(p,t['name'])
            if reach is not None:hits.append({'reach_cm':reach,'text_sample':p[:700]})
            elif reason=='explicit_unlisted':absence=True
        vals=sorted({round(x['reach_cm'],2) for x in hits})
        rec={'name':t['name'],'target_key':key,'url':url,'title':title}
        if len(vals)==1:
            rec.update({'status':'lead','reach_cm':vals[0],'samples':hits[:5]})
        elif len(vals)>1:
            rec.update({'status':'article_conflict','reach_values_cm':vals,'samples':hits[:8]})
        elif absence:rec['status']='explicit_unlisted'
        else:rec['status']='no_numeric_reach'
        rows.append(rec)
    return rows

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    urls,diag=discover(targets)

    def one(item):
        u,keys=item
        try:
            final,raw,ct=fetch(u,3_000_000,30)
            return parse_article(raw,keys,targets,final)
        except Exception as e:
            return [{'url':u,'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]}]

    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        nested=list(ex.map(one,urls.items()))
    rows=[x for group in nested for x in group]
    leads=[x for x in rows if x.get('status')=='lead']
    counts={}
    for x in rows:counts[x.get('status')]=counts.get(x.get('status'),0)+1
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'missing_targets':len(targets),'candidate_articles':len(urls),'status_counts':counts,
      'lead_count':len(leads),'leads':leads,'discovery_diagnostics':diag,'rows':rows,
      'policy':'SportsBetListings is lead-tier only. Public sitemap/category discovery, article-body fighter identity, and explicit numeric reach in fighter-specific paragraph required. Height is never used as reach. Explicit unlisted/N/A remains missing. Strict promotion requires a separate independent agreeing source via cross-probe consensus.'
    }
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_targets','candidate_articles','status_counts','lead_count')},indent=2))

if __name__=='__main__':main()
