"""Versioned research master; no changes to collection, live rules, or email."""
import collections, datetime as dt, hashlib, json, math, re, sqlite3
from pathlib import Path
from features import summary, age, namekey
from enrich_history import stats, rounds_value
from build_punch_profiles import weighted as punch_weighted, norm_name as punch_namekey
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

def load_ibf_bout_context():
    path=ROOT.parent/'official_bouts'/'ibf_bouts.json'
    out={};conflicts=set()
    if not path.exists():return out
    try:rows=json.loads(path.read_text())
    except Exception:return out
    grouped=collections.defaultdict(list)
    for r in rows:
        date=str(r.get('date') or '')
        a=namekey(r.get('fighter_a') or '');b=namekey(r.get('fighter_b') or '')
        if not date or not a or not b or a==b:continue
        key=(date,*sorted((a,b)))
        grouped[key].append(r)
    for key,items in grouped.items():
        sigs={
          (str(x.get('weight_class') or ''),str(x.get('organization') or ''),
           str(x.get('bout_type') or ''),str(x.get('location') or ''),
           str(x.get('promoter') or ''))
          for x in items
        }
        if len(sigs)!=1:
            conflicts.add(key);continue
        w,o,t,loc,prom=next(iter(sigs))
        out[key]={
          'weight_class':w or None,'organization':o or None,'bout_type':t or None,
          'location':loc or None,'promoter':prom or None,
          'title_or_eliminator_flag':bool(re.search(r'championship|defense|unification|eliminator|mandatory|vacant',t,re.I)),
          'source':'official_ibf_bout_api'
        }
    return out

