import datetime as dt
import unittest

from collect_boxingscene_compubox_summaries import resolve_bouts

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

    def test_rematch_pair_without_safe_date_stays_ambiguous(self):
        items=[
          {'date':'2012-06-09','fighter_a':'Manny Pacquiao','fighter_b':'Timothy Bradley','rounds':'12'},
          {'date':'2014-04-12','fighter_a':'Manny Pacquiao','fighter_b':'Timothy Bradley','rounds':'12'},
        ]
        bouts,quality=resolve_bouts('Pacquiao-Bradley CompuBox Historical Review',None,{},items)
        self.assertEqual([],bouts)
        self.assertIsNone(quality)

if __name__=='__main__':
    unittest.main()
