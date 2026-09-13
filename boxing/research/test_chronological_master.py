import unittest
from build_chronological_master import elo_before, canonical_events
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
if __name__=='__main__':unittest.main()
