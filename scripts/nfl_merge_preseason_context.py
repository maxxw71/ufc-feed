from __future__ import annotations

from pathlib import Path
import json
import os

import numpy as np
import pandas as pd

CTX = Path(os.environ.get("NFL_CONTEXT_ROOT", "/home/appwiza-runner/nfl-context-data"))
RAW = CTX / "raw"
DERIVED = CTX / "derived"
PRE = RAW / "preseason_team_2006_2026.csv"
CONTEXT = DERIVED / "team_game_pregame_context_2006_2026.parquet"
STATUS = CTX / "NFL_PRESEASON_CONTEXT_STATUS.json"

TEAM_MAP = {"SD": "LAC", "OAK": "LV", "STL": "LA", "LAR": "LA", "WSH": "WAS"}
def canon(x):
    if pd.isna(x): return x
    s = str(x)
    return TEAM_MAP.get(s, s)

if not PRE.exists():
    raise FileNotFoundError(PRE)
if not CONTEXT.exists():
    raise FileNotFoundError(CONTEXT)

pre = pd.read_csv(PRE)
ctx = pd.read_parquet(CONTEXT)
pre["team_canon"] = pre.team.map(canon)
ctx["team_canon"] = ctx.team_canon.map(canon)
ctx["opponent_canon"] = ctx.opponent_canon.map(canon)

base_cols = [
    "season", "team_canon", "preseason_games", "preseason_wins", "preseason_losses",
    "preseason_ties", "preseason_win_pct", "preseason_pf", "preseason_pa",
    "preseason_point_diff", "preseason_avg_point_diff", "preseason_source",
]
pre = pre[base_cols].drop_duplicates(["season", "team_canon"])

# Remove stale copies so reruns are idempotent.
team_features = [c for c in base_cols if c not in {"season", "team_canon"}]
opp_features = ["opp_" + c for c in team_features]
ctx = ctx.drop(columns=[c for c in team_features + opp_features if c in ctx.columns], errors="ignore")
ctx = ctx.merge(pre, on=["season", "team_canon"], how="left")
opp = pre.rename(columns={"team_canon": "opponent_canon", **{c: "opp_" + c for c in team_features}})
ctx = ctx.merge(opp, on=["season", "opponent_canon"], how="left")

# Prespecified descriptive features. These are context/risk labels only until validated.
ctx["preseason_losing_record"] = np.where(
    ctx.preseason_games.notna(), (pd.to_numeric(ctx.preseason_win_pct, errors="coerce") < .5).astype(float), np.nan
)
ctx["preseason_negative_point_diff"] = np.where(
    ctx.preseason_games.notna(), (pd.to_numeric(ctx.preseason_point_diff, errors="coerce") < 0).astype(float), np.nan
)
ctx["preseason_form_disadvantage"] = np.where(
    ctx.preseason_games.notna() & ctx.opp_preseason_games.notna(),
    (
        (pd.to_numeric(ctx.preseason_win_pct, errors="coerce") < pd.to_numeric(ctx.opp_preseason_win_pct, errors="coerce"))
        & (pd.to_numeric(ctx.preseason_point_diff, errors="coerce") < pd.to_numeric(ctx.opp_preseason_point_diff, errors="coerce"))
    ).astype(float),
    np.nan,
)

ctx.to_parquet(CONTEXT, index=False)
ctx[ctx.season.eq(2026)].to_csv(DERIVED / "team_game_pregame_context_2026.csv", index=False)

lac = ctx[(ctx.season == 2026) & (ctx.team_canon == "LAC")].copy()
cols = [c for c in [
    "season", "week", "team", "opponent", "moneyline", "preseason_games", "preseason_wins",
    "preseason_losses", "preseason_win_pct", "preseason_point_diff", "opp_preseason_wins",
    "opp_preseason_losses", "opp_preseason_win_pct", "opp_preseason_point_diff",
    "preseason_losing_record", "preseason_negative_point_diff", "preseason_form_disadvantage"
] if c in lac.columns]

status = {
    "rows": int(len(ctx)),
    "team_pregame_rows_with_preseason": int(ctx.preseason_games.notna().sum()),
    "opponent_pregame_rows_with_preseason": int(ctx.opp_preseason_games.notna().sum()),
    "seasons_with_preseason_context": sorted(int(x) for x in ctx.loc[ctx.preseason_games.notna(), "season"].unique()),
    "output": str(CONTEXT),
    "notes": [
        "Same-season preseason results are known before Week 1 and therefore safe pregame context.",
        "Raw preseason W-L is not treated as a veto because starter usage varies by team and game.",
        "Primary validation targets are Weeks 1-3 and early-season favorite/method loss audits.",
    ],
}
STATUS.write_text(json.dumps(status, indent=2, default=str))
print(json.dumps(status, indent=2, default=str))
print("=== 2026 CHARGERS PRESEASON CONTEXT ===")
print(lac[cols].head(5).to_string(index=False))
