#!/usr/bin/env python3
"""Collect verified structured fight-total punch stats from Ready To Fight.

This source is kept separate from CompuBox. It provides fight-total Total/Jab/
Power landed and thrown counts, not round-by-round observations.

Strict gates:
- explicitly seeded public RTF fight URLs only;
- page title must identify two fighters;
- displayed fight date must resolve to exactly one local finished date+pair
  (same day or +/-1 day only for UTC/local-date rollover);
- both sides must have Total/Jab/Power thrown+landed values;
- landed arithmetic Total == Jab + Power must hold exactly;
- thrown arithmetic discrepancy must be <=2.5% per side (reported source
  values are preserved; no value is corrected or inferred);
- no current-fight result is used as a predictive feature.

Output rows are source-labeled and can be merged chronologically with stronger
round-level reports while deduplicating same date+pair observations.
"""
from __future__ import annotations
import argparse,datetime as dt,json,re,sqlite3,time,unicodedata,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'research'/'boxing.sqlite3'
OUT=ROOT/'punch_supplements'/'ready_to_fight_punch_observations.jsonl'
REPORT=ROOT/'punch_supplements'/'ready_to_fight_punch_report.json'
UA='Mozilla/5.0 AppwizaBoxingRTFPunch/1.0'

SEEDS=[
 'https://rtfight.com/fights/essuman-vs-kongo',
 'https://rtfight.com/de/fights/2781/analytics',
 'https://rtfight.com/fights/essuman-vs-vaughan',
 'https://rtfight.com/fights/jack-catterall-vs-ekow-essuman',
 'https://rtfight.com/fights/baraou-vs-eggington',
 'https://rtfight.com/fights/crocker-vs-donovan-1',
 'https://rtfight.com/fights/crocker-vs-donovan',
 'https://rtfight.com/fights/2455/analytics',
 'https://rtfight.com/fights/2456/analytics',
 'https://rtfight.com/fights/1090/analytics',
 'https://rtfight.com/fights/davis-vs-martin',

 # Current indexed repeat-fighter targets; verified to expose both-side
 # Total/Jab/Power fight totals. Strict parser/date/pair/arithmetic gates below
 # still decide acceptance.
 'https://rtfight.com/fights/rea-vs-arthur',
 'https://rtfight.com/fights/arthur-vs-cameron',
 'https://rtfight.com/fights/bivol-vs-arthur',
 'https://rtfight.com/fights/arthur-vs-nahuel-suarez',
 'https://rtfight.com/fights/eubank-vs-erdogan',
 'https://rtfight.com/fights/eubank-vs-antin',
 'https://rtfight.com/fights/crocker-vs-walker',

 # Additional current structured pages discovered during repeat-history audit.
 'https://rtfight.com/fights/3438/analytics',
 'https://rtfight.com/fights/3437',
 'https://rtfight.com/fights/baraou-vs-mcgowan',
]

def norm(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,limit=5_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=40) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def page_text(raw):
    soup=BeautifulSoup(raw,'lxml')
    return soup,re.sub(r'\s+',' ',' '.join(soup.stripped_strings)).strip()

def title_pair(soup):
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    title=re.sub(r'\s+',' ',title).strip()
    m=re.match(r'^(.+?)\s+vs\.?\s+(.+?)\s+(?:Results|Result|Scorecards|Fight|—|-)',title,re.I)
    if not m:
        h=soup.find('h1')
        txt=re.sub(r'\s+',' ',h.get_text(' ',strip=True)).strip() if h else ''
        m=re.match(r'^(.+?)\s+vs\.?\s+(.+?)(?:\s+Results|\s*$)',txt,re.I)
    return (m.group(1).strip(),m.group(2).strip()) if m else None

def display_date(text):
    m=re.search(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),\s+(20\d{2})\b',text,re.I)
    if not m:return None
    s=f'{m.group(1).replace("Sept","Sep")} {m.group(2)}, {m.group(3)}'
    return dt.datetime.strptime(s,'%b %d, %Y').date()

def rounds_observed(text):
    m=re.search(r'\b(\d{1,2})\s*/\s*(\d{1,2})\s+Rounds\b',text,re.I)
    if m:return int(m.group(1)),int(m.group(2))
    return None,None

