import sqlite3
import unittest

from build_punch_profiles import (load_reports, load_reviewed_chart_reports, fight_observations, pre_fight_profiles, merge_observation_tiers, merge_round_report_tiers)


class PunchProfileIntegrityTests(unittest.TestCase):
    def db(self):
        d=sqlite3.connect(':memory:')
        d.execute('CREATE TABLE punch_reports(url TEXT PRIMARY KEY,bout_date TEXT,title TEXT,status TEXT)')
        d.execute('CREATE TABLE round_punches(report_url TEXT,fighter_label TEXT,round INTEGER,category TEXT,landed INTEGER,thrown INTEGER)')
        d.execute('CREATE TABLE fight_punch_totals(report_url TEXT,fighter_label TEXT,category TEXT,landed INTEGER,body_landed INTEGER,thrown INTEGER)')
        d.execute('CREATE TABLE reviewed_round_charts(source_url TEXT PRIMARY KEY,bout_date TEXT,fighters_json TEXT,raw_path TEXT,sha256 TEXT,quality TEXT,totals_json TEXT)')
        d.execute('CREATE TABLE reviewed_chart_rounds(source_url TEXT,fighter TEXT,round INTEGER,category TEXT,landed INTEGER,thrown INTEGER)')
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


    def add_reviewed_chart(self,d,url='https://publisher.example/chart',date='2025-12-27',missing_thrown=False):
        fighters=['ALPHA ONE','BRAVO TWO']
        totals={}
        for fighter,base in [('ALPHA ONE',5),('BRAVO TWO',3)]:
            totals[fighter]={}
            for cat,offset in [('total',0),('jab',1),('power',2)]:
                pairs=[]
                for rnd in (1,2):
                    landed=base+offset+rnd
                    thrown=landed+10
                    if missing_thrown and fighter=='BRAVO TWO' and cat=='power' and rnd==2:
                        thrown=None
                    d.execute('INSERT INTO reviewed_chart_rounds VALUES(?,?,?,?,?,?)',
                              (url,fighter,rnd,cat,landed,thrown))
                    pairs.append((landed,thrown))
                if any(x[1] is None for x in pairs):
                    totals[fighter][cat]=[sum(x[0] for x in pairs),999]
                else:
                    totals[fighter][cat]=[sum(x[0] for x in pairs),sum(x[1] for x in pairs)]
        import json
        d.execute('INSERT INTO reviewed_round_charts VALUES(?,?,?,?,?,?,?)',
                  (url,date,json.dumps(fighters),'raw','sha',
                   'publisher_reproduced_chart; visual_transcription_and_arithmetic_checked',
                   json.dumps(totals)))
        return {
          url:{
            'available_from_date':'2025-12-28',
            'required_db_quality':['publisher_reproduced_chart','visual_transcription_and_arithmetic_checked']
          }
        }

    def test_reviewed_chart_strict_acceptance_and_availability(self):
        d=self.db();meta=self.add_reviewed_chart(d)
        reports=load_reviewed_chart_reports(d,meta)
        self.assertEqual(1,len(reports))
        self.assertEqual('2025-12-28',reports[0]['available_from_date'])
        self.assertEqual('publisher_reproduced_compubox_round_chart',reports[0]['source_quality'])
        obs=fight_observations(reports)
        self.assertEqual(2,len(obs))
        self.assertTrue(all(r['available_from_date']=='2025-12-28' for r in obs))
        self.assertTrue(all(r['source_quality']=='publisher_reproduced_compubox_round_chart' for r in obs))

    def test_reviewed_chart_missing_thrown_is_rejected(self):
        d=self.db();meta=self.add_reviewed_chart(d,missing_thrown=True)
        self.assertEqual([],load_reviewed_chart_reports(d,meta))

    def test_direct_round_report_precedes_reviewed_duplicate_pair(self):
        d=self.db();url='https://publisher.example/chart';meta=self.add_reviewed_chart(d,url=url)
        self.add_report(
            d,'https://beta.compuboxdata.com/round-stats/999','2025-12-27',
            'ALPHA ONE UD 2 BRAVO TWO','ONE','TWO'
        )
        direct=load_reports(d)
        reviewed=load_reviewed_chart_reports(d,meta)
        merged,accepted,skipped=merge_round_report_tiers(direct,reviewed)
        self.assertEqual(1,len(merged))
        self.assertEqual(0,accepted)
        self.assertEqual(1,skipped)
        self.assertIn('round-stats/999',merged[0]['url'])

    def test_available_from_date_blocks_same_day_leakage(self):
        history={
          'bout_date':'2025-12-27','report_url':'history','available_from_date':'2025-12-28',
          'fighter_key':'alpha','fighter_label':'Alpha','fighter_full_name':'Alpha One',
          'opponent_key':'beta','opponent_label':'Beta','opponent_full_name':'Beta Two',
          'identity_quality':'report_title_full_name_suffix_match','rounds_observed':2,
          'total_landed_per_round':5.0
        }
        same_day=dict(history,bout_date='2025-12-28',report_url='target-same',
                      available_from_date=None,opponent_key='gamma',opponent_label='Gamma',
                      opponent_full_name='Gamma Three')
        snaps=pre_fight_profiles([history,same_day])
        target=next(x for x in snaps if x['report_url']=='target-same')
        self.assertEqual(0,target['prior_punch_fights'])

        next_day=dict(same_day,bout_date='2025-12-29',report_url='target-next')
        snaps=pre_fight_profiles([history,next_day])
        target=next(x for x in snaps if x['report_url']=='target-next')
        self.assertEqual(1,target['prior_punch_fights'])
        self.assertEqual(2,target['prior_punch_rounds'])


if __name__=='__main__':
    unittest.main()
