#!/usr/bin/env python3
"""Offline regression tests for UFC matchup gate, including same-day/future exclusion."""
import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE=Path(__file__).with_name("ufc_matchup_review_v1.py")
spec=importlib.util.spec_from_file_location("review",MODULE)
review=importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)

FIELDS=["event_date","player1","player2","result","method","round","time","p1_rd1_Td","p2_rd1_Td"]

def fight(d,a,b,result="W",method="DECISION - UNANIMOUS",rnd="3",time="5:00"):
    return dict(event_date=d,player1=a,player2=b,result=result,method=method,
                round=rnd,time=time,p1_rd1_Td="1 of 3",p2_rd1_Td="0 of 1")

class MatchupReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name);(root/"raw").mkdir()
        self.root=root
        (root/"regional_history").mkdir()
        self.regional_file=root/"regional_history"/"regional_fight_history.csv"
        self.regional_file.write_text("event_date,fighter,opponent,result,method,round,organization,source,source_url,source_url_2,verification,source_cutoff_date\n")
        self.rows=[
          fight("2025-01-01","Favorite","A","L","SUBMISSION",1,"2:00"),
          fight("2025-03-01","Favorite","B"),
          fight("2025-04-01","Opponent","C","W","SUBMISSION",1,"1:10"),
          fight("2025-06-01","Opponent","D","W","KO/TKO",1,"1:20"),
          # Same event & future records must never be observed by the pre-bet gate.
          fight("2026-10-10","Favorite","Opponent","W","SUBMISSION",1,"2:00"),
          fight("2026-11-01","Opponent","E","W","SUBMISSION",1,"2:00"),
        ]
        with (root/"raw"/"competitions.csv").open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(self.rows)
        self.pred={"fighter_a":"Favorite","fighter_b":"Opponent","market_a":0.8,
                   "market_b":0.2,"p_a":.77,"p_b":.23,"ev_a_pct":-4.5}
    def tearDown(self):
        self.tmp.cleanup()
    def test_submission_collision_holds(self):
        r=review.assess_selection(self.pred,"Favorite","2026-10-10",self.root)
        self.assertEqual(r["status"],"HOLD")
        self.assertIn("SUBMISSION_TRAP_OPPONENT_SUB_WINS_AND_FAVORITE_SUB_LOSSES",r["reasons"])
        self.assertEqual(r["favorite_history"]["submission_losses"],1)
        self.assertEqual(r["opponent_history"]["submission_wins"],1)
        self.assertEqual(r["opponent_history"]["fights"],2)
        self.assertIn("NONPOSITIVE_MODEL_EXPECTED_VALUE",r["warnings"])
    def test_wrestler_held_even_without_documented_ufc_submission_loss(self):
        # DWCS/regional losses can be missing from UFCStats history.
        # Prevent a false green light when a takedown-heavy favorite meets
        # an opponent with a known submission finish.
        self.rows[0]["method"]="DECISION - UNANIMOUS"
        with (self.root/"raw"/"competitions.csv").open("w",newline="") as fh:
            writer=csv.DictWriter(fh,fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(self.rows)
        review._cached_index.cache_clear()
        r=review.assess_selection(self.pred,"Favorite","2026-10-10",self.root)
        self.assertEqual(r["favorite_history"]["submission_losses"],0)
        self.assertGreater(r["favorite_history"]["td_attempts_per15"],3)
        self.assertEqual(r["status"],"HOLD")
        self.assertIn("WRESTLER_TAKEDOWN_ENTRY_VS_DOCUMENTED_SUBMISSION_FINISHER",r["reasons"])
    def test_each_side_is_its_own_matchup(self):
        r=review.assess_selection(self.pred,"Opponent","2026-10-10",self.root)
        self.assertEqual(r["status"],"PASS")
        self.assertEqual(r["favorite_history"]["submission_wins"],1)
    def test_incomplete_prior_records_hold(self):
        r=review.assess_selection(self.pred,"Favorite","2025-03-01",self.root)
        self.assertIn("FAVORITE_INSUFFICIENT_VERIFIED_PRO_FIGHT_CONTEXT",r["reasons"])
    def test_missing_raw_history_holds_not_passes(self):
        r=review.assess_selection(self.pred,"Favorite","2026-10-10",self.root/"missing")
        self.assertEqual(r["status"],"HOLD")
        self.assertIn("UFC_OR_REGIONAL_HISTORY_UNAVAILABLE",r["reasons"])
    def test_verified_regional_submission_counted_before_fight(self):
        # Reproduce Herbert's May 2016 BAMMA rear naked choke.
        with self.regional_file.open("a") as f:
            f.write("2016-05-14,Jai Herbert,Tony Morgan,W,Submission,2,BAMMA 25,crosschecked_sherdog_espn,https://www.sherdog.com/fighter/Jai-Herbert-168551,https://www.espn.com/mma/fighter/history/_/id/4078246/jai-herbert,independently_checked,\n")
        r=review.assess_selection(
          dict(self.pred,fighter_a="Favorite",fighter_b="Jai Herbert"),
          "Favorite","2026-10-10",self.root)
        self.assertEqual(r["opponent_history"]["regional_submission_wins"],1)
        self.assertEqual(r["opponent_history"]["submission_wins"],1)
        self.assertEqual(r["opponent_history"]["independently_checked_regional_fights"],1)
    def test_old_herbert_regional_sub_is_caution_not_automatic_veto(self):
        with self.regional_file.open("a") as f:
            f.write("2016-05-14,Jai Herbert,Tony Morgan,W,Submission,2,BAMMA 25,verified,https://example.com,,independently_checked,\\n")
        # Favorite has a prior submission loss, but opponent's ONE submission
        # was ten years earlier; this must not trigger an automatic trap veto.
        r=review.assess_selection(
            dict(self.pred,fighter_a="Favorite",fighter_b="Jai Herbert"),
            "Favorite","2026-10-10",self.root)
        self.assertIn("HISTORICAL_ONLY_SUBMISSION_WIN_REQUIRES_RELEVANCE_REVIEW",r["warnings"])
        self.assertNotIn("SUBMISSION_TRAP_OPPONENT_SUB_WINS_AND_FAVORITE_SUB_LOSSES",r["reasons"])
    def test_future_regional_record_never_leaks(self):
        with self.regional_file.open("a") as f:
            f.write("2026-11-01,Opponent,New Fighter,W,Submission,1,Test,test,https://example.com,,archive_derived,\n")
        r=review.assess_selection(self.pred,"Favorite","2026-10-10",self.root)
        self.assertEqual(r["opponent_history"]["submission_wins"],1)
    def test_regional_duplicate_ufc_does_not_double_count(self):
        with self.regional_file.open("a") as f:
            f.write("2025-01-01,Favorite,A,L,Submission,1,Test,test,https://example.com,,archive_derived,\n")
        r=review.assess_selection(self.pred,"Favorite","2026-10-10",self.root)
        self.assertEqual(r["favorite_history"]["fights"],2)
        self.assertEqual(r["favorite_history"]["submission_losses"],1)
    def test_unknown_identity_holds(self):
        r=review.assess_selection(self.pred,"Stranger","2026-10-10",self.root)
        self.assertEqual(r["status"],"HOLD")

if __name__=="__main__": unittest.main()
