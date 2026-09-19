from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import hashlib
import json
import math
import re
import unicodedata
from typing import Iterable, Mapping, Sequence

STATUS_WEIGHTS = {
    "OUT": 1.0,
    "SUSPENDED": 1.0,
    "QUESTIONABLE": 0.35,
    "DOUBTFUL": 0.65,
    "AVAILABLE": 0.0,
}

TEAM_ALIASES = {
    "atlanta united": "Atlanta United",
    "atlanta united fc": "Atlanta United",
    "austin fc": "Austin FC",
    "charlotte fc": "Charlotte FC",
    "chicago fire": "Chicago Fire FC",
    "chicago fire fc": "Chicago Fire FC",
    "fc cincinnati": "FC Cincinnati",
    "colorado rapids": "Colorado Rapids",
    "columbus crew": "Columbus Crew",
    "dc united": "D.C. United",
    "d c united": "D.C. United",
    "d.c. united": "D.C. United",
    "fc dallas": "FC Dallas",
    "houston dynamo": "Houston Dynamo FC",
    "houston dynamo fc": "Houston Dynamo FC",
    "inter miami": "Inter Miami CF",
    "inter miami cf": "Inter Miami CF",
    "la galaxy": "LA Galaxy",
    "los angeles galaxy": "LA Galaxy",
    "lafc": "LAFC",
    "los angeles fc": "LAFC",
    "minnesota united": "Minnesota United FC",
    "minnesota united fc": "Minnesota United FC",
    "cf montreal": "CF Montréal",
    "cf montréal": "CF Montréal",
    "montreal impact": "CF Montréal",
    "nashville sc": "Nashville SC",
    "new england revolution": "New England Revolution",
    "new york city fc": "New York City FC",
    "nycfc": "New York City FC",
    "new york red bulls": "New York Red Bulls",
    "orlando city": "Orlando City SC",
    "orlando city sc": "Orlando City SC",
    "philadelphia union": "Philadelphia Union",
    "portland timbers": "Portland Timbers",
    "real salt lake": "Real Salt Lake",
    "san diego fc": "San Diego FC",
    "san jose earthquakes": "San Jose Earthquakes",
    "seattle sounders": "Seattle Sounders FC",
    "seattle sounders fc": "Seattle Sounders FC",
    "sporting kansas city": "Sporting Kansas City",
    "sporting kc": "Sporting Kansas City",
    "st louis city sc": "St. Louis CITY SC",
    "st. louis city sc": "St. Louis CITY SC",
    "saint louis city sc": "St. Louis CITY SC",
    "toronto fc": "Toronto FC",
    "vancouver whitecaps": "Vancouver Whitecaps FC",
    "vancouver whitecaps fc": "Vancouver Whitecaps FC",
}

LEGACY_LINE_RE = re.compile(
    r"^(?P<player>.+?)\s*[-–—]\s*(?P<reason>.+?)\s*\((?P<status>out|questionable|doubtful|suspended)\)\s*$",
    re.IGNORECASE,
)
PREFIX_LINE_RE = re.compile(
    r"^(?P<status>out|questionable|doubtful|suspended)\s*:\s*(?P<player>.+?)(?:\s*\((?P<reason>.*?)\))?\s*$",
    re.IGNORECASE,
)


def _ascii_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def canonical_team(value: str) -> str | None:
    key = _ascii_key(value)
    if key in TEAM_ALIASES:
        return TEAM_ALIASES[key]
    return None


def canonical_player_key(value: str) -> str:
    return _ascii_key(value)


def normalize_status(value: str) -> str:
    key = _ascii_key(value).upper().replace(" ", "_")
    aliases = {
        "OUT": "OUT",
        "QUESTIONABLE": "QUESTIONABLE",
        "DOUBTFUL": "DOUBTFUL",
        "SUSPENDED": "SUSPENDED",
        "AVAILABLE": "AVAILABLE",
    }
    return aliases.get(key, key)


def classify_reason(reason: str) -> str:
    key = _ascii_key(reason)
    if not key:
        return "UNSPECIFIED"
    if "suspend" in key or "red card" in key or "yellow card" in key:
        return "SUSPENSION"
    if "international" in key:
        return "INTERNATIONAL_DUTY"
    if "not due to injury" in key or "not injury" in key:
        return "NON_INJURY"
    if "illness" in key or "sick" in key:
        return "ILLNESS"
    if "concussion" in key or "head injury evaluation" in key:
        return "CONCUSSION"
    return "INJURY"


def parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class AvailabilityEntry:
    player: str
    status: str
    reason: str = ""
    reason_category: str = "UNSPECIFIED"

    @property
    def weight(self) -> float:
        return STATUS_WEIGHTS.get(self.status, 0.0)


@dataclass
class TeamAvailabilitySnapshot:
    team: str
    observed_at: datetime
    source_url: str
    entries: list[AvailabilityEntry] = field(default_factory=list)
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    matchday: int | None = None
    source_sha256: str | None = None
    snapshot_id: str | None = None

    def __post_init__(self) -> None:
        canonical = canonical_team(self.team)
        if canonical:
            self.team = canonical
        self.observed_at = parse_datetime(self.observed_at) or datetime.now(timezone.utc)
        self.published_at = parse_datetime(self.published_at)
        self.retrieved_at = parse_datetime(self.retrieved_at)
        if not self.snapshot_id:
            payload = "|".join(
                [
                    self.team,
                    self.observed_at.isoformat(),
                    self.source_url,
                    str(self.matchday or ""),
                    self.source_sha256 or "",
                ]
            )
            self.snapshot_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def to_dict(self) -> dict:
        out = asdict(self)
        for key in ("observed_at", "published_at", "retrieved_at"):
            if out[key] is not None:
                out[key] = out[key].isoformat()
        return out

    @classmethod
    def from_dict(cls, row: Mapping) -> "TeamAvailabilitySnapshot":
        entries = [AvailabilityEntry(**entry) for entry in row.get("entries", [])]
        return cls(
            team=row["team"],
            observed_at=parse_datetime(row["observed_at"]),
            source_url=row["source_url"],
            entries=entries,
            published_at=parse_datetime(row.get("published_at")),
            retrieved_at=parse_datetime(row.get("retrieved_at")),
            matchday=row.get("matchday"),
            source_sha256=row.get("source_sha256"),
            snapshot_id=row.get("snapshot_id"),
        )


def parse_status_line(text: str) -> AvailabilityEntry | None:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text or _ascii_key(text) in {"none", "no players listed"}:
        return None

    match = PREFIX_LINE_RE.match(text) or LEGACY_LINE_RE.match(text)
    if not match:
        return None

    status = normalize_status(match.group("status"))
    player = match.group("player").strip(" -–—:")
    reason = (match.groupdict().get("reason") or "").strip()

    # Some historical MLS rows encode "Suspended (Out)".
    reason_category = classify_reason(reason)
    if reason_category == "SUSPENSION":
        status = "SUSPENDED"

    return AvailabilityEntry(
        player=player,
        status=status,
        reason=reason,
        reason_category=reason_category,
    )


def select_snapshot_asof(
    snapshots: Iterable[TeamAvailabilitySnapshot],
    team: str,
    kickoff: datetime,
    safety_minutes: int = 0,
) -> TeamAvailabilitySnapshot | None:
    canonical = canonical_team(team) or team
    kickoff_utc = parse_datetime(kickoff)
    if kickoff_utc is None:
        raise ValueError("kickoff is required")
    cutoff = kickoff_utc - timedelta(minutes=safety_minutes)
    eligible = [
        snap
        for snap in snapshots
        if snap.team == canonical and snap.observed_at <= cutoff
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda snap: snap.observed_at)


def _best_strength_match(
    player_name: str,
    strengths: Sequence[Mapping],
    team: str | None = None,
    fuzzy_threshold: float = 0.92,
) -> tuple[Mapping | None, float]:
    target = canonical_player_key(player_name)
    candidates = []
    for row in strengths:
        row_team = row.get("team") or row.get("team_name")
        if team and row_team:
            canonical_row_team = canonical_team(str(row_team)) or str(row_team)
            if canonical_row_team != team:
                continue
        name = str(row.get("player_name") or row.get("player") or "")
        key = canonical_player_key(name)
        if not key:
            continue
        if key == target:
            return row, 1.0
        candidates.append((row, key))

    best_row = None
    best_score = 0.0
    for row, key in candidates:
        score = SequenceMatcher(None, target, key).ratio()
        if score > best_score:
            best_row, best_score = row, score
    if best_score >= fuzzy_threshold:
        return best_row, best_score
    return None, best_score


