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


def load_reports(db):
    t=tables(db)
    if not {'punch_reports','round_punches'}<=t:
        raise RuntimeError('database needs punch_reports and round_punches')
    report_rows=db.execute("select url,bout_date,title,status from punch_reports where status='parsed' and bout_date is not null order by bout_date,url").fetchall()
    total_lookup={}
    if 'fight_punch_totals' in t:
        for row in db.execute("select report_url,fighter_label,category,landed,body_landed,thrown from fight_punch_totals"):
            total_lookup[(row[0],row[1],row[2])]={'landed':row[3],'body_landed':row[4],'thrown':row[5]}
    out=[]
    for url,bout_date,title,status in report_rows:
        rows=db.execute("select fighter_label,round,category,landed,thrown from round_punches where report_url=? order by round,category,fighter_label",(url,)).fetchall()
        fighters=sorted({r[0] for r in rows if r[2]=='total' and norm_name(r[0])})
        if len(fighters)!=2:continue
        by=defaultdict(dict)
        for fighter,rnd,cat,landed,thrown in rows:
            if fighter in fighters and cat in CATEGORIES:
                by[fighter][(int(rnd),cat)]=(int(landed),int(thrown))
        common=sorted(set(r for r,c in by[fighters[0]] if c=='total') & set(r for r,c in by[fighters[1]] if c=='total'))
        if not common:continue
        out.append({'url':url,'date':bout_date,'title':title or '', 'fighters':fighters,'by':by,'rounds':common,'totals':total_lookup})
    return out


def summarize_side(report,fighter,opponent):
    by=report['by'];rounds=report['rounds']
    result={'report_url':report['url'],'bout_date':report['date'],'report_title':report['title'],
            'fighter_label':fighter,'fighter_key':norm_name(fighter),'opponent_label':opponent,'opponent_key':norm_name(opponent),
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
    result['body_landed_share_pct']=pct(div(result['body_landed'],ft.get('landed'))) if ft and ft.get('landed') else None
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
            h=history[row['fighter_key']]
            snap={'bout_date':date,'report_url':url,'fighter_key':row['fighter_key'],'fighter_label':row['fighter_label'],
                  'opponent_key':row['opponent_key'],'opponent_label':row['opponent_label'],
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
    reports=load_reports(db);obs=fight_observations(reports);snaps=pre_fight_profiles(obs)
    with (out/'fight_punch_observations.jsonl').open('w') as f:
        for row in obs:f.write(json.dumps(row,sort_keys=True)+'\n')
    with (out/'prefight_punch_profiles.jsonl').open('w') as f:
        for row in snaps:f.write(json.dumps(row,sort_keys=True)+'\n')
    coverage={
        'parsed_reports_with_two_sides':len(reports),
        'fighter_fight_observations':len(obs),
        'prefight_snapshots':len(snaps),
        'fighters_with_any_prior_punch_fight':len({r['fighter_key'] for r in snaps if r['prior_punch_fights']>0}),
        'snapshots_with_3plus_prior_punch_fights':sum(r['prior_punch_fights']>=3 for r in snaps),
        'date_min':min((r['date'] for r in reports),default=None),'date_max':max((r['date'] for r in reports),default=None),
        'leakage_policy':'snapshot before current report update; same-report sides frozen together',
        'avoidance_definition':'100 - opponent connect percentage; not literal slips/blocks/parries',
    }
    (out/'coverage.json').write_text(json.dumps(coverage,indent=2))
    print(json.dumps(coverage,indent=2))

if __name__=='__main__':main()
