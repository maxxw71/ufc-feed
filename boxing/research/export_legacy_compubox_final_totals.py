#!/usr/bin/env python3
"""Export strict fight-total punch observations from staged legacy CompuBox pages.

This tier is intentionally lower resolution than full round tables. It accepts
only pages that provide:
- an exact archived PunchStat header with both fighter names and a full date;
- an exact date+pair match to the local finished-bout graph;
- complete Total/Jab/Power landed+thrown values for BOTH fighters;
- exact arithmetic Total == Jab + Power for landed and thrown;
- a Wayback capture date proving when the observation was publicly available.

Pages already accepted into the strict full-round archive are skipped here.
No punch count is inferred and no round-level metric is manufactured.
"""
from __future__ import annotations
import argparse,datetime as dt,json,re,sqlite3,unicodedata,urllib.parse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CANDIDATES=ROOT/'public_punch_audit'/'legacy_compubox_fetched_candidates.json'
FULL_ROUNDS=ROOT/'punch_supplements'/'legacy_compubox_round_reports.jsonl'
OUT=ROOT/'punch_supplements'/'legacy_compubox_final_totals.jsonl'
REPORT=ROOT/'punch_supplements'/'legacy_compubox_final_totals_report.json'

HEADER=re.compile(
    r'PunchStat\s+Report\s+(.+?)\s+vs\s+(.+?)\s+'
    r'(\d{1,2}/\d{1,2}/\d{4})(?=\s|$)',re.I
)

