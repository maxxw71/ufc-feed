from __future__ import annotations

import pandas as pd


REQUIRED = {"match_id", "kickoff", "team", "player_id", "starter", "minutes"}


def build_lineup_continuity(lineups: pd.DataFrame, rolling_matches: int = 5) -> pd.DataFrame:
    """Create pre-match lineup-continuity features without future leakage.

    Input is one row per player/team/match. For each match, the features use
    only starts/minutes from matches with an earlier kickoff.
    """
    missing = REQUIRED - set(lineups.columns)
    if missing:
        raise ValueError(f"Missing lineup columns: {sorted(missing)}")

    df = lineups.copy()
    df["kickoff"] = pd.to_datetime(df["kickoff"], utc=True, errors="raise")
    df["starter"] = df["starter"].astype(bool)
    df["minutes"] = pd.to_numeric(df["minutes"], errors="coerce").fillna(0.0)

    match_rows = []
    grouped = df.sort_values("kickoff").groupby("team", sort=False)
    for team, team_df in grouped:
        matches = []
        for (kickoff, match_id), group in team_df.groupby(["kickoff", "match_id"], sort=True):
            starters = set(group.loc[group["starter"], "player_id"].astype(str))
            minutes = dict(zip(group["player_id"].astype(str), group["minutes"].astype(float)))
            matches.append((kickoff, match_id, starters, minutes))

        history = []
        for kickoff, match_id, starters, minutes in matches:
            previous = history[-1] if history else None
            prev_starters = previous[2] if previous else set()
            xi_overlap_prev = len(starters & prev_starters) / 11.0 if previous else None

            recent = history[-rolling_matches:]
            recent_starts = {}
            recent_minutes = {}
            for _, _, old_starters, old_minutes in recent:
                for player in old_starters:
                    recent_starts[player] = recent_starts.get(player, 0) + 1
                for player, mins in old_minutes.items():
                    recent_minutes[player] = recent_minutes.get(player, 0.0) + mins

            denom = max(1, len(recent))
            expected_core = {p for p, count in recent_starts.items() if count / denom >= 0.6}
            core_available = len(starters & expected_core) / max(1, len(expected_core)) if recent else None
            returning_minutes = sum(recent_minutes.get(p, 0.0) for p in starters)
            all_recent_minutes = sum(recent_minutes.values())
            returning_minutes_share = (
                returning_minutes / all_recent_minutes if all_recent_minutes > 0 else None
            )

            match_rows.append(
                {
                    "match_id": match_id,
                    "team": team,
                    "kickoff": kickoff,
                    "xi_overlap_prev": xi_overlap_prev,
                    f"core_starter_retention_{rolling_matches}": core_available,
                    f"returning_minutes_share_{rolling_matches}": returning_minutes_share,
                    "prior_matches_for_lineup_features": len(recent),
                }
            )
            history.append((kickoff, match_id, starters, minutes))

    return pd.DataFrame(match_rows).sort_values(["kickoff", "team"]).reset_index(drop=True)
