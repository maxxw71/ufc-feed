"""Release manifest and priced-bout exclusions, using the research engine's own gates.

Run only on appwiza after building a chronological master. No source DB writes.
"""
import argparse,collections,datetime as dt,gzip,hashlib,json,os,pathlib,sqlite3,subprocess
from market_consensus import canonical_market_rows,quote_signature

def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def write(p,x):
    tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(x,indent=2,ensure_ascii=False));os.replace(tmp,p)

def pair(r):
    a=(r.get('fighter') or {}).get('id');b=(r.get('opponent') or {}).get('id')
    return (r.get('bout_date'),*sorted([a,b])) if a and b else None

def priced_master(path):
    keys=set();source_ids=set()
    with open(path) as f:
        for line in f:
            r=json.loads(line)
            if r.get('quotes'):
                source_ids.add(r['source_id'])
                if pair(r):keys.add(pair(r))
    rows=[]
    with open(path) as f:
        for line in f:
            r=json.loads(line)
            if r['source_id'] in source_ids or pair(r) in keys:rows.append(r)
    return rows

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run',type=pathlib.Path,required=True)
    ap.add_argument('--repo',type=pathlib.Path,required=True);ap.add_argument('--out',type=pathlib.Path,required=True)
    args=ap.parse_args();out=args.out;out.mkdir(parents=True,exist_ok=True)
    db=args.run/'source.sqlite3';master=args.run/'boxing_prefight_master.jsonl'
    validpath=args.repo/'boxing/public_reports/HISTORICAL_ODDS_STRICT_UNION_ROWS.json'
    valid=json.loads(validpath.read_text());vrows=valid.get('rows',[])
    allowed={quote_signature({**r,'decimal_price':r.get('stored_decimal_price'),'odds_bout_id':r.get('bout_id')}) for r in vrows};allowed.discard(None)
    verified_ids={str(r['bout_id']) for r in vrows if r.get('bout_id')}
    rows=priced_master(master)
    d=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True);d.row_factory=sqlite3.Row
    universe={str(r[0]) for r in d.execute('select distinct bout_id from odds')}
    universe.update(str(r[0]) for r in d.execute('select distinct odds_bout_id from priced_bout_research'))
    ledger={bid:{'odds_bout_id':bid,'has_verified_quote':bid in verified_ids,'tiers':{},'fighter_ids':set(),'fighters':set()} for bid in universe}
    for r in d.execute('select distinct bout_id,selection from odds'):
        if str(r[0]) in ledger and r[1]:ledger[str(r[0])]['fighters'].add(r[1])
    profiles=collections.defaultdict(set)
    for r in d.execute('select source_id,name from normalized_fighters'):
        if r[1]:profiles[str(r[1]).casefold()].add(r[0])
    counts={};eligible_keys={};priority=collections.defaultdict(lambda:{'verified_price_bouts':set(),'other_priced_bouts':set(),'reasons':collections.Counter()})
    for tier,minbooks,signatures in [('broad',2,None),('verified_one_book',1,allowed),('verified_two_books',2,allowed)]:
        audit=[];market=canonical_market_rows(rows,min_books=minbooks,allowed_quote_signatures=signatures,audit=audit)
        counts[tier]=len(market);eligible_keys[tier]=[list(x['key']) for x in market]
        for rec in audit:
            for bid in rec['odds_bout_ids']:
                dest=ledger.setdefault(bid,{'odds_bout_id':bid,'has_verified_quote':bid in verified_ids,'tiers':{},'fighter_ids':set(),'fighters':set()})
                state=dest['tiers'].setdefault(tier,{'eligible':False,'reasons':set(),'source_ids':set()})
                state['eligible'] |= rec['eligible'];state['reasons'].update(rec['reasons']);state['source_ids'].update(rec['source_ids'])
                dest['fighter_ids'].update(rec['fighter_ids']);dest['fighters'].update(rec['fighters'])
        for dest in ledger.values():
            if tier not in dest['tiers']:
                dest['tiers'][tier]={'eligible':False,'reasons':{'no_linked_research_feature'},'source_ids':set()}
            state=dest['tiers'][tier]
            if signatures is not None and not dest['has_verified_quote']:state['reasons'].add('no_verified_pre_event_quote')
            # Preserve failed alternatives as diagnostics, never count them as eligible exclusions.
            state['alternative_observation_failures']=sorted(state['reasons']) if state['eligible'] else []
            state['reasons']=[] if state['eligible'] else sorted(state['reasons']);state['source_ids']=sorted(state['source_ids'])
    for dest in ledger.values():
        for name in dest['fighters']:
            matches=profiles.get(name.casefold(),set())
            if len(matches)==1:dest['fighter_ids'].update(matches)
        dest['fighter_ids']=sorted(dest['fighter_ids']);dest['fighters']=sorted(dest['fighters'])
        reasons=dest['tiers']['verified_one_book']['reasons']
        for fid in dest['fighter_ids']:
            p=priority[fid];p['verified_price_bouts' if dest['has_verified_quote'] else 'other_priced_bouts'].add(dest['odds_bout_id']);p['reasons'].update(reasons)
    with gzip.open(out/'priced_bout_exclusions.jsonl.gz','wt') as f:
        for bid in sorted(ledger):f.write(json.dumps(ledger[bid],ensure_ascii=False)+'\n')
    queue=[]
    repair_reasons={'prior_record_incomplete_or_inconsistent','unverified_pair_or_unresolved_identity','missing_or_duplicate_fighter_perspective','no_linked_research_feature'}
    for fid,p in priority.items():
        if repair_reasons.intersection(p['reasons']):
            queue.append({'fighter_id':fid,'verified_price_bouts':sorted(p['verified_price_bouts']),'other_priced_bouts':sorted(p['other_priced_bouts']),'reasons':dict(p['reasons']), 'action':'review_exact_identity_and_complete_prior_professional_record'})
    queue.sort(key=lambda x:(-len(x['verified_price_bouts']),-len(x['other_priced_bouts']),x['fighter_id']))
    write(out/'history_repair_queue.json',queue)
    write(out/'unresolved_identity_targets.json',[{'odds_bout_id':r['odds_bout_id'],'names':r['fighters'],'verified_quote':r['has_verified_quote']} for r in ledger.values() if not r['fighter_ids']])
    exclusions={tier:dict(collections.Counter(reason for r in ledger.values() for reason in r['tiers'][tier]['reasons'])) for tier in counts}
    # Reconcile against independently emitted research reports when present.
    search=args.run/'validated_price_method_search.json'
    if search.exists():
        expected=json.loads(search.read_text())['tiers']
        assert counts['verified_one_book']==expected['one_or_more_verified_books']['eligible_verified_price_bouts']
        assert counts['verified_two_books']==expected['two_or_more_verified_books']['eligible_verified_price_bouts']
    inputs={};executed_root=args.run.parents[1]
    for folder in ['profile_supplements','supplemental_careers','punch_supplements','rankings','official_bouts','prospective_odds']:
        root=executed_root.parent/folder
        if not root.exists():root=args.repo/'boxing'/folder
        for p in sorted(root.rglob('*')):
            if p.is_file():inputs[folder+'/'+str(p.relative_to(root))]=sha(p)
    code={p.name:sha(p) for p in sorted(executed_root.glob('*.py'))}
    commit=subprocess.check_output(['git','-C',str(args.repo),'rev-parse','HEAD'],text=True).strip()
    dirty=subprocess.check_output(['git','-C',str(args.repo),'status','--porcelain','--untracked-files=normal'],text=True)
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    manifest={'schema_version':1,'built_at':now,'status':'audited_research_release_not_live_methods','source_db':str(db),'database_sha256':sha(db),'master_sha256':sha(master),'master_path':str(master),'code_revision':commit,'working_tree_changes':dirty.splitlines(),'code_sha256':code,'supplement_sha256':inputs,'price_validation_sha256':sha(validpath),'eligibility':counts,'odds_bout_ids_audited':len(ledger),'exclusions_by_reason_nonexclusive':exclusions,'history_repair_targets':len(queue),'lanes':{'broad':'career_context_research_only','deep_stats':'requires_separate_prior_punch_depth_eligibility'},'limitations':['Historical event chronology is not proof of original publication availability.','Verified quotes do not certify bookmaker draw/NC settlement rules.','Historical method searches share inspected data; no pristine holdout claim.'],'supersedes_for_coverage':['boxing/public_reports/DATA_GAP_AUDIT.json','server LATEST_CHRONOLOGICAL_MASTER.txt before this release'],'artifacts':{p.name:sha(p) for p in out.iterdir() if p.is_file() and p.name!='release_manifest.json'}}
    write(out/'eligible_bout_keys.json',eligible_keys)
    write(out/'release_manifest.json',manifest)
    # Immutable forward hypothesis baseline. Future edits require a new version.
    methods=args.repo/'boxing/methods.json'
    write(out/'frozen_hypotheses.json',{'frozen_at':now,'methods_sha256':sha(methods),'methods':json.loads(methods.read_text()),'historical_results_are_exploratory':True,'forward_eligibility':'Only captures and events after frozen_at; exact hypothesis version, release ID and price evidence required.','live_promotion':False})
    print(json.dumps({'release':str(out),'eligibility':counts,'priced_population':len(ledger),'repair_targets':len(queue),'exclusions':exclusions}))

if __name__=='__main__':main()
