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
        for r in d.execute("select bout_id,opponent_id from opponent_links_v2"):
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


def explicit_opponent_strength(row):
    """Parse a source-stated opponent pre-fight record when explicitly present."""
    try:data=json.loads(row.get('data') or '{}')
    except Exception:return None
    candidates=[
        data.get('opponent_record_at_bout'),
        data.get("opponent's pre-fight record"),
        data.get("opponent's record"),
        data.get('opp record'),
    ]
    for value in candidates:
        if isinstance(value,dict):
            try:w=int(value.get('wins',0));l=int(value.get('losses',0))
            except Exception:continue
        else:
            m=re.search(r'(\d+)\s*[–−-]\s*(\d+)(?:\s*[–−-]\s*(\d+))?',str(value or ''))
            if not m:continue
            w,l=int(m.group(1)),int(m.group(2))
        if w+l>=5:return w/(w+l)
    return None

def bout_context(r):
    try:data=json.loads(r.get('data') or '{}')
    except Exception:data={}
    notes=' '.join(str(data.get(k,'') or '') for k in ('notes','note','note(s)','more')).strip()
    low=notes.casefold()
    terminal,scheduled=rounds_value(r.get('rounds'))
    location=r.get('venue') or data.get('location') or data.get('venue and location') or data.get('venue') or ''
    loc_parts=[x.strip() for x in str(location).split(',') if x.strip()]
    country=loc_parts[-1] if loc_parts else ''
    country_norm={
      'u.s.':'USA','u.s.a.':'USA','united states':'USA','united states of america':'USA',
      'england':'United Kingdom','scotland':'United Kingdom','wales':'United Kingdom',
      'northern ireland':'United Kingdom'
    }.get(country.casefold(),country)

    title=bool(re.search(r'\btitle\b|\bchampionship\b',low))
    org_patterns={
      'WBA':r'\bwba\b|world boxing association',
      'WBC':r'\bwbc\b|world boxing council',
      'IBF':r'\bibf\b|international boxing federation',
      'WBO':r'\bwbo\b|world boxing organization',
      'IBO':r'\bibo\b|international boxing organization',
      'THE_RING':r'\bthe ring\b|\bring magazine\b',
      'EBU':r'\bebu\b|european boxing union',
      'BBBofC':r'\bbbofc\b|british boxing board',
      'NABF':r'\bnabf\b|north american boxing federation',
      'NABO':r'\bnabo\b',
      'OPBF':r'\bopbf\b|oriental and pacific boxing federation',
      'COMMONWEALTH':r'\bcommonwealth\b',
    }
    title_orgs=sorted(k for k,p in org_patterns.items() if re.search(p,low))
    major=[x for x in title_orgs if x in {'WBA','WBC','IBF','WBO'}]

    regional_words=r'\binternational\b|\binter[- ]continental\b|\bcontinental\b|\bsilver\b|\bnabo\b|\bnabf\b|\bopbf\b|\beuropean\b|\bcommonwealth\b|\bbritish\b|\bpan pacific\b|\blatino\b|\bregional\b'
    regional=bool(re.search(regional_words,low))
    if title and major and not regional:
        title_tier='major_world'
    elif title and regional:
        title_tier='regional_or_secondary'
    elif title:
        title_tier='other_title'
    else:
        title_tier='none'

    divisions=[
      ('super middleweight',('super middleweight','super-middleweight')),
      ('light heavyweight',('light heavyweight','light-heavyweight')),
      ('super welterweight',('super welterweight','super-welterweight','light middleweight','light-middleweight','junior middleweight')),
      ('super lightweight',('super lightweight','super-lightweight','light welterweight','light-welterweight','junior welterweight')),
      ('super featherweight',('super featherweight','super-featherweight','junior lightweight')),
      ('super bantamweight',('super bantamweight','super-bantamweight','junior featherweight')),
      ('super flyweight',('super flyweight','super-flyweight','junior bantamweight')),
      ('light flyweight',('light flyweight','light-flyweight','junior flyweight')),
      ('minimumweight',('minimumweight','mini flyweight','strawweight')),
      ('bridgerweight',('bridgerweight',)),
      ('cruiserweight',('cruiserweight',)),
      ('heavyweight',('heavyweight',)),
      ('middleweight',('middleweight',)),
      ('welterweight',('welterweight',)),
      ('lightweight',('lightweight',)),
      ('featherweight',('featherweight',)),
      ('bantamweight',('bantamweight',)),
      ('flyweight',('flyweight',)),
    ]
    context_text=' '.join(str(data.get(k,'') or '') for k in ('division','weight class','weight','notes','note','note(s)','more')).casefold()
    division=None
    for canonical,aliases in divisions:
        if any(re.search(r'(?<![a-z])'+re.escape(a)+r'(?![a-z])',context_text) for a in aliases):
            division=canonical;break

    return {
      'location':location,
      'location_country_raw':country_norm,
      'scheduled_rounds':scheduled,
      'scheduled_rounds_explicit':scheduled is not None,
      'title_bout':title,
      'vacant_title':bool(title and 'vacant' in low),
      'title_organizations':title_orgs,
      'major_world_title_orgs':major,
      'title_tier':title_tier,
      'division_from_record_text':division,
      'professional_debut':bool('professional debut' in low),
      'context_source':f"{r.get('source','career')} record row; only pre-fight-knowable context flags exposed",
      'outcome_parse':{'terminal_round':terminal,'method_family':
          'stoppage' if str(r.get('method') or '').upper() in {'KO','TKO','RTD'}
          else 'decision' if str(r.get('method') or '').upper() in {'UD','SD','MD','PTS','TD'}
          else 'other'}
    }

