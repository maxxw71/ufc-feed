"""Join actual archived selections to result consensus without fuzzy guessing.

This builds research rows, not validated recommendations. No timing/settlement
assumptions are silently applied. Conflicts and date mismatches stay excluded.
"""
import collections,json,sqlite3
from pathlib import Path
from features import namekey
ROOT=Path(__file__).resolve().parent
def main():
    db=sqlite3.connect(ROOT/'boxing.sqlite3',timeout=120);db.row_factory=sqlite3.Row
    index=collections.defaultdict(list)
    for row in db.execute("SELECT * FROM bouts WHERE status='FINISHED' AND date>='1990-01-01'").fetchall():
        index[(row['date'],*sorted([namekey(row['boxer_a']),namekey(row['boxer_b'])]))].append(dict(row))
    features={r['source_id'] for r in db.execute('SELECT source_id FROM pre_bout_features').fetchall()}
    matchups={r['source_id']:json.loads(r['data']) for r in db.execute("SELECT source_id,data FROM source_rows WHERE source='proboxingodds' AND kind='matchup'").fetchall()}
    db.execute('''CREATE TABLE IF NOT EXISTS priced_bout_research(quote_rowid INTEGER PRIMARY KEY,odds_bout_id TEXT,event_date TEXT,bookmaker TEXT,selection TEXT,decimal_price REAL,result TEXT,decisive_profit_per_unit REAL,feature_bout_id TEXT,result_sources_json TEXT,readiness TEXT)''')
    quotes=db.execute("SELECT rowid AS quote_rowid,* FROM odds WHERE source='proboxingodds'").fetchall()
    db.execute('DELETE FROM priced_bout_research')
    counts=collections.Counter();years=collections.Counter();linked_bouts=set()
    for quote in quotes:
        meta=matchups.get(quote['bout_id'],{});names=list(meta.get('participants',{}).values());date=meta.get('event_date')
        if len(names)!=2 or not date:counts['missing_matchup_metadata']+=1;continue
        candidates=index[(date,*sorted(map(namekey,names)))];selection=namekey(quote['selection'])
        if selection not in set(map(namekey,names)):counts['invalid_selection_or_result']+=1;continue
        if not candidates:counts['no_exact_date_and_pair_match']+=1;continue
        outcomes=set()
        for r in candidates:
            outcome=r['winner']
            outcomes.add(namekey(r['boxer_a']) if outcome=='BOXER A' else namekey(r['boxer_b']) if outcome=='BOXER B' else outcome)
        if len(outcomes)!=1:counts['conflicting_results']+=1;continue
        outcome=next(iter(outcomes));profit=None
        if outcome in ['DRAW','NO CONTEST']:result=outcome
        elif outcome==selection:result='WIN';profit=quote['decimal_price']-1
        elif outcome in set(map(namekey,names)):result='LOSS';profit=-1
        else:counts['invalid_selection_or_result']+=1;continue
        own=[r for r in candidates if r['source_id'] in features and namekey(r['boxer_a'])==selection]
        ownid=own[0]['source_id'] if len(own)==1 else None
        readiness='exploratory_only_quote_time_unverified' if result in ['WIN','LOSS'] else 'excluded_draw_nc_settlement_unknown'
        db.execute('INSERT OR REPLACE INTO priced_bout_research VALUES(?,?,?,?,?,?,?,?,?,?,?)',(quote['quote_rowid'],quote['bout_id'],date,quote['bookmaker'],quote['selection'],quote['decimal_price'],result,profit,ownid,json.dumps([{'source':r['source'],'id':r['source_id']} for r in candidates]),readiness))
        counts['linked_quotes']+=1;counts['linked_quotes_with_fighter_features']+=bool(ownid);counts['draw_or_nc_quotes']+=result in ['DRAW','NO CONTEST'];years[date[:4]]+=1;linked_bouts.add(quote['bout_id'])
    db.commit()
    report={'counts':dict(counts),'distinct_result_linked_bouts':len(linked_bouts),'linked_quotes_by_year':dict(sorted(years.items())),'limitations':['Exact date and normalized pair only; timezone/date discrepancies remain unmatched.','Same bout may have many bookmakers and two selections; these are not independent bets.','Result agreement is not independent archival odds verification.','Per-unit decisive result payoff is arithmetic, not a strategy ROI.','All quotes remain unverified in timing; no rows certified for prospective-valid backtesting.']}
    (ROOT/'PRICE_MATCH_COVERAGE.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
