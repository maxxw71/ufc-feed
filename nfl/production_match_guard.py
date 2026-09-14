"""Fail-closed identity gate for NFL records sent to Appwiza/email/bet tracking.

A research candidate is never safe to publish merely because its metrics match.
Before a record leaves the scanner, this module binds it to the authoritative
schedule row and to the exact ESPN odds event, bookmaker, side, team and price.

Safety policy: ambiguity means rejection. Missing identity fields, mismatched
teams, duplicate game records, stale/wrong-event odds, a mismatched bookmaker
payload, or a guard exception must never be converted into a default selection.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
import re
from typing import Any, Iterable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
MAX_ODDS_AGE_SECONDS = 15 * 60
MAX_KICKOFF_DRIFT_SECONDS = 90
MIN_KICKOFF_LEAD_SECONDS = 5 * 60
GAME_ID_RE = re.compile(r"^(?P<season>\d{4})_(?P<week>\d{2})_(?P<away>[A-Z0-9]{2,3})_(?P<home>[A-Z0-9]{2,3})$")
ODDS_PATH_RE = re.compile(r"/events/(?P<event>\d+)/competitions/(?P<competition>\d+)/odds/?$")
TEAM_REF_RE = re.compile(r"/teams/(?P<team>\d+)(?:\?|$)")

# ESPN NFL team IDs are stable franchise identifiers used in the odds payload's
# team.$ref values. Historical aliases never reach production schedule rows.
ESPN_TEAM_IDS = {
    "ATL": "1", "BUF": "2", "CHI": "3", "CIN": "4", "CLE": "5",
    "DAL": "6", "DEN": "7", "DET": "8", "GB": "9", "TEN": "10",
    "IND": "11", "KC": "12", "LV": "13", "LA": "14", "MIA": "15",
    "MIN": "16", "NE": "17", "NO": "18", "NYG": "19", "NYJ": "20",
    "PHI": "21", "ARI": "22", "PIT": "23", "LAC": "24", "SF": "25",
    "SEA": "26", "TB": "27", "WAS": "28", "CAR": "29", "JAX": "30",
    "BAL": "33", "HOU": "34",
}


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n) or n != int(n):
        return None
    return int(n)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _espn_id(value: Any) -> str | None:
    n = _integer(value)
    return str(n) if n is not None and n > 0 else None


def _schedule_kickoff(row: dict[str, Any]) -> datetime | None:
    gameday = _text(row.get("gameday"))
    gametime = _text(row.get("gametime"))
    if not gameday or not gametime:
        return None
    try:
        return datetime.fromisoformat(f"{gameday}T{gametime}").replace(tzinfo=NY).astimezone(timezone.utc)
    except ValueError:
        return None


def _rows(schedule: Any) -> list[dict[str, Any]]:
    if schedule is None:
        return []
    if hasattr(schedule, "to_dict"):
        try:
            data = schedule.to_dict("records")
            return [dict(row) for row in data]
        except Exception:
            pass
    try:
        return [dict(row) for row in schedule]
    except Exception:
        return []


def _source_event_ids(source: Any) -> tuple[str, str] | None:
    source = _text(source)
    if not source:
        return None
    try:
        parsed = urlparse(source)
    except Exception:
        return None
    if parsed.scheme != "https" or parsed.netloc.lower() != "sports.core.api.espn.com":
        return None
    match = ODDS_PATH_RE.search(parsed.path)
    if not match:
        return None
    return match.group("event"), match.group("competition")


def _team_ref_id(side_payload: Any) -> str | None:
    if not isinstance(side_payload, dict):
        return None
    team = side_payload.get("team")
    if not isinstance(team, dict):
        return None
    ref = _text(team.get("$ref"))
    match = TEAM_REF_RE.search(ref)
    return match.group("team") if match else None


def _moneyline_from_side(side_payload: Any) -> float | None:
    if not isinstance(side_payload, dict):
        return None
    current = side_payload.get("current")
    if not isinstance(current, dict):
        return None
    line = current.get("moneyLine")
    if not isinstance(line, dict):
        return None
    return _number(line.get("american"))


def _raw_odds_identity_reasons(
    odds: dict[str, Any],
    espn: str | None,
    side: str,
    home: str,
    away: str,
    selected_team: str,
    price: float | None,
) -> list[str]:
    """Prove the selected bookmaker's raw ESPN payload matches this exact bet."""
    raw = odds.get("raw")
    if not isinstance(raw, dict):
        return ["missing_odds_raw"]
    items = raw.get("items")
    if not isinstance(items, list) or not items:
        return ["missing_odds_raw_items"]

    bookmaker = _text(odds.get("bookmaker"))
    if not bookmaker:
        return ["missing_bookmaker"]
    if side not in {"home", "away"}:
        return ["raw_odds_identity_uncheckable"]

    expected_selected_id = ESPN_TEAM_IDS.get(selected_team)
    opponent = away if side == "home" else home
    expected_opponent_id = ESPN_TEAM_IDS.get(opponent)
    if expected_selected_id is None or expected_opponent_id is None:
        return ["unknown_team_identity"]

    side_key = "homeTeamOdds" if side == "home" else "awayTeamOdds"
    other_key = "awayTeamOdds" if side == "home" else "homeTeamOdds"
    provider_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        provider = item.get("provider")
        if isinstance(provider, dict) and _text(provider.get("name")) == bookmaker:
            provider_items.append(item)

    if not provider_items:
        return ["bookmaker_not_in_raw_odds"]
    if len(provider_items) != 1:
        return ["ambiguous_bookmaker_raw_odds"]

    item = provider_items[0]
    reasons: list[str] = []

    item_ref = _text(item.get("$ref"))
    if espn is None or f"/events/{espn}/competitions/{espn}/odds/" not in item_ref:
        reasons.append("raw_odds_event_mismatch")

    selected_payload = item.get(side_key)
    opponent_payload = item.get(other_key)
    if _team_ref_id(selected_payload) != expected_selected_id:
        reasons.append("raw_odds_selected_team_mismatch")
    if _team_ref_id(opponent_payload) != expected_opponent_id:
        reasons.append("raw_odds_opponent_team_mismatch")

    raw_price = _moneyline_from_side(selected_payload)
    if price is None or raw_price is None or abs(raw_price - price) > 1e-9:
        reasons.append("raw_odds_price_mismatch")

    return reasons


