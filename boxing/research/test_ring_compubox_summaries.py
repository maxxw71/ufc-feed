import unittest
from collect_ring_compubox_summaries import parse_pair

class RingCompuBoxSummaryTests(unittest.TestCase):
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

if __name__=='__main__':unittest.main()