def load_wbc_rankings():
    candidates=[ROOT/'rankings',ROOT.parent/'rankings']
    rfile=next((p/'wbc_monthly_rankings.json' for p in candidates if (p/'wbc_monthly_rankings.json').exists()),None)
    cfile=next((p/'wbc_monthly_champions.json' for p in candidates if (p/'wbc_monthly_champions.json').exists()),None)
    ranks=collections.defaultdict(list);champs=collections.defaultdict(list)
    if rfile:
        try:
            for r in json.loads(rfile.read_text()):
                key=namekey(r.get('name') or '')
                if key:ranks[key].append(r)
        except Exception:pass
    if cfile:
        try:
            for r in json.loads(cfile.read_text()):
                key=namekey(r.get('name') or '')
                if key:champs[key].append(r)
        except Exception:pass
    for x in (ranks,champs):
        for k in x:x[k].sort(key=lambda r:r.get('safe_effective_date') or '')
    return ranks,champs

def wbc_before(index,name,date,max_age_days=75):
    rows=index.get(namekey(name or ''),[])
    eligible=[r for r in rows if (r.get('safe_effective_date') or '')<=date]
    if not eligible:return None
    r=eligible[-1]
    try:
        age=(dt.date.fromisoformat(date)-dt.date.fromisoformat(r['safe_effective_date'])).days
    except Exception:return None
    return r if 0<=age<=max_age_days else None

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
    wbc_ranks,wbc_champs=load_wbc_rankings()
    events=canonical_events(histories,links); eventkeys={k for k,s in events}
    targets=collections.defaultdict(set)
    for fid,history in histories.items():
        for r in history:
            targets[r['date']].add(fid)
            if links.get(r['source_id']):targets[r['date']].add(links[r['source_id']])
    elo=elo_before(events,targets)
    strength={};strength_source=collections.Counter()
    for fid,history in histories.items():
        for r in history:
            opp=links.get(r['source_id']); s=summary(histories.get(opp,[]),r['date'])
            n=s['career_observed_wins']+s['career_observed_losses']
            if n>=5:
                strength[r['source_id']]=s['career_observed_wins']/n
                strength_source['verified_linked_history']+=1
            else:
                x=explicit_opponent_strength(r)
                if x is not None:
                    strength[r['source_id']]=x
                    strength_source['source_stated_prefight_record']+=1
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
                        'weight_class_static_proxy':p.get('weight_class_snapshot'),
                        'physical_proxy_quality':'identity-linked current biography snapshot when available; height/reach treated as stable adult proxies; stance/nationality exploratory only',
                        'elo':rating,'elo_prior_verified_bouts':n,'last8_wins':sum(x['winner']=='BOXER A' for x in prior[-8:]),'last8_sample':len(prior[-8:]),
                        'input_bout_ids':[x['source_id'] for x in prior]}
                    _wr=wbc_before(wbc_ranks, sides[label].get('id') and p.get('name') or (r['boxer_a'] if label=='fighter' else r['boxer_b']), date)
                    _wc=wbc_before(wbc_champs, sides[label].get('id') and p.get('name') or (r['boxer_a'] if label=='fighter' else r['boxer_b']), date)
                    sides[label]['wbc_rank']=_wr.get('rank') if _wr else None
                    sides[label]['wbc_rank_division']=_wr.get('division') if _wr else None
                    sides[label]['wbc_rank_source_month']=f"{_wr.get('rating_year')}-{int(_wr.get('rating_month')):02d}" if _wr else None
                    sides[label]['wbc_champion_status']=_wc.get('status') if _wc else None
                    sides[label]['wbc_ranking_quality']='official_monthly_pdf_safe_effective_date' if (_wr or _wc) else None
                    assert not s['latest_input_bout_date'] or s['latest_input_bout_date']<date
                matched=[q for q in quotes[r['source_id']] if q['event_date']==date]
                row={'source_id':r['source_id'],'career_source':r['source'],'bout_date':date,'fighter_name':r['boxer_a'],'opponent_name':r['boxer_b'],
                     **sides,'canonical_verified_pair':bool(pair and (date,*pair) in eventkeys),'context':bout_context(r),
                     'wbc_rank_gap':(sides['opponent']['wbc_rank']-sides['fighter']['wbc_rank']) if sides.get('fighter') and sides.get('opponent') and sides['fighter'].get('wbc_rank') is not None and sides['opponent'].get('wbc_rank') is not None else None,
                     'outcome':{'result':r['winner'],'method':r['method'],'rounds':r['rounds']},'quotes':matched,'validated_price_eligible':False,
                     'historically_verified_physical_stats':False,'historically_linked_prior_punch_stats':None}
                f.write(json.dumps(row,ensure_ascii=False)+'\n');total+=1;source_rows[r['source']]+=1
                y=years[date[:4]];y['fighter_bout_rows']+=1;y['reciprocal_pair_rows']+=row['canonical_verified_pair'];y['price_linked_rows']+=bool(matched)
                y['both_record_totals_match']+=bool(sides['opponent'] and all(sides[x]['record_totals_match'] for x in ('fighter','opponent')))
    report={'built_at':stamp,'rows':total,'career_sources':dict(source_rows),'verified_graph_bouts':len(events),'identity_links':len(links),'opponent_strength_sources':dict(strength_source),
            'wbc_ranking_names_loaded':len(wbc_ranks),'wbc_champion_names_loaded':len(wbc_champs),'years':dict(sorted(years.items())),
            'validated_price_rows':0,'limitations':['Career rows preserve source provenance; secondary observed histories are not relabeled as Wikipedia.',
            'Source observations are not a census of all boxing fights.','Own Elo: 1500 initial, K32, reciprocal graph only; all same-day updates batched.',
            'Record totals agreement is an internal audit, not independent full-career certification.',
            'Opponent strength prefers linked point-in-time histories; explicit source-stated opponent pre-fight records are a secondary fallback.',
            'Biography age uses available birth date; height/reach are static adult proxies when available; stance/nationality remain exploratory.',
            'Title/location/scheduled-round context comes from record rows but only pre-fight-knowable flags are exposed.',
            'WBC ranking fields are used only from official monthly PDFs after their conservative safe effective date; missing remains unknown, never inferred unranked.',
            'All quotes have unverified timing/settlement. No validated ROI or live eligibility.',
            'Prior punch features unavailable until identity, date and coverage gates pass.']}
    (outdir/'coverage.json').write_text(json.dumps(report,indent=2));(ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').write_text(str(outdir)+'\n')
    print(json.dumps({'run':str(outdir),'rows':total,'career_sources':dict(source_rows),'verified_graph_bouts':len(events),'identity_links':len(links),'years':report['years']}))
if __name__=='__main__':main()
