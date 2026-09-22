import sqlite3
import unittest

from backfill_wba_consensus_careers import result_index,resolve_career


class WBAConsensusCareerTests(unittest.TestCase):
    def db(self):
        d=sqlite3.connect(':memory:')
        d.row_factory=sqlite3.Row
        d.execute('''create table bouts(
            source text,source_id text,date text,boxer_a text,boxer_b text,
            winner text,method text,rounds text,venue text,status text,data text
        )''')
        return d

    def test_partial_wba_list_reconstructed_from_independent_graph(self):
        d=self.db()
        # Target Alpha has two career bouts, but WBA page lists only the first.
        d.execute("insert into bouts values(?,?,?,?,?,?,?,?,?,?,?)",
                  ('wikipedia','b1','2024-01-01','Opponent One','Alpha Boxer','BOXER B','UD','6','X','FINISHED','{}'))
        d.execute("insert into bouts values(?,?,?,?,?,?,?,?,?,?,?)",
                  ('champinon','b2','2024-02-01','Opponent Two','Alpha Boxer','BOXER A','KO','2','Y','FINISHED','{}'))
        idx=result_index(d)
        page={
          'stated_record':(1,1,0),
          'rows':[{'date':'2024-01-01','opponent':'Opponent One','type':'UD','round_time':'6','location':'X','raw':{}}],
          'wba_row_count':1,'wba_rows_complete':False
        }
        rows,recon=resolve_career('Alpha Boxer',page,idx,d)
        self.assertEqual(2,len(rows))
        self.assertEqual(['win','loss'],[r['result'] for r in rows])
        self.assertEqual(1,recon['reconstructed_missing_wba_rows'])

    def test_conflicting_independent_result_rejected(self):
        d=self.db()
        d.execute("insert into bouts values(?,?,?,?,?,?,?,?,?,?,?)",
                  ('wikipedia','b1','2024-01-01','Opponent One','Alpha Boxer','BOXER B','UD','6','X','FINISHED','{}'))
        d.execute("insert into bouts values(?,?,?,?,?,?,?,?,?,?,?)",
                  ('champinon','b2','2024-01-01','Opponent One','Alpha Boxer','BOXER A','UD','6','X','FINISHED','{}'))
        idx=result_index(d)
        page={
          'stated_record':(1,0,0),
          'rows':[{'date':'2024-01-01','opponent':'Opponent One','type':'UD','round_time':'6','location':'X','raw':{}}],
          'wba_row_count':1,'wba_rows_complete':True
        }
        with self.assertRaises(ValueError):
            resolve_career('Alpha Boxer',page,idx,d)


if __name__=='__main__':
    unittest.main()
