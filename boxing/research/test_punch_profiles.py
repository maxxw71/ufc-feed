import sqlite3
import unittest

from build_punch_profiles import load_reports, fight_observations, pre_fight_profiles


class PunchProfileIntegrityTests(unittest.TestCase):
    def db(self):
        d=sqlite3.connect(':memory:')
        d.execute('CREATE TABLE punch_reports(url TEXT PRIMARY KEY,bout_date TEXT,title TEXT,status TEXT)')
        d.execute('CREATE TABLE round_punches(report_url TEXT,fighter_label TEXT,round INTEGER,category TEXT,landed INTEGER,thrown INTEGER)')
        d.execute('CREATE TABLE fight_punch_totals(report_url TEXT,fighter_label TEXT,category TEXT,landed INTEGER,body_landed INTEGER,thrown INTEGER)')
        return d

    def add_report(self,d,url,date,title,a,b,a_pair=(5,10),b_pair=(3,10)):
        d.execute('INSERT INTO punch_reports VALUES(?,?,?,?)',(url,date,title,'parsed'))
        d.execute('INSERT INTO round_punches VALUES(?,?,?,?,?,?)',(url,a,1,'total',a_pair[0],a_pair[1]))
        d.execute('INSERT INTO round_punches VALUES(?,?,?,?,?,?)',(url,b,1,'total',b_pair[0],b_pair[1]))

    def test_duplicate_hosts_collapse_and_full_names_resolve(self):
        d=self.db()
        self.add_report(d,'https://app2.compuboxdata.com/round-stats/100','2026-01-01','ALPHA ONE UD 4 BRAVO TWO','ONE','TWO')
        self.add_report(d,'https://beta.compuboxdata.com/round-stats/100','2026-01-01','ALPHA ONE UD 4 BRAVO TWO','ONE','TWO')
        self.add_report(d,'https://beta.compuboxdata.com/round-stats/101','2026-02-01','ALPHA ONE UD 4 CHARLIE THREE','ONE','THREE')
        reports=load_reports(d)
        self.assertEqual(2,len(reports))
        first=reports[0]
        self.assertEqual('100',first['report_id'])
        self.assertEqual(2,first['variant_count'])
        self.assertEqual('ALPHA ONE',first['identity_map']['ONE'])
        self.assertEqual('BRAVO TWO',first['identity_map']['TWO'])

        obs=fight_observations(reports)
        alpha=[r for r in obs if r.get('fighter_full_name')=='ALPHA ONE']
        self.assertEqual(2,len(alpha))
        snaps=pre_fight_profiles(obs)
        alpha_snaps=sorted([r for r in snaps if r.get('fighter_full_name')=='ALPHA ONE'],key=lambda r:r['bout_date'])
        self.assertEqual([0,1],[r['prior_punch_fights'] for r in alpha_snaps])
        self.assertEqual('2026-02-01',alpha_snaps[1]['bout_date'])

    def test_unresolved_title_identity_does_not_create_crossfight_key(self):
        d=self.db()
        self.add_report(d,'https://beta.compuboxdata.com/round-stats/200','2026-03-01','UNPARSEABLE HEADER','ONE','TWO')
        reports=load_reports(d)
        self.assertEqual(1,len(reports))
        self.assertEqual({},reports[0]['identity_map'])
        obs=fight_observations(reports)
        self.assertTrue(all(r['fighter_key'] is None for r in obs))
        self.assertEqual([],pre_fight_profiles(obs))


if __name__=='__main__':
    unittest.main()
