#!/usr/bin/env python3
"""Recover missing priced careers from official WBA fight lists plus internal result consensus.

WBA supplies identity, overall W-L-D and dated opponent rows. W/L is NEVER
inferred from WBA method text or row order. A career is accepted only when:
- the WBA row count equals the stated W-L-D total,
- every row resolves to one unambiguous outcome from existing exact date+pair
  finished-bout observations,
- reconstructed W-L-D equals the WBA stated record, and
- at least one archived priced matchup matches exact date + opponent.
"""
from __future__ import annotations
import argparse,collections,datetime as dt,json,re,sqlite3,time,unicodedata,urllib.parse,urllib.request
from bs4 import BeautifulSoup
from pathlib import Path
from backfill_priced_careers import DB,OUT,nk,priced_missing,match_evidence

UA='Mozilla/5.0 AppwizaBoxingWBAConsensus/1.0'
# IDs confirmed from official WBA profile URLs during the gap audit.
WBA_IDS={
 'Vaida Masiokaite':11929,'Boris Crighton':15366,'Gemma Ruegg':13141,
 'Rhys Edwards':17687,'Xolisani Ndongeni':3551,'Israel Duffus':5491,
 'Bec Connolly':9839,'River Wilson Bent':14409,'Chris Jenkins':5410,
 'Alexis Boureima Kabore':10338,'Fara El Bousairi':17229,'Jordan Grant':18459,
 'Cesar Juarez':5284,'Mickey Ellison':11884,'Yanina Del Carmen Lescano':6594,
 'Juan Carlos Rubio':11156,'Kevin Nicolas Espindola':12764,
}

def get(url,timeout=35):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=timeout) as r:return r.read()

def stated_record(soup):
    for tr in soup.find_all('tr'):
        cells=[re.sub(r'\s+',' ',x.get_text(' ',strip=True)).strip() for x in tr.find_all(['th','td'],recursive=False)]
        if len(cells)>=2 and cells[0].casefold().startswith('record'):
            m=re.search(r'(\d+)\s*[-–−]\s*(\d+)\s*[-–−]\s*(\d+)',cells[1])
            if m:return tuple(map(int,m.groups()))
    return None

def parse_wba(name,wba_id):
    url=f'https://www.wbaboxing.com/wba-boxer-profile?id={wba_id}'
    soup=BeautifulSoup(get(url),'lxml')
    h=soup.find(['h1','h2']);title=re.sub(r'\s+',' ',h.get_text(' ',strip=True)).strip() if h else ''
    if nk(title)!=nk(name):raise ValueError(f'identity heading mismatch: {title!r}')
    record=stated_record(soup)
    if record is None:raise ValueError('missing stated W-L-D')
    rows=[]
    # WBA profile pages use the second table for fights. Rows are:
    # method/round, opponent, division/title, YYYY-MM-DD, country.
    tables=soup.find_all('table')
    if len(tables)<2:raise ValueError('missing fight table')
    for tr in tables[1].find_all('tr'):
        cells=[re.sub(r'\s+',' ',x.get_text(' ',strip=True)).strip() for x in tr.find_all(['th','td'],recursive=False)]
        if len(cells)<4:continue
        m=re.fullmatch(r'(20\d{2}|19\d{2})-(\d{2})-(\d{2})',cells[3])
        if not m:continue
        date=cells[3];opp=cells[1].strip()
        if not opp:continue
        opp_url=None
        opp_cell=tr.find_all(['th','td'],recursive=False)[1]
        a=opp_cell.find('a',href=True) if opp_cell else None
        if a:
            candidate=urllib.parse.urljoin(url,a.get('href'))
            if re.search(r'/wba-boxer-profile/?\?id=\d+',candidate,re.I):
                opp_url=candidate.split('#')[0].rstrip('/')+'/'
        method_round=cells[0]
        mm=re.search(r'\b(KO|TKO|UD|SD|MD|PTS|RTD|DQ|TD|NC)\b',method_round,re.I)
        method=mm.group(1).upper() if mm else ''
        rows.append({'date':date,'opponent':opp,'type':method,'round_time':method_round,
                     'location':cells[4] if len(cells)>4 else '',
                     'division':cells[2] if len(cells)>2 else '',
                     'raw':{'method_round':method_round,'opponent':opp,'division':cells[2] if len(cells)>2 else '',
                            'date':date,'country':cells[4] if len(cells)>4 else '',
                            'opponent_source_url':opp_url,
                            'source':'wba_profile'}})
    # Exact completeness gate: partial recent-fight WBA lists are rejected.
    if len(rows)!=sum(record):raise ValueError(f'partial WBA fight list {len(rows)}/{sum(record)}')
    if len({(r['date'],nk(r['opponent'])) for r in rows})!=len(rows):raise ValueError('duplicate date/opponent rows')
    rows.sort(key=lambda r:r['date'])
    return {'title':title,'url':url,'stated_record':record,'rows':rows}

