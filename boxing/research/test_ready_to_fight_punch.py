import unittest
from collect_ready_to_fight_punch import parse_stats,validate

SAMPLE=(
"Statistics of punches in the Essuman VS Kongo fight "
"Punches Ekow Chris Total number of punches thrown per fight Total Landed 639 "
"Total number of punches thrown 711 Total number of punches landed per fight "
"Total Landed 53 (8%) Total number of punches landed 52 (7%) "
"Jabs Ekow Chris Total number of jabs thrown per fight Total Landed 315 "
"Total number of jabs thrown 484 Total number of jabs landed per fight "
"Total Landed 22 (7%) Total number of jabs landed 30 (6%) "
"Power Punches Ekow Chris Total number of power punches thrown per fight Total Landed 324 "
"Total number of power punches thrown 227 Total number of power punches landed per fight "
"Total Landed 31 (10%) Total number of power punches landed 22 (10%) Punch Map"
)

class ReadyToFightPunchTests(unittest.TestCase):
    def test_parse_and_arithmetic_accept(self):
        vals=parse_stats(SAMPLE)
        self.assertEqual(vals['total'],(639,711,53,52))
        self.assertEqual(vals['jab'],(315,484,22,30))
        self.assertEqual(vals['power'],(324,227,31,22))
        self.assertEqual(validate(vals),(True,None))

    def test_landed_category_mismatch_rejected(self):
        bad=SAMPLE.replace('Total Landed 31 (10%)','Total Landed 30 (10%)')
        vals=parse_stats(bad)
        ok,reason=validate(vals)
        self.assertFalse(ok)
        self.assertEqual(reason,'landed_arithmetic_mismatch')

    def test_large_thrown_category_mismatch_rejected(self):
        bad=SAMPLE.replace('Total Landed 324 Total number of power punches thrown 227',
                           'Total Landed 300 Total number of power punches thrown 227')
        vals=parse_stats(bad)
        ok,reason=validate(vals)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith('thrown_arithmetic_mismatch_'))

if __name__=='__main__':
    unittest.main()