def bout_context(r):
    try:data=json.loads(r.get('data') or '{}')
    except Exception:data={}
    notes=' '.join(str(data.get(k,'') or '') for k in ('notes','note','note(s)','more','titles')).strip()
    low=notes.casefold()
    terminal,scheduled=rounds_value(r.get('rounds'))
    # Prefer an explicit scheduled-round field from the source when present.
    explicit_sched=None
    m=re.search(r'\d{1,2}',str(r.get('scheduled_rounds') or data.get('scheduled_rounds') or ''))
    if m:
        v=int(m.group())
        if 1<=v<=15:explicit_sched=v
    if explicit_sched is not None:scheduled=explicit_sched
    location=r.get('venue') or data.get('location') or data.get('venue and location') or data.get('venue') or ''
    loc_parts=[x.strip() for x in str(location).split(',') if x.strip()]
    country=loc_parts[-1] if loc_parts else ''
    country_norm={
      'u.s.':'USA','u.s.a.':'USA','united states':'USA','united states of america':'USA',
      'england':'United Kingdom','scotland':'United Kingdom','wales':'United Kingdom',
      'northern ireland':'United Kingdom'
    }.get(country.casefold(),country)

    title=bool(re.search(r'\btitle\b|\bchampionship\b',low) or data.get('titles'))
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
    context_text=' '.join([str(r.get('division') or ''),str(data.get('weight_class') or ''),str(data.get('division') or ''),str(data.get('weight class') or ''),str(data.get('weight') or ''),str(data.get('notes') or ''),str(data.get('note') or ''),str(data.get('note(s)') or ''),str(data.get('more') or '')]).casefold()
    division=None
    for canonical,aliases in divisions:
        if any(re.search(r'(?<![a-z])'+re.escape(a)+r'(?![a-z])',context_text) for a in aliases):
            division=canonical;break

    return {
      'location':location,
      'location_country_raw':country_norm,
      'scheduled_rounds':scheduled,
      'scheduled_rounds_explicit':explicit_sched is not None,
      'scheduled_rounds_source':'structured_source_field' if explicit_sched is not None else ('record_round_parse' if scheduled is not None else None),
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

def load_punch_history():
    path=ROOT/'punch_profiles'/'fight_punch_observations.jsonl'
    hist=collections.defaultdict(list)
    if not path.exists():
        return hist
    for line in path.read_text().splitlines():
        if not line.strip():continue
        try:r=json.loads(line)
        except Exception:continue
        if r.get('identity_quality')!='report_title_full_name_suffix_match':continue
        key=r.get('fighter_key')
        date=r.get('bout_date')
        if not key or not date:continue
        try:dt.date.fromisoformat(date)
        except Exception:continue
        hist[key].append(r)
    for key in hist:
        hist[key].sort(key=lambda r:(r['bout_date'],r.get('report_id') or r.get('report_url') or ''))
    return hist

def punch_before(hist,name,date):
    key=punch_namekey(name or '')
    rows=[r for r in hist.get(key,[]) if r.get('bout_date') and r['bout_date']<date]
    if not rows:return None
    out={'prior_punch_fights':len(rows),'prior_punch_rounds':sum(int(r.get('rounds_observed') or 0) for r in rows),
         'latest_prior_punch_date':rows[-1]['bout_date'],'identity_quality':'exact_full_name_from_compubox_report_title'}
    for cat in ('total','jab','power'):
        for metric in ('landed_per_round','thrown_per_round','accuracy_pct','avoidance_pct','net_landed_per_round','round_edge_rate','landed_diff_slope','late_vs_early_net_delta'):
            field=f'{cat}_{metric}'
            out['career_'+field]=punch_weighted(rows,field)
            out['last3_'+field]=punch_weighted(rows,field,limit=3)
    out['career_body_landed_share_pct']=punch_weighted(rows,'body_landed_share_pct')
    out['last3_body_landed_share_pct']=punch_weighted(rows,'body_landed_share_pct',limit=3)
    return out

def load_punch_summary_history():
    path=ROOT.parent/'punch_supplements'/'boxingscene_compubox_summaries.jsonl'
    hist=collections.defaultdict(list)
    if not path.exists():return hist
    for line in path.read_text().splitlines():
        if not line.strip():continue
        try:r=json.loads(line)
        except Exception:continue
        aliases=r.get('fighter_aliases') or [r.get('fighter')]
        keys=sorted({punch_namekey(x or '') for x in aliases if punch_namekey(x or '')})
        date=r.get('bout_date')
        if not keys or not date:continue
        try:dt.date.fromisoformat(date)
        except Exception:continue
        for key in keys:
            hist[key].append(r)
    for key in hist:
        hist[key].sort(key=lambda r:(r['bout_date'],r.get('source_url') or ''))
    return hist

def _summary_metric(row,cat,kind):
    direct=row.get(f'{cat}_{kind}_per_round')
    if direct is not None:
        try:return float(direct)
        except Exception:return None
    total=row.get(f'{cat}_{kind}')
    rounds=row.get('rounds_observed')
    try:
        total=float(total);rounds=int(rounds)
    except Exception:return None
    if rounds<=0:return None
    return total/rounds

def _mean(values):
    vals=[]
    for x in values:
        if x is None:continue
        try:v=float(x)
        except Exception:continue
        if math.isfinite(v):vals.append(v)
    return sum(vals)/len(vals) if vals else None

def punch_summary_before(hist,name,date):
    key=punch_namekey(name or '')
    rows=[r for r in hist.get(key,[]) if r.get('bout_date') and r['bout_date']<date]
    if not rows:return None
    out={
      'prior_summary_fights':len(rows),
      'latest_prior_summary_date':rows[-1]['bout_date'],
      'quality':'compubox_authored_boxingscene_explicit_numeric_summary_exact_date_pair',
      'source_tier':'historical_summary_separate_from_full_round_reports'
    }
    for cat in ('total','jab','power'):
        for kind in ('landed','thrown'):
            vals=[_summary_metric(r,cat,kind) for r in rows]
            out[f'career_{cat}_{kind}_per_round']=_mean(vals)
            out[f'last3_{cat}_{kind}_per_round']=_mean(vals[-3:])
        ratios=[];ratios3=[]
        for rr in rows:
            direct=rr.get(f'{cat}_accuracy_pct')
            if direct is not None:
                try:ratios.append(float(direct))
                except Exception:ratios.append(None)
            else:
                l=_summary_metric(rr,cat,'landed');t=_summary_metric(rr,cat,'thrown')
                ratios.append((100*l/t) if l is not None and t not in (None,0) else None)
        for rr in rows[-3:]:
            direct=rr.get(f'{cat}_accuracy_pct')
            if direct is not None:
                try:ratios3.append(float(direct))
                except Exception:ratios3.append(None)
            else:
                l=_summary_metric(rr,cat,'landed');t=_summary_metric(rr,cat,'thrown')
                ratios3.append((100*l/t) if l is not None and t not in (None,0) else None)
        out[f'career_{cat}_accuracy_pct']=_mean(ratios)
        out[f'last3_{cat}_accuracy_pct']=_mean(ratios3)
    known_rounds=[]
    for r in rows:
        try:n=int(r.get('rounds_observed'))
        except Exception:continue
        if n>0:known_rounds.append(n)
    out['prior_summary_rounds_known']=sum(known_rounds)
    out['prior_summary_fights_with_known_rounds']=len(known_rounds)
    return out

def load_compubox_prefight_baselines():
    path=ROOT.parent/'punch_supplements'/'boxingscene_compubox_prefight_baselines.jsonl'
    raw_by_key_date=collections.defaultdict(list)
    if not path.exists():return {}
    for line in path.read_text().splitlines():
        if not line.strip():continue
        try:r=json.loads(line)
        except Exception:continue
        available=str(r.get('available_from_date') or r.get('article_date') or '')
        target=str(r.get('target_bout_date') or '')
        try:
            dt.date.fromisoformat(available)
            dt.date.fromisoformat(target)
        except Exception:
            continue
        aliases=r.get('fighter_aliases') or [r.get('fighter')]
        for alias in aliases:
            key=punch_namekey(alias or '')
            if key:raw_by_key_date[(key,available)].append(r)

    by_fighter=collections.defaultdict(list)
    meta_fields={
      'source_url','article_title','article_date','available_from_date','target_bout_date',
      'target_bout_eligible','fighter','fighter_aliases','opponent','quality','timing_evidence'
    }
    for (key,available),rows in raw_by_key_date.items():
        merged={
          'quality':'compubox_authored_boxingscene_explicit_historical_prefight_aggregate',
          'source_tier':'publication_timed_prefight_historical_baseline',
          'available_from_date':available,
          'source_urls':sorted({r.get('source_url') for r in rows if r.get('source_url')}),
          'source_target_bout_dates':sorted({r.get('target_bout_date') for r in rows if r.get('target_bout_date')}),
          'direct_target_dates':sorted({
              r.get('target_bout_date') for r in rows
              if r.get('target_bout_eligible') and r.get('target_bout_date')
          }),
          'history_windows':sorted({
              int(r['history_window_fights']) for r in rows
              if r.get('history_window_fights') is not None
          }),
          'timing_evidence':sorted({r.get('timing_evidence') for r in rows if r.get('timing_evidence')})
        }
        bad=False
        fields=sorted({
          k for r in rows for k,v in r.items()
          if k not in meta_fields and isinstance(v,(int,float)) and not isinstance(v,bool)
        })
        for field in fields:
            vals={float(r[field]) for r in rows if r.get(field) is not None}
            if len(vals)>1:
                bad=True;break
            if vals:merged[field]=next(iter(vals))
        if not bad:
            by_fighter[key].append(merged)

    for key in by_fighter:
        by_fighter[key].sort(key=lambda r:(r['available_from_date'],','.join(r.get('source_urls') or [])))
    return by_fighter

def compubox_prefight_baseline(index,name,date):
    key=punch_namekey(name or '')
    snapshots=[r for r in index.get(key,[]) if r.get('available_from_date') and r['available_from_date']<date]
    if not snapshots:return None
    direct=[r for r in snapshots if date in (r.get('direct_target_dates') or [])]
    chosen=dict(direct[-1] if direct else snapshots[-1])
    chosen['direct_target_match']=bool(direct)
    chosen['leakage_policy']='publication date strictly before bout date'
    return chosen

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

def load_wba_rankings():
    candidates=[ROOT/'rankings',ROOT.parent/'rankings']
    rfile=next((p/'wba_monthly_rankings.json' for p in candidates if (p/'wba_monthly_rankings.json').exists()),None)
    cfile=next((p/'wba_monthly_champions.json' for p in candidates if (p/'wba_monthly_champions.json').exists()),None)
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

def load_wbo_rankings():
    candidates=[ROOT/'rankings',ROOT.parent/'rankings']
    rfile=next((p/'wbo_monthly_rankings.json' for p in candidates if (p/'wbo_monthly_rankings.json').exists()),None)
    cfile=next((p/'wbo_monthly_champions.json' for p in candidates if (p/'wbo_monthly_champions.json').exists()),None)
    ranks=collections.defaultdict(list);champs=collections.defaultdict(list)
    if rfile:
        try:
            for r in json.loads(rfile.read_text()):
                key=namekey(r.get('name') or '')
                if key and r.get('safe_effective_date'):ranks[key].append(r)
        except Exception:pass
    if cfile:
        try:
            for r in json.loads(cfile.read_text()):
                key=namekey(r.get('name') or '')
                if key and r.get('safe_effective_date'):champs[key].append(r)
        except Exception:pass
    for x in (ranks,champs):
        for k in x:x[k].sort(key=lambda r:r.get('safe_effective_date') or '')
    return ranks,champs

def load_ibf_rankings():
    candidates=[ROOT/'rankings',ROOT.parent/'rankings']
    rfile=next((p/'ibf_monthly_rankings.json' for p in candidates if (p/'ibf_monthly_rankings.json').exists()),None)
    cfile=next((p/'ibf_monthly_champions.json' for p in candidates if (p/'ibf_monthly_champions.json').exists()),None)
    ranks=collections.defaultdict(list);champs=collections.defaultdict(list)
    if rfile:
        try:
            for r in json.loads(rfile.read_text()):
                key=namekey(r.get('name') or '')
                if key and r.get('safe_effective_date'):ranks[key].append(r)
        except Exception:pass
    if cfile:
        try:
            for r in json.loads(cfile.read_text()):
                key=namekey(r.get('name') or '')
                if key and r.get('safe_effective_date'):champs[key].append(r)
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
    wba_ranks,wba_champs=load_wba_rankings()
    wbo_ranks,wbo_champs=load_wbo_rankings()
    ibf_ranks,ibf_champs=load_ibf_rankings()
    ibf_bout_context=load_ibf_bout_context()
    punch_history=load_punch_history()
    punch_summary_history=load_punch_summary_history()
    compubox_prefight_baselines=load_compubox_prefight_baselines()
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
    years=collections.defaultdict(collections.Counter); total=0;source_rows=collections.Counter();context_counts=collections.Counter()
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
                        'prior_punch':punch_before(punch_history,p.get('name') or (r['boxer_a'] if label=='fighter' else r['boxer_b']),date),
                        'prior_punch_summary':punch_summary_before(punch_summary_history,p.get('name') or (r['boxer_a'] if label=='fighter' else r['boxer_b']),date),
                        'compubox_prefight_baseline':compubox_prefight_baseline(compubox_prefight_baselines,p.get('name') or (r['boxer_a'] if label=='fighter' else r['boxer_b']),date),
                        'input_bout_ids':[x['source_id'] for x in prior]}
                    _wr=wbc_before(wbc_ranks, sides[label].get('id') and p.get('name') or (r['boxer_a'] if label=='fighter' else r['boxer_b']), date)
                    _wc=wbc_before(wbc_champs, sides[label].get('id') and p.get('name') or (r['boxer_a'] if label=='fighter' else r['boxer_b']), date)
                    sides[label]['wbc_rank']=_wr.get('rank') if _wr else None
                    sides[label]['wbc_rank_division']=_wr.get('division') if _wr else None
                    sides[label]['wbc_rank_source_month']=f"{_wr.get('rating_year')}-{int(_wr.get('rating_month')):02d}" if _wr else None
                    sides[label]['wbc_champion_status']=_wc.get('status') if _wc else None
                    sides[label]['wbc_ranking_quality']='official_monthly_pdf_safe_effective_date' if (_wr or _wc) else None
                    _bar=wbc_before(wba_ranks, sides[label].get('id') and p.get('name') or (r['boxer_a'] if label=='fighter' else r['boxer_b']), date, max_age_days=95)
                    _bac=wbc_before(wba_champs, sides[label].get('id') and p.get('name') or (r['boxer_a'] if label=='fighter' else r['boxer_b']), date, max_age_days=95)
                    sides[label]['wba_rank']=_bar.get('rank') if _bar else None
                    sides[label]['wba_rank_division']=_bar.get('division') if _bar else None
                    sides[label]['wba_rank_source_month']=f"{int(_bar.get('year'))}-{int(_bar.get('month')):02d}" if _bar else None
                    sides[label]['wba_rank_safe_effective_date']=_bar.get('safe_effective_date') if _bar else None
                    sides[label]['wba_champion_status']=_bac.get('title') if _bac else None
                    sides[label]['wba_ranking_quality']='official_monthly_pdf_official_posting_date' if (_bar or _bac) else None
                    _bor=wbc_before(wbo_ranks, sides[label].get('id') and p.get('name') or (r['boxer_a'] if label=='fighter' else r['boxer_b']), date, max_age_days=95)
                    _boc=wbc_before(wbo_champs, sides[label].get('id') and p.get('name') or (r['boxer_a'] if label=='fighter' else r['boxer_b']), date, max_age_days=95)
                    sides[label]['wbo_rank']=_bor.get('rank') if _bor else None
                    sides[label]['wbo_rank_division']=_bor.get('division') if _bor else None
                    sides[label]['wbo_rank_safe_effective_date']=_bor.get('safe_effective_date') if _bor else None
                    sides[label]['wbo_champion_status']=_boc.get('status') if _boc else None
                    sides[label]['wbo_ranking_quality']='official_wbo_pdf_official_article_publication_date' if (_bor or _boc) else None
                    _ibr=wbc_before(ibf_ranks, sides[label].get('id') and p.get('name') or (r['boxer_a'] if label=='fighter' else r['boxer_b']), date, max_age_days=95)
                    _ibc=wbc_before(ibf_champs, sides[label].get('id') and p.get('name') or (r['boxer_a'] if label=='fighter' else r['boxer_b']), date, max_age_days=95)
                    sides[label]['ibf_rank']=_ibr.get('rank') if _ibr else None
                    sides[label]['ibf_rank_division']=_ibr.get('division') if _ibr else None
                    sides[label]['ibf_rank_safe_effective_date']=_ibr.get('safe_effective_date') if _ibr else None
                    sides[label]['ibf_champion_status']=_ibc.get('status') if _ibc else None
                    sides[label]['ibf_ranking_quality']='official_ibf_api_post_date_plus_one_day' if (_ibr or _ibc) else None
                    assert not s['latest_input_bout_date'] or s['latest_input_bout_date']<date
                matched=[q for q in quotes[r['source_id']] if q['event_date']==date]
                _ctx=bout_context(r)
                _ibf_ctx=ibf_bout_context.get((date,*sorted((namekey(r['boxer_a']),namekey(r['boxer_b'])))))
                row={'source_id':r['source_id'],'career_source':r['source'],'bout_date':date,'fighter_name':r['boxer_a'],'opponent_name':r['boxer_b'],
                     **sides,'canonical_verified_pair':bool(pair and (date,*pair) in eventkeys),'context':_ctx,
                     'ibf_official_bout_context':_ibf_ctx,
                     'wbc_rank_gap':(sides['opponent']['wbc_rank']-sides['fighter']['wbc_rank']) if sides.get('fighter') and sides.get('opponent') and sides['fighter'].get('wbc_rank') is not None and sides['opponent'].get('wbc_rank') is not None else None,
                     'wba_rank_gap':(sides['opponent']['wba_rank']-sides['fighter']['wba_rank']) if sides.get('fighter') and sides.get('opponent') and sides['fighter'].get('wba_rank') is not None and sides['opponent'].get('wba_rank') is not None else None,
                     'wbo_rank_gap':(sides['opponent']['wbo_rank']-sides['fighter']['wbo_rank']) if sides.get('fighter') and sides.get('opponent') and sides['fighter'].get('wbo_rank') is not None and sides['opponent'].get('wbo_rank') is not None else None,
                     'ibf_rank_gap':(sides['opponent']['ibf_rank']-sides['fighter']['ibf_rank']) if sides.get('fighter') and sides.get('opponent') and sides['fighter'].get('ibf_rank') is not None and sides['opponent'].get('ibf_rank') is not None else None,
                     'outcome':{'result':r['winner'],'method':r['method'],'rounds':r['rounds']},'quotes':matched,'validated_price_eligible':False,
                     'historically_verified_physical_stats':False,
                     'historically_linked_prior_punch_stats':{
                         'fighter':sides['fighter'].get('prior_punch') if sides.get('fighter') else None,
                         'opponent':sides['opponent'].get('prior_punch') if sides.get('opponent') else None
                     } if (sides.get('fighter') and sides['fighter'].get('prior_punch')) or (sides.get('opponent') and sides['opponent'].get('prior_punch')) else None,
                     'historically_linked_prior_punch_summary_stats':{
                         'fighter':sides['fighter'].get('prior_punch_summary') if sides.get('fighter') else None,
                         'opponent':sides['opponent'].get('prior_punch_summary') if sides.get('opponent') else None
                     } if (sides.get('fighter') and sides['fighter'].get('prior_punch_summary')) or (sides.get('opponent') and sides['opponent'].get('prior_punch_summary')) else None,
                     'compubox_prefight_historical_baseline':{
                         'fighter':sides['fighter'].get('compubox_prefight_baseline') if sides.get('fighter') else None,
                         'opponent':sides['opponent'].get('compubox_prefight_baseline') if sides.get('opponent') else None
                     } if (sides.get('fighter') and sides['fighter'].get('compubox_prefight_baseline')) or (sides.get('opponent') and sides['opponent'].get('compubox_prefight_baseline')) else None}
                f.write(json.dumps(row,ensure_ascii=False)+'\n');total+=1;source_rows[r['source']]+=1
                context_counts['scheduled_rounds']+=bool(_ctx.get('scheduled_rounds'))
                context_counts['division']+=bool(_ctx.get('division_from_record_text') or (_ibf_ctx and _ibf_ctx.get('weight_class')))
                context_counts['location']+=bool(_ctx.get('location') or (_ibf_ctx and _ibf_ctx.get('location')))
                context_counts['title_bout']+=bool(_ctx.get('title_bout') or (_ibf_ctx and _ibf_ctx.get('title_or_eliminator_flag')))
                context_counts['major_world_title']+=bool(_ctx.get('major_world_title_orgs'))
                context_counts['ibf_official_context']+=bool(_ibf_ctx)
                y=years[date[:4]];y['fighter_bout_rows']+=1;y['reciprocal_pair_rows']+=row['canonical_verified_pair'];y['price_linked_rows']+=bool(matched)
                y['both_record_totals_match']+=bool(sides['opponent'] and all(sides[x]['record_totals_match'] for x in ('fighter','opponent')))
                y['fighter_has_prior_punch']+=bool(sides.get('fighter') and sides['fighter'].get('prior_punch'))
                y['both_have_prior_punch']+=bool(sides.get('fighter') and sides.get('opponent') and sides['fighter'].get('prior_punch') and sides['opponent'].get('prior_punch'))
                y['fighter_has_prior_punch_summary']+=bool(sides.get('fighter') and sides['fighter'].get('prior_punch_summary'))
                y['both_have_prior_punch_summary']+=bool(sides.get('fighter') and sides.get('opponent') and sides['fighter'].get('prior_punch_summary') and sides['opponent'].get('prior_punch_summary'))
                y['fighter_has_compubox_prefight_baseline']+=bool(sides.get('fighter') and sides['fighter'].get('compubox_prefight_baseline'))
                y['both_have_compubox_prefight_baseline']+=bool(sides.get('fighter') and sides.get('opponent') and sides['fighter'].get('compubox_prefight_baseline') and sides['opponent'].get('compubox_prefight_baseline'))
                y['ibf_official_context_rows']+=bool(_ibf_ctx)
    context_coverage={k:{'rows':int(v),'pct':round(100*v/total,2) if total else None} for k,v in sorted(context_counts.items())}
    report={'built_at':stamp,'rows':total,'career_sources':dict(source_rows),'verified_graph_bouts':len(events),'identity_links':len(links),'opponent_strength_sources':dict(strength_source),
            'normalized_context_coverage':context_coverage,
            'wbc_ranking_names_loaded':len(wbc_ranks),'wbc_champion_names_loaded':len(wbc_champs),
            'wba_ranking_names_loaded':len(wba_ranks),'wba_champion_names_loaded':len(wba_champs),
            'wbo_ranking_names_loaded':len(wbo_ranks),'wbo_champion_names_loaded':len(wbo_champs),
            'ibf_ranking_names_loaded':len(ibf_ranks),'ibf_champion_names_loaded':len(ibf_champs),
            'ibf_official_bout_contexts_loaded':len(ibf_bout_context),
            'punch_identity_fighters_loaded':len(punch_history),'punch_observations_loaded':sum(len(v) for v in punch_history.values()),
            'punch_summary_identity_fighters_loaded':len(punch_summary_history),'punch_summary_observations_loaded':sum(len(v) for v in punch_summary_history.values()),
            'compubox_prefight_baseline_fighters_loaded':len(compubox_prefight_baselines),
            'compubox_prefight_baseline_snapshots_loaded':sum(len(v) for v in compubox_prefight_baselines.values()),
            'years':dict(sorted(years.items())),
            'validated_price_rows':0,'limitations':['Career rows preserve source provenance; secondary observed histories are not relabeled as Wikipedia.',
            'Source observations are not a census of all boxing fights.','Own Elo: 1500 initial, K32, reciprocal graph only; all same-day updates batched.',
            'Record totals agreement is an internal audit, not independent full-career certification.',
            'Opponent strength prefers linked point-in-time histories; explicit source-stated opponent pre-fight records are a secondary fallback.',
            'Biography age uses available birth date; height/reach are static adult proxies when available; stance/nationality remain exploratory.',
            'Title/location/scheduled-round context comes from record rows but only pre-fight-knowable flags are exposed.',
            'WBC ranking fields are used only from official monthly PDFs after their conservative safe effective date; missing remains unknown, never inferred unranked.',
            'WBA ranking fields are kept separate from WBC and are usable only on/after the official WBA posting date; missing remains unknown.',
            'WBO ranking fields are kept separate and usable only on/after the official WBO article publication date; missing remains unknown.',
            'IBF ranking fields are kept separate and usable only after the official IBF API post date; missing/vacant rank slots remain unknown.',
            'IBF official bout type/location/promoter context is attached only on an exact date+participant-pair match and never uses the stored result field as a feature.',
            'All quotes have unverified timing/settlement. No validated ROI or live eligibility.',
            'Prior full-round punch features require exact full-name identity from CompuBox report titles and only earlier report dates; missing remains unknown.',
            'Historical CompuBox/BoxingScene summary features are a separate lower-resolution tier: exact date+pair, explicit numeric statements only, strictly prior dates, and only comparable per-round/accuracy aggregates are exposed.']}
    (outdir/'coverage.json').write_text(json.dumps(report,indent=2));(ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').write_text(str(outdir)+'\n')
    print(json.dumps({'run':str(outdir),'rows':total,'career_sources':dict(source_rows),'verified_graph_bouts':len(events),'identity_links':len(links),'years':report['years']}))
if __name__=='__main__':main()
