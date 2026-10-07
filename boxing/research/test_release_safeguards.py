import sqlite3,unittest,tempfile,pathlib,json
from unittest.mock import patch
import apply_supplemental_careers as supplements
from market_consensus import canonical_market_rows,quote_signature,load_master
from audit_prospective_odds import result_diagnostics,result_from_supplements

def fixture():
    def side(fid):return {'id':fid,'record_totals_match':True,'summary':{'observed_prior_bouts':8}}
    rows=[]
    for fid,other,win,price in [('A','B','BOXER A',1.5),('B','A','BOXER B',3.0)]:
        rows.append({'source_id':fid+'-bout','bout_date':'2025-01-01','canonical_verified_pair':True,
            'fighter':side(fid),'opponent':side(other),'fighter_name':fid,'outcome':{'result':win},
            'quotes':[{'event_date':'2025-01-01','odds_bout_id':'123','bookmaker':'Book','selection':fid,'decimal_price':price,'result':'WIN' if fid=='A' else 'LOSS'}]})
    return rows

class Gates(unittest.TestCase):
    def test_refresh_reordering_does_not_duplicate_career_bouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);db=root/'boxing.sqlite3';sup=root/'supp.jsonl'
            con=sqlite3.connect(db)
            con.executescript('''CREATE TABLE fighters(source,source_id,name,born,height_cm,snapshot,PRIMARY KEY(source,source_id));
                CREATE TABLE normalized_fighters(source_id PRIMARY KEY,name,born,height_cm,reach_cm,stance,nationality,weight_class_snapshot,source_url,quality);
                CREATE TABLE bouts(source,source_id,date,boxer_a,boxer_b,winner,method,rounds,scheduled_rounds,division,venue,status,url,data,PRIMARY KEY(source,source_id));
                CREATE TABLE priced_bout_research(quote_rowid,selection,odds_bout_id,event_date,feature_bout_id,result);''')
            con.close()
            item={'requested_name':'A','source_url':'https://example.org/A','career_rows':[
                {'date':'2024-01-01','opponent':'B','result':'win'},
                {'date':'2024-06-01','opponent':'C','result':'loss'}]}
            with patch.multiple(supplements,DB=db,SUP=sup,REPORT=root/'report.json'):
                sup.write_text(json.dumps(item));supplements.main()
                item['career_rows'].reverse();sup.write_text(json.dumps(item));supplements.main()
            con=sqlite3.connect(db);self.assertEqual(con.execute('select count(*) from bouts').fetchone()[0],2)
            self.assertEqual(json.loads((root/'report.json').read_text())['reused_existing_bouts'],2)
    def test_streaming_preserves_unpriced_duplicate_perspective(self):
        rows=fixture();duplicate=dict(rows[0],source_id='A-duplicate',quotes=[]);rows.append(duplicate)
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)/'master.jsonl';p.write_text(''.join(json.dumps(x)+'\n' for x in rows))
            self.assertEqual(canonical_market_rows(load_master(p),min_books=1),canonical_market_rows(rows,min_books=1))
            self.assertEqual(len(load_master(p)),3)
    def test_audit_preserves_eligible_output(self):
        rows=fixture();audit=[]
        self.assertEqual(canonical_market_rows(rows,min_books=1),canonical_market_rows(rows,min_books=1,audit=audit))
        self.assertEqual(len(audit),1);self.assertTrue(audit[0]['eligible'])
    def test_one_verified_side_cannot_form_book(self):
        rows=fixture();audit=[];allowed={quote_signature(rows[0]['quotes'][0])}
        self.assertEqual(canonical_market_rows(rows,min_books=1,allowed_quote_signatures=allowed,audit=audit),[])
        self.assertIn('insufficient_verified_two_sided_books',audit[0]['reasons'])
    def test_all_history_failures_reported(self):
        rows=fixture();rows[0]['fighter']['record_totals_match']=False;rows[0]['fighter']['summary']['observed_prior_bouts']=2
        audit=[];self.assertEqual(canonical_market_rows(rows,min_books=1,audit=audit),[])
        self.assertIn('prior_record_incomplete_or_inconsistent',audit[0]['reasons'])
        self.assertIn('fewer_than_five_prior_bouts',audit[0]['reasons'])
    def test_conflicting_results_block_automatic_settlement(self):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        db.execute('create table bouts(source,source_id,date,boxer_a,boxer_b,winner,status,url)')
        db.executemany('insert into bouts values (?,?,?,?,?,?,?,?)',[
            ('a','1','2025-01-01','A','B','BOXER A','FINISHED','https://a.example'),
            ('b','2','2025-01-01','A','B','BOXER B','FINISHED','https://b.example')])
        result=result_diagnostics(db,'2025-01-01',['A','B'])
        self.assertTrue(result['automatic_settlement_blocked']);self.assertEqual(result['review_reason'],'conflicting_exact_pair_results')
    def test_same_publisher_does_not_count_twice(self):
        r={'event_date':'2025-01-01','participants':['A','B'],'winner':'A','sources':['https://a.example/one','https://www.a.example/two']}
        self.assertIsNone(result_from_supplements([r],'2025-01-01',['A','B']))
        r['sources'][1]='https://b.example/result'
        self.assertEqual(result_from_supplements([r],'2025-01-01',['A','B'])['winner'],'A')

if __name__=='__main__':unittest.main()
