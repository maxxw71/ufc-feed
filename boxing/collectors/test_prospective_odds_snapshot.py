import datetime as dt,unittest
from prospective_odds_snapshot import parse_home

HTML='''<h2>September 19th Boxing Odds</h2><table class="odds-table"><thead><tr><th></th><th data-b="1">FanDuel</th><th data-b="2">Kalshi</th></tr></thead><tbody>
<tr><th><a href="/fighters/a">Alpha</a></th><td class="but-sg" data-li="[1,1,9]"><span id="oID1">-200</span></td><td class="but-sg" data-li="[2,1,9]"><span id="oID2">-190</span></td></tr>
<tr><th><a href="/fighters/b">Beta</a></th><td class="but-sg" data-li="[1,2,9]"><span id="oID3">+170</span></td><td class="but-sg" data-li="[2,2,9]"><span id="oID4">+160</span></td></tr>
<tr><th>Alpha wins by decision</th><td class="but-sg" data-li="[1,3,9]"><span id="oID5">+300</span></td></tr>
</tbody></table>'''

class ProspectiveOdds(unittest.TestCase):
    def test_moneyline_only_and_timestamp_quality(self):
        now=dt.datetime(2026,9,13,12,tzinfo=dt.timezone.utc)
        rows=parse_home(HTML,now)
        self.assertEqual(len(rows),4)
        self.assertEqual({r['event_date'] for r in rows},{'2026-09-19'})
        self.assertEqual({r['timing_quality'] for r in rows},{'verified_pre_event_date'})
        self.assertEqual({r['market_class'] for r in rows},{'sportsbook','prediction_market'})
        fd=[r for r in rows if r['bookmaker']=='FanDuel' and r['selection']=='Alpha'][0]
        self.assertAlmostEqual(fd['decimal_price'],1.5)


    def test_non_heading_date_label(self):
        html='''<a class="date-tab">September 26th Boxing Odds</a><div><table class="odds-table"><thead><tr><th></th><th data-b="1">FanDuel</th></tr></thead><tbody>
        <tr><th>19:00 <a href="/fighters/a">Alpha</a></th><td class="but-sg" data-li="[1,1,11]"><span id="oID11">-150</span></td></tr>
        <tr><th>UTC <a href="/fighters/b">Beta</a></th><td class="but-sg" data-li="[1,2,11]"><span id="oID12">+130</span></td></tr>
        </tbody></table></div>'''
        now=dt.datetime(2026,9,21,12,tzinfo=dt.timezone.utc)
        rows=parse_home(html,now)
        self.assertEqual({r['event_date'] for r in rows},{'2026-09-26'})
        self.assertEqual({r['event_start_utc'] for r in rows},{'2026-09-26T19:00:00Z'})
        self.assertEqual({r['timing_quality'] for r in rows},{'verified_pre_event_time'})

if __name__=='__main__':unittest.main()
