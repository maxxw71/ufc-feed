from bs4 import BeautifulSoup
import datetime as dt
import unittest

from collect_boxingscene_compubox_summaries import resolve_bouts,parse_explicit_stats,text_content,dedupe_summary_rows,is_historical_review,parse_prefight_baselines

class BoxingSceneCompuBoxResolutionTests(unittest.TestCase):
    def test_multi_fight_title_resolves_two_unique_pairs(self):
        bydate={
          '2010-04-10':{
            ('andreberto','carlosquintana'):{'date':'2010-04-10','fighter_a':'Andre Berto','fighter_b':'Carlos Quintana','rounds':'8'},
            ('celestinocaballero','daudyordan'):{'date':'2010-04-10','fighter_a':'Celestino Caballero','fighter_b':'Daud Yordan','rounds':'12'},
          }
        }
        allitems=list(bydate['2010-04-10'].values())
        bouts,quality=resolve_bouts('CompuBox Stats: Berto-Quintana, Caballero-Yordan',dt.date(2010,4,11),bydate,allitems)
        self.assertEqual(2,len(bouts))
        self.assertEqual('publication_date_window',quality)

    def test_dated_body_pair_resolves_one_sided_title(self):
        bydate={
          '2011-11-26':{
            ('saulalvarez','kermitcintron'):{'date':'2011-11-26','fighter_a':'Saul Alvarez','fighter_b':'Kermit Cintron','rounds':'5'}
          }
        }
        items=list(bydate['2011-11-26'].values())
        body="Canelo Alvarez pressed forward against Kermit Cintron throughout the fight and CompuBox recorded the action."
        bouts,quality=resolve_bouts('CompuBox Stats: Canelo Ends Cintron With Big Numbers',dt.date(2011,11,27),bydate,items,body)
        self.assertEqual(1,len(bouts))
        self.assertEqual('publication_date_body_pair',quality)

    def test_rematch_pair_without_safe_date_stays_ambiguous(self):
        items=[
          {'date':'2012-06-09','fighter_a':'Manny Pacquiao','fighter_b':'Timothy Bradley','rounds':'12'},
          {'date':'2014-04-12','fighter_a':'Manny Pacquiao','fighter_b':'Timothy Bradley','rounds':'12'},
        ]
        bouts,quality=resolve_bouts('Pacquiao-Bradley CompuBox Historical Review',None,{},items)
        self.assertEqual([],bouts)
        self.assertIsNone(quality)

    def test_khan_malignaggi_jab_counts(self):
        text="Amir Khan threw 369 jabs and landed 151 for a 41% margin. Paulie Malignaggi only threw 280 jabs and could only land 20% or 57."
        out=parse_explicit_stats(text,'Amir Khan','Paulie Malignaggi')
        self.assertEqual(out['Amir Khan']['jab_thrown'],369)
        self.assertEqual(out['Amir Khan']['jab_landed'],151)
        self.assertEqual(out['Paulie Malignaggi']['jab_thrown'],280)
        self.assertEqual(out['Paulie Malignaggi']['jab_landed'],57)

    def test_explicit_accuracy_and_per_round_activity(self):
        text=("Bernabe Concepcion landed 37% of the 39 total punches he threw per round and 47% of his power shots. "
              "Mario Santiago averaged 79 punches thrown per round and landed 20%.")
        out=parse_explicit_stats(text,'Bernabe Concepcion','Mario Santiago')
        self.assertIn('total_accuracy_pct',out['Bernabe Concepcion'],out)
        self.assertEqual(out['Bernabe Concepcion']['total_accuracy_pct'],37.0)
        self.assertEqual(out['Bernabe Concepcion']['total_thrown_per_round'],39.0)
        self.assertEqual(out['Bernabe Concepcion']['power_accuracy_pct'],47.0)
        self.assertEqual(out['Mario Santiago']['total_thrown_per_round'],79.0)
        self.assertEqual(out['Mario Santiago']['total_accuracy_pct'],20.0)

    def test_migrated_meta_description_and_thrown_rate(self):
        html='''<html><head><meta name="description" content="Matthew Macklin averaged 92 punches thrown per round, doubling Felix Sturm’s output. Sturm was the more accurate fighter, landing 45% of his power shots."></head><body><main>unrelated current stories</main></body></html>'''
        txt=text_content(BeautifulSoup(html,'lxml'))
        self.assertIn('Macklin averaged 92 punches',txt)
        out=parse_explicit_stats(txt,'Matthew Macklin','Felix Sturm')
        self.assertEqual(out['Matthew Macklin']['total_thrown_per_round'],92.0)
        self.assertEqual(out['Felix Sturm']['power_accuracy_pct'],45.0)

    def test_jacobs_arias_sentence_isolation(self):
        text=("Daniel Jacobs landed 44% of his power shots vs. Luis Arias, who averaged just 29 thrown per round (8 landed). "
              "Jacobs landed 17 of 53 per round.")
        out=parse_explicit_stats(text,'Daniel Jacobs','Luis Arias')
        self.assertEqual(out['Daniel Jacobs']['total_landed_per_round'],17.0)
        self.assertEqual(out['Daniel Jacobs']['total_thrown_per_round'],53.0)
        self.assertEqual(out['Luis Arias']['total_landed_per_round'],8.0)
        self.assertEqual(out['Luis Arias']['total_thrown_per_round'],29.0)

    def test_alias_rows_collapse_when_metrics_agree(self):
        base={'source_url':'u','bout_date':'2013-03-30','opponent':'Nobuhiro Ishida',
              'rounds_observed':3,'jab_landed':53,'jab_thrown':108,'power_landed':52,'power_thrown':97}
        rows=[dict(base,fighter='Gennady Golovkin'),dict(base,fighter='Gennadiy Golovkin')]
        out,conf=dedupe_summary_rows(rows)
        self.assertEqual(1,len(out))
        self.assertEqual([],conf)
        self.assertEqual({'Gennady Golovkin','Gennadiy Golovkin'},set(out[0]['fighter_aliases']))

    def test_alias_metric_conflict_quarantined(self):
        base={'source_url':'u','bout_date':'2013-03-30','opponent':'Nobuhiro Ishida','rounds_observed':3}
        rows=[dict(base,fighter='Gennady Golovkin',jab_landed=53),dict(base,fighter='Gennadiy Golovkin',jab_landed=54)]
        out,conf=dedupe_summary_rows(rows)
        self.assertEqual([],out)
        self.assertEqual(1,len(conf))

    def test_historical_review_parsed_as_prefight_baseline(self):
        text=("Danny Garcia (last 12 fights) threw and landed slightly below the weight class average. "
              "Garcia landed 40.3% of his power punches. "
              "Brandon Rios' last 7 opponents landed 40.8% of their power shots, while Brandon Rios landed 38.5%.")
        self.assertTrue(is_historical_review('Danny Garcia vs. Brandon Rios - CompuBox Historical Review',text))
        out=parse_prefight_baselines(text,'Danny Garcia','Brandon Rios')
        self.assertEqual(out['Danny Garcia']['history_window_fights'],12.0)
        self.assertEqual(out['Danny Garcia']['power_accuracy_pct'],40.3)
        self.assertEqual(out['Brandon Rios']['history_window_fights'],7.0)
        self.assertEqual(out['Brandon Rios']['opponent_power_accuracy_pct'],40.8)
        self.assertEqual(out['Brandon Rios']['power_accuracy_pct'],38.5)

    def test_safe_postfight_patterns(self):
        text=("Amir Khan controlled most of the fight with his jab (5 of 28 per round), "
              "but Julio Diaz gave away rounds, averaging just 33 punches thrown per frame. "
              "Terence Crawford landed 50% of his non-jabs in the fight. "
              "Jose Ramirez's pressure (67 thrown per round) and power punching (19 of 45 per round) was the difference.")
        out=parse_explicit_stats(text,'Amir Khan','Julio Diaz')
        self.assertEqual(out['Amir Khan']['jab_landed_per_round'],5.0)
        self.assertEqual(out['Amir Khan']['jab_thrown_per_round'],28.0)
        self.assertEqual(out['Julio Diaz']['total_thrown_per_round'],33.0)
        out2=parse_explicit_stats(text,'Terence Crawford','Jose Ramirez')
        self.assertEqual(out2['Terence Crawford']['power_accuracy_pct'],50.0)
        self.assertEqual(out2['Jose Ramirez']['total_thrown_per_round'],67.0)
        self.assertEqual(out2['Jose Ramirez']['power_landed_per_round'],19.0)
        self.assertEqual(out2['Jose Ramirez']['power_thrown_per_round'],45.0)

    def test_historical_review_extended_rate_patterns(self):
        text=(
          "Gary Russell averaged 32.7 jabs per round, but landed just 15%. "
          "Russell opponents landed just 7.8 power shots per round and just 28%. "
          "Joseph Diaz landed 19 of 38 power shots per round in his last 5 fights (50%). "
          "Adrien Broner is more accurate, landing 47% of his power punches and 41% overall vs. recent opponents. "
          "14 of Shawn Porter's 17 landed punches per round were power shots. "
          "Manny Pacquiao's opponents landed 33% of their power shots."
        )
        out=parse_prefight_baselines(text,'Gary Russell','Joseph Diaz')
        self.assertEqual(out['Gary Russell']['jab_thrown_per_round'],32.7)
        self.assertEqual(out['Gary Russell']['jab_accuracy_pct'],15.0)
        self.assertEqual(out['Gary Russell']['opponent_power_landed_per_round'],7.8)
        self.assertEqual(out['Gary Russell']['opponent_power_accuracy_pct'],28.0)
        self.assertEqual(out['Joseph Diaz']['power_landed_per_round'],19.0)
        self.assertEqual(out['Joseph Diaz']['power_thrown_per_round'],38.0)
        self.assertEqual(out['Joseph Diaz']['history_window_fights'],5.0)
        self.assertEqual(out['Joseph Diaz']['power_accuracy_pct'],50.0)

        out2=parse_prefight_baselines(text,'Adrien Broner','Shawn Porter')
        self.assertEqual(out2['Adrien Broner']['power_accuracy_pct'],47.0)
        self.assertEqual(out2['Adrien Broner']['total_accuracy_pct'],41.0)
        self.assertEqual(out2['Shawn Porter']['power_landed_per_round'],14.0)
        self.assertEqual(out2['Shawn Porter']['total_landed_per_round'],17.0)

        out3=parse_prefight_baselines(text,'Manny Pacquiao','Brandon Rios')
        self.assertEqual(out3['Manny Pacquiao']['opponent_power_accuracy_pct'],33.0)

if __name__=='__main__':
    unittest.main()
