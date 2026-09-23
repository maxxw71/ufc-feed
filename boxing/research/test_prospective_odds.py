import unittest
from audit_prospective_odds import result_from_supplements

class ProspectiveResultSupplementTests(unittest.TestCase):
    def test_two_source_exact_pair_is_accepted(self):
        records=[{
          'event_date':'2026-09-21',
          'participants':['David Ssemujju','Jin Sasaki'],
          'winner':'Jin Sasaki',
          'method':'KO','round':6,'time':'0:58',
          'sources':['https://source-a.example/result','https://source-b.example/result']
        }]
        out=result_from_supplements(records,'2026-09-21',['Jin Sasaki','David Ssemujju'])
        self.assertEqual(out['winner'],'Jin Sasaki')
        self.assertEqual(out['result_round'],6)
        self.assertEqual(len(out['result_sources']),2)

    def test_one_source_is_rejected(self):
        records=[{
          'event_date':'2026-09-21',
          'participants':['David Ssemujju','Jin Sasaki'],
          'winner':'Jin Sasaki','sources':['https://source-a.example/result']
        }]
        self.assertIsNone(result_from_supplements(records,'2026-09-21',['Jin Sasaki','David Ssemujju']))

    def test_wrong_pair_is_rejected(self):
        records=[{
          'event_date':'2026-09-21',
          'participants':['Other Fighter','Jin Sasaki'],
          'winner':'Jin Sasaki',
          'sources':['https://source-a.example/result','https://source-b.example/result']
        }]
        self.assertIsNone(result_from_supplements(records,'2026-09-21',['Jin Sasaki','David Ssemujju']))

if __name__=='__main__':
    unittest.main()
