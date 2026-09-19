from datetime import datetime, timezone
from pathlib import Path
import unittest

from mls_research.collect_mls_status import parse_status_html


class StatusCollectorTests(unittest.TestCase):
    def test_report_is_parsed_as_complete_team_snapshots(self):
        fixture = Path(__file__).with_name("fixture_status.html").read_text(encoding="utf-8")
        snapshots = parse_status_html(
            fixture,
            source_url="https://example.test/status",
            retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        )
        by_team = {snap.team: snap for snap in snapshots}
        self.assertEqual(set(by_team), {"FC Cincinnati", "Austin FC", "Colorado Rapids"})
        self.assertEqual(len(by_team["Austin FC"].entries), 0)
        self.assertEqual(by_team["FC Cincinnati"].matchday, 27)
        self.assertEqual(
            by_team["FC Cincinnati"].observed_at,
            datetime(2026, 9, 18, 16, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(len(by_team["FC Cincinnati"].entries), 2)


if __name__ == "__main__":
    unittest.main()
