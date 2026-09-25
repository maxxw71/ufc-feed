import unittest
from collect_ring_compubox_summaries import parse_pair,supplement_pair_ok

class RingCompuBoxSummaryTests(unittest.TestCase):
    def test_verified_pair_supplement_is_exact(self):
        url='https://www.ringmagazine.com/news/jack-catterall-vs-harlem-eubank--compubox-punch-stats-5S39F1SF97apVMe5OL8PIZ'
        ok,why=supplement_pair_ok('2025-07-05',['Harlem Eubank','Jack Catterall'],url)
        self.assertTrue(ok);self.assertIn('independent_result',why)
        self.assertFalse(supplement_pair_ok('2025-07-06',['Harlem Eubank','Jack Catterall'],url)[0])
        self.assertFalse(supplement_pair_ok('2025-07-05',['Harlem Eubank','Other Fighter'],url)[0])
        self.assertFalse(supplement_pair_ok('2025-07-05',['Harlem Eubank','Jack Catterall'],url+'-wrong')[0])

    def test_full_total_pair(self):
        t="Mark Chamberlain went 287 of 952 (30%) in total punches while Jack Rafferty went 202 of 761 (27%)."
        out=parse_pair(t,'Mark Chamberlain','Jack Rafferty')
        self.assertEqual(out['Mark Chamberlain']['total_landed'],287)
        self.assertEqual(out['Mark Chamberlain']['total_thrown'],952)
        self.assertEqual(out['Jack Rafferty']['total_landed'],202)
        self.assertEqual(out['Jack Rafferty']['total_thrown'],761)

    def test_outlanded_pair(self):
        t="Hrgovic outlanded Adeleye 228-92 on total punches across all ten rounds, including power shots (169-47)."
        out=parse_pair(t,'Filip Hrgovic','David Adeleye')
        self.assertEqual(out['Filip Hrgovic']['total_landed'],228)
        self.assertEqual(out['David Adeleye']['total_landed'],92)
        self.assertEqual(out['Filip Hrgovic']['power_landed'],169)
        self.assertEqual(out['David Adeleye']['power_landed'],47)

    def test_category_run(self):
        t="Hitchins landed 205 of 398, 52% in total punches, 118 of 233, 51% in jabs and 87 of 165, 53% in power punches."
        out=parse_pair(t,'Richardson Hitchins','George Kambosos Jr.')
        self.assertEqual(out['Richardson Hitchins']['jab_landed'],118)
        self.assertEqual(out['Richardson Hitchins']['jab_thrown'],233)
        self.assertEqual(out['Richardson Hitchins']['power_landed'],87)
        self.assertEqual(out['Richardson Hitchins']['power_thrown'],165)

    def test_additional_ring_totals(self):
        out=parse_pair(
            "From the sixth round on, Bivol found another gear. Bivol landed 170 punches - the most by a Beterbiev opponent.",
            'Dmitry Bivol','Artur Beterbiev')
        self.assertEqual(out['Dmitry Bivol']['total_landed'],170)

        out=parse_pair(
            "Stevenson landed 165 of 372 punches (44.4%) compared to 72 of 468 (15.4%) for Lopez, per CompuBox.",
            'Shakur Stevenson','Teofimo Lopez')
        self.assertEqual(out['Shakur Stevenson']['total_landed'],165)
        self.assertEqual(out['Shakur Stevenson']['total_thrown'],372)
        self.assertEqual(out['Teofimo Lopez']['total_landed'],72)
        self.assertEqual(out['Teofimo Lopez']['total_thrown'],468)
        self.assertEqual(out['Teofimo Lopez']['total_accuracy_pct'],15.4)

    def test_ball_goodman_overall_landed_pair(self):
        out=parse_pair(
            "Ball threw considerably more shots and landed at a slightly higher clip overall (240-220) over 12 rounds, while Goodman was credited with 5.7% more accuracy.",
            'Nick Ball','Sam Goodman')
        self.assertEqual(out['Nick Ball']['total_landed'],240)
        self.assertEqual(out['Sam Goodman']['total_landed'],220)

    def test_new_ring_pair_formats(self):
        out=parse_pair(
            "Resendiz landed 186 of 600 total punches while Plant went 108 of 509. "
            "Resendiz held a 109 to 70 connect advantage in power punches.",
            'Armando Resendiz','Caleb Plant')
        self.assertEqual(out['Armando Resendiz']['total_landed'],186)
        self.assertEqual(out['Armando Resendiz']['total_thrown'],600)
        self.assertEqual(out['Caleb Plant']['total_landed'],108)
        self.assertEqual(out['Caleb Plant']['total_thrown'],509)
        self.assertEqual(out['Armando Resendiz']['power_landed'],109)
        self.assertEqual(out['Caleb Plant']['power_landed'],70)

        out=parse_pair(
            "Essuman landed 140 of 363, 22% in total punches and Taylor landed 125 of 493, 25%. "
            "Taylor did hold a 117-115 connect advantage in power punches in the end.",
            'Josh Taylor','Ekow Essuman')
        self.assertEqual(out['Ekow Essuman']['total_landed'],140)
        self.assertEqual(out['Josh Taylor']['total_landed'],125)
        self.assertEqual(out['Josh Taylor']['power_landed'],117)
        self.assertEqual(out['Ekow Essuman']['power_landed'],115)

        out=parse_pair(
            "Melikuziev landed 170 of 442 punches and Fulghum landed 169 of 407.",
            'Bektemir Melikuziev','Darius Fulghum')
        self.assertEqual(out['Bektemir Melikuziev']['total_landed'],170)
        self.assertEqual(out['Darius Fulghum']['total_thrown'],407)

    def test_catterall_and_inoue_pair_formats(self):
        out=parse_pair(
            "After six completed rounds, Catterall had landed almost three times as many punches (51) than Eubank (17), "
            "thrown more than twice more attempts (186-85).",
            'Jack Catterall','Harlem Eubank')
        self.assertEqual(out['Jack Catterall']['total_landed'],51)
        self.assertEqual(out['Harlem Eubank']['total_landed'],17)
        self.assertEqual(out['Jack Catterall']['total_thrown'],186)
        self.assertEqual(out['Harlem Eubank']['total_thrown'],85)

        out=parse_pair(
            "Inoue had a 161-63 edge in jabs landed, 60 more power shots (167-107) and 30 more to the body (96-66). "
            "Inoue averaged 27 punches landed per round, while Picasso could only muster half that tally (14) per frame.",
            'Naoya Inoue','Alan Picasso')
        self.assertEqual(out['Naoya Inoue']['jab_landed'],161)
        self.assertEqual(out['Alan Picasso']['jab_landed'],63)
        self.assertEqual(out['Naoya Inoue']['power_landed'],167)
        self.assertEqual(out['Alan Picasso']['body_landed'],66)
        self.assertEqual(out['Naoya Inoue']['total_landed_per_round'],27)
        self.assertEqual(out['Alan Picasso']['total_landed_per_round'],14)

    def test_simple_outlanded_total_pair(self):
        out=parse_pair("Roach outlanded Davis 112 to 103 in the fight.",'Lamont Roach','Gervonta Davis')
        self.assertEqual(out['Lamont Roach']['total_landed'],112)
        self.assertEqual(out['Gervonta Davis']['total_landed'],103)

    def test_itauma_hyphen_of_hyphen_total(self):
        out=parse_pair(
            "Itauma, 17 years his junior, admitted needing to quickly be wary of the firepower flashing back at him after absorbing a shot - Whyte landed just two - while the unbeaten 20-year-old connected on 19-of-34 punches (55.9%).",
            'Moses Itauma','Dillian Whyte')
        self.assertEqual(out['Moses Itauma']['total_landed'],19)
        self.assertEqual(out['Moses Itauma']['total_thrown'],34)
        self.assertEqual(out['Moses Itauma']['total_accuracy_pct'],55.9)
        self.assertEqual(out['Dillian Whyte']['total_landed'],2)
        self.assertNotIn('total_thrown',out['Dillian Whyte'])
        self.assertNotEqual(out['Dillian Whyte']['total_landed'],19)

if __name__=='__main__':unittest.main()