def _reject(record: dict[str, Any], reasons: Iterable[str]) -> dict[str, Any]:
    side = _text(record.get("selection_side")).lower()
    return {
        "game_id": record.get("game_id"),
        "season": record.get("season"),
        "week": record.get("week"),
        "home": record.get("home"),
        "away": record.get("away"),
        "selection_side": side or None,
        "selected_team": record.get("selected_team"),
        "rules": record.get("rules"),
        "reasons": sorted(set(reasons)),
    }


def filter_records(records: Iterable[dict[str, Any]], schedule: Any, now: datetime | None = None):
    """Return (approved, rejected), validating every output against schedule + raw odds identity."""
    now = _utc(now or datetime.now(timezone.utc))
    if now is None:
        raise ValueError("now_must_be_timezone_aware")

    schedule_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _rows(schedule):
        gid = _text(row.get("game_id"))
        if gid:
            schedule_index[gid].append(row)

    raw_records: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    for source in records:
        try:
            raw_records.append(dict(source))
        except Exception:
            malformed.append({"game_id": None, "reasons": ["malformed_record"]})

    counts: dict[str, int] = defaultdict(int)
    for record in raw_records:
        counts[_text(record.get("game_id"))] += 1

    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = list(malformed)

    for record in raw_records:
        reasons: list[str] = []
        game_id = _text(record.get("game_id"))
        if not game_id:
            reasons.append("missing_game_id")
        elif counts[game_id] != 1:
            reasons.append("duplicate_game_id")

        encoded = GAME_ID_RE.fullmatch(game_id) if game_id else None
        if not encoded:
            reasons.append("invalid_game_id")

        schedule_matches = schedule_index.get(game_id, []) if game_id else []
        if not schedule_matches:
            reasons.append("game_not_in_schedule")
            scheduled = None
        elif len(schedule_matches) != 1:
            reasons.append("ambiguous_schedule_game")
            scheduled = None
        else:
            scheduled = schedule_matches[0]

        season = _integer(record.get("season"))
        week = _integer(record.get("week"))
        home = _text(record.get("home"))
        away = _text(record.get("away"))
        espn = _espn_id(record.get("espn"))
        kickoff = _utc(record.get("kickoff"))

        if season is None:
            reasons.append("missing_season")
        if week is None:
            reasons.append("missing_week")
        if not home or not away or home == away:
            reasons.append("invalid_teams")
        if home not in ESPN_TEAM_IDS or away not in ESPN_TEAM_IDS:
            reasons.append("unknown_team_identity")
        if espn is None:
            reasons.append("missing_espn_event")
        if kickoff is None:
            reasons.append("invalid_kickoff")
        elif (kickoff - now).total_seconds() < MIN_KICKOFF_LEAD_SECONDS:
            reasons.append("game_started_or_too_close")

        if encoded:
            if season != int(encoded.group("season")):
                reasons.append("game_id_season_mismatch")
            if week != int(encoded.group("week")):
                reasons.append("game_id_week_mismatch")
            if away != encoded.group("away") or home != encoded.group("home"):
                reasons.append("game_id_team_mismatch")

        if scheduled is not None:
            s_season = _integer(scheduled.get("season"))
            s_week = _integer(scheduled.get("week"))
            s_home = _text(scheduled.get("home_team"))
            s_away = _text(scheduled.get("away_team"))
            s_espn = _espn_id(scheduled.get("espn"))
            s_kickoff = _schedule_kickoff(scheduled)
            if season != s_season:
                reasons.append("schedule_season_mismatch")
            if week != s_week:
                reasons.append("schedule_week_mismatch")
            if home != s_home or away != s_away:
                reasons.append("schedule_team_mismatch")
            if espn != s_espn:
                reasons.append("schedule_espn_mismatch")
            if s_kickoff is None or kickoff is None or abs((kickoff - s_kickoff).total_seconds()) > MAX_KICKOFF_DRIFT_SECONDS:
                reasons.append("schedule_kickoff_mismatch")

        side = _text(record.get("selection_side")).lower()
        selected_team = _text(record.get("selected_team"))
        if side not in {"home", "away"}:
            reasons.append("missing_or_invalid_selection_side")
        else:
            expected_selected = home if side == "home" else away
            if not selected_team:
                reasons.append("missing_selected_team")
            elif selected_team != expected_selected:
                reasons.append("selected_team_mismatch")

        rules = record.get("rules")
        if not isinstance(rules, list) or not rules or any(not isinstance(rule, str) or not rule.strip() for rule in rules):
            reasons.append("missing_or_invalid_rules")

        odds = record.get("odds")
        if not isinstance(odds, dict):
            reasons.append("missing_odds")
            odds = {}

        price = _number(odds.get("moneyline"))
        if price is None or abs(price) < 100:
            reasons.append("missing_or_invalid_moneyline")
        if not _text(odds.get("bookmaker")):
            reasons.append("missing_bookmaker")

        retrieved_at = _utc(odds.get("retrieved_at"))
        if retrieved_at is None:
            reasons.append("missing_odds_timestamp")
        else:
            age = (now - retrieved_at).total_seconds()
            if age < -300 or age > MAX_ODDS_AGE_SECONDS:
                reasons.append("stale_odds")

        source_ids = _source_event_ids(odds.get("source"))
        if source_ids is None:
            reasons.append("invalid_odds_source")
        elif espn is None or source_ids != (espn, espn):
            reasons.append("odds_event_mismatch")

        odds_side = _text(odds.get("market_side")).lower()
        odds_team = _text(odds.get("market_team"))
        if odds_side != side:
            reasons.append("odds_side_mismatch")
        if odds_team != selected_team or not odds_team:
            reasons.append("odds_team_mismatch")

        reasons.extend(_raw_odds_identity_reasons(odds, espn, side, home, away, selected_team, price))

        if reasons:
            rejected.append(_reject(record, reasons))
        else:
            record["integrity_status"] = "approved"
            record["integrity_checked_at"] = now.isoformat()
            approved.append(record)

    return approved, rejected

# deployment retrigger marker 2026-09-14
