"""Approved staging policy for legacy NFL home-opener methods.

Production intent:
- original and stricter home-opener methods require >=2 preseason wins
  whenever that NFL season actually had a preseason.
- If the NFL held no preseason league-wide (e.g. 2020), the preseason gate is neutral.
- If a preseason existed but the selected team's record is unavailable, fail closed.

This module is staged in-repo; the privileged live deployment path still has to
integrate it into nfl_home_opener_scanner.py.
"""
from __future__ import annotations

LEGACY_PRESEASON_GATED_METHODS = frozenset({"original", "stricter"})
MIN_PRESEASON_WINS = 2


def passes_preseason_gate(method: str, preseason_wins: int | None, league_had_preseason: bool) -> bool:
    if method not in LEGACY_PRESEASON_GATED_METHODS:
        return True
    if not league_had_preseason:
        return True
    if preseason_wins is None:
        return False
    return int(preseason_wins) >= MIN_PRESEASON_WINS
