#!/usr/bin/env python3
"""Collect official IBF men's historical ranking posts from the IBF REST API.

The official site exposes ratings as a custom WordPress post type with
Organization and Weight Class taxonomies. We use only IBF-owned REST data.

Timing policy:
- rating period comes from the official post title (MM/YYYY);
- safe_effective_date is the calendar day AFTER the WordPress publication date,
  avoiding same-day/intraday look-ahead ambiguity.

No missing ranks are inferred. Conflicting rank slots are quarantined.
"""
from __future__ import annotations
import datetime as dt,json,re,urllib.parse,urllib.request,urllib.error
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'rankings';OUT.mkdir(parents=True,exist_ok=True)
BASE='https://www.ibf-usba-boxing.com'
UA='Mozilla/5.0 AppwizaBoxingIBFArchive/1.0'

ALIASES={
 'HEAVYWEIGHT':'heavyweight','CRUISERWEIGHT':'cruiserweight',
 'LT. HEAVYWEIGHT':'light heavyweight','LIGHT HEAVYWEIGHT':'light heavyweight',
 'S. MIDDLEWEIGHT':'super middleweight','SUPER MIDDLEWEIGHT':'super middleweight',
 'MIDDLEWEIGHT':'middleweight','JR. MIDDLEWEIGHT':'junior middleweight',
 'JUNIOR MIDDLEWEIGHT':'junior middleweight','WELTERWEIGHT':'welterweight',
 'JR. WELTERWEIGHT':'junior welterweight','JUNIOR WELTERWEIGHT':'junior welterweight',
 'LIGHTWEIGHT':'lightweight','JR. LIGHTWEIGHT':'junior lightweight',
 'JUNIOR LIGHTWEIGHT':'junior lightweight','FEATHERWEIGHT':'featherweight',
 'JR. FEATHERWEIGHT':'junior featherweight','JUNIOR FEATHERWEIGHT':'junior featherweight',
 'BANTAMWEIGHT':'bantamweight','JR. BANTAMWEIGHT':'junior bantamweight',
 'JUNIOR BANTAMWEIGHT':'junior bantamweight','FLYWEIGHT':'flyweight',
 'JR. FLYWEIGHT':'junior flyweight','JUNIOR FLYWEIGHT':'junior flyweight',
 'MINI FLYWEIGHT':'minimumweight','MINIMUMWEIGHT':'minimumweight'
}

def get_json(url,limit=8_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json','Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=40) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return json.loads(raw.decode('utf-8')),dict(r.headers)

def norm_div(s):
    s=re.sub(r'\s+',' ',str(s or '').upper()).strip()
    s=re.sub(r'\s*\([^)]*\)\s*',' ',s)
    s=re.sub(r'\s+',' ',s).strip(' -–')
    # strip weight suffixes that survive parentheses
    s=re.sub(r'\b(?:OVER\s+)?\d+\s*LBS?\b.*$','',s).strip(' -–')
    return ALIASES.get(s)

def clean_name(s):
    s=BeautifulSoup(str(s or ''),'lxml').get_text(' ',strip=True)
    s=re.sub(r'\s+',' ',s).strip(' .-–')
    # Strip record/country fragments but do not infer anything from them.
    s=re.sub(r'\s*\(\s*\d+\s*[-–]\s*\d+[^)]*\)\s*.*$','',s).strip()
    s=re.sub(r'\s+[A-Z]{3}\s*$','',s).strip()
    return s

def parse_title(title):
    text=BeautifulSoup(str(title or ''),'lxml').get_text(' ',strip=True)
    m=re.search(r'^IBF:\s*(.+?)\s*[–-]\s*(\d{1,2})/(\d{4})\s*$',text,re.I)
    if not m:return None
    div=norm_div(m.group(1))
    if not div:return None
    month=int(m.group(2));year=int(m.group(3))
    if not 1<=month<=12:return None
    return div,year,month,text

