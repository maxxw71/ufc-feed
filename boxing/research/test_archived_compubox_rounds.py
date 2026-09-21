import sqlite3
import unittest

from import_archived_compubox_rounds import parse_candidate

SAMPLE={
 'tables':[[[
   'CompuBox PunchStat Report Floyd Mayweather W 3 Juan Manuel Marquez 09/19/2009 LAS VEGAS '
   'Total Punches Landed / Thrown Round 1 2 3 Mayweather 18/31 18/26 15/31 58% 69% 48% '
   'Marquez 4/52 4/34 7/41 8% 12% 17% '
   'Jabs Landed / Thrown Round 1 2 3 Mayweather 14/20 9/14 11/23 70% 64% 48% '
   'Marquez 2/33 3/21 2/21 6% 14% 10% '
   'Power Punches Landed / Thrown Round 1 2 3 Mayweather 4/11 9/12 4/8 36% 75% 50% '
   'Marquez 2/19 1/13 5/20 11% 8% 25% Final PunchStat Report'
 ]]]
}

class ArchivedCompuboxTests(unittest.TestCase):
    def db(self):
        d=sqlite3.connect(':memory:')
        d.row_factory=sqlite3.Row
        d.execute('create table bouts(date text,status text,boxer_a text,boxer_b text,source text,source_id text)')
        d.execute("insert into bouts values('2009-09-19','FINISHED','Floyd Mayweather Jr','Juan Manuel Marquez','test','1')")
        return d

    def test_strict_full_round_accept(self):
        d=self.db()
        out,err=parse_candidate(d,'https://web.archive.org/web/2011/http://compuboxonline.com/news.php?news_id=9',SAMPLE)
        self.assertIsNone(err)
        self.assertEqual('2009-09-19',out['date'])
        self.assertEqual(3,out['rounds'])
        self.assertEqual({'Floyd Mayweather Jr','Juan Manuel Marquez'},set(out['fighters']))

    def test_arithmetic_mismatch_rejected(self):
        d=self.db()
        bad={'tables':[[[SAMPLE['tables'][0][0][0].replace('4/11','5/11')]]]}
        out,err=parse_candidate(d,'https://web.archive.org/web/2011/http://compuboxonline.com/news.php?news_id=9',bad)
        self.assertIsNone(out)
        self.assertIn('arithmetic mismatch',err)

if __name__=='__main__':
    unittest.main()
