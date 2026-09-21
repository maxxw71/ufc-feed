import unittest
from rebuild_identity_links import discover,ibf_bridge

class IdentityV2(unittest.TestCase):
    def test_unique_reciprocal_alias_link(self):
        rows=[
            {'source_id':'a1','url':'A','date':'2024-01-01','boxer_a':'Alpha','boxer_b':'Beta Jr.','winner':'BOXER A'},
            {'source_id':'b1','url':'B','date':'2024-01-01','boxer_a':'Beta Jr.','boxer_b':'Alpha','winner':'BOXER B'},
        ]
        add,diag=discover(rows,{'A':'Alpha','B':'Beta Jr.'},{})
        self.assertEqual(add,{'a1':'B','b1':'A'})
    def test_conflicting_result_rejected(self):
        rows=[
            {'source_id':'a1','url':'A','date':'2024-01-01','boxer_a':'Alpha','boxer_b':'Beta','winner':'BOXER A'},
            {'source_id':'b1','url':'B','date':'2024-01-01','boxer_a':'Beta','boxer_b':'Alpha','winner':'BOXER A'},
        ]
        add,_=discover(rows,{'A':'Alpha','B':'Beta'},{})
        self.assertEqual(add,{})
    def test_ambiguous_identity_rejected(self):
        rows=[
            {'source_id':'a1','url':'A','date':'2024-01-01','boxer_a':'Alpha','boxer_b':'Chris Lee','winner':'BOXER A'},
            {'source_id':'b1','url':'B','date':'2024-01-01','boxer_a':'Chris Lee','boxer_b':'Alpha','winner':'BOXER B'},
            {'source_id':'c1','url':'C','date':'2024-01-01','boxer_a':'Chris Lee','boxer_b':'Alpha','winner':'BOXER B'},
        ]
        add,diag=discover(rows,{'A':'Alpha','B':'Chris Lee','C':'Chris Lee'},{})
        self.assertNotIn('a1',add)
        self.assertGreaterEqual(diag['ambiguous'],1)

    def test_ibf_exact_pair_result_bridge(self):
        rows=[
            {'source_id':'a1','url':'A','date':'2024-02-01','boxer_a':'Alpha Smith','boxer_b':'Beta Jones','winner':'BOXER A'},
            {'source_id':'b-old','url':'B','date':'2023-01-01','boxer_a':'Beta Jones','boxer_b':'Other','winner':'BOXER A'},
        ]
        ibf=[{'date':'2024-02-01','fighter_a':'Alpha Smith','fighter_b':'Beta Jones','winner_side':'A'}]
        add,diag=ibf_bridge(rows,{'A':'Alpha Smith','B':'Beta Jones'},{},ibf)
        self.assertEqual(add,{'a1':'B'})
        self.assertEqual(diag['ibf_bridge_links'],1)

    def test_ibf_conflicting_result_rejected(self):
        rows=[
            {'source_id':'a1','url':'A','date':'2024-02-01','boxer_a':'Alpha Smith','boxer_b':'Beta Jones','winner':'BOXER A'},
            {'source_id':'b-old','url':'B','date':'2023-01-01','boxer_a':'Beta Jones','boxer_b':'Other','winner':'BOXER A'},
        ]
        ibf=[{'date':'2024-02-01','fighter_a':'Alpha Smith','fighter_b':'Beta Jones','winner_side':'B'}]
        add,_=ibf_bridge(rows,{'A':'Alpha Smith','B':'Beta Jones'},{},ibf)
        self.assertEqual(add,{})

if __name__=='__main__':unittest.main()
