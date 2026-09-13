import unittest
from audit_chronological_sample import parse_raw
from features import summary
class SampleAudit(unittest.TestCase):
    def test_price_sign_and_duplicate_ambiguity(self):
        html='''<table class="odds-table"><thead><th data-b="1">Book</th></thead><tbody><tr><th><a href="/fighters/a">Fighter A</a></th><td class="but-sg" data-li="[1,1,9]"><span id="oID1">-200</span></td><td class="but-sg" data-li="[1,1,9]"><span id="oID2">+200</span></td></tr></tbody></table>'''
        values=parse_raw(html)[('9','Book','fightera')]
        self.assertEqual(values,{1.5,3.0}) # retained ambiguity must not become best-price choice
    def test_future_and_target_bout_excluded(self):
        past={'date':'2020-01-01','winner':'BOXER A','method':'KO'}
        future={'date':'2021-01-01','winner':'BOXER B','method':'KO'}
        target={'date':'2020-06-01','winner':'BOXER B','method':'KO'}
        self.assertEqual(summary([past],'2020-06-01'),summary([past,target,future],'2020-06-01'))
if __name__=='__main__':unittest.main()
