from __future__ import annotations

from pathlib import Path
import io
import json
import os

import numpy as np
import pandas as pd
import requests

CTX = Path(os.environ.get("NFL_CONTEXT_ROOT", "/home/appwiza-runner/nfl-context-data"))
RAW = CTX / "raw"
OUT = RAW / "preseason_team_2006_2026.csv"
STATUS = CTX / "NFL_PRESEASON_HISTORY_STATUS.json"
RAW.mkdir(parents=True, exist_ok=True)

HIST_URL = "https://raw.githubusercontent.com/projectunmuted/nfl-preseason-vs-regular-season/main/nfl-preseason-vs-regular-season-2000-2025.csv"
CURR_URL = "https://raw.githubusercontent.com/projectunmuted/nfl-preseason-vs-regular-season/main/nfl-preseason-2026.csv"
UA = {"User-Agent": "AppWizaNFLResearch/1.0 (+https://appwiza.com)"}

TEAM_MAP = {
    "ari":"ARI","atl":"ATL","bal":"BAL","buf":"BUF","car":"CAR","chi":"CHI","cin":"CIN","cle":"CLE",
    "dal":"DAL","den":"DEN","det":"DET","gb":"GB","hou":"HOU","ind":"IND","jax":"JAX","kc":"KC",
    "lv":"LV","lac":"LAC","lar":"LA","mia":"MIA","min":"MIN","ne":"NE","no":"NO","nyg":"NYG",
    "nyj":"NYJ","phi":"PHI","pit":"PIT","sf":"SF","sea":"SEA","tb":"TB","ten":"TEN","wsh":"WAS",
}


def fetch_csv(url: str) -> pd.DataFrame:
    r = requests.get(url, headers=UA, timeout=45)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text))


hist = fetch_csv(HIST_URL)
curr = fetch_csv(CURR_URL)
required = ["season", "team", "team_name", "preseason_wins", "preseason_games", "preseason_win_pct"]
for name, df in [("historical", hist), ("current", curr)]:
    missing = set(required) - set(df.columns)
    if missing:
        raise RuntimeError(f"{name} preseason source missing columns: {sorted(missing)}")

src = pd.concat([hist[required], curr[required]], ignore_index=True)
src["season"] = pd.to_numeric(src.season, errors="coerce")
src = src[src.season.between(2006, 2026)].copy()
src["source_team_slug"] = src.team.astype(str).str.lower()
src["team"] = src.source_team_slug.map(TEAM_MAP)
if src.team.isna().any():
    bad = sorted(src.loc[src.team.isna(), "source_team_slug"].dropna().unique())
    raise RuntimeError(f"Unmapped ESPN team slugs: {bad}")

for c in ["preseason_wins", "preseason_games", "preseason_win_pct"]:
    src[c] = pd.to_numeric(src[c], errors="coerce")

# Source convention: a tie counts as half a win. Preserve that convention explicitly.
src["preseason_nonwin_equiv"] = src.preseason_games - src.preseason_wins
src["preseason_record_equiv"] = src.apply(
    lambda r: f"{r.preseason_wins:g}-{r.preseason_nonwin_equiv:g}" if pd.notna(r.preseason_games) else None,
    axis=1,
)
src["preseason_losing_record"] = (src.preseason_win_pct < .5).astype(float)
src["preseason_even_record"] = np.isclose(src.preseason_win_pct, .5).astype(float)
src["preseason_winning_record"] = (src.preseason_win_pct > .5).astype(float)
src["preseason_unbeaten"] = np.isclose(src.preseason_win_pct, 1.0).astype(float)
src["preseason_winless"] = np.isclose(src.preseason_win_pct, 0.0).astype(float)
src["preseason_source"] = "projectunmuted_espn_schedule_audited"
src = src.sort_values(["season", "team"]).drop_duplicates(["season", "team"], keep="last")

# 2020 is intentionally absent because the NFL preseason was cancelled.
if 2020 in set(src.season.astype(int)):
    raise RuntimeError("2020 should be absent: NFL preseason was cancelled")

coverage = {int(y): len(z) for y, z in src.groupby(src.season.astype(int))}
for y in range(2006, 2027):
    if y == 2020:
        continue
    if coverage.get(y, 0) != 32:
        raise RuntimeError(f"Unexpected preseason coverage for {y}: {coverage.get(y, 0)} teams")

src.to_csv(OUT, index=False)
lac = src[(src.season == 2026) & (src.team == "LAC")]
expected_lac_ok = False
if len(lac) == 1:
    r = lac.iloc[0]
    expected_lac_ok = (
        np.isclose(float(r.preseason_wins), 1.0)
        and np.isclose(float(r.preseason_games), 3.0)
        and np.isclose(float(r.preseason_win_pct), 1/3)
    )

status = {
    "rows": int(len(src)),
    "season_min": int(src.season.min()),
    "season_max": int(src.season.max()),
    "seasons_with_data": sorted(int(x) for x in src.season.unique()),
    "team_seasons_by_year": {str(y): int(n) for y, n in coverage.items()},
    "chargers_2026_expected_1_2": expected_lac_ok,
    "sources": [HIST_URL, CURR_URL],
    "output": str(OUT),
    "notes": [
        "Underlying public dataset is built from ESPN schedule endpoints and audited by its publisher.",
        "A tie counts as half a win in preseason_wins, matching the source methodology.",
        "2020 preseason was cancelled and remains NA, never 0-0 form.",
        "Raw preseason W-L is a candidate early-season context feature, not an automatic betting veto.",
        "This source provides record/win-rate, not preseason scoring margin; no point differential is fabricated.",
    ],
}
STATUS.write_text(json.dumps(status, indent=2, default=str))
print(json.dumps(status, indent=2, default=str))
print("=== 2026 LAC PRESEASON ===")
print(lac.to_string(index=False))
if not expected_lac_ok:
    raise RuntimeError("2026 Chargers preseason validation failed; expected 1-2 equivalent record")
