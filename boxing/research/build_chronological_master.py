"""Versioned research master; no changes to collection, live rules, or email."""
import collections, datetime as dt, hashlib, json, math, re, sqlite3
from pathlib import Path
from features import summary, age
from enrich_history import stats, rounds_value
ROOT=Path(__file__).resolve().parent
ALLOWED_CAREER_SOURCES=('wikipedia','champinon','wba_consensus')

def canonical_events(histories, links):
    groups=collections.defaultdict(list)
    for fid, history in histories.items():
        for r in history:
            other=links.get(r['source_id'])
            if not other or other==fid: continue
            pair=tuple(sorted([fid,other])); result=r['winner']
            if result not in ('BOXER A','BOXER B','DRAW'):continue
            score=.5 if result=='DRAW' else float((result=='BOXER A')==(fid==pair[0]))
            groups[(r['date'],*pair)].append((fid,score))
    return [(key,rs[0][1]) for key,rs in sorted(groups.items())
            if len(rs)==2 and len({r[0] for r in rs})==2 and len({r[1] for r in rs})==1]

def load_links(d):
    links={r['bout_id']:r['opponent_id'] for r in d.execute("select * from opponent_links where evidence='reciprocal_result_confirmed'")}
    if d.execute("select 1 from sqlite_master where type='table' and name='opponent_links_v2'").fetchone():
        for r in d.execute("select bout_id,opponent_id from opponent_links_v2 where evidence='reciprocal_result_observed_alias'"):
            links.setdefault(r['bout_id'],r['opponent_id'])
    return links

def elo_before(events, targets):
    ratings=collections.defaultdict(lambda:1500.); counts=collections.Counter(); out={}
    byday=collections.defaultdict(list)
    for key,score in events:byday[key[0]].append((key,score))
    for date in sorted(set(byday)|set(targets)):
        for fid in targets.get(date,[]):out[(date,fid)]=(ratings[fid],counts[fid])
        changes=collections.Counter(); increment=collections.Counter()
        for (_,a,b),score in byday[date]:
            delta=32*(score-1/(1+10**((ratings[b]-ratings[a])/400)))
            changes[a]+=delta;changes[b]-=delta;increment[a]+=1;increment[b]+=1
        for fid,delta in changes.items():ratings[fid]+=delta
        counts.update(increment)
    return out

def record_matches(history, date, s):
    rows=[r for r in history if r['date']<date]
    if not rows or len({(r['date'],r['boxer_b']) for r in rows})!=len(rows):return False
    m=re.fullmatch(r'\s*(\d+)\s*[–−-]\s*(\d+)(?:\s*[–−-]\s*(\d+))?\s*',json.loads(rows[-1]['data']).get('record',''))
    if not m:return False
    expected=tuple(int(x or 0) for x in m.groups())
    return expected==tuple(s['career_observed_'+x] for x in ('wins','losses','draws')) and sum(expected)==len(rows)

def bout_context(r):
    try:data=json.loads(r.get('data') or '{}')
    except Exception:data={}
    notes=' '.join(str(data.get(k,'') or '') for k in ('notes','note','note(s)','more')).strip()
    low=notes.casefold();terminal,scheduled=rounds_value(r.get('rounds'))
    title=bool(re.search(r'\btitle\b|\bchampionship\b',low));world_orgs=sorted(set(re.findall(r'\b(?:wba|wbc|ibf|wbo)\b',low)))
    return {'location':r.get('venue') or data.get('location') or data.get('venue and location') or '',
            'scheduled_rounds':scheduled,'title_bout':title,'vacant_title':bool(title and 'vacant' in low),
            'major_world_title_orgs':world_orgs,'professional_debut':bool('professional debut' in low),
            'context_source':f"{r.get('source','career')} record row; only pre-fight-knowable context flags exposed"}

