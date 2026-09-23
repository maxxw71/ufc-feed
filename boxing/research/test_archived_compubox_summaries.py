import unittest
from export_archived_compubox_summaries import parse_summary_metrics,archive_date

class ArchivedCompuBoxSummaryTests(unittest.TestCase):
    def test_garcia_burgos_current_fight_rates_only(self):
        text=("Methodical win for Garcia, who landed 29% of his 47 punches thrown per round. "
              "Garcia's occasional displays of power tamed Burgos, who avg'd 47 punches thrown per round and landed just 16%. "
              "Burgos avg'd 70 punches thrown per round one year ago.")
        out=parse_summary_metrics(text,'Mikey Garcia','Juan Carlos Burgos')
        self.assertEqual(out['Mikey Garcia']['total_accuracy_pct'],29.0)
        self.assertEqual(out['Mikey Garcia']['total_thrown_per_round'],47.0)
        self.assertEqual(out['Juan Carlos Burgos']['total_thrown_per_round'],47.0)
        self.assertEqual(out['Juan Carlos Burgos']['total_accuracy_pct'],16.0)

    def test_jennings_szpilka_summary(self):
        text=("Jennings movement was too much for Szpilka, who avg'd just 37 punches thrown per round, landing 24%. "
              "Jennings avg'd 42 punches thrown per round, while landing 46% of his power shots.")
        out=parse_summary_metrics(text,'Bryant Jennings','Artur Szpilka')
        self.assertEqual(out['Artur Szpilka']['total_thrown_per_round'],37.0)
        self.assertEqual(out['Artur Szpilka']['total_accuracy_pct'],24.0)
        self.assertEqual(out['Bryant Jennings']['total_thrown_per_round'],42.0)
        self.assertEqual(out['Bryant Jennings']['power_accuracy_pct'],46.0)

    def test_wayback_capture_date(self):
        self.assertEqual(archive_date('https://web.archive.org/web/20151208081612/http://compuboxonline.com/x/'),'2015-12-08')
        self.assertIsNone(archive_date('https://compuboxonline.com/x/'))

if __name__=='__main__':
    unittest.main()