def result_index(con):
    idx=collections.defaultdict(set)
    for r in con.execute("SELECT source,date,boxer_a,boxer_b,winner FROM bouts WHERE status='FINISHED' AND date IS NOT NULL"):
        a=nk(r['boxer_a']);b=nk(r['boxer_b'])
        if not a or not b:continue
        w=r['winner']
        if w=='BOXER A':winner=a
        elif w=='BOXER B':winner=b
        elif w=='DRAW':winner='DRAW'
        elif w=='NO CONTEST':winner='NO CONTEST'
        else:continue
        idx[(r['date'],*sorted((a,b)))].add(winner)
    return idx

def resolve_career(name,page,idx):
    me=nk(name);w=l=d=0;resolved=[]
    for r in page['rows']:
        opp=nk(r['opponent']);outcomes=idx.get((r['date'],*sorted((me,opp))),set())
        if len(outcomes)!=1:raise ValueError(f'unresolved/conflicting result {r["date"]} {r["opponent"]}: {sorted(outcomes)}')
        outcome=next(iter(outcomes))
        if outcome==me:result='win';w+=1
        elif outcome==opp:result='loss';l+=1
        elif outcome=='DRAW':result='draw';d+=1
        else:raise ValueError(f'non-WLD result {r["date"]} {r["opponent"]}: {outcome}')
        rr=dict(r);rr['result']=result;rr['record']=f'{w}-{l}-{d}';rr['raw']=dict(rr['raw']);rr['raw']['record']=rr['record'];rr['raw']['resolved_outcome']=result
        resolved.append(rr)
    if (w,l,d)!=page['stated_record']:raise ValueError(f'reconstructed record {(w,l,d)} != stated {page["stated_record"]}')
    return resolved

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--limit',type=int,default=100);args=ap.parse_args()
    con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
    OUT.parent.mkdir(parents=True,exist_ok=True)
    done={}
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            try:x=json.loads(line);done[nk(x.get('requested_name') or x.get('verified_title'))]=x
            except Exception:pass
    missing={nk(x['name']):x for x in priced_missing(con) if nk(x['name']) not in done}
    idx=result_index(con);accepted=[];failed=[]
    candidates=[(name,wid,missing.get(nk(name))) for name,wid in WBA_IDS.items() if missing.get(nk(name))]
    candidates=candidates[:args.limit]
    for i,(name,wid,item) in enumerate(candidates,1):
        try:
            page=parse_wba(name,wid);rows=resolve_career(name,page,idx)
            evidence=match_evidence({'rows':rows},item['evidence'])
            if not evidence:raise ValueError('no exact priced date+opponent evidence match')
            found={'requested_name':name,'verified_title':page['title'],'source':'wba_consensus','source_url':page['url'],
                   'born':'','profile':{},'career_rows':rows,'matched_price_evidence':evidence,
                   'priced_bouts':item['priced_bouts'],'bookmakers':item['bookmakers'],
                   'career_complete':True,'stated_record':list(page['stated_record']),
                   'quality':'official_wba_complete_list_plus_independent_exact_result_consensus',
                   'verification':'WBA exact identity + complete row count + every exact date/pair independently resolved + reconstructed W-L-D equals WBA record + priced evidence match',
                   'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()}
            with OUT.open('a',encoding='utf-8') as f:f.write(json.dumps(found,ensure_ascii=False)+'\n')
            accepted.append({'name':name,'rows':len(rows),'priced_bouts':item['priced_bouts']});print(i,name,'OK',len(rows),flush=True)
        except Exception as e:
            failed.append({'name':name,'wba_id':wid,'priced_bouts':item['priced_bouts'],'error':str(e)[:500]});print(i,name,'NO_MATCH',str(e)[:180],flush=True)
        time.sleep(.2)
    report={'attempted':len(candidates),'accepted':len(accepted),'failed':len(failed),'accepted_items':accepted,'failed_items':failed,'source':'wba_consensus'}
    (OUT.parent/'latest_wba_consensus_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
