from datetime import datetime, timedelta, timezone

from production_match_guard import filter_records

NOW = datetime(2026, 9, 14, 5, 35, tzinfo=timezone.utc)
ESPN = "401772900"
GAME_ID = "2026_04_ARI_LAC"
KICKOFF = datetime(2026, 10, 4, 20, 5, tzinfo=timezone.utc)


def schedule():
    return [{
        "game_id": GAME_ID,
        "season": 2026,
        "week": 4,
        "away_team": "ARI",
        "home_team": "LAC",
        "espn": int(ESPN),
        # 4:05 PM Eastern on Oct. 4, 2026 = 20:05 UTC.
        "gameday": "2026-10-04",
        "gametime": "16:05",
    }]


def good():
    return {
        "game_id": GAME_ID,
        "season": 2026,
        "week": 4,
        "away": "ARI",
        "home": "LAC",
        "espn": ESPN,
        "kickoff": KICKOFF.isoformat(),
        "selection_side": "away",
        "selected_team": "ARI",
        "rules": ["test_rule"],
        "odds": {
            "bookmaker": "FanDuel",
            "moneyline": -150,
            "retrieved_at": NOW.isoformat(),
            "source": f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{ESPN}/competitions/{ESPN}/odds",
            "market_side": "away",
            "market_team": "ARI",
        },
    }


def why(record, sched=None, now=NOW):
    approved, rejected = filter_records([record], schedule() if sched is None else sched, now)
    reasons = set(rejected[0]["reasons"]) if rejected else set()
    return approved, reasons


def main():
    approved, rejected = filter_records([good()], schedule(), NOW)
    assert len(approved) == 1 and not rejected

    # Critical regression: a record can no longer silently default to the home team.
    missing_side = good()
    del missing_side["selection_side"]
    approved, reasons = why(missing_side)
    assert not approved and "missing_or_invalid_selection_side" in reasons

    missing_team = good()
    del missing_team["selected_team"]
    approved, reasons = why(missing_team)
    assert not approved and "missing_selected_team" in reasons

    wrong_team = good()
    wrong_team["selected_team"] = "LAC"
    approved, reasons = why(wrong_team)
    assert not approved and "selected_team_mismatch" in reasons

    # Odds must be for the exact selected side/team, not merely the same fixture.
    wrong_odds_side = good()
    wrong_odds_side["odds"]["market_side"] = "home"
    wrong_odds_side["odds"]["market_team"] = "LAC"
    approved, reasons = why(wrong_odds_side)
    assert not approved and {"odds_side_mismatch", "odds_team_mismatch"} <= reasons

    # Odds must also belong to the exact ESPN event attached to the schedule row.
    wrong_event = good()
    wrong_event["odds"]["source"] = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/999/competitions/999/odds"
    approved, reasons = why(wrong_event)
    assert not approved and "odds_event_mismatch" in reasons

    wrong_week = good()
    wrong_week["week"] = 5
    approved, reasons = why(wrong_week)
    assert not approved and {"game_id_week_mismatch", "schedule_week_mismatch"} <= reasons

    swapped = good()
    swapped["away"], swapped["home"] = swapped["home"], swapped["away"]
    approved, reasons = why(swapped)
    assert not approved and "game_id_team_mismatch" in reasons and "schedule_team_mismatch" in reasons

    wrong_espn = good()
    wrong_espn["espn"] = "401000000"
    approved, reasons = why(wrong_espn)
    assert not approved and "schedule_espn_mismatch" in reasons

    wrong_kickoff = good()
    wrong_kickoff["kickoff"] = (KICKOFF + timedelta(hours=1)).isoformat()
    approved, reasons = why(wrong_kickoff)
    assert not approved and "schedule_kickoff_mismatch" in reasons

    stale = good()
    stale["odds"]["retrieved_at"] = (NOW - timedelta(minutes=16)).isoformat()
    approved, reasons = why(stale)
    assert not approved and "stale_odds" in reasons

    no_price = good()
    no_price["odds"]["moneyline"] = None
    approved, reasons = why(no_price)
    assert not approved and "missing_or_invalid_moneyline" in reasons

    started = good()
    approved, reasons = why(started, now=KICKOFF - timedelta(minutes=4))
    assert not approved and "game_started_or_too_close" in reasons

    # Two records for one event are ambiguous. Reject both rather than choose one.
    first, second = good(), good()
    second["selection_side"] = "home"
    second["selected_team"] = "LAC"
    second["odds"]["market_side"] = "home"
    second["odds"]["market_team"] = "LAC"
    approved, rejected = filter_records([first, second], schedule(), NOW)
    assert not approved and len(rejected) == 2
    assert all("duplicate_game_id" in item["reasons"] for item in rejected)

    # A missing or duplicated schedule row is never guessed around.
    approved, reasons = why(good(), sched=[])
    assert not approved and "game_not_in_schedule" in reasons
    approved, reasons = why(good(), sched=schedule() + schedule())
    assert not approved and "ambiguous_schedule_game" in reasons

    print("NFL production matchup integrity regression tests: PASS")


if __name__ == "__main__":
    main()
