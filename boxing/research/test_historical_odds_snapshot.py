import unittest
from validate_historical_odds_snapshot import american_to_decimal,parse_snapshot,snapshot_variants,wayback_timestamp

class HistoricalOddsArchiveValidationTests(unittest.TestCase):
    def test_american_decimal_conversion(self):
        self.assertAlmostEqual(american_to_decimal('+140'),2.4)
        self.assertAlmostEqual(american_to_decimal('-200'),1.5)
        self.assertIsNone(american_to_decimal('n/a'))

    def test_wayback_snapshot_variants_stay_on_requested_timestamp(self):
        url='https://web.archive.org/web/20210528114306id_/https://www.proboxingodds.com/events/2021-05-29-1344'
        variants=snapshot_variants(url)
        self.assertGreaterEqual(len(variants),8)
        self.assertTrue(all('/web/20210528114306' in x for x in variants))
        self.assertTrue(any('/http://www.proboxingodds.com/' in x for x in variants))
        self.assertTrue(any('/https://proboxingodds.com/' in x for x in variants))
        self.assertEqual(wayback_timestamp(url),'20210528114306')
        self.assertEqual(wayback_timestamp('https://web.archive.org/web/20210119154525id_/x'),'20210119154525')

    def test_exact_book_bout_selection_cell(self):
        html=b'''<table class="odds-table"><thead><tr><th></th><th></th><th data-b="20">BetWay</th></tr></thead>
        <tbody><tr><th scope="row">Jorge Linares</th><td class="but-sg" data-li="[20,2,9211]"><span>+500</span></td></tr></tbody></table>'''
        out=parse_snapshot(html)
        row=out[('9211','betway','jorgelinares')]
        self.assertEqual(row['american_price'],'+500')
        self.assertAlmostEqual(row['decimal_from_archive'],6.0)

if __name__=='__main__':unittest.main()
