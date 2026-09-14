from datetime import datetime, timedelta, timezone

from production_approval_guard import approve

NOW = datetime(2026, 9, 14, 5, 15, tzinfo=timezone.utc)


def good(game_id="2026_04_TEST", week=4):
    return {
        "game_id": game_id,
        "season": 2026,
        "week": week,
        "method": "base_ypp_v1",
        "selection_side": "away",
        "prior_games": 3,
        "opponent_prior_games": 3,
        "pre_off_ypp": 6.3,
        "opp_pre_def_ypp_allowed": 6.0,
        "kickoff": (NOW + timedelta(days=1)).isoformat(),
        "odds": {
            "moneyline": -150,
            "retrieved_at": NOW.isoformat(),
            "bookmaker": "test",
        },
    }


def reasons(record):
    approved, rejected = approve([record], NOW)
    return approved, set(rejected[0]["reasons"]) if rejected else set()


def main():
    # Regression: a Week-1 favorite such as the LAC/ARI failure mode must never pass.
    lac = good("2026_01_LAC_ARI", week=1)
    lac["odds"]["moneyline"] = -500
    approved, why = reasons(lac)
    assert not approved and "insufficient_history" in why

    approved, rejected = approve([good()], NOW)
    assert len(approved) == 1 and not rejected

    missing = good("missing")
    del missing["pre_off_ypp"]
    approved, why = reasons(missing)
    assert not approved and "missing_feature" in why

    unsupported = good("unsupported")
    unsupported["method"] = "pass_defense_offense24"
    approved, why = reasons(unsupported)
    assert not approved and "unsupported_method" in why

    started = good("started")
    started["kickoff"] = (NOW - timedelta(minutes=1)).isoformat()
    approved, why = reasons(started)
    assert not approved and "started_game" in why

    stale = good("stale")
    stale["odds"]["retrieved_at"] = (NOW - timedelta(hours=3)).isoformat()
    approved, why = reasons(stale)
    assert not approved and "stale_odds" in why

    wrong_side = good("home")
    wrong_side["selection_side"] = "home"
    approved, why = reasons(wrong_side)
    assert not approved and "selection_not_road_favorite" in why

    low_ypp = good("low-ypp")
    low_ypp["pre_off_ypp"] = 6.19
    approved, why = reasons(low_ypp)
    assert not approved and "method_conditions_failed" in why

    records = [good("dup"), good("dup")]
    approved, rejected = approve(records, NOW)
    assert len(approved) == 1
    assert any("duplicate" in r["reasons"] for r in rejected)

    print("NFL production approval guard regression tests: PASS")


if __name__ == "__main__":
    main()
