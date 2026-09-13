"""Join actual archived selections to result consensus without fuzzy guessing.

This builds research rows, not validated recommendations. No timing/settlement
assumptions are silently applied. Conflicts stay excluded. A one-day event-date
recovery is allowed only when the exact normalized fighter pair exists on one
and only one adjacent calendar date; recovered rows are explicitly labeled.
"""
import collections,datetime as dt,json,sqlite3
from pathlib import Path
from features import namekey
ROOT=Path(__file__).resolve().parent


def shifted(date,days):
    try:return (dt.date.fromisoformat(date)+dt.timedelta(days=days)).isoformat()
    except Exception:return None


def main():
    db=sqlite3.connect(ROOT/'boxing.sqlite3',timeout=120);db.row_factory=sqlite3.Row
    index=collections.defaultdict(list)
    for row in db.execute("SELECT * FROM bouts WHERE status='FINISHED' AND date>='1990-01-01'").fetchall():
        index[(row['date'],*sorted([namekey(row['boxer_a']),namekey(row['boxer_b'])]))].append(dict(row))
    # Accepted career sources reconstruct pre-fight histories directly from their
    # source-specific bout rows; provenance remains separate in the database.
    eligible_bout_sources={'wikipedia','champinon','wba_consensus'}
    matchups={r['source_id']:json.loads(r['data']) for r in db.execute("SELECT source_id,data FROM source_rows WHERE source='proboxingodds' AND kind='matchup'").fetchall()}
    db.execute('''CREATE TABLE IF NOT EXISTS priced_bout_research(
        quote_rowid INTEGER PRIMARY KEY,odds_bout_id TEXT,event_date TEXT,bookmaker TEXT,
        selection TEXT,decimal_price REAL,result TEXT,decisive_profit_per_unit REAL,
        feature_bout_id TEXT,result_sources_json TEXT,readiness TEXT)''')
    cols={r[1] for r in db.execute('PRAGMA table_info(priced_bout_research)')}
    if 'match_method' not in cols:db.execute('ALTER TABLE priced_bout_research ADD COLUMN match_method TEXT')
    if 'date_offset_days' not in cols:db.execute('ALTER TABLE priced_bout_research ADD COLUMN date_offset_days INTEGER')
    quotes=db.execute("SELECT rowid AS quote_rowid,* FROM odds WHERE source='proboxingodds'").fetchall()
    db.execute('DELETE FROM priced_bout_research')
    counts=collections.Counter();years=collections.Counter();linked_bouts=set()
    for quote in quotes:
        meta=matchups.get(quote['bout_id'],{});names=list(meta.get('participants',{}).values());date=meta.get('event_date')
        if len(names)!=2 or not date:counts['missing_matchup_metadata']+=1;continue
        pair=tuple(sorted(map(namekey,names)));selection=namekey(quote['selection'])
        if selection not in set(map(namekey,names)):counts['invalid_selection_or_result']+=1;continue
        candidates=index[(date,*pair)];match_method='exact_date_pair';offset=0;result_date=date
        if not candidates:
            nearby=[]
            for off in (-1,1):
                d=shifted(date,off)
                if d:
                    c=index[(d,*pair)]
                    if c:nearby.append((off,d,c))
            if len(nearby)==1:
                offset,result_date,candidates=nearby[0]
                match_method='exact_pair_adjacent_date'
            else:
                counts['no_exact_date_and_pair_match']+=1
                if len(nearby)>1:counts['ambiguous_adjacent_date_pair']+=1
                continue
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
        own=[r for r in candidates if r.get('source') in eligible_bout_sources and namekey(r['boxer_a'])==selection]
        ownids=sorted({r['source_id'] for r in own})
        ownid=ownids[0] if len(ownids)==1 else None
        if len(ownids)>1:counts['ambiguous_fighter_orientation']+=1
        readiness='exploratory_only_quote_time_unverified' if result in ['WIN','LOSS'] else 'excluded_draw_nc_settlement_unknown'
        db.execute('''INSERT OR REPLACE INTO priced_bout_research(
            quote_rowid,odds_bout_id,event_date,bookmaker,selection,decimal_price,result,
            decisive_profit_per_unit,feature_bout_id,result_sources_json,readiness,match_method,date_offset_days)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (quote['quote_rowid'],quote['bout_id'],date,quote['bookmaker'],quote['selection'],quote['decimal_price'],
             result,profit,ownid,json.dumps([{'source':r['source'],'id':r['source_id'],'result_date':result_date} for r in candidates]),
             readiness,match_method,offset))
        counts['linked_quotes']+=1
        counts['linked_quotes_exact_date']+=match_method=='exact_date_pair'
        counts['linked_quotes_adjacent_date']+=match_method=='exact_pair_adjacent_date'
        counts['linked_quotes_with_fighter_features']+=bool(ownid)
        counts['draw_or_nc_quotes']+=result in ['DRAW','NO CONTEST']
        years[date[:4]]+=1;linked_bouts.add(quote['bout_id'])
    db.commit()
    report={'counts':dict(counts),'distinct_result_linked_bouts':len(linked_bouts),'linked_quotes_by_year':dict(sorted(years.items())),
            'limitations':['Exact normalized pair required; no fuzzy-name matching is used.',
                           'Adjacent-date recovery is allowed only when the exact pair occurs on exactly one of event_date +/- 1 day.',
                           'Same bout may have many bookmakers and two selections; these are not independent bets.',
                           'Result agreement is not independent archival odds verification.',
                           'Per-unit decisive result payoff is arithmetic, not a strategy ROI.',
                           'All quotes remain unverified in timing; no rows certified for prospective-valid backtesting.']}
    (ROOT/'PRICE_MATCH_COVERAGE.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
