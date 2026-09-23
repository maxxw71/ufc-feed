import sqlite3
import unittest

from build_punch_profiles import load_reports, fight_observations, pre_fight_profiles, merge_observation_tiers


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

    def test_round_level_preferred_over_total_supplement(self):
        base=[
          {'bout_date':'2024-01-01','fighter_key':'alpha','opponent_key':'beta','source_quality':'observed_round_table'},
          {'bout_date':'2024-01-01','fighter_key':'beta','opponent_key':'alpha','source_quality':'observed_round_table'},
        ]
        total=[
          {'bout_date':'2024-01-01','fighter_key':'alpha','opponent_key':'beta','source_quality':'structured_fight_total_table_ready_to_fight'},
          {'bout_date':'2024-01-01','fighter_key':'beta','opponent_key':'alpha','source_quality':'structured_fight_total_table_ready_to_fight'},
          {'bout_date':'2024-02-01','fighter_key':'alpha','opponent_key':'gamma','source_quality':'structured_fight_total_table_ready_to_fight'},
          {'bout_date':'2024-02-01','fighter_key':'gamma','opponent_key':'alpha','source_quality':'structured_fight_total_table_ready_to_fight'},
        ]
        merged,accepted,skipped=merge_observation_tiers(base,total)
        self.assertEqual(4,len(merged))
        self.assertEqual(2,accepted)
        self.assertEqual(2,skipped)

    def test_unresolved_title_identity_does_not_create_crossfight_key(self):
        d=self.db()
        self.add_report(d,'https://beta.compuboxdata.com/round-stats/200','2026-03-01','UNPARSEABLE HEADER','ONE','TWO')
        reports=load_reports(d)
        self.assertEqual(1,len(reports))
        self.assertEqual({},reports[0]['identity_map'])
        obs=fight_observations(reports)
        self.assertTrue(all(r['fighter_key'] is None for r in obs))
        self.assertEqual([],pre_fight_profiles(obs))



    def test_missing_body_count_stays_null(self):
        d=self.db()
        self.add_report(d,'https://web.archive.org/web/1/http://compuboxonline.com/a','2026-01-01','ALPHA ONE UD 1 BRAVO TWO','ONE','TWO')
        d.execute("insert into fight_punch_totals values(?,?,?,?,?,?)",
                  ('https://web.archive.org/web/1/http://compuboxonline.com/a','ONE','total',5,None,10))
        reports=load_reports(d)
        obs=fight_observations(reports)
        one=next(r for r in obs if r['fighter_label']=='ONE')
        self.assertIsNone(one['body_landed'])
        self.assertIsNone(one['body_landed_share_pct'])


    def test_verified_archive_full_labels_resolve(self):
        d=self.db()
        self.add_report(
            d,
            'https://web.archive.org/web/1/http://compuboxonline.com/a',
            '2009-09-19',
            'Floyd Mayweather Jr. vs Juan Manuel Marquez archived CompuBox',
            'Floyd Mayweather Jr.','Juan Manuel Marquez'
        )
        reports=load_reports(d)
        self.assertEqual(1,len(reports))
        obs=fight_observations(reports)
        self.assertEqual(
            {'floydmayweatherjr','juanmanuelmarquez'},
            {r['fighter_key'] for r in obs}
        )
        self.assertTrue(all(r['identity_quality']=='report_title_full_name_suffix_match' for r in obs))

if __name__=='__main__':
    unittest.main()