def category_segment(text,start,end=None):
    i=text.find(start)
    if i<0:return ''
    j=text.find(end,i+len(start)) if end else -1
    return text[i:(j if j>=0 else len(text))]

def four_counts(segment,kind):
    # RTF visible text consistently emits: thrown-A, thrown-B, landed-A, landed-B.
    if kind=='total':
        thrown_marker='Total number of punches thrown per fight'
        thrown_label='Total number of punches thrown'
        landed_marker='Total number of punches landed per fight'
        landed_label='Total number of punches landed'
    elif kind=='jab':
        thrown_marker='Total number of jabs thrown per fight'
        thrown_label='Total number of jabs thrown'
        landed_marker='Total number of jabs landed per fight'
        landed_label='Total number of jabs landed'
    else:
        thrown_marker='Total number of power punches thrown per fight'
        thrown_label='Total number of power punches thrown'
        landed_marker='Total number of power punches landed per fight'
        landed_label='Total number of power punches landed'
    pat=(
      re.escape(thrown_marker)+r'.{0,160}?Total\s+Landed\s+(\d+)\s+'+
      re.escape(thrown_label)+r'\s+(\d+).{0,180}?'+
      re.escape(landed_marker)+r'.{0,160}?Total\s+Landed\s+(\d+)\s*\(\d+(?:\.\d+)?%\)\s+'+
      re.escape(landed_label)+r'\s+(\d+)\s*\(\d+(?:\.\d+)?%\)'
    )
    m=re.search(pat,segment,re.I)
    if not m:return None
    return tuple(int(x) for x in m.groups())

def parse_stats(text):
    seg_total=category_segment(text,'### Punches','### Jabs') or category_segment(text,'Punches','Jabs')
    seg_jab=category_segment(text,'### Jabs','### Power Punches') or category_segment(text,'Jabs','Power Punches')
    seg_power=category_segment(text,'### Power Punches','### Punch Map') or category_segment(text,'Power Punches','Punch Map')
    # HTML rendering strips markdown hashes, so retry clean headings.
    vals={
      'total':four_counts(seg_total,'total'),
      'jab':four_counts(seg_jab,'jab'),
      'power':four_counts(seg_power,'power')
    }
    if not all(vals.values()):return None
    return vals

def local_bouts():
    con=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);con.row_factory=sqlite3.Row
    rows=[]
    for r in con.execute("select date,boxer_a,boxer_b,source,source_id from bouts where status='FINISHED' and date is not null"):
        a,b=str(r['boxer_a'] or '').strip(),str(r['boxer_b'] or '').strip()
        if a and b:rows.append(dict(r))
    con.close()
    return rows

def resolve_local(pair,shown_date,rows):
    if not pair or not shown_date:return None
    a0,b0=map(norm,pair)
    candidates={}
    for r in rows:
        try:d=dt.date.fromisoformat(r['date'])
        except Exception:continue
        if abs((d-shown_date).days)>1:continue
        ra,rb=norm(r['boxer_a']),norm(r['boxer_b'])
        if {ra,rb}!={a0,b0}:continue
        key=(r['date'],tuple(sorted([ra,rb])))
        candidates.setdefault(key,[]).append(r)
    if len(candidates)!=1:return None
    key,items=next(iter(candidates.items()))
    # choose display names from first row; identity is exact normalized pair.
    r=items[0]
    return {'date':r['date'],'fighter_a':r['boxer_a'],'fighter_b':r['boxer_b'],
            'sources':[(x['source'],x['source_id']) for x in items]}

def validate(vals):
    for side in (0,1):
        tt=vals['total'][side];tl=vals['total'][side+2]
        jt=vals['jab'][side];jl=vals['jab'][side+2]
        pt=vals['power'][side];pl=vals['power'][side+2]
        if min(tt,tl,jt,jl,pt,pl)<0 or tl>tt or jl>jt or pl>pt:return False,'invalid_count'
        if tl!=jl+pl:return False,'landed_arithmetic_mismatch'
        diff=abs(tt-(jt+pt))/max(1,tt)
        if diff>0.025:return False,f'thrown_arithmetic_mismatch_{diff:.4f}'
    return True,None

