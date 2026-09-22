import unittest
from build_chronological_master import elo_before, canonical_events, compubox_prefight_baseline
class Chronology(unittest.TestCase):
    def test_same_day_frozen_and_future_invariant(self):
        events=[(('2020-01-01','a','b'),1.),(('2020-01-01','a','c'),1.)]
        targets={'2020-01-01':{'a','b','c'},'2020-01-02':{'a','b','c'}}
        before=elo_before(events,targets)
        self.assertEqual(before['2020-01-01','a'],(1500,0))
        self.assertEqual(before['2020-01-02','a'],(1532,2))
        after=elo_before(events+[(('2021-01-01','a','b'),0.)],targets)
        self.assertEqual(before,after)
    def test_reciprocal_and_conflict(self):
        a={'date':'2020-01-01','source_id':'a1','winner':'BOXER A'}
        b={'date':'2020-01-01','source_id':'b1','winner':'BOXER B'}
        h={'a':[a],'b':[b]};links={'a1':'b','b1':'a'}
        self.assertEqual(len(canonical_events(h,links)),1)
        b['winner']='BOXER A'
        self.assertEqual(canonical_events(h,links),[])
        b['winner']='BOXER B';h['a'].append(a.copy())
        self.assertEqual(canonical_events(h,links),[])
    def test_compubox_baseline_publication_gate(self):
        idx={'fighter':[
            {'available_from_date':'2020-01-02','direct_target_dates':[],'metric':1.0},
        ]}
        self.assertIsNone(compubox_prefight_baseline(idx,'Fighter','2020-01-01'))
        self.assertIsNone(compubox_prefight_baseline(idx,'Fighter','2020-01-02'))
        later=compubox_prefight_baseline(idx,'Fighter','2020-01-03')
        self.assertEqual(later['metric'],1.0)
        self.assertFalse(later['direct_target_match'])

    def test_compubox_direct_target_requires_prior_publication_date(self):
        idx={'fighter':[
            {'available_from_date':'2019-12-31','direct_target_dates':['2020-01-01'],'metric':2.0},
        ]}
        row=compubox_prefight_baseline(idx,'Fighter','2020-01-01')
        self.assertEqual(row['metric'],2.0)
        self.assertTrue(row['direct_target_match'])

if __name__=='__main__':unittest.main()
