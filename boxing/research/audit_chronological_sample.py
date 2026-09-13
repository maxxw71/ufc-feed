"""Reconcile selected sample against frozen records and hashed cached odds HTML."""
import collections,gzip,hashlib,json,math,re,sqlite3
from pathlib import Path
from bs4 import BeautifulSoup
from features import summary,namekey
ROOT=Path(__file__).resolve().parent

def parse_raw(blob):
    soup=BeautifulSoup(blob,'html.parser');out=collections.defaultdict(set)
    for table in soup.select('table.odds-table'):
        books={int(c['data-b']):c.get_text(' ',strip=True) for c in table.select('thead th[data-b]')}
        for row in table.select('tbody tr'):
            if 'pr' in row.get('class',[]):continue
            fighter=row.select_one('th a[href^="/fighters/"]')
            if not fighter:continue
            for cell in row.select('td.but-sg[data-li]'):
                ids=json.loads(cell['data-li']);value=cell.select_one('span[id^="oID"]')
                if len(ids)!=3 or not value:continue
                text=value.get_text(strip=True).replace('−','-')
                if not re.fullmatch(r'[+-]\d+',text):continue
                american=int(text)
                if abs(american)<100:continue
                dec=1+american/100 if american>0 else 1+100/abs(american)
                out[(str(ids[2]),books.get(ids[0]),namekey(fighter.get_text(' ',strip=True)))].add(dec)
    return out

def main():
    run=Path((ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').read_text().strip())
    d=sqlite3.connect(run/'source.sqlite3');d.row_factory=sqlite3.Row
    bouts={r['source_id']:dict(r) for r in d.execute("select * from bouts where source='wikipedia'")}
    quotes={r['rowid']:dict(r) for r in d.execute('select rowid,* from odds')}
    captures={r['url']:dict(r) for r in d.execute('select * from captures')}
    sample=json.loads((run/'priced_selections.json').read_text());ids={r['feature_bout_id'] for r in sample}
    master={}
    for line in (run/'boxing_prefight_master.jsonl').open():
        row=json.loads(line)
        if row['source_id'] in ids:master[row['source_id']]=row
    rawcache={};audit=[]
    for sel in sample:
        m=master[sel['feature_bout_id']];q=quotes[sel['quote_rowid']];issues=[];sidechecks={}
        for label in ('fighter','opponent'):
            side=m[label];inputs=[bouts.get(x) for x in side['input_bout_ids']]
            good=all(x and x['url']==side['id'] and x['date']<m['bout_date'] for x in inputs)
            unique=len({x['source_id'] for x in inputs if x})==len(inputs)
            rebuilt=summary(sorted([x for x in inputs if x],key=lambda x:(x['date'],x['source_id'])),m['bout_date'])
            equal=rebuilt==side['summary']
            sidechecks[label]={'strict_prior_dates_and_identity':good,'unique_inputs':unique,'summary_reproduces':equal}
            if not all((good,unique,equal)):issues.append(label+'_lineage_failed')
        source=bouts[sel['feature_bout_id']]
        if namekey(q['selection'])!=namekey(source['boxer_a']):issues.append('selection_identity_mismatch')
        if not math.isclose(q['decimal_price'],sel['price'],abs_tol=1e-10):issues.append('database_price_mismatch')
        expected=source['winner']=='BOXER A'
        if expected!=sel['win'] or source['winner'] not in ('BOXER A','BOXER B'):issues.append('result_mismatch')
        cap=captures.get(q['url']);rawstatus='missing_capture';fetch=None
        if cap:
            fetch=cap['fetched_at']
            if q['url'] not in rawcache:
                path=ROOT/cap['path']
                if path.exists():
                    blob=gzip.decompress(path.read_bytes()) if path.suffix=='.gz' else path.read_bytes()
                    rawcache[q['url']]=(hashlib.sha256(blob).hexdigest()==cap['sha256'],parse_raw(blob))
                else:rawcache[q['url']]=(False,{})
            hashok,parsed=rawcache[q['url']]
            vals=parsed.get((q['bout_id'],q['bookmaker'],namekey(q['selection'])),set())
            rawstatus='matched' if hashok and len(vals)==1 and math.isclose(next(iter(vals)),sel['price'],abs_tol=1e-10) else 'hash_or_quote_mismatch'
        if rawstatus!='matched':issues.append(rawstatus)
        # Fetch time is NOT quote time. Unknown settlement never upgraded by matching a display.
        audit.append({**sel,'source_url':q['url'],'capture_fetched_at':fetch,'quoted_at':q['quoted_at'],
          'timestamp_quality':q['timestamp_quality'],'raw_display_audit':rawstatus,'lineage':sidechecks,
          'structural_pass':not issues,'validated_price_pass':False,'issues':issues})
    (run/'sample_audit.json').write_text(json.dumps(audit,indent=2))
    # Both sides of a selected two-sided bout must pass before retaining it.
    bad={tuple(r['key']) for r in audit if not r['structural_pass']}
    passed=[r for r in audit if tuple(r['key']) not in bad]
    from scan_chronological_master import metrics
    age=[r for r in passed if r['favorite'] and r['price']>=1.2 and r['younger_by'] is not None and r['younger_by']>=3]
    report={'selection_rows':len(audit),'bouts':len({tuple(r['key']) for r in audit}),
      'structural_pass_bouts':len({tuple(r['key']) for r in passed}),'strict_price_validated_bouts':0,
      'issues':dict(collections.Counter(x for r in audit for x in r['issues'])),
      'timestamp_quality':dict(collections.Counter(r['timestamp_quality'] for r in audit)),
      'younger_favorite_fixed_rule_by_year':{str(y):metrics([r for r in age if r['year']==y]) for y in range(2021,2027)},
      'limitations':['Raw display reproduction is not independent bookmaker or pre-event timestamp verification.',
      'Zero strict validated bets; all ROI below uses unverified archived display prices.',
      'Fixed age rule earlier discovery years remain retrospective. No new loss-based filters selected.']}
    (run/'sample_audit_summary.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
if __name__=='__main__':main()
