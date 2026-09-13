import unittest
from backfill_priced_careers import match_evidence

class PricedCareerEvidence(unittest.TestCase):
    def test_exact_date_opponent_accepts(self):
        page={'rows':[{'date':'2024-05-04','opponent':'John Smith'}]}
        ev=[{'date':'2024-05-04','opponent':'John Smith','odds_bout_id':'1'}]
        self.assertEqual(len(match_evidence(page,ev)),1)
    def test_wrong_date_or_opponent_rejects(self):
        page={'rows':[{'date':'2024-05-04','opponent':'John Smith'}]}
        ev=[{'date':'2024-05-05','opponent':'John Smith','odds_bout_id':'1'},
            {'date':'2024-05-04','opponent':'Jane Smith','odds_bout_id':'2'}]
        self.assertEqual(match_evidence(page,ev),[])
    def test_diacritic_and_punctuation_normalization(self):
        page={'rows':[{'date':'2024-05-04','opponent':'José Pérez Jr.'}]}
        ev=[{'date':'2024-05-04','opponent':'Jose Perez Jr','odds_bout_id':'1'}]
        self.assertEqual(len(match_evidence(page,ev)),1)

if __name__=='__main__':unittest.main()