def _num(row: Mapping | None, key: str) -> float:
    if not row:
        return 0.0
    value = row.get(key, 0.0)
    try:
        value = float(value)
        return value if math.isfinite(value) else 0.0
    except (TypeError, ValueError):
        return 0.0


def build_team_availability_features(
    snapshot: TeamAvailabilitySnapshot | None,
    player_strengths: Sequence[Mapping],
    kickoff: datetime,
    fuzzy_threshold: float = 0.92,
) -> dict:
    kickoff_utc = parse_datetime(kickoff)
    features = {
        "availability_snapshot_present": int(snapshot is not None),
        "availability_snapshot_age_hours": None,
        "availability_out_count": 0.0,
        "availability_questionable_count": 0.0,
        "availability_suspended_count": 0.0,
        "availability_injury_count": 0.0,
        "availability_illness_count": 0.0,
        "availability_international_duty_count": 0.0,
        "availability_non_injury_count": 0.0,
        "availability_concussion_count": 0.0,
        "missing_impact_index": 0.0,
        "missing_minutes_rate": 0.0,
        "missing_gplus_p96": 0.0,
        "missing_xg_xa_p96": 0.0,
        "missing_touch_share": 0.0,
        "missing_salary_share": 0.0,
        "missing_dp_count": 0.0,
        "availability_unresolved_players": 0,
        "availability_min_match_score": None,
    }
    if snapshot is None:
        return features

    features["availability_snapshot_age_hours"] = max(
        0.0, (kickoff_utc - snapshot.observed_at).total_seconds() / 3600.0
    )
    match_scores = []

    for entry in snapshot.entries:
        weight = entry.weight
        if entry.status in {"OUT", "SUSPENDED", "DOUBTFUL"}:
            features["availability_out_count"] += weight
        if entry.status == "QUESTIONABLE":
            features["availability_questionable_count"] += 1.0
        if entry.status == "SUSPENDED":
            features["availability_suspended_count"] += 1.0

        reason_key = entry.reason_category.lower()
        reason_feature = {
            "injury": "availability_injury_count",
            "illness": "availability_illness_count",
            "international_duty": "availability_international_duty_count",
            "non_injury": "availability_non_injury_count",
            "concussion": "availability_concussion_count",
            "suspension": "availability_suspended_count",
        }.get(reason_key)
        if reason_feature and reason_feature != "availability_suspended_count":
            features[reason_feature] += weight

        strength, score = _best_strength_match(
            entry.player, player_strengths, team=snapshot.team, fuzzy_threshold=fuzzy_threshold
        )
        match_scores.append(score)
        if strength is None:
            features["availability_unresolved_players"] += 1
            continue

        features["missing_impact_index"] += weight * _num(strength, "impact_index")
        features["missing_minutes_rate"] += weight * _num(strength, "minutes_rate")
        features["missing_gplus_p96"] += weight * _num(strength, "gplus_p96")
        features["missing_xg_xa_p96"] += weight * _num(strength, "xg_xa_p96")
        features["missing_touch_share"] += weight * _num(strength, "share_team_touches")
        features["missing_salary_share"] += weight * _num(strength, "salary_share")
        if bool(strength.get("is_designated_player", False)):
            features["missing_dp_count"] += weight

    if match_scores:
        features["availability_min_match_score"] = min(match_scores)
    return features


def write_snapshots_jsonl(path: str, snapshots: Iterable[TeamAvailabilitySnapshot]) -> int:
    existing_ids = set()
    try:
        with open(path, "r", encoding="utf-8") as src:
            for line in src:
                try:
                    existing_ids.add(json.loads(line)["snapshot_id"])
                except Exception:
                    continue
    except FileNotFoundError:
        pass

    added = 0
    with open(path, "a", encoding="utf-8") as dst:
        for snap in snapshots:
            if snap.snapshot_id in existing_ids:
                continue
            dst.write(json.dumps(snap.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            existing_ids.add(snap.snapshot_id)
            added += 1
    return added


def read_snapshots_jsonl(path: str) -> list[TeamAvailabilitySnapshot]:
    rows = []
    with open(path, "r", encoding="utf-8") as src:
        for line in src:
            line = line.strip()
            if line:
                rows.append(TeamAvailabilitySnapshot.from_dict(json.loads(line)))
    return rows
