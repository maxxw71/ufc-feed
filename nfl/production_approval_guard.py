"""Fail-closed approval contract for production NFL bet selections.

Research candidates are NOT production bets. Only explicitly allow-listed,
fully populated records can pass this gate. Website and email must consume the
same returned approved list.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

APPROVED_METHODS = frozenset({"base_ypp_v1"})
MIN_PRIOR_GAMES = 3
MIN_WEEK = 4
MIN_MARKET_PROB = 0.55
MIN_OFFENSE_YPP = 6.2
MIN_OPP_DEF_YPP_ALLOWED = 5.8
MAX_ODDS_AGE_SECONDS = 2 * 60 * 60


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def implied_probability(american_odds: float) -> float | None:
    if american_odds == 0:
        return None
    if american_odds < 0:
        x = abs(american_odds)
        return x / (x + 100.0)
    return 100.0 / (american_odds + 100.0)


def _reject(rejected: list[dict[str, Any]], record: dict[str, Any], reasons: list[str]) -> None:
    rejected.append({
        "game_id": record.get("game_id"),
        "method": record.get("method") or record.get("approved_method"),
        "week": record.get("week"),
        "reasons": sorted(set(reasons)),
    })


def approve(records: Iterable[dict[str, Any]], now: datetime | None = None):
    """Return (approved, rejected) and fail closed on every ambiguity/error."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_games: set[str] = set()

    for source in records:
        try:
            record = dict(source)
        except Exception:
            rejected.append({"game_id": None, "method": None, "week": None, "reasons": ["malformed_record"]})
            continue

        reasons: list[str] = []
        game_id = str(record.get("game_id") or "").strip()
        if not game_id:
            reasons.append("missing_game_id")
        elif game_id in seen_games:
            reasons.append("duplicate")
        else:
            seen_games.add(game_id)

        method = record.get("method") or record.get("approved_method")
        if method not in APPROVED_METHODS:
            reasons.append("unsupported_method")

        week = _integer(record.get("week"))
        prior_games = _integer(record.get("prior_games"))
        opponent_prior_games = _integer(record.get("opponent_prior_games"))
        if week is None or week < MIN_WEEK:
            reasons.append("insufficient_history")
        if prior_games is None or opponent_prior_games is None or min(prior_games, opponent_prior_games) < MIN_PRIOR_GAMES:
            reasons.append("insufficient_history")

        kickoff = _utc(record.get("kickoff"))
        if kickoff is None:
            reasons.append("missing_kickoff")
        elif kickoff <= now:
            reasons.append("started_game")

        if str(record.get("selection_side") or "").lower() != "away":
            reasons.append("selection_not_road_favorite")

        odds = record.get("odds")
        if not isinstance(odds, dict):
            reasons.append("missing_odds")
            odds = {}
        moneyline = _number(odds.get("moneyline"))
        if moneyline is None:
            reasons.append("missing_odds")
        else:
            prob = implied_probability(moneyline)
            if moneyline >= 0 or prob is None or prob < MIN_MARKET_PROB:
                reasons.append("market_filter_failed")

        retrieved_at = _utc(odds.get("retrieved_at"))
        if retrieved_at is None:
            reasons.append("missing_odds_timestamp")
        else:
            age = (now - retrieved_at).total_seconds()
            if age < -300 or age > MAX_ODDS_AGE_SECONDS:
                reasons.append("stale_odds")

        offense_ypp = _number(record.get("pre_off_ypp"))
        opp_def_ypp = _number(record.get("opp_pre_def_ypp_allowed"))
        if offense_ypp is None or opp_def_ypp is None:
            reasons.append("missing_feature")
        else:
            if offense_ypp < MIN_OFFENSE_YPP or opp_def_ypp < MIN_OPP_DEF_YPP_ALLOWED:
                reasons.append("method_conditions_failed")

        if reasons:
            _reject(rejected, record, reasons)
        else:
            record["approval_status"] = "approved"
            record["approval_method"] = "base_ypp_v1"
            record["approved_at"] = now.isoformat()
            approved.append(record)

    return approved, rejected
