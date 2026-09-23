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

    def test_additional_explicit_archive_phrases(self):
        out=parse_summary_metrics(
            "PunchStat Report Donaire landed the harder punches. Mathebula was busier, averaging 92 thrown per round.",
            'Nonito Donaire','Jeffrey Mathebula')
        self.assertEqual(out['Jeffrey Mathebula']['total_thrown_per_round'],92.0)

        out=parse_summary_metrics(
            "Cotto, desperate for a win, absolutely mugged Rodriguez, landing 47 power shots (54%) in 6:18. "
            "27 of Cottos 55 landed punches were to Rodriguezs body.",
            'Miguel Cotto','Delvin Rodriguez')
        self.assertEqual(out['Miguel Cotto']['power_landed'],47.0)
        self.assertEqual(out['Miguel Cotto']['power_accuracy_pct'],54.0)
        self.assertEqual(out['Miguel Cotto']['body_landed'],27.0)
        self.assertEqual(out['Miguel Cotto']['total_landed'],55.0)

        out=parse_summary_metrics(
            "Klitschko mixed up his attack (9 jabs landed per round/24 thrown- 10 power landed/13 thrown) "
            "vs. the over-matched Pianeta, who landed just 24 total punches all fight.",
            'Wladimir Klitschko','Francesco Pianeta')
        self.assertEqual(out['Wladimir Klitschko']['jab_landed_per_round'],9.0)
        self.assertEqual(out['Wladimir Klitschko']['jab_thrown_per_round'],24.0)
        self.assertEqual(out['Wladimir Klitschko']['power_landed_per_round'],10.0)
        self.assertEqual(out['Wladimir Klitschko']['power_thrown_per_round'],13.0)
        self.assertEqual(out['Francesco Pianeta']['total_landed'],24.0)

        out=parse_summary_metrics(
            "After landing a huge left hook in round three, Danny Garcia outlanded Amir Khan 44-23 in power shots.",
            'Danny Garcia','Amir Khan')
        self.assertEqual(out['Danny Garcia']['power_landed'],44.0)
        self.assertEqual(out['Amir Khan']['power_landed'],23.0)

    def test_wayback_capture_date(self):
        self.assertEqual(archive_date('https://web.archive.org/web/20151208081612/http://compuboxonline.com/x/'),'2015-12-08')
        self.assertIsNone(archive_date('https://compuboxonline.com/x/'))

if __name__=='__main__':
    unittest.main()
