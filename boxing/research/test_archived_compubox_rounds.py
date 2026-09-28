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
        d.execute('create table bouts(date text,status text,boxer_a text,boxer_b text,source text,source_id text,method text,rounds text)')
        d.execute("insert into bouts values('2009-09-19','FINISHED','Floyd Mayweather Jr','Juan Manuel Marquez','test','1','UD','12')")
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

    def test_full_date_prefix_header_accept(self):
        d=self.db()
        d.execute("insert into bouts values('2008-05-03','FINISHED','Oscar De La Hoya','Steve Forbes','test','2','UD','12')")
        payload={'tables':[[[
          '5/3/08 - Carson, CA Oscar De La Hoya W 12 Steve Forbes '
          'Total Punches Landed/Thrown Round 1 2 3 4 5 6 7 8 9 10 11 12 '
          'Hoya 1/2 1/2 1/2 1/2 1/2 1/2 1/2 1/2 1/2 1/2 1/2 1/2 '
          'Forbes 1/3 1/3 1/3 1/3 1/3 1/3 1/3 1/3 1/3 1/3 1/3 1/3 '
          'Total Jabs Thrown/Landed Round 1 2 3 4 5 6 7 8 9 10 11 12 '
          'Hoya 1/1 1/1 1/1 1/1 1/1 1/1 1/1 1/1 1/1 1/1 1/1 1/1 '
          'Forbes 0/1 0/1 0/1 0/1 0/1 0/1 0/1 0/1 0/1 0/1 0/1 0/1 '
          'Total Power Punches Landed/Thrown Round 1 2 3 4 5 6 7 8 9 10 11 12 '
          'Hoya 0/1 0/1 0/1 0/1 0/1 0/1 0/1 0/1 0/1 0/1 0/1 0/1 '
          'Forbes 1/2 1/2 1/2 1/2 1/2 1/2 1/2 1/2 1/2 1/2 1/2 1/2 Final Punch Stats'
        ]]]}
        out,err=parse_candidate(d,'https://web.archive.org/web/20080704/http://compuboxonline.com/stat_files/delahoya-forbes.html',payload)
        self.assertIsNone(err)
        self.assertEqual('2008-05-03',out['date'])
        self.assertEqual(12,out['rounds'])

    def test_trailing_zero_category_cell_recovered(self):
        d=self.db()
        d.execute("insert into bouts values('2008-12-13','FINISHED','Wladimir Klitschko','Hasim Rahman','test','3','TKO','7')")
        payload={'tables':[[[
          'PunchStat Report Wladimir Klitschko vs Hasim Rahman 12/13/2008 '
          'Total Punches Landed / Thrown Round 1 2 3 4 5 6 7 '
          'Klitschko 2/4 2/4 2/4 2/4 2/4 2/4 1/2 '
          'Rahman 1/3 1/3 1/3 1/3 1/3 1/3 0/5 '
          'Jabs Landed / Thrown Round 1 2 3 4 5 6 7 '
          'Klitschko 1/2 1/2 1/2 1/2 1/2 1/2 1/1 '
          'Rahman 1/2 1/2 1/2 1/2 1/2 1/2 0/5 '
          'Power Punches Landed / Thrown Round 1 2 3 4 5 6 7 '
          'Klitschko 1/2 1/2 1/2 1/2 1/2 1/2 0/1 '
          'Rahman 0/1 0/1 0/1 0/1 0/1 0/1 Final PunchStat Report'
        ]]]}
        out,err=parse_candidate(d,'https://web.archive.org/web/20090131/http://compuboxonline.com/stat_files/KLI-RAH.htm',payload)
        self.assertIsNone(err)
        self.assertEqual((0,0),out['cats']['power']['Hasim Rahman'][-1])

    def test_nonzero_missing_category_cell_still_rejected(self):
        d=self.db()
        d.execute("insert into bouts values('2008-12-13','FINISHED','Wladimir Klitschko','Hasim Rahman','test','4','TKO','2')")
        payload={'tables':[[[
          'PunchStat Report Wladimir Klitschko vs Hasim Rahman 12/13/2008 '
          'Total Punches Landed / Thrown Round 1 2 Klitschko 2/4 2/4 Rahman 1/3 1/3 '
          'Jabs Landed / Thrown Round 1 2 Klitschko 1/2 1/2 Rahman 1/2 1/2 '
          'Power Punches Landed / Thrown Round 1 2 Klitschko 1/2 1/2 Rahman 0/1 Final PunchStat Report'
        ]]]}
        out,err=parse_candidate(d,'https://web.archive.org/web/20090131/http://compuboxonline.com/stat_files/KLI-RAH.htm',payload)
        self.assertIsNone(out)
        self.assertIn('missing power round row',err)

if __name__=='__main__':
    unittest.main()
