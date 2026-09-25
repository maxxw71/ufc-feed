import unittest
from validate_historical_odds_snapshot import american_to_decimal,parse_snapshot,snapshot_variants,wayback_timestamp,validate_quote_across_snapshots

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

    def test_quote_can_validate_from_older_exact_pre_event_snapshot(self):
        q={'quote_rowid':7,'bout_id':'99','bookmaker':'BetWay','selection':'Fighter A','decimal_price':2.0}
        key=('99','betway','fightera')
        snapshots=[
          {'timestamp':'20250102120000','snapshot_used_url':'new',
           'cells':{key:{'american_price':'+120','decimal_from_archive':2.2}}},
          {'timestamp':'20250101120000','snapshot_used_url':'old',
           'cells':{key:{'american_price':'+100','decimal_from_archive':2.0}}},
        ]
        row,checks=validate_quote_across_snapshots(q,'2025-01-03','https://www.proboxingodds.com/events/2025-01-03-1',snapshots)
        self.assertEqual(row['snapshot_timestamp'],'20250101120000')
        self.assertEqual(row['snapshot_url'],'old')
        self.assertEqual(len(checks),2)
        self.assertFalse(checks[0]['validated'])
        self.assertTrue(checks[1]['validated'])

    def test_exact_book_bout_selection_cell(self):
        html=b'''<table class="odds-table"><thead><tr><th></th><th></th><th data-b="20">BetWay</th></tr></thead>
        <tbody><tr><th scope="row">Jorge Linares</th><td class="but-sg" data-li="[20,2,9211]"><span>+500</span></td></tr></tbody></table>'''
        out=parse_snapshot(html)
        row=out[('9211','betway','jorgelinares')]
        self.assertEqual(row['american_price'],'+500')
        self.assertAlmostEqual(row['decimal_from_archive'],6.0)

if __name__=='__main__':unittest.main()
