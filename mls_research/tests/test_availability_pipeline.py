from datetime import datetime, timezone
import unittest

from mls_research.availability_pipeline import (
    AvailabilityEntry,
    TeamAvailabilitySnapshot,
    build_team_availability_features,
    parse_status_line,
    select_snapshot_asof,
)


class AvailabilityPipelineTests(unittest.TestCase):
    def test_parses_legacy_status_row(self):
        row = parse_status_line("Luciano Acosta – Foot (Questionable)")
        self.assertIsNotNone(row)
        self.assertEqual(row.player, "Luciano Acosta")
        self.assertEqual(row.status, "QUESTIONABLE")
        self.assertEqual(row.reason_category, "INJURY")

    def test_parses_current_status_row(self):
        row = parse_status_line("OUT: Zack Steffen (shoulder)")
        self.assertIsNotNone(row)
        self.assertEqual(row.player, "Zack Steffen")
        self.assertEqual(row.status, "OUT")

    def test_suspension_is_separate(self):
        row = parse_status_line("Olwethu Makhanya - Suspended (Out)")
        self.assertEqual(row.status, "SUSPENDED")
        self.assertEqual(row.reason_category, "SUSPENSION")

    def test_future_snapshot_is_never_selected(self):
        before = TeamAvailabilitySnapshot(
            team="FC Cincinnati",
            observed_at=datetime(2026, 9, 18, 12, tzinfo=timezone.utc),
            source_url="before",
            entries=[],
        )
        future = TeamAvailabilitySnapshot(
            team="FC Cincinnati",
            observed_at=datetime(2026, 9, 20, 12, tzinfo=timezone.utc),
            source_url="future",
            entries=[AvailabilityEntry("Example", "OUT", "Knee", "INJURY")],
        )
        kickoff = datetime(2026, 9, 19, 23, 30, tzinfo=timezone.utc)
        picked = select_snapshot_asof([future, before], "FC Cincinnati", kickoff)
        self.assertEqual(picked.source_url, "before")

    def test_questionable_is_fractional_player_value(self):
        snap = TeamAvailabilitySnapshot(
            team="FC Cincinnati",
            observed_at=datetime(2026, 9, 19, 12, tzinfo=timezone.utc),
            source_url="test",
            entries=[
                AvailabilityEntry("Star Player", "OUT", "Knee", "INJURY"),
                AvailabilityEntry("Maybe Player", "QUESTIONABLE", "Ankle", "INJURY"),
            ],
        )
        strengths = [
            {
                "team_name": "FC Cincinnati",
                "player_name": "Star Player",
                "impact_index": 0.8,
                "minutes_rate": 0.9,
                "gplus_p96": 0.2,
                "xg_xa_p96": 0.4,
                "share_team_touches": 0.1,
                "salary_share": 0.15,
            },
            {
                "team_name": "FC Cincinnati",
                "player_name": "Maybe Player",
                "impact_index": 0.6,
                "minutes_rate": 0.7,
                "gplus_p96": 0.1,
                "xg_xa_p96": 0.2,
                "share_team_touches": 0.08,
                "salary_share": 0.1,
            },
        ]
        features = build_team_availability_features(
            snap,
            strengths,
            kickoff=datetime(2026, 9, 19, 23, 30, tzinfo=timezone.utc),
        )
        self.assertAlmostEqual(features["missing_impact_index"], 0.8 + 0.35 * 0.6)
        self.assertEqual(features["availability_unresolved_players"], 0)


if __name__ == "__main__":
    unittest.main()
