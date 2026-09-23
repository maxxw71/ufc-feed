#!/usr/bin/env python3
"""Build chronological boxing punch profiles from observed round-level reports.

This module is deliberately source-agnostic at the table boundary. It reads the
normalized `punch_reports`, `round_punches`, and optional `fight_punch_totals`
tables and creates fight-level punch observations plus *pre-fight* fighter
profiles. A fighter's row for date D is built only from reports dated before D;
the current fight is added to history only after its snapshot is emitted.

Important interpretation note: `avoidance_pct` is a defensive proxy equal to
1 - opponent connect percentage. It does NOT claim to distinguish slips,
blocks, parries, footwork, or punches that missed for other reasons.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import statistics
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

CATEGORIES=("total","jab","power")


def norm_name(value:str)->str:
    value=unicodedata.normalize("NFKD",str(value or "")).encode("ascii","ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+","",value)


def div(a,b):
    return (float(a)/float(b)) if b else None


def mean(values):
    values=[float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.mean(values) if values else None


def slope(values):
    vals=[(i,float(v)) for i,v in enumerate(values,1) if v is not None and math.isfinite(float(v))]
    if len(vals)<2:return None
    xs=[x for x,_ in vals];ys=[y for _,y in vals]
    xm=statistics.mean(xs);ym=statistics.mean(ys)
    den=sum((x-xm)**2 for x in xs)
    return sum((x-xm)*(y-ym) for x,y in vals)/den if den else None


def pct(v):
    return None if v is None else 100.0*v


def tables(db):
    return {r[0] for r in db.execute("select name from sqlite_master where type='table'")}


def report_id(url):
    m=re.search(r'(?:^|/)round-stats/(\d+)(?:/|$)',urlsplit(str(url or '')).path)
    return m.group(1) if m else str(url or '')

def title_pair(title):
    s=re.sub(r'\s+',' ',str(title or '')).strip()
    m=re.match(r'^(.+?)\s+(UD|SD|MD|KO|TKO|RTD|DQ|PTS|TD|DRAW|NC)\s+(\d{1,2})\s+(.+?)$',s,re.I)
    return (m.group(1).strip(),m.group(4).strip()) if m else None

def _name_core(value):
    x=norm_name(value)
    return re.sub(r'(?:jr|sr|ii|iii|iv)$','',x)

def resolve_title_identities(labels,title):
    # Strict archived importer writes canonical full names directly into
    # fighter_label and tags the report title. Those labels were already
    # resolved against one verified local bout, so do not downgrade them back
    # to surname-only identities here.
    if str(title or '').endswith(' archived CompuBox') and len(labels)==2:
        if all(len(re.findall(r"[A-Za-zÀ-ÿ0-9'-]+",str(x)))>=2 for x in labels):
            return {x:x for x in labels}
    pair=title_pair(title)
    if not pair:return {}
    out={}
    for label in labels:
        lk=_name_core(label)
        candidates=[full for full in pair if lk and _name_core(full).endswith(lk)]
        if len(candidates)==1:
            out[label]=candidates[0]
    if len(out)==2 and len({norm_name(x) for x in out.values()})==2:
        return out
    return {}


def load_reports(db):
    t=tables(db)
    if not {'punch_reports','round_punches'}<=t:
        raise RuntimeError('database needs punch_reports and round_punches')
    report_rows=db.execute("select url,bout_date,title,status from punch_reports where status='parsed' and bout_date is not null order by bout_date,url").fetchall()
    total_lookup={}
    if 'fight_punch_totals' in t:
        for row in db.execute("select report_url,fighter_label,category,landed,body_landed,thrown from fight_punch_totals"):
            total_lookup[(row[0],row[1],row[2])]={'landed':row[3],'body_landed':row[4],'thrown':row[5]}
    grouped=defaultdict(list)
    for row in report_rows:
        grouped[report_id(row[0])].append(row)
    host_priority={'beta.compuboxdata.com':0,'app2.compuboxdata.com':1,'api2.compuboxdata.com':2}
    out=[]
    for rid,variants in sorted(grouped.items(),key=lambda kv:(kv[1][0][1],kv[0])):
        variants=sorted(variants,key=lambda r:(host_priority.get(urlsplit(r[0]).hostname,9),r[0]))
        chosen=None
        for url,bout_date,title,status in variants:
            rows=db.execute("select fighter_label,round,category,landed,thrown from round_punches where report_url=? order by round,category,fighter_label",(url,)).fetchall()
            fighters=sorted({r[0] for r in rows if r[2]=='total' and norm_name(r[0])})
            if len(fighters)!=2:continue
            by=defaultdict(dict)
            for fighter,rnd,cat,landed,thrown in rows:
                if fighter in fighters and cat in CATEGORIES:
                    by[fighter][(int(rnd),cat)]=(int(landed),int(thrown))
            common=sorted(set(r for r,cat in by[fighters[0]] if cat=='total') & set(r for r,cat in by[fighters[1]] if cat=='total'))
            if not common:continue
            identities=resolve_title_identities(fighters,title or '')
            chosen={'url':url,'report_id':rid,'date':bout_date,'title':title or '',
                    'fighters':fighters,'identity_map':identities,'by':by,'rounds':common,
                    'totals':total_lookup,'variant_count':len(variants)}
            break
        if chosen:out.append(chosen)
    return out

def summarize_side(report,fighter,opponent):
    by=report['by'];rounds=report['rounds']
    full=report.get('identity_map',{}).get(fighter)
    opp_full=report.get('identity_map',{}).get(opponent)
    result={'report_url':report['url'],'report_id':report.get('report_id'),'bout_date':report['date'],'report_title':report['title'],
            'fighter_label':fighter,'fighter_full_name':full,'fighter_key':norm_name(full) if full else None,
            'opponent_label':opponent,'opponent_full_name':opp_full,'opponent_key':norm_name(opp_full) if opp_full else None,
            'identity_quality':'report_title_full_name_suffix_match' if full and opp_full else 'unresolved_report_label',
            'rounds_observed':len(rounds)}
    for cat in CATEGORIES:
        f=[by[fighter].get((r,cat)) for r in rounds]
        o=[by[opponent].get((r,cat)) for r in rounds]
        valid=[(r,fp,op) for r,fp,op in zip(rounds,f,o) if fp is not None and op is not None]
        fl=sum(x[1][0] for x in valid);ft=sum(x[1][1] for x in valid);ol=sum(x[2][0] for x in valid);ot=sum(x[2][1] for x in valid)
        result[f'{cat}_landed']=fl;result[f'{cat}_thrown']=ft;result[f'{cat}_accuracy_pct']=pct(div(fl,ft))
        result[f'opp_{cat}_landed']=ol;result[f'opp_{cat}_thrown']=ot;result[f'opp_{cat}_accuracy_pct']=pct(div(ol,ot))
        result[f'{cat}_avoidance_pct']=pct(1-div(ol,ot)) if ot else None
        result[f'{cat}_landed_per_round']=div(fl,len(valid));result[f'{cat}_thrown_per_round']=div(ft,len(valid))
        result[f'opp_{cat}_landed_per_round']=div(ol,len(valid));result[f'net_{cat}_landed_per_round']=div(fl-ol,len(valid))
        diffs=[fp[0]-op[0] for _,fp,op in valid]
        result[f'{cat}_landed_diff_slope']=slope(diffs)
        result[f'{cat}_round_edge_count']=sum(1 for d in diffs if d>0)
        result[f'{cat}_round_edge_rate']=div(result[f'{cat}_round_edge_count'],len(diffs))
        if len(diffs)>=3:
            result[f'{cat}_first3_net_landed']=mean(diffs[:3])
            result[f'{cat}_last3_net_landed']=mean(diffs[-3:])
            result[f'{cat}_late_vs_early_net_delta']=(result[f'{cat}_last3_net_landed']-result[f'{cat}_first3_net_landed'])
        else:
            result[f'{cat}_first3_net_landed']=result[f'{cat}_last3_net_landed']=result[f'{cat}_late_vs_early_net_delta']=None
    ft=report['totals'].get((report['url'],fighter,'total'))
    result['body_landed']=ft.get('body_landed') if ft else None
    result['body_landed_share_pct']=pct(div(result['body_landed'],ft.get('landed'))) if ft and ft.get('landed') and result['body_landed'] is not None else None
    result['source_quality']='observed_round_table'
    result['round_edge_note']='punch-count edge only; not a judge score or inferred 10-9 round'
    result['avoidance_note']='100 - opponent connect%; proxy only, not literal evasion tracking'
    return result


def fight_observations(reports):
    rows=[]
    for report in reports:
        a,b=report['fighters']
        rows.append(summarize_side(report,a,b));rows.append(summarize_side(report,b,a))
    return rows


def load_total_supplement_observations():
    path=Path(__file__).resolve().parent.parent/'punch_supplements'/'ready_to_fight_punch_observations.jsonl'
    if not path.exists():return []
    rows=[]
    for line in path.read_text().splitlines():
        if not line.strip():continue
        try:r=json.loads(line)
        except Exception:continue
        if r.get('source_quality')!='structured_fight_total_table_ready_to_fight':continue
        if not r.get('bout_date') or not r.get('fighter_key') or not r.get('opponent_key'):continue
        if not r.get('rounds_observed'):continue
        rows.append(r)
    # Require reciprocal two-sided observations for every supplement fight.
    grouped=defaultdict(list)
    for r in rows:
        key=(r['bout_date'],r.get('report_url') or '',tuple(sorted([r['fighter_key'],r['opponent_key']])))
        grouped[key].append(r)
    out=[]
    for key,items in grouped.items():
        if len(items)!=2:continue
        a,b=items
        if a['fighter_key']!=b['opponent_key'] or a['opponent_key']!=b['fighter_key']:continue
        out.extend(items)
    return out

def merge_observation_tiers(round_rows,total_rows):
    """Prefer round-level observations when the same exact date+pair exists."""
    strong=set()
    for r in round_rows:
        if not r.get('fighter_key') or not r.get('opponent_key'):continue
        strong.add((r['bout_date'],tuple(sorted([r['fighter_key'],r['opponent_key']]))))
    out=list(round_rows);accepted=0;skipped=0
    grouped=defaultdict(list)
    for r in total_rows:
        grouped[(r['bout_date'],tuple(sorted([r['fighter_key'],r['opponent_key']])))] .append(r)
    for key,items in sorted(grouped.items()):
        if key in strong:
            skipped+=len(items);continue
        if len(items)!=2:
            skipped+=len(items);continue
        out.extend(items);accepted+=len(items)
    return out,accepted,skipped


def weighted(history,key,weight='rounds_observed',limit=None):
    rows=history[-limit:] if limit else history
    vals=[(r.get(key),r.get(weight)) for r in rows if r.get(key) is not None and r.get(weight)]
    den=sum(w for _,w in vals)
    return sum(v*w for v,w in vals)/den if den else None


def pre_fight_profiles(observations):
    history=defaultdict(list);snapshots=[]
    grouped=defaultdict(list)
    for row in observations:grouped[(row['bout_date'],row['report_url'])].append(row)
    for (date,url),fight_rows in sorted(grouped.items()):
        # Freeze same-day state: every snapshot is created before any side from this report is added.
        pending=[]
        for row in fight_rows:
            if not row.get('fighter_key'):
                continue
            h=history[row['fighter_key']]
            snap={'bout_date':date,'report_url':url,'fighter_key':row['fighter_key'],'fighter_label':row['fighter_label'],
                  'fighter_full_name':row.get('fighter_full_name'),'opponent_key':row.get('opponent_key'),
                  'opponent_label':row['opponent_label'],'opponent_full_name':row.get('opponent_full_name'),
                  'identity_quality':row.get('identity_quality'),
                  'prior_punch_fights':len(h),'prior_punch_rounds':sum(x['rounds_observed'] for x in h)}
            for cat in CATEGORIES:
                for metric in ('landed_per_round','thrown_per_round','accuracy_pct','avoidance_pct','net_landed_per_round','round_edge_rate','landed_diff_slope','late_vs_early_net_delta'):
                    key=f'{cat}_{metric}'
                    snap['career_'+key]=weighted(h,key)
                    snap['last3_'+key]=weighted(h,key,limit=3)
                    snap['last5_'+key]=weighted(h,key,limit=5)
            snap['career_body_landed_share_pct']=weighted(h,'body_landed_share_pct')
            snap['last3_body_landed_share_pct']=weighted(h,'body_landed_share_pct',limit=3)
            snapshots.append(snap);pending.append(row)
        for row in pending:history[row['fighter_key']].append(row)
    return snapshots


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',default='boxing.sqlite3')
    ap.add_argument('--out',default=None,help='Output directory; defaults beside DB under punch_profiles/')
    args=ap.parse_args()
    dbpath=Path(args.db);out=Path(args.out) if args.out else dbpath.resolve().parent/'punch_profiles';out.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(dbpath)
    reports=load_reports(db)
    round_obs=fight_observations(reports)
    total_obs=load_total_supplement_observations()
    obs,supplement_obs_accepted,supplement_obs_skipped=merge_observation_tiers(round_obs,total_obs)
    snaps=pre_fight_profiles(obs)
    with (out/'fight_punch_observations.jsonl').open('w') as f:
        for row in obs:f.write(json.dumps(row,sort_keys=True)+'\n')
    with (out/'prefight_punch_profiles.jsonl').open('w') as f:
        for row in snaps:f.write(json.dumps(row,sort_keys=True)+'\n')
    coverage={
        'parsed_reports_with_two_sides':len(reports),
        'unique_report_ids':len({r.get('report_id') for r in reports}),
        'resolved_full_identity_reports':sum(len(r.get('identity_map',{}))==2 for r in reports),
        'duplicate_source_variants_collapsed':sum(max(0,int(r.get('variant_count',1))-1) for r in reports),
        'fighter_fight_observations':len(obs),
        'round_level_fighter_observations':len(round_obs),
        'fight_total_supplement_observations_loaded':len(total_obs),
        'fight_total_supplement_observations_accepted':supplement_obs_accepted,
        'fight_total_supplement_observations_skipped_due_to_stronger_or_invalid_pair':supplement_obs_skipped,
        'fight_total_supplement_fights_accepted':supplement_obs_accepted//2,
        'source_quality_counts':dict(__import__('collections').Counter(r.get('source_quality') or 'unknown' for r in obs)),
        'prefight_snapshots':len(snaps),
        'fighters_with_any_prior_punch_fight':len({r['fighter_key'] for r in snaps if r['prior_punch_fights']>0}),
        'snapshots_with_3plus_prior_punch_fights':sum(r['prior_punch_fights']>=3 for r in snaps),
        'date_min':min((r['bout_date'] for r in obs),default=None),'date_max':max((r['bout_date'] for r in obs),default=None),
        'leakage_policy':'snapshot before current report update; same-report sides frozen together',
        'avoidance_definition':'100 - opponent connect percentage; not literal slips/blocks/parries',
    }
    (out/'coverage.json').write_text(json.dumps(coverage,indent=2))
    print(json.dumps(coverage,indent=2))

if __name__=='__main__':main()
