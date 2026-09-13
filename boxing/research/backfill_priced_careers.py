#!/usr/bin/env python3
"""Backfill missing priced fighters with evidence-verified Wikipedia careers.

A page is accepted only when its professional record contains at least one exact
archived-odds matchup by full date + normalized opponent name. Discovery is
allowed to use direct title variants, Wikipedia search, or an exact dated row on
a known opponent's record page. Identity acceptance remains strict and evidence
based; ambiguous candidates are retained as failures, never guessed.

Known no-match names are cooled down for 14 days so successive bounded runs move
deeper into the missing-fighter queue instead of repeatedly searching the same
Wikipedia dead ends. The cache is only an efficiency hint; it expires.
"""
from __future__ import annotations
import argparse,datetime as dt,json,re,sqlite3,sys,time,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parent
COLLECTORS=ROOT.parent/'collectors';sys.path.insert(0,str(COLLECTORS))
from wiki_record_tables import record_tables,record_date
DB=ROOT/'boxing.sqlite3'
OUT=ROOT.parent/'supplemental_careers'/'verified_priced_careers.jsonl'
CACHE=ROOT.parent/'backfill_state'/'wikipedia_failures.json'
UA='Mozilla/5.0 AppwizaBoxingCareerBackfill/1.2'
COOLDOWN_DAYS=14

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]','',x)

def get(url,timeout=35):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=timeout) as r:return r.read()

def load_cache():
    try:data=json.loads(CACHE.read_text())
    except Exception:data={}
    now=dt.datetime.now(dt.timezone.utc)
    active={}
    for key,v in (data if isinstance(data,dict) else {}).items():
        try:when=dt.datetime.fromisoformat(v['attempted_at'])
        except Exception:continue
        if when.tzinfo is None:when=when.replace(tzinfo=dt.timezone.utc)
        if (now-when).days<COOLDOWN_DAYS:active[key]=v
    return active

def save_cache(cache):
    CACHE.parent.mkdir(parents=True,exist_ok=True)
    CACHE.write_text(json.dumps(cache,indent=2,ensure_ascii=False))

def direct_titles(name):
    base=str(name or '').strip()
    if not base:return []
    out=[base]
    low=base.casefold()
    if '(boxer)' not in low:out.append(base+' (boxer)')
    if re.search(r'\bJr\.?$',base,re.I):
        nojr=re.sub(r'\s+Jr\.?$','',base,flags=re.I).strip()
        out.extend([nojr,nojr+' (boxer)'])
    return list(dict.fromkeys(out))

def search_titles(name):
    titles=[];titles.extend(direct_titles(name))
    for query in (f'"{name}" boxer',f'"{name}" boxing',str(name)):
        q=urllib.parse.urlencode({'action':'query','list':'search','srsearch':query,'srlimit':10,'format':'json','utf8':1})
        try:data=json.loads(get('https://en.wikipedia.org/w/api.php?'+q))
        except Exception:continue
        titles.extend(x.get('title') for x in data.get('query',{}).get('search',[]) if x.get('title'))
    titles=list(dict.fromkeys(titles))
    titles.sort(key=lambda t:(nk(t)!=nk(name), '(boxer)' not in t.casefold(), len(t)))
    return titles

def parse_page(title):
    url='https://en.wikipedia.org/wiki/'+urllib.parse.quote(str(title).replace(' ','_'))
    soup=BeautifulSoup(get(url),'lxml')
    info=soup.select_one('table.infobox');profile={}
    if info:
        for tr in info.find_all('tr'):
            th=tr.find('th');td=tr.find('td')
            if th and td:profile[th.get_text(' ',strip=True)]=td.get_text(' ',strip=True)
    born=soup.select_one('.bday');rows=[]
    for headers,trs in record_tables(soup):
        try:opp_idx=headers.index('opponent')
        except ValueError:continue
        for tr in trs:
            cells=tr.find_all(['th','td'],recursive=False)
            if len(cells)!=len(headers):continue
            r=dict(zip(headers,[c.get_text(' ',strip=True) for c in cells]))
            result=str(r.get('result','')).casefold()
            if result not in ('win','loss','draw','nc','no contest'):continue
            try:date=record_date(r.get('date',''))
            except Exception:continue
            opponent_titles=[]
            if opp_idx < len(cells):
                for a in cells[opp_idx].find_all('a',href=True):
                    href=urllib.parse.urljoin(url,a['href']).split('#')[0]
                    u=urllib.parse.urlsplit(href)
                    if u.hostname=='en.wikipedia.org' and '/wiki/' in u.path and ':' not in u.path and 'redlink' not in href:
                        opponent_titles.append(urllib.parse.unquote(u.path.split('/wiki/',1)[1]).replace('_',' '))
            rows.append({'date':date,'result':result,'opponent':r.get('opponent',''),'type':r.get('type',''),
                         'round_time':r.get('round, time',''),'location':r.get('location',''),'record':r.get('record',''),
                         'opponent_titles':list(dict.fromkeys(opponent_titles)),'raw':r})
    return {'title':title,'url':url,'born':born.get_text(strip=True) if born else None,'profile':profile,'rows':rows}

