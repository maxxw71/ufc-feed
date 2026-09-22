import datetime as dt
import unittest

from collect_boxingscene_compubox_summaries import resolve_bouts,parse_explicit_stats

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

if __name__=='__main__':
    unittest.main()
