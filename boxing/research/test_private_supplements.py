import json,pathlib,tempfile,unittest
from unittest.mock import patch
import audit_prospective_odds as audit

class PrivateResults(unittest.TestCase):
    def test_private_overlay_keeps_conflict_for_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d);public=root/'public.json';private=root/'private.json'
            base={'event_date':'2025-01-01','participants':['A','B'],'winner':'A','sources':['https://a.example/result','https://b.example/result']}
            public.write_text(json.dumps({'results':[base]}))
            private.write_text(json.dumps({'results':[{**base,'winner':'B'}]}))
            with patch.multiple(audit,RESULT_SUPPLEMENTS=public,PRIVATE_RESULT_SUPPLEMENTS=private):
                records=audit.load_result_supplements()
                self.assertEqual(len(records),2)
                self.assertIsNone(audit.result_from_supplements(records,'2025-01-01',['A','B']))
    def test_absent_private_overlay_and_malformed_overlay(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d);public=root/'public.json';private=root/'private.json'
            public.write_text('{"results":[]}')
            with patch.multiple(audit,RESULT_SUPPLEMENTS=public,PRIVATE_RESULT_SUPPLEMENTS=private):
                self.assertEqual(audit.load_result_supplements(),[])
                private.write_text('bad')
                with self.assertRaises(json.JSONDecodeError):audit.load_result_supplements()
