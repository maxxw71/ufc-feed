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

    def test_cordina_quiroz_total_attempts_wording(self):
        out=parse_pair(
            "Joe Cordina landed 146 of 498 total punches over 10 rounds, while Jaret Gonzalez Quiroz was 83-of-422 on his attempts.",
            'Joe Cordina','Jaret Gonzalez Quiroz')
        self.assertEqual(out['Joe Cordina']['total_landed'],146)
        self.assertEqual(out['Joe Cordina']['total_thrown'],498)
        self.assertEqual(out['Jaret Gonzalez Quiroz']['total_landed'],83)
        self.assertEqual(out['Jaret Gonzalez Quiroz']['total_thrown'],422)

    def test_melikuziev_fulghum_body_edge(self):
        out=parse_pair(
            "Melikuziev held a 44-33 edge in body punches landed.",
            'Bektemir Melikuziev','Darius Fulghum')
        self.assertEqual(out['Bektemir Melikuziev']['body_landed'],44)
        self.assertEqual(out['Darius Fulghum']['body_landed'],33)

    def test_simple_outlanded_total_pair(self):
        out=parse_pair("Roach outlanded Davis 112 to 103 in the fight.",'Lamont Roach','Gervonta Davis')
        self.assertEqual(out['Lamont Roach']['total_landed'],112)
        self.assertEqual(out['Gervonta Davis']['total_landed'],103)

    def test_ring_result_final_tally_and_equal_power(self):
        out=parse_pair(
            "CompuBox credited Romero and Garcia for landing only 18 power punches apiece in 12 rounds. "
            "Garcia landed nine more punches overall, according to CompuBox's final tally (66-of-210 to 57-of-280).",
            'Rolando Romero','Ryan Garcia')
        self.assertEqual(out['Ryan Garcia']['total_landed'],66)
        self.assertEqual(out['Ryan Garcia']['total_thrown'],210)
        self.assertEqual(out['Rolando Romero']['total_landed'],57)
        self.assertEqual(out['Rolando Romero']['total_thrown'],280)
        self.assertEqual(out['Ryan Garcia']['power_landed'],18)
        self.assertEqual(out['Rolando Romero']['power_landed'],18)

    def test_new_exact_pair_supplements(self):
        mel='https://www.ringmagazine.com/news/bektemir-melikuziev-drops-outlasts-darius-fulghum-to-win-in-thrilling-battle-5J42VnOm6DguwAfdsxa1NV'
        ok,_=supplement_pair_ok('2025-05-30',['Bektemir Melikuziev','Darius Fulghum'],mel)
        self.assertTrue(ok)
        dav='https://www.ringmagazine.com/news/tank-says-judges-took-fight-from-him-due-to-kneel-former-nysac-commish-says-tank-shouldve-been-dqd-4q5xioFCxVFevPVkthmWzP'
        ok,_=supplement_pair_ok('2025-03-01',['Gervonta Davis','Lamont Roach'],dav)
        self.assertTrue(ok)

    def test_roach_cruz_overall_prefix(self):
        out=parse_pair(
            "Overall in the fight, Roach outlanded Cruz 191 to 159.",
            'Lamont Roach','Isaac Cruz')
        self.assertEqual(out['Lamont Roach']['total_landed'],191)
        self.assertEqual(out['Isaac Cruz']['total_landed'],159)

    def test_muratalla_cruz_full_tally_and_categories(self):
        out=parse_pair(
            "Cruz landed one more punch overall according to CompuBox (176-of-537 to 175-of-611). "
            "Muratalla was credited for landing 13 more power punches (112 of 296 to 99 of 251), "
            "whereas Cruz landed 14 more jabs (77 of 286 to 63 of 215).",
            'Raymond Muratalla','Andy Cruz')
        self.assertEqual(out['Andy Cruz']['total_landed'],176)
        self.assertEqual(out['Andy Cruz']['total_thrown'],537)
        self.assertEqual(out['Raymond Muratalla']['total_landed'],175)
        self.assertEqual(out['Raymond Muratalla']['power_landed'],112)
        self.assertEqual(out['Andy Cruz']['power_landed'],99)
        self.assertEqual(out['Andy Cruz']['jab_landed'],77)
        self.assertEqual(out['Raymond Muratalla']['jab_landed'],63)

    def test_barrios_pacquiao_full_tally_and_categories(self):
        out=parse_pair(
            "Barrios landed only 19 more punches overall (120 of 658 to 101 of 577). "
            "CompuBox recorded more power punches for Pacquiao (81 of 259 to 75 of 235) "
            "and more jabs for Barrios (45 of 423 to 20 of 318).",
            'Mario Barrios','Manny Pacquiao')
        self.assertEqual(out['Mario Barrios']['total_landed'],120)
        self.assertEqual(out['Manny Pacquiao']['total_thrown'],577)
        self.assertEqual(out['Manny Pacquiao']['power_landed'],81)
        self.assertEqual(out['Mario Barrios']['power_landed'],75)
        self.assertEqual(out['Mario Barrios']['jab_landed'],45)
        self.assertEqual(out['Manny Pacquiao']['jab_landed'],20)

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


    def test_figueroa_gonzalez_parenthetical_overall(self):
        out=parse_pair(
            "According to CompuBox's unofficial punch stats, Figueroa landed 17 more punches overall than Gonzalez (282 of 1,071 to 265 of 821).",
            'Brandon Figueroa','Joet Gonzalez')
        self.assertEqual(out['Brandon Figueroa']['total_landed'],282)
        self.assertEqual(out['Brandon Figueroa']['total_thrown'],1071)
        self.assertEqual(out['Joet Gonzalez']['total_landed'],265)
        self.assertEqual(out['Joet Gonzalez']['total_thrown'],821)

    def test_parker_buatsi_outlanded_parenthetical_totals(self):
        out=parse_pair(
            "CompuBox counted more connections for Parker, who unofficially out-landed Buatsi by 24 punches overall (100-of-326 to 76-of-368). Parker landed more power punches (66-of-174 to 31-of-157), according to CompuBox, but Buatsi hit him with more jabs (45-of-211 to 34-of-152).",
            'Joshua Buatsi','Zach Parker')
        self.assertEqual(out['Zach Parker']['total_landed'],100)
        self.assertEqual(out['Zach Parker']['total_thrown'],326)
        self.assertEqual(out['Joshua Buatsi']['total_landed'],76)
        self.assertEqual(out['Joshua Buatsi']['total_thrown'],368)
        self.assertEqual(out['Zach Parker']['power_landed'],66)
        self.assertEqual(out['Joshua Buatsi']['power_landed'],31)
        self.assertEqual(out['Joshua Buatsi']['jab_landed'],45)
        self.assertEqual(out['Zach Parker']['jab_landed'],34)


    def test_ball_figueroa_parenthetical_and_body_total(self):
        out=parse_pair(
            "Ball (249 of 567) outlanded Figueroa in all but one of their 11 completed rounds. "
            "Figueroa's body work (94 of his 214 landed punches) paid dividends in a big way.",
            'Nick Ball','Brandon Figueroa')
        self.assertEqual(out['Nick Ball']['total_landed'],249)
        self.assertEqual(out['Nick Ball']['total_thrown'],567)
        self.assertEqual(out['Brandon Figueroa']['total_landed'],214)
        self.assertEqual(out['Brandon Figueroa']['body_landed'],94)
        self.assertNotIn('total_thrown',out['Brandon Figueroa'])



    def test_adames_williams_exact_total_landed(self):
        out=parse_pair(
            "Adames (25-1-1, 18 KOs) dropped Williams (20-2, 13 KOs) in the second round and outlanded him 254 to 127, according to CompuBox, to score the victory.",
            'Carlos Adames','Austin Williams')
        self.assertEqual(out['Carlos Adames']['total_landed'],254)
        self.assertEqual(out['Austin Williams']['total_landed'],127)

    def test_fury_makhmudov_compubox_prefix_total_landed(self):
        out=parse_pair(
            "According to CompuBox, Fury out-landed his man 199-59. In the fifth and sixth rounds, Makhmudov landed just two punches.",
            'Tyson Fury','Arslanbek Makhmudov')
        self.assertEqual(out['Tyson Fury']['total_landed'],199)
        self.assertEqual(out['Arslanbek Makhmudov']['total_landed'],59)

    def test_crawford_canelo_counted_full_totals(self):
        out=parse_pair(
            "CompuBox counted 115 of 534 punches for Crawford and 99 of 338 for Alvarez, who landed more power punches.",
            'Terence Crawford','Canelo Alvarez')
        self.assertEqual(out['Terence Crawford']['total_landed'],115)
        self.assertEqual(out['Terence Crawford']['total_thrown'],534)
        self.assertEqual(out['Canelo Alvarez']['total_landed'],99)
        self.assertEqual(out['Canelo Alvarez']['total_thrown'],338)

    def test_inoue_picasso_category_sum_total_landed(self):
        out=parse_pair(
            "Inoue connected on 158 more punches than Picasso, had a 161-63 edge in jabs landed, 60 more power shots (167-107) and 30 more to the body (96-66).",
            'Naoya Inoue','Alan Picasso')
        self.assertEqual(out['Naoya Inoue']['jab_landed'],161)
        self.assertEqual(out['Alan Picasso']['jab_landed'],63)
        self.assertEqual(out['Naoya Inoue']['power_landed'],167)
        self.assertEqual(out['Alan Picasso']['power_landed'],107)
        self.assertEqual(out['Naoya Inoue']['total_landed'],328)
        self.assertEqual(out['Alan Picasso']['total_landed'],170)

    def test_nakatani_hernandez_two_half_total_landed(self):
        out=parse_pair(
            "Nakatani outlanded Hernandez 142-94 over the first six rounds. Hernandez threw considerably more and outlanded the Japanese star 179-155 over the final six rounds.",
            'Junto Nakatani','Sebastian Hernandez')
        self.assertEqual(out['Junto Nakatani']['total_landed'],297)
        self.assertEqual(out['Sebastian Hernandez']['total_landed'],273)


if __name__=='__main__':unittest.main()