def main():
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    outdir=ROOT/'research_runs'/stamp;outdir.mkdir(parents=True)
    live=sqlite3.connect(ROOT/'boxing.sqlite3',timeout=120)
    d=sqlite3.connect(outdir/'source.sqlite3');live.backup(d);live.close();d.row_factory=sqlite3.Row
    histories=collections.defaultdict(list)
    marks=','.join('?'*len(ALLOWED_CAREER_SOURCES))
    for row in d.execute(f"select * from bouts where source in ({marks}) and status='FINISHED' order by date,source_id",ALLOWED_CAREER_SOURCES):
        r=dict(row)
        try:dt.date.fromisoformat(r['date'])
        except (ValueError,TypeError):continue
        histories[r['url']].append(r)
    links=load_links(d)
    profiles={r['source_id']:dict(r) for r in d.execute('select * from normalized_fighters')}
    events=canonical_events(histories,links); eventkeys={k for k,s in events}
    targets=collections.defaultdict(set)
    for fid,history in histories.items():
        for r in history:
            targets[r['date']].add(fid)
            if links.get(r['source_id']):targets[r['date']].add(links[r['source_id']])
    elo=elo_before(events,targets)
    strength={}
    for fid,history in histories.items():
        for r in history:
            opp=links.get(r['source_id']); s=summary(histories.get(opp,[]),r['date'])
            n=s['career_observed_wins']+s['career_observed_losses']
            if n>=5:strength[r['source_id']]=s['career_observed_wins']/n
    quotes=collections.defaultdict(list)
    for r in d.execute('select * from priced_bout_research where feature_bout_id is not null'):quotes[r['feature_bout_id']].append(dict(r))
    years=collections.defaultdict(collections.Counter); total=0;source_rows=collections.Counter()
    with (outdir/'boxing_prefight_master.jsonl').open('w') as f:
        for fid,history in histories.items():
            for r in history:
                date=r['date']
                if date<'1990-01-01':continue
                opp=links.get(r['source_id']); pair=tuple(sorted([fid,opp])) if opp else ()
                sides={}
                for label,who,other in [('fighter',fid,opp),('opponent',opp,fid)]:
                    if who not in histories:sides[label]=None;continue
                    h=histories[who];s=summary(h,date);ex=stats(h,date,other,links,histories,strength)
                    prior=[x for x in h if x['date']<date];rating,n=elo[(date,who)];p=profiles.get(who,{})
                    sides[label]={'id':who,'record_totals_match':record_matches(h,date,s),'summary':s,'extended':ex,
                        'age_from_biography':age(p.get('born'),date),'height_cm_static_proxy':p.get('height_cm'),'reach_cm_static_proxy':p.get('reach_cm'),
                        'stance_static_proxy':p.get('stance'),'nationality_static_proxy':p.get('nationality'),
                        'physical_proxy_quality':'identity-linked current biography snapshot when available; height/reach treated as stable adult proxies; stance/nationality exploratory only',
                        'elo':rating,'elo_prior_verified_bouts':n,'last8_wins':sum(x['winner']=='BOXER A' for x in prior[-8:]),'last8_sample':len(prior[-8:]),
                        'input_bout_ids':[x['source_id'] for x in prior]}
                    assert not s['latest_input_bout_date'] or s['latest_input_bout_date']<date
                matched=[q for q in quotes[r['source_id']] if q['event_date']==date]
                row={'source_id':r['source_id'],'career_source':r['source'],'bout_date':date,'fighter_name':r['boxer_a'],'opponent_name':r['boxer_b'],
                     **sides,'canonical_verified_pair':bool(pair and (date,*pair) in eventkeys),'context':bout_context(r),
                     'outcome':{'result':r['winner'],'method':r['method'],'rounds':r['rounds']},'quotes':matched,'validated_price_eligible':False,
                     'historically_verified_physical_stats':False,'historically_linked_prior_punch_stats':None}
                f.write(json.dumps(row,ensure_ascii=False)+'\n');total+=1;source_rows[r['source']]+=1
                y=years[date[:4]];y['fighter_bout_rows']+=1;y['reciprocal_pair_rows']+=row['canonical_verified_pair'];y['price_linked_rows']+=bool(matched)
                y['both_record_totals_match']+=bool(sides['opponent'] and all(sides[x]['record_totals_match'] for x in ('fighter','opponent')))
    report={'built_at':stamp,'rows':total,'career_sources':dict(source_rows),'verified_graph_bouts':len(events),'identity_links':len(links),'years':dict(sorted(years.items())),
            'validated_price_rows':0,'limitations':['Career rows preserve source provenance; secondary observed histories are not relabeled as Wikipedia.',
            'Source observations are not a census of all boxing fights.','Own Elo: 1500 initial, K32, reciprocal graph only; all same-day updates batched.',
            'Record totals agreement is an internal audit, not independent full-career certification.',
            'Biography age uses available birth date; height/reach are static adult proxies when available; stance/nationality remain exploratory.',
            'Title/location/scheduled-round context comes from record rows but only pre-fight-knowable flags are exposed.',
            'All quotes have unverified timing/settlement. No validated ROI or live eligibility.',
            'Prior punch features unavailable until identity, date and coverage gates pass.']}
    (outdir/'coverage.json').write_text(json.dumps(report,indent=2));(ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').write_text(str(outdir)+'\n')
    print(json.dumps({'run':str(outdir),'rows':total,'career_sources':dict(source_rows),'verified_graph_bouts':len(events),'identity_links':len(links),'years':report['years']}))
if __name__=='__main__':main()