def parse_post(post):
    pt=parse_title(post.get('title',{}).get('rendered') if isinstance(post.get('title'),dict) else post.get('title'))
    if not pt:return None,'title_not_ibf_rating'
    div,year,month,title=pt
    try:pub=dt.datetime.fromisoformat(str(post.get('date')).replace('Z','+00:00')).date()
    except Exception:return None,'missing_publication_date'
    safe=(pub+dt.timedelta(days=1)).isoformat()
    html=(post.get('content') or {}).get('rendered','') if isinstance(post.get('content'),dict) else str(post.get('content') or '')
    soup=BeautifulSoup(html,'lxml')
    candidates=[];champions=[]
    # Prefer explicit HTML table structure.
    for tr in soup.find_all('tr'):
        cells=[c.get_text(' ',strip=True) for c in tr.find_all(['td','th'])]
        if not cells:continue
        if any(re.search(r'\bchampion\b',x,re.I) for x in cells[:2]):
            vals=[clean_name(x) for x in cells[1:] if clean_name(x)]
            if vals:champions.append(vals[0])
        rank_i=None
        for i,x in enumerate(cells[:3]):
            m=re.fullmatch(r'#?\s*(\d{1,2})\.?',x.strip())
            if m and 1<=int(m.group(1))<=15:
                rank_i=(i,int(m.group(1)));break
        if rank_i:
            i,rank=rank_i
            vals=[clean_name(x) for x in cells[i+1:] if clean_name(x)]
            if vals:candidates.append((rank,vals[0]))
    # Fallback to visible text when legacy posts are not table-marked.
    if len(candidates)<5:
        for line in soup.stripped_strings:
            m=re.match(r'^\s*#?\s*(\d{1,2})[.)-]?\s+(.+?)\s*$',line)
            if m and 1<=int(m.group(1))<=15:
                candidates.append((int(m.group(1)),clean_name(m.group(2))))
    grouped={}
    for rank,name in candidates:
        if not name or len(name)<2 or re.fullmatch(r'[\d .-]+',name):continue
        grouped.setdefault(rank,set()).add(name)
    conflicts={r:sorted(v) for r,v in grouped.items() if len({re.sub(r'[^a-z0-9]+','',x.lower()) for x in v})>1}
    if conflicts:return None,{'reason':'conflicting_rank_slots','conflicts':conflicts}
    ranks=[]
    for rank,names in grouped.items():
        name=sorted(names)[0]
        ranks.append({'division':div,'rank':rank,'name':name,'rating_year':year,'rating_month':month,
                      'published_date':pub.isoformat(),'safe_effective_date':safe,
                      'source_url':post.get('link'),'source_post_id':post.get('id')})
    ranks.sort(key=lambda x:x['rank'])
    # IBF frequently leaves #1/#2 vacant pending eliminators, so do not require 15.
    if len(ranks)<8:
        return None,{'reason':'insufficient_rank_rows','count':len(ranks),'text_sample':list(soup.stripped_strings)[:80]}
    champion_rows=[]
    for name in sorted(set(champions)):
        if name:
            champion_rows.append({'division':div,'status':'Champion','name':name,'rating_year':year,'rating_month':month,
                                  'published_date':pub.isoformat(),'safe_effective_date':safe,
                                  'source_url':post.get('link'),'source_post_id':post.get('id')})
    return {'division':div,'year':year,'month':month,'published_date':pub.isoformat(),
            'safe_effective_date':safe,'title':title,'ranks':ranks,'champions':champion_rows,
            'source_url':post.get('link'),'post_id':post.get('id')},None

def main():
    types,_=get_json(BASE+'/wp-json/wp/v2/types')
    rating_type=types.get('ratings')
    if not rating_type:raise SystemExit('IBF REST has no ratings type')
    rest_base=rating_type.get('rest_base') or 'ratings'
    orgs,_=get_json(BASE+'/wp-json/wp/v2/org?per_page=100')
    ibf=next((x for x in orgs if str(x.get('slug','')).lower()=='ibf' or 'international boxing federation' in str(x.get('name','')).lower()),None)
    if not ibf:raise SystemExit('IBF organization term not found')
    posts=[];page=1
    while True:
        qs=urllib.parse.urlencode({'per_page':100,'page':page,'org':ibf['id'],'orderby':'date','order':'asc',
                                   '_fields':'id,date,slug,title,content,link,org,weight-class'})
        try:data,headers=get_json(f'{BASE}/wp-json/wp/v2/{rest_base}?{qs}')
        except urllib.error.HTTPError as e:
            if e.code==400 and page>1:break
            raise
        if not data:break
        posts.extend(data)
        total_pages=int(headers.get('X-WP-TotalPages','1') or 1)
        if page>=total_pages:break
        page+=1
    parsed=[];review=[]
    for p in posts:
        item,err=parse_post(p)
        if item:parsed.append(item)
        else:review.append({'id':p.get('id'),'link':p.get('link'),'title':(p.get('title') or {}).get('rendered') if isinstance(p.get('title'),dict) else p.get('title'),'error':err})
    ranks=[r for x in parsed for r in x['ranks']]
    champs=[r for x in parsed for r in x['champions']]
    # A body/date/division/rank can only identify one contender. Quarantine any
    # residual conflict across duplicate posts.
    slots={}
    for r in ranks:slots.setdefault((r['safe_effective_date'],r['division'],r['rank']),[]).append(r)
    clean=[];conflicts=[]
    for key,group in slots.items():
        names={re.sub(r'[^a-z0-9]+','',x['name'].lower()) for x in group}
        if len(names)>1:conflicts.append({'slot':key,'rows':group})
        else:clean.append(sorted(group,key=lambda x:(x['rating_year'],x['rating_month'],x.get('source_post_id') or 0))[-1])
    ranks=sorted(clean,key=lambda x:(x['safe_effective_date'],x['division'],x['rank']))
    (OUT/'ibf_monthly_rankings.json').write_text(json.dumps(ranks,indent=2,ensure_ascii=False))
    (OUT/'ibf_monthly_champions.json').write_text(json.dumps(champs,indent=2,ensure_ascii=False))
    meta={'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'rest_type':rest_base,'org_term_id':ibf['id'],
          'posts_scanned':len(posts),'parsed_division_posts':len(parsed),'review_posts':len(review),
          'ranking_rows':len(ranks),'champion_rows':len(champs),'years':sorted({r['rating_year'] for r in ranks}),
          'rating_periods':len({(r['rating_year'],r['rating_month']) for r in ranks}),
          'quarantined_conflicting_rank_slots':len(conflicts),
          'review_sample':review[:100],
          'policy':'Official IBF REST ratings posts only. Safe effective date is the day after the official WordPress publication date; vacant/missing ranks are never inferred.'}
    (OUT/'ibf_monthly_rankings_meta.json').write_text(json.dumps(meta,indent=2,ensure_ascii=False))
    print(json.dumps({k:meta[k] for k in ['posts_scanned','parsed_division_posts','review_posts','ranking_rows','champion_rows','years','rating_periods','quarantined_conflicting_rank_slots']},indent=2))

if __name__=='__main__':main()
