from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis


def _flatten_goals_added(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["player_id", "team_id", "gplus_raw", "gplus_above_avg", "gplus_minutes"])
    rows = []
    for _, row in df.iterrows():
        payload = row.get("data") or []
        raw = 0.0
        above = 0.0
        for item in payload:
            if not isinstance(item, dict):
                continue
            raw += float(item.get("goals_added_raw") or 0.0)
            above += float(item.get("goals_added_above_avg") or 0.0)
        rows.append(
            {
                "player_id": row.get("player_id"),
                "team_id": row.get("team_id"),
                "gplus_raw": raw,
                "gplus_above_avg": above,
                "gplus_minutes": float(row.get("minutes_played") or 0.0),
            }
        )
    return pd.DataFrame(rows)


def _latest_salary_asof(df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["player_id", "team_id", "guaranteed_compensation", "mlspa_release"])
    out = df.copy()
    out["mlspa_release"] = pd.to_datetime(out["mlspa_release"], errors="coerce", utc=True)
    cutoff = pd.Timestamp(as_of, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    out = out[out["mlspa_release"].notna() & (out["mlspa_release"] <= cutoff)]
    out = out.sort_values("mlspa_release")
    out = out.drop_duplicates(["player_id", "team_id"], keep="last")
    return out[["player_id", "team_id", "guaranteed_compensation", "mlspa_release"]]


def _pct_rank(series: pd.Series) -> pd.Series:
    if series.notna().sum() <= 1:
        return pd.Series(np.where(series.notna(), 0.5, 0.0), index=series.index, dtype=float)
    return series.fillna(0.0).rank(method="average", pct=True)


def build_strength_snapshot(as_of: date, lookback_days: int = 365) -> pd.DataFrame:
    start = as_of - timedelta(days=lookback_days)
    client = AmericanSoccerAnalysis(lazy_load=True)

    common = dict(
        leagues="mls",
        start_date=start.isoformat(),
        end_date=as_of.isoformat(),
        split_by_teams=True,
    )
    xg = client.get_player_xgoals(**common)
    xp = client.get_player_xpass(**common)
    ga = _flatten_goals_added(client.get_player_goals_added(**common))

    # Salary is point-in-time public information. Pull a broad enough range,
    # then retain only releases that were public on/before this snapshot date.
    salary_start = date(max(2013, as_of.year - 2), 1, 1)
    salaries = client.get_player_salaries(
        leagues="mls",
        start_date=salary_start.isoformat(),
        end_date=as_of.isoformat(),
    )
    salaries = _latest_salary_asof(salaries, as_of)

    players = client.get_players(leagues="mls")[["player_id", "player_name"]].drop_duplicates("player_id")
    teams = client.get_teams(leagues="mls")[["team_id", "team_name"]].drop_duplicates("team_id")

    keys = ["player_id", "team_id"]
    base_cols = keys + [
        "general_position",
        "minutes_played",
        "xgoals",
        "xassists",
        "xgoals_plus_xassists",
    ]
    base = xg[[c for c in base_cols if c in xg.columns]].copy()
    if "minutes_played" in base.columns:
        base = base.rename(columns={"minutes_played": "xg_minutes"})

    xp_cols = keys + ["minutes_played", "share_team_touches", "passes_completed_over_expected_p100"]
    xp_small = xp[[c for c in xp_cols if c in xp.columns]].copy()
    if "minutes_played" in xp_small.columns:
        xp_small = xp_small.rename(columns={"minutes_played": "xpass_minutes"})

    out = base.merge(xp_small, on=keys, how="outer")
    out = out.merge(ga, on=keys, how="outer")
    out = out.merge(salaries, on=keys, how="left")
    out = out.merge(players, on="player_id", how="left")
    out = out.merge(teams, on="team_id", how="left")

    minute_cols = [c for c in ["xg_minutes", "xpass_minutes", "gplus_minutes"] if c in out.columns]
    out["minutes_played"] = out[minute_cols].max(axis=1).fillna(0.0) if minute_cols else 0.0
    denom = out["minutes_played"].replace(0, np.nan)
    out["xg_xa_p96"] = out.get("xgoals_plus_xassists", 0.0).fillna(0.0) * 96.0 / denom
    out["gplus_p96"] = out.get("gplus_raw", 0.0).fillna(0.0) * 96.0 / denom
    out["gplus_above_avg_p96"] = out.get("gplus_above_avg", 0.0).fillna(0.0) * 96.0 / denom
    out[["xg_xa_p96", "gplus_p96", "gplus_above_avg_p96"]] = out[
        ["xg_xa_p96", "gplus_p96", "gplus_above_avg_p96"]
    ].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    team_max_minutes = out.groupby("team_id")["minutes_played"].transform("max").replace(0, np.nan)
    out["minutes_rate"] = (out["minutes_played"] / team_max_minutes).clip(0, 1).fillna(0.0)

    salary = pd.to_numeric(out.get("guaranteed_compensation", 0.0), errors="coerce").fillna(0.0)
    team_salary = salary.groupby(out["team_id"]).transform("sum").replace(0, np.nan)
    out["salary_share"] = (salary / team_salary).fillna(0.0)

    if "share_team_touches" not in out.columns:
        out["share_team_touches"] = 0.0
    out["share_team_touches"] = pd.to_numeric(out["share_team_touches"], errors="coerce").fillna(0.0)

    # Transparent within-team composite. Salary is deliberately not included;
    # it remains a separate signal and can be tested without hard-wiring pay to quality.
    components = {
        "minutes_pct": ("minutes_rate", 0.35),
        "gplus_pct": ("gplus_p96", 0.25),
        "xg_xa_pct": ("xg_xa_p96", 0.25),
        "touch_pct": ("share_team_touches", 0.15),
    }
    for target, (source, _) in components.items():
        out[target] = out.groupby("team_id")[source].transform(_pct_rank)
    out["impact_index"] = sum(out[col] * weight for col, (_, weight) in components.items())

    out["snapshot_asof"] = pd.Timestamp(as_of, tz="UTC")
    out["lookback_days"] = lookback_days
    return out.sort_values(["team_name", "impact_index"], ascending=[True, False]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build leakage-safe MLS player-strength snapshot from ASA.")
    parser.add_argument("--as-of", required=True, help="Snapshot date YYYY-MM-DD")
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
    df = build_strength_snapshot(as_of, args.lookback_days)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if output.suffix.lower() == ".parquet":
        df.to_parquet(output, index=False)
    else:
        df.to_csv(output, index=False)
    print(f"Wrote {len(df)} player-team strength rows to {output}")


if __name__ == "__main__":
    main()
