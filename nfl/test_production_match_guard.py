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


def raw_odds():
    return {
        "items": [{
            "$ref": f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{ESPN}/competitions/{ESPN}/odds/1?lang=en&region=us",
            "provider": {"name": "FanDuel"},
            "awayTeamOdds": {
                "team": {"$ref": "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/teams/22?lang=en&region=us"},
                "current": {"moneyLine": {"american": -150}},
            },
            "homeTeamOdds": {
                "team": {"$ref": "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/teams/24?lang=en&region=us"},
                "current": {"moneyLine": {"american": 130}},
            },
        }]
    }


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
            "raw": raw_odds(),
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

    # M1/M2 regression: being better than a terrible opponent is not enough.
    # The selected home team must have finished the prior season at least .500.
    m1 = good()
    m1["selection_side"] = "home"
    m1["selected_team"] = "LAC"
    m1["rules"] = ["original"]
    m1["home_record"] = {"wins": 9, "losses": 8, "ties": 0, "pct": 9/17}
    m1["m1_enriched_veto_status"] = "PASS"
    m1["opp_last_rank_def_allowed_giveaway_rate"] = 20.0
    m1["odds"]["market_side"] = "home"
    m1["odds"]["market_team"] = "LAC"
    m1["odds"]["moneyline"] = 130
    m1["odds"]["raw"]["items"][0]["homeTeamOdds"]["current"]["moneyLine"]["american"] = 130
    approved, reasons = why(m1)
    assert approved and not reasons

    m1_500 = good()
    m1_500["selection_side"] = "home"
    m1_500["selected_team"] = "LAC"
    m1_500["rules"] = ["M1"]
    m1_500["home_record"] = {"wins": 8, "losses": 8, "ties": 1, "pct": .5}
    m1_500["m1_enriched_veto_status"] = "PASS"
    m1_500["opp_last_rank_def_allowed_giveaway_rate"] = 20.0
    m1_500["odds"]["market_side"] = "home"
    m1_500["odds"]["market_team"] = "LAC"
    m1_500["odds"]["moneyline"] = 130
    m1_500["odds"]["raw"]["items"][0]["homeTeamOdds"]["current"]["moneyLine"]["american"] = 130
    approved, reasons = why(m1_500)
    assert approved and not reasons

    missing_m1_enriched = good()
    missing_m1_enriched["selection_side"] = "home"
    missing_m1_enriched["selected_team"] = "LAC"
    missing_m1_enriched["rules"] = ["original"]
    missing_m1_enriched["home_record"] = {"wins": 9, "losses": 8, "ties": 0, "pct": 9/17}
    missing_m1_enriched["odds"]["market_side"] = "home"
    missing_m1_enriched["odds"]["market_team"] = "LAC"
    missing_m1_enriched["odds"]["moneyline"] = 130
    missing_m1_enriched["odds"]["raw"]["items"][0]["homeTeamOdds"]["current"]["moneyLine"]["american"] = 130
    approved, reasons = why(missing_m1_enriched)
    assert not approved and "missing_m1_enriched_veto_status" in reasons

    vetoed_m1 = good()
    vetoed_m1["selection_side"] = "home"
    vetoed_m1["selected_team"] = "LAC"
    vetoed_m1["rules"] = ["M1"]
    vetoed_m1["home_record"] = {"wins": 9, "losses": 8, "ties": 0, "pct": 9/17}
    vetoed_m1["m1_enriched_veto_status"] = "VETO"
    vetoed_m1["opp_last_rank_def_allowed_giveaway_rate"] = 10.0
    vetoed_m1["odds"]["market_side"] = "home"
    vetoed_m1["odds"]["market_team"] = "LAC"
    vetoed_m1["odds"]["moneyline"] = 130
    vetoed_m1["odds"]["raw"]["items"][0]["homeTeamOdds"]["current"]["moneyLine"]["american"] = 130
    approved, reasons = why(vetoed_m1)
    assert not approved and "m1_enriched_veto_not_pass" in reasons and "m1_enriched_veto_triggered" in reasons

    both = good()
    both["selection_side"] = "home"
    both["selected_team"] = "LAC"
    both["rules"] = ["original","stricter"]
    both["home_record"] = {"wins": 9, "losses": 8, "ties": 0, "pct": 9/17}
    both["odds"]["market_side"] = "home"
    both["odds"]["market_team"] = "LAC"
    both["odds"]["moneyline"] = 130
    both["odds"]["raw"]["items"][0]["homeTeamOdds"]["current"]["moneyLine"]["american"] = 130
    approved, reasons = why(both)
    assert approved and not reasons
    assert approved[0]["rules"] == ["stricter"]
    assert approved[0]["method_suppressions"][0]["method"] == "M1"

    losing_record = good()
    losing_record["selection_side"] = "home"
    losing_record["selected_team"] = "LAC"
    losing_record["rules"] = ["original", "stricter"]
    losing_record["home_record"] = {"wins": 8, "losses": 9, "ties": 0, "pct": 8/17}
    losing_record["odds"]["market_side"] = "home"
    losing_record["odds"]["market_team"] = "LAC"
    losing_record["odds"]["moneyline"] = 130
    losing_record["odds"]["raw"]["items"][0]["homeTeamOdds"]["current"]["moneyLine"]["american"] = 130
    approved, reasons = why(losing_record)
    assert not approved and "prior_season_home_record_below_500" in reasons

    missing_prior_record = good()
    missing_prior_record["selection_side"] = "home"
    missing_prior_record["selected_team"] = "LAC"
    missing_prior_record["rules"] = ["M2"]
    missing_prior_record["odds"]["market_side"] = "home"
    missing_prior_record["odds"]["market_team"] = "LAC"
    missing_prior_record["odds"]["moneyline"] = 130
    missing_prior_record["odds"]["raw"]["items"][0]["homeTeamOdds"]["current"]["moneyLine"]["american"] = 130
    approved, reasons = why(missing_prior_record)
    assert not approved and "missing_prior_season_home_record" in reasons

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

    # Raw bookmaker payload is authoritative; app-supplied market_team cannot override it.
    wrong_raw_team = good()
    wrong_raw_team["odds"]["raw"]["items"][0]["awayTeamOdds"]["team"]["$ref"] = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/teams/24?lang=en&region=us"
    approved, reasons = why(wrong_raw_team)
    assert not approved and "raw_odds_selected_team_mismatch" in reasons

    wrong_raw_price = good()
    wrong_raw_price["odds"]["raw"]["items"][0]["awayTeamOdds"]["current"]["moneyLine"]["american"] = -145
    approved, reasons = why(wrong_raw_price)
    assert not approved and "raw_odds_price_mismatch" in reasons

    # Two records for one event are ambiguous. Reject both rather than choose one.
    first, second = good(), good()
    second["selection_side"] = "home"
    second["selected_team"] = "LAC"
    second["odds"]["market_side"] = "home"
    second["odds"]["market_team"] = "LAC"
    second["odds"]["moneyline"] = 130
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