def norm(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def clean(s):
    return re.sub(r'\s+',' ',str(s or '')).strip()

def surname(s):
    toks=re.findall(r"[A-Za-zÀ-ÿ0-9]+",str(s or ''))
    return toks[-1] if toks else ''

def aliases(name):
    toks=re.findall(r"[A-Za-zÀ-ÿ0-9]+",str(name or ''))
    vals={surname(name)}
    for n in (2,3):
        if len(toks)>=n: vals.add(''.join(toks[-n:]))
    return sorted((x for x in vals if x),key=len,reverse=True)

def full_round_keys():
    keys=set();urls=set()
    if not FULL_ROUNDS.exists(): return keys,urls
    for line in FULL_ROUNDS.read_text().splitlines():
        if not line.strip():continue
        try:x=json.loads(line)
        except Exception:continue
        fs=x.get('fighters') or []
        if len(fs)==2 and x.get('bout_date'):
            keys.add((str(x['bout_date']),tuple(sorted(norm(v) for v in fs))))
        u=str(x.get('report_url') or '')
        m=re.search(r'id_/https?://(?:www\.)?([^/]+)(/[^?#]+)',u,re.I)
        if m: urls.add((m.group(1)+m.group(2)).lower())
    return keys,urls

def candidate_text(row):
    vals=[row.get('text_sample') or '']
    for table in row.get('tables') or []:
        for tr in table or []:
            for cell in tr or []:
                if isinstance(cell,str): vals.append(cell)
    return clean(' '.join(vals))

def parse_date(s):
    try:return dt.datetime.strptime(s,'%m/%d/%Y').date().isoformat()
    except ValueError:return None

def resolve_pair(db,text):
    m=HEADER.search(text)
    if not m:return None
    a,b,date=m.group(1).strip(),m.group(2).strip(),parse_date(m.group(3))
    if not date or not norm(a) or not norm(b) or norm(a)==norm(b):return None
    rows=db.execute(
      "select boxer_a,boxer_b,rounds from bouts where status='FINISHED' and date=?",(date,)
    ).fetchall()
    hits=[]
    wanted={norm(a),norm(b)}
    for ra,rb,rounds in rows:
        if {norm(ra),norm(rb)}==wanted:
            hits.append((ra,rb,rounds))
    if not hits:return None
    # Source duplicates/reciprocal rows are allowed only when they all encode
    # the same exact normalized dated pair.
    if any({norm(x[0]),norm(x[1])}!=wanted for x in hits):return None
    def terminal(v):
        mm=re.search(r'\d+',str(v or ''))
        return int(mm.group()) if mm else None
    round_vals={terminal(x[2]) for x in hits if terminal(x[2])}
    rounds=max(round_vals) if len(round_vals)==1 else None
    return {'date':date,'fighters':[a,b],'rounds':rounds}

def parse_final(text,fighters):
    m=re.search(r'Final\s+Punch(?:Stat)?\s+(?:Report|Stats)',text,re.I)
    if not m:return None
    tail=text[m.end():]
    out={}
    spans=[]
    for fighter in fighters:
        label='(?:'+'|'.join(re.escape(x) for x in aliases(fighter))+')'
        fm=re.search(
          r'(?<![A-Za-z0-9])'+label+r'(?![A-Za-z0-9])\s+'
          r'(\d{1,4})\s*/\s*(\d{1,4})\s+'
          r'(\d{1,4})\s*/\s*(\d{1,4})\s+'
          r'(\d{1,4})\s*/\s*(\d{1,4})',tail,re.I)
        if not fm:return None
        if any(not (fm.end()<=a or fm.start()>=b) for a,b in spans):return None
        spans.append(fm.span())
        tl,tt,jl,jt,pl,pt=map(int,fm.groups())
        if min(tl,tt,jl,jt,pl,pt)<0 or tl>tt or jl>jt or pl>pt:return None
        if (tl,tt)!=(jl+pl,jt+pt):return None
        out[fighter]={'total':(tl,tt),'jab':(jl,jt),'power':(pl,pt)}
    return out

def observation(url,available,date,fighter,opponent,rounds,vals,opp):
    out={
      'report_url':url,'report_id':'legacy-final:'+url,'bout_date':date,
      'available_from_date':available,
      'report_title':f'{fighter} vs {opponent} archived CompuBox final totals',
      'fighter_label':fighter,'fighter_full_name':fighter,'fighter_key':norm(fighter),
      'opponent_label':opponent,'opponent_full_name':opponent,'opponent_key':norm(opponent),
      'identity_quality':'exact_archived_header_date_pair_match',
      'rounds_observed':rounds,
      'source_quality':'archived_compubox_final_total_table_strict',
      'round_edge_note':'unavailable: source provides fight totals, not a round table',
      'avoidance_note':'100 - opponent connect%; proxy only, not literal evasion tracking',
      'body_landed':None,'body_landed_share_pct':None,
    }
    for cat in ('total','jab','power'):
        fl,ft=vals[cat];ol,ot=opp[cat]
        out[f'{cat}_landed']=fl;out[f'{cat}_thrown']=ft
        out[f'{cat}_accuracy_pct']=100*fl/ft if ft else None
        out[f'opp_{cat}_landed']=ol;out[f'opp_{cat}_thrown']=ot
        out[f'opp_{cat}_accuracy_pct']=100*ol/ot if ot else None
        out[f'{cat}_avoidance_pct']=100*(1-ol/ot) if ot else None
        out[f'{cat}_landed_per_round']=fl/rounds if rounds else None
        out[f'{cat}_thrown_per_round']=ft/rounds if rounds else None
        out[f'opp_{cat}_landed_per_round']=ol/rounds if rounds else None
        out[f'net_{cat}_landed_per_round']=(fl-ol)/rounds if rounds else None
        for suffix in ('landed_diff_slope','round_edge_count','round_edge_rate',
                       'first3_net_landed','last3_net_landed','late_vs_early_net_delta'):
            out[f'{cat}_{suffix}']=None
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);args=ap.parse_args()
    if not CANDIDATES.exists():raise SystemExit(f'missing {CANDIDATES}')
    obj=json.loads(CANDIDATES.read_text())
    full_keys,full_urls=full_round_keys()
    db=sqlite3.connect(args.db)
    observations=[];accepted=[];rejected={}
    for row in obj.get('rows') or []:
        if row.get('status')!='fetched':continue
        key=str(row.get('canonical_key') or '').lower()
        if key in full_urls:
            rejected['already_full_round']=rejected.get('already_full_round',0)+1;continue
        text=candidate_text(row)
        pair=resolve_pair(db,text)
        if not pair:
            rejected['no_exact_header_date_pair']=rejected.get('no_exact_header_date_pair',0)+1;continue
        pkey=(pair['date'],tuple(sorted(norm(x) for x in pair['fighters'])))
        if pkey in full_keys:
            rejected['already_full_round']=rejected.get('already_full_round',0)+1;continue
        vals=parse_final(text,pair['fighters'])
        if not vals:
            rejected['no_complete_exact_arithmetic_final_table']=rejected.get('no_complete_exact_arithmetic_final_table',0)+1;continue
        if not pair['rounds']:
            rejected['unknown_round_count']=rejected.get('unknown_round_count',0)+1;continue
        ts=str(row.get('timestamp') or '')
        available=(ts[:4]+'-'+ts[4:6]+'-'+ts[6:8]) if len(ts)>=8 else None
        if not available:
            rejected['no_capture_date']=rejected.get('no_capture_date',0)+1;continue
        a,b=pair['fighters']
        url=str(row.get('snapshot_url') or '')
        observations.extend([
          observation(url,available,pair['date'],a,b,pair['rounds'],vals[a],vals[b]),
          observation(url,available,pair['date'],b,a,pair['rounds'],vals[b],vals[a])
        ])
        accepted.append({'canonical_key':key,'url':url,'date':pair['date'],
                         'available_from_date':available,'fighters':[a,b],
                         'rounds':pair['rounds'],'counts':vals})
    db.close()
    OUT.parent.mkdir(parents=True,exist_ok=True)
    with OUT.open('w') as f:
        for x in sorted(observations,key=lambda r:(r['bout_date'],r['fighter_key'])):
            f.write(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n')
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'staged_fetched_pages':sum(x.get('status')=='fetched' for x in obj.get('rows') or []),
      'accepted_fights':len(accepted),'fighter_observations':len(observations),
      'date_min':min((x['date'] for x in accepted),default=None),
      'date_max':max((x['date'] for x in accepted),default=None),
      'rejections':rejected,'accepted':accepted,
      'policy':'Archived staged CompuBox pages only; exact full-date + exact pair match; BOTH sides require complete Total/Jab/Power final totals; exact Total=Jab+Power arithmetic; full-round archive always wins; no inferred counts or round metrics.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('staged_fetched_pages','accepted_fights','fighter_observations','date_min','date_max','rejections')},indent=2))

if __name__=='__main__':main()
