import datetime as dt
import json
import re
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'rankings'

class RankingArchiveIntegrityTests(unittest.TestCase):
    def load(self,name):
        p=ROOT/name
        self.assertTrue(p.exists(),str(p))
        return json.loads(p.read_text())

    def validate_rows(self,rows,body,max_rank):
        self.assertGreater(len(rows),100)
        slots=set()
        for r in rows:
            self.assertIn('name',r)
            name=str(r['name']).strip()
            self.assertGreaterEqual(len(name),2)
            self.assertLessEqual(len(name),100)
            self.assertFalse(re.fullmatch(r'[\d .-]+',name))
            rank=int(r['rank'])
            self.assertGreaterEqual(rank,1);self.assertLessEqual(rank,max_rank)
            eff=r.get('safe_effective_date')
            self.assertIsNotNone(eff)
            dt.date.fromisoformat(eff)
            div=str(r.get('division') or '').strip()
            self.assertTrue(div)
            # At a body/date/division snapshot, a numerical rank slot can only
            # belong to one contender.
            key=(body,eff,div,rank)
            self.assertNotIn(key,slots,key)
            slots.add(key)

    def test_wba_rankings(self):
        rows=self.load('wba_monthly_rankings.json')
        self.validate_rows(rows,'WBA',15)
        meta=self.load('wba_monthly_rankings_meta.json')
        self.assertEqual(len(rows),meta['ranking_rows'])
        self.assertGreaterEqual(meta['usable_documents'],50)

    def test_wbc_rankings(self):
        rows=self.load('wbc_monthly_rankings.json')
        self.validate_rows(rows,'WBC',40)
        meta=self.load('wbc_monthly_rankings_meta.json')
        self.assertEqual(len(rows),meta['ranking_rows'])
        self.assertGreaterEqual(meta['parsed_documents'],10)

    def test_wbo_rankings(self):
        rows=self.load('wbo_monthly_rankings.json')
        self.validate_rows(rows,'WBO',15)
        meta=self.load('wbo_monthly_rankings_meta.json')
        self.assertEqual(len(rows),meta['ranking_rows'])
        self.assertGreaterEqual(meta['parsed_documents'],20)
        self.assertEqual(meta.get('quarantined_conflicting_rank_slots'),0)

    def test_ibf_rankings(self):
        rows=self.load('ibf_monthly_rankings.json')
        self.validate_rows(rows,'IBF',15)
        meta=self.load('ibf_monthly_rankings_meta.json')
        self.assertEqual(len(rows),meta['ranking_rows'])
        self.assertGreaterEqual(meta['rating_periods'],100)
        self.assertEqual(meta.get('quarantined_conflicting_rank_slots'),0)

if __name__=='__main__':
    unittest.main()
