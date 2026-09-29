import unittest

from probe_boxerlist_reach import parse


class BoxerListReachParserTests(unittest.TestCase):
    def test_strips_boxer_suffix_and_does_not_copy_height_into_missing_reach(self):
        html=b"""
        <html><body>
          <h1>James Metcalf boxer</h1>
          <div>Height</div><div>175 cm</div>
          <div>Reach</div><div>-</div>
        </body></html>
        """
        name,height,reach,vals=parse(html)
        self.assertEqual(name,'James Metcalf')
        self.assertEqual(height,175)
        self.assertIsNone(reach)
        self.assertEqual(vals,[])

    def test_reads_only_explicit_reach_value(self):
        html=b"""
        <html><body>
          <h1>Test Fighter (Nickname) boxer</h1>
          <div>Height</div><div>180 cm</div>
          <div>Reach</div><div>72&quot; / 183 cm</div>
        </body></html>
        """
        name,height,reach,vals=parse(html)
        self.assertEqual(name,'Test Fighter')
        self.assertEqual(height,180)
        self.assertEqual(reach,183)
        self.assertEqual(vals,[183])

if __name__=='__main__':
    unittest.main()