def priced_missing(con):
    known={nk(r['name']) for r in con.execute('select name from normalized_fighters') if r['name']}
    for r in con.execute("select distinct boxer_a from bouts where source in ('wikipedia','champinon')"):
        if r['boxer_a']:known.add(nk(r['boxer_a']))
    groups={}
    for r in con.execute("select odds_bout_id,event_date,selection,bookmaker from priced_bout_research where result in ('WIN','LOSS')"):
        k=(r['odds_bout_id'],r['event_date']);g=groups.setdefault(k,{'names':set(),'quotes':0,'books':set()})
        if r['selection']:g['names'].add(str(r['selection']).strip())
        g['quotes']+=1;g['books'].add(r['bookmaker'])
    miss={}
    for (bid,date),g in groups.items():
        if len(g['names'])!=2:continue
        a,b=sorted(g['names'])
        for target,opp in ((a,b),(b,a)):
            if nk(target) in known:continue
            x=miss.setdefault(target,{'name':target,'evidence':[],'bouts':set(),'quotes':0,'books':set()})
            x['evidence'].append({'odds_bout_id':bid,'date':date,'opponent':opp})
            x['bouts'].add((bid,date));x['quotes']+=g['quotes'];x['books'].update(g['books'])
    ranked=[]
    for x in miss.values():
        x['priced_bouts']=len(x.pop('bouts'));x['bookmakers']=len(x.pop('books'))
        x['evidence']=[dict(t) for t in {tuple(sorted(e.items())) for e in x['evidence']}]
        ranked.append(x)
    ranked.sort(key=lambda x:(-x['priced_bouts'],-x['quotes'],x['name']))
    return ranked

def match_evidence(page,evidence):
    idx={(r['date'],nk(r['opponent'])) for r in page['rows']}
    return [e for e in evidence if (e['date'],nk(e['opponent'])) in idx]

def discover_from_opponents(item,cache):
    found=[];target_key=nk(item['name'])
    for e in item['evidence'][:6]:
        for opp_title in search_titles(e['opponent'])[:8]:
            try:page=cache.setdefault(opp_title,parse_page(opp_title))
            except Exception:continue
            for r in page['rows']:
                if r['date']!=e['date'] or nk(r['opponent'])!=target_key:continue
                found.extend(r.get('opponent_titles') or [])
    return list(dict.fromkeys(found))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--limit',type=int,default=60);args=ap.parse_args()
    con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
    OUT.parent.mkdir(parents=True,exist_ok=True)
    done={}
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            try:r=json.loads(line);done[nk(r['requested_name'])]=r
            except Exception:pass
    failcache=load_cache()
    missing=[x for x in priced_missing(con) if nk(x['name']) not in done]
    queue=[x for x in missing if nk(x['name']) not in failcache][:args.limit]
    accepted=[];failed=[];cache={}
    print('QUEUE',len(queue),'SKIPPED_COOLDOWN',sum(nk(x['name']) in failcache for x in missing),flush=True)
    for i,item in enumerate(queue,1):
        found=None;tried=[];discovery='';candidates=[]
        candidates.extend((t,'direct_or_search') for t in search_titles(item['name']))
        for t in discover_from_opponents(item,cache):candidates.append((t,'opponent_exact_date_link'))
        seen=set()
        for title,how in candidates:
            if title in seen:continue
            seen.add(title);tried.append(title)
            try:page=cache.setdefault(title,parse_page(title))
            except Exception:continue
            matches=match_evidence(page,item['evidence'])
            if matches:
                discovery=how
                found={'requested_name':item['name'],'verified_title':title,'source':'wikipedia','source_url':page['url'],'born':page['born'],
                       'profile':page['profile'],'career_rows':page['rows'],'matched_price_evidence':matches,
                       'priced_bouts':item['priced_bouts'],'bookmakers':item['bookmakers'],'discovery':how,
                       'verification':'professional record contains exact full-date + normalized-opponent priced matchup',
                       'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()}
                break
        key=nk(item['name'])
        if found:
            with OUT.open('a',encoding='utf-8') as f:f.write(json.dumps(found,ensure_ascii=False)+'\n')
            accepted.append({'name':item['name'],'page':found['verified_title'],'rows':len(found['career_rows']),
                             'matched':len(found['matched_price_evidence']),'priced_bouts':item['priced_bouts'],'discovery':discovery})
            failcache.pop(key,None)
        else:
            failed.append({'name':item['name'],'priced_bouts':item['priced_bouts'],'tried':tried})
            failcache[key]={'name':item['name'],'attempted_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                            'priced_bouts':item['priced_bouts'],'candidate_titles':tried[:30]}
        print(i,item['name'],'OK' if found else 'NO_MATCH',discovery,flush=True);time.sleep(.15)
    save_cache(failcache)
    report={'attempted':len(queue),'accepted':len(accepted),'failed':len(failed),'cooldown_skipped':sum(nk(x['name']) in failcache for x in missing if x not in queue),
            'accepted_items':accepted,'failed_items':failed,'total_supplemental_fighters':len(done)+len(accepted)}
    (OUT.parent/'latest_backfill_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
