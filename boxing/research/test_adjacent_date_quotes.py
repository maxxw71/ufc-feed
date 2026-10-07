import copy, datetime as dt, json, unittest
from adjacent_date_quotes import archive_times, matches_bout

class AdjacentDateQuotes(unittest.TestCase):
    def setUp(self):
        self.b = dict(source_id='fighter#1', date='2021-10-23', boxer_a='Fighter A', winner='BOXER A')
        self.q = dict(feature_bout_id='fighter#1', event_date='2021-10-24', match_method='exact_pair_adjacent_date',
                      date_offset_days=-1, selection='Fighter A', result='WIN', odds_bout_id='123', bookmaker='Book',
                      decimal_price=1.5, result_sources_json=json.dumps([dict(id='fighter#1', result_date='2021-10-23')]))
        self.e = dict(event_date='2021-10-24', bout_id='123', bookmaker='Book', selection='Fighter A',
                      stored_decimal_price=1.5, snapshot_timestamp='20211020120000')
    def test_verified_recovery_preserves_original_signature_fields(self):
        original=copy.deepcopy(self.q)
        self.assertTrue(matches_bout(self.q,self.b,archive_times([self.e])))
        self.assertEqual(self.q,original)
    def test_missing_or_late_timing_is_rejected(self):
        self.assertFalse(matches_bout(self.q,self.b,{}))
        for stamp in ['20211022100000','20211023000000','20211024000000','bad']:
            self.e['snapshot_timestamp']=stamp
            self.assertFalse(matches_bout(self.q,self.b,archive_times([self.e])))
    def test_wrong_identity_price_result_offset_or_provenance_rejected(self):
        for key,value in [('feature_bout_id','other'),('selection','Fighter B'),('decimal_price',2.0),
                          ('result','LOSS'),('date_offset_days',1),('match_method','guessed'),
                          ('result_sources_json','[]')]:
            changed={**self.q,key:value}
            self.assertFalse(matches_bout(changed,self.b,archive_times([self.e])),key)
    def test_positive_offset_and_exact_date(self):
        self.b['date']='2021-10-25';self.q['date_offset_days']=1
        self.q['result_sources_json']=json.dumps([dict(id='fighter#1',result_date='2021-10-25')])
        self.assertTrue(matches_bout(self.q,self.b,archive_times([self.e])))
        self.q['event_date']=self.b['date']
        self.assertTrue(matches_bout(self.q,self.b,{}))
