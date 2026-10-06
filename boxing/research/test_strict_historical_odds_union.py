import unittest
from combine_strict_historical_odds import combine, evidence_check

def row(**kw):
    r={'event_date':'2023-01-21','bout_id':'1','bookmaker':'DraftKings',
       'selection':'Frazer Clarke','stored_decimal_price':1+100/6000,
       'archived_american_price':'-6000','snapshot_timestamp':'20230120120000',
       'snapshot_url':'https://web.archive.org/web/20230120120000id_/https://www.proboxingodds.com/'}
    r.update(kw);return r

class UnionTests(unittest.TestCase):
    def test_near_favorite_prices_are_not_equivalent(self):
        self.assertFalse(evidence_check(row(archived_american_price='-8000'))['accepted'])
        self.assertTrue(evidence_check(row())['accepted'])

    def test_timestamp_drift_same_day_and_future_rejected(self):
        for kw in [{'snapshot_timestamp':'20230119120000'},
                   {'event_date':'2023-01-20'}, {'event_date':'2023-01-19'},
                   {'snapshot_url':'https://example.com/web/20230120120000/x'}]:
            self.assertFalse(evidence_check(row(**kw))['accepted'])

    def test_valid_alternate_route_is_selected_and_mismatch_retained(self):
        good=row();bad=row(archived_american_price='-8000')
        rows,held,absent,counts=combine([('event_page',{'rows':[bad]}),('homepage',{'rows':[good]})])
        self.assertEqual(len(rows),1);self.assertFalse(held)
        self.assertEqual(rows[0]['archived_american_price'],'-6000')
        self.assertEqual(rows[0]['strict_validation_routes'],['homepage'])
        self.assertEqual(rows[0]['rejected_validation_evidence'][0]['archived_american_price'],'-8000')

    def test_no_valid_route_is_held(self):
        rows,held,_,_=combine([('event_page',{'rows':[row(archived_american_price='-8000')]})])
        self.assertFalse(rows);self.assertEqual(len(held),1)

    def test_disappeared_evidence_survives_repeated_rebuilds(self):
        old=row(bout_id='old')
        rows,held,absent,_=combine([('homepage',{'rows':[row()]})],[old])
        self.assertEqual(absent,[old])
        _,_,again,_=combine([('homepage',{'rows':[row()]})],rows,absent)
        self.assertEqual(again,[old])

    def test_different_prices_are_distinct_observations(self):
        rows,_,_,_=combine([('homepage',{'rows':[row(),row(stored_decimal_price=1.0125,archived_american_price='-8000')]})])
        self.assertEqual(len(rows),2)

if __name__=='__main__':unittest.main()