def observation(url,date,fighter,opponent,rounds,vals,side):
    other=1-side
    out={
      'report_url':url,'report_id':url,'bout_date':date,
      'report_title':f'{fighter} vs {opponent} Ready To Fight structured totals',
      'fighter_label':fighter,'fighter_full_name':fighter,'fighter_key':norm(fighter),
      'opponent_label':opponent,'opponent_full_name':opponent,'opponent_key':norm(opponent),
      'identity_quality':'exact_local_date_pair_match',
      'rounds_observed':rounds,'source_quality':'structured_fight_total_table_ready_to_fight',
      'round_edge_note':'unavailable: source provides fight totals, not round table',
      'avoidance_note':'100 - opponent connect%; proxy only, not literal evasion tracking'
    }
    for cat in ('total','jab','power'):
        ft=vals[cat][side];fl=vals[cat][side+2]
        ot=vals[cat][other];ol=vals[cat][other+2]
        out[f'{cat}_landed']=fl;out[f'{cat}_thrown']=ft
        out[f'{cat}_accuracy_pct']=100*fl/ft if ft else None
        out[f'opp_{cat}_landed']=ol;out[f'opp_{cat}_thrown']=ot
        out[f'opp_{cat}_accuracy_pct']=100*ol/ot if ot else None
        out[f'{cat}_avoidance_pct']=100*(1-ol/ot) if ot else None
        out[f'{cat}_landed_per_round']=fl/rounds if rounds else None
        out[f'{cat}_thrown_per_round']=ft/rounds if rounds else None
        out[f'opp_{cat}_landed_per_round']=ol/rounds if rounds else None
        out[f'net_{cat}_landed_per_round']=(fl-ol)/rounds if rounds else None
        for suffix in ('landed_diff_slope','round_edge_count','round_edge_rate','first3_net_landed','last3_net_landed','late_vs_early_net_delta'):
            out[f'{cat}_{suffix}']=None
    out['body_landed']=None;out['body_landed_share_pct']=None
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--sleep',type=float,default=.08);args=ap.parse_args()
    rows=local_bouts();observations=[];accepted=[];rejected=[]
    for url in SEEDS:
        try:
            final,raw=fetch(url);soup,text=page_text(raw)
            pair=title_pair(soup);shown=display_date(text);ro,scheduled=rounds_observed(text)
            local=resolve_local(pair,shown,rows)
            vals=parse_stats(text)
            if not pair or not shown or not ro or not local or not vals:
                raise ValueError(f'missing required structure pair={pair} date={shown} rounds={ro} local={bool(local)} stats={bool(vals)}')
            ok,reason=validate(vals)
            if not ok:raise ValueError(reason)
            # Align page title order to canonical local names.
            pagekeys=[norm(x) for x in pair];canon=[local['fighter_a'],local['fighter_b']]
            ordered=[]
            for pk in pagekeys:
                hits=[x for x in canon if norm(x)==pk]
                if len(hits)!=1:raise ValueError('canonical identity alignment failed')
                ordered.append(hits[0])
            observations.append(observation(final,local['date'],ordered[0],ordered[1],ro,vals,0))
            observations.append(observation(final,local['date'],ordered[1],ordered[0],ro,vals,1))
            accepted.append({'url':final,'date':local['date'],'fighters':ordered,'rounds_observed':ro,'scheduled_rounds':scheduled,
                             'local_sources':local['sources'],'reported_counts':vals})
        except Exception as e:
            rejected.append({'url':url,'error':str(e)[:300]})
        time.sleep(max(0,args.sleep))
    OUT.parent.mkdir(parents=True,exist_ok=True)
    with OUT.open('w',encoding='utf-8') as f:
        for x in sorted(observations,key=lambda r:(r['bout_date'],r['report_url'],r['fighter_key'])):
            f.write(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n')
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'seed_urls':len(SEEDS),
      'accepted_fights':len(accepted),'fighter_observations':len(observations),
      'date_min':min((x['date'] for x in accepted),default=None),'date_max':max((x['date'] for x in accepted),default=None),
      'accepted':accepted,'rejected':rejected,
      'policy':'Ready To Fight structured fight totals are a separate source tier; exact date+pair; Total/Jab/Power both sides; exact landed arithmetic; <=2.5% thrown-category discrepancy; no inferred counts.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ['seed_urls','accepted_fights','fighter_observations','date_min','date_max']},indent=2))
    if rejected:print(json.dumps({'rejected':rejected},indent=2))

if __name__=='__main__':main()
