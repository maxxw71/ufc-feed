"""Bounded evidence-first recovery for unresolved prospective and verified-price fighters.

No guessed IDs, no access bypass, no fuzzy admission, no live database writes.
"""
import argparse,collections,datetime as dt,hashlib,json,pathlib,sqlite3,urllib.error,urllib.parse,urllib.request
import backfill_priced_careers as careers

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--repo',type=pathlib.Path,required=True);ap.add_argument('--out',type=pathlib.Path,required=True)
    ap.add_argument('--limit',type=int,default=40);ap.add_argument('--db',default='/home/anestishkurti92/boxing-research/boxing.sqlite3');args=ap.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect('file:'+args.db+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    settlement=json.loads((args.repo/'boxing/prospective_odds/settled_bouts.json').read_text())
    targets={}
    for r in settlement['past_unresolved']:
        names=r['participants']
        for name,other in [names,list(reversed(names))]:
            t=targets.setdefault(name,{'name':name,'evidence':[],'priority':'past_unresolved'})
            t['evidence'].append({'date':r['event_date'],'opponent':other,'odds_bout_id':r['bout_id']})
    known=collections.defaultdict(set)
    for r in db.execute('select name,source_id from normalized_fighters'):
        if str(r['source_id']).startswith('https://en.wikipedia.org/wiki/'):known[careers.nk(r['name'])].add(r['source_id'])
    attempts=[];accepted=[];blocked=False
    original_get=careers.get
    def capture(url,timeout=10):
        content=original_get(url,timeout=timeout);digest=hashlib.sha256(content).hexdigest()
        (args.out/(digest+'.html')).write_bytes(content)
        attempts.append({'url':url,'sha256':digest,'bytes':len(content),'status':'retrieved'})
        return content
    careers.get=capture
    for item in list(targets.values())[:args.limit]:
        urls=known.get(careers.nk(item['name']),set())
        titles=[urllib.parse.unquote(u.split('/wiki/',1)[1]).replace('_',' ') for u in urls] if len(urls)==1 else careers.direct_titles(item['name'])[:1]
        status={'name':item['name'],'targets':item['evidence'],'status':'no_exact_dated_match'}
        for title in titles:
            try:page=careers.parse_page(title)
            except urllib.error.HTTPError as e:
                status.update(status='source_http_error',http_status=e.code)
                if e.code in (403,429):blocked=True
                break
            except Exception as e:status.update(status='source_error',error_type=type(e).__name__);break
            matches=careers.match_evidence(page,item['evidence'])
            if matches:
                row={'requested_name':item['name'],'verified_title':title,'source':'wikipedia','source_url':page['url'],'born':page['born'],'profile':page['profile'],'career_rows':page['rows'],'matched_price_evidence':matches,'priced_bouts':len(matches),'bookmakers':0,'discovery':'targeted_exact_date_pair_repair','verification':'professional record contains exact full-date and normalized opponent target','collected_at':dt.datetime.now(dt.timezone.utc).isoformat()}
                accepted.append(row);status.update(status='verified_career_candidate',rows=len(page['rows']),matches=len(matches))
        attempts.append(status)
        if blocked:break
    (args.out/'verified_career_candidates.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in accepted))
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'target_fighters':len(targets),'attempted_fighters':sum('name' in r for r in attempts),'accepted_candidates':len(accepted),'stopped_for_access_restriction':blocked,'remaining_status':'requires_other_authorized_source' if blocked else 'review_missing_evidence','attempts':attempts,'canonical_database_changed':False,'settlement_policy':'A recovered career is not two independent result sources; do not auto-settle from this artifact.'}
    (args.out/'targeted_repair_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:v for k,v in report.items() if k!='attempts'}))

if __name__=='__main__':main()
