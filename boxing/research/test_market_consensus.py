import unittest
from market_consensus import canonical_market_rows


def row(fid,opp,name,oppname,result,quotes):
    return {
        'bout_date':'2025-01-01','canonical_verified_pair':True,
        'fighter_name':name,'opponent_name':oppname,
        'fighter':{'id':fid,'record_totals_match':True,'summary':{'observed_prior_bouts':8}},
        'opponent':{'id':opp,'record_totals_match':True,'summary':{'observed_prior_bouts':9}},
        'outcome':{'result':result},'quotes':quotes,
    }


def q(book,bout,price,result,rowid):
    return {'bookmaker':book,'odds_bout_id':bout,'decimal_price':price,'result':result,'quote_rowid':rowid}


class ConsensusMarket(unittest.TestCase):
    def test_median_no_vig_consensus_and_price_policy(self):
        a=row('a','b','A','B','BOXER A',[
            q('ZBook','9',1.60,'WIN',1),q('ABook','9',1.50,'WIN',2),
            # Duplicate same-side quote makes this bookmaker ambiguous and excluded.
            q('BadBook','9',1.55,'WIN',3),q('BadBook','9',1.57,'WIN',4)])
        b=row('b','a','B','A','BOXER B',[
            q('ZBook','9',2.50,'LOSS',5),q('ABook','9',2.70,'LOSS',6),q('BadBook','9',2.60,'LOSS',7)])
        rows=canonical_market_rows([b,a],min_books=2)
        self.assertEqual(len(rows),1)
        r=rows[0]
        self.assertEqual(r['favorite'],'A')
        self.assertEqual(r['book_count'],2)
        self.assertEqual(r['books'],['ABook','ZBook'])
        self.assertAlmostEqual(r['median_price'],1.55)
        self.assertAlmostEqual(r['best_price'],1.60)
        self.assertAlmostEqual(r['profit_median'],.55)
        self.assertTrue(.60<r['market_prob']<.65)

    def test_verified_quote_filter_requires_both_sides(self):
        a=row('a','b','A','B','BOXER A',[
            q('One','9',1.50,'WIN',1),q('Two','9',1.55,'WIN',2)])
        b=row('b','a','B','A','BOXER B',[
            q('One','9',2.70,'LOSS',3),q('Two','9',2.60,'LOSS',4)])
        rows=canonical_market_rows([a,b],min_books=1,allowed_quote_rowids={1,3})
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['books'],['One'])
        self.assertEqual(canonical_market_rows([a,b],min_books=2,allowed_quote_rowids={1,3}),[])
        self.assertEqual(canonical_market_rows([a,b],min_books=1,allowed_quote_rowids={1}),[])

    def test_requires_two_clean_books(self):
        a=row('a','b','A','B','BOXER A',[q('Only','9',1.5,'WIN',1)])
        b=row('b','a','B','A','BOXER B',[q('Only','9',2.7,'LOSS',2)])
        self.assertEqual(canonical_market_rows([a,b],min_books=2),[])

if __name__=='__main__':unittest.main()
