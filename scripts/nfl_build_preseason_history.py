from __future__ import annotations

from pathlib import Path
import io
import json
import os
import re
import time

import numpy as np
import pandas as pd
import requests

CTX = Path(os.environ.get("NFL_CONTEXT_ROOT", "/home/appwiza-runner/nfl-context-data"))
RAW = CTX / "raw"
CACHE = RAW / "preseason_pfr_html"
OUT = RAW / "preseason_team_2006_2026.csv"
STATUS = CTX / "NFL_PRESEASON_HISTORY_STATUS.json"
RAW.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

SEASONS = list(range(2006, 2027))
UA = {"User-Agent": "Mozilla/5.0 (compatible; AppWizaNFLResearch/1.0; +https://appwiza.com)"}

TEAM_NAMES = {
    "Arizona Cardinals": "ARI",
    "Atlanta Falcons": "ATL",
    "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR",
    "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN",
    "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN",
    "Detroit Lions": "DET",
    "Green Bay Packers": "GB",
    "Houston Texans": "HOU",
    "Indianapolis Colts": "IND",
    "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV",
    "Oakland Raiders": "LV",
    "Los Angeles Chargers": "LAC",
    "San Diego Chargers": "LAC",
    "Los Angeles Rams": "LA",
    "St. Louis Rams": "LA",
    "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN",
    "New England Patriots": "NE",
    "New Orleans Saints": "NO",
    "New York Giants": "NYG",
    "New York Jets": "NYJ",
    "Philadelphia Eagles": "PHI",
    "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA",
    "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN",
    "Washington Commanders": "WAS",
    "Washington Football Team": "WAS",
    "Washington Redskins": "WAS",
}


def clean_team(x: object) -> str:
    s = re.sub(r"[\*\+†‡]", "", str(x))
    s = re.sub(r"\s+", " ", s).strip()
    return s


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    z = df.copy()
    if isinstance(z.columns, pd.MultiIndex):
        z.columns = [" ".join(str(v) for v in c if str(v) != "nan").strip() for c in z.columns]
    else:
        z.columns = [str(c).strip() for c in z.columns]
    return z


def fetch_year(season: int) -> tuple[str, str]:
    cache = CACHE / f"{season}.html"
    # Historical pages are immutable; always refresh current season only.
    if cache.exists() and season < 2026:
        return cache.read_text(errors="ignore"), "cache"
    url = f"https://www.pro-football-reference.com/years/{season}/preseason.htm"
    r = requests.get(url, headers=UA, timeout=45)
    r.raise_for_status()
    cache.write_text(r.text)
    time.sleep(1.0)
    return r.text, url


rows: list[dict] = []
source_status: dict[str, dict] = {}
for season in SEASONS:
    try:
        html, source = fetch_year(season)
        tables = [flatten_columns(t) for t in pd.read_html(io.StringIO(html))]
        found: dict[str, dict] = {}
        for t in tables:
            cols = {str(c).strip().lower(): c for c in t.columns}
            tm_col = cols.get("tm")
            w_col = cols.get("w")
            l_col = cols.get("l")
            pf_col = cols.get("pf")
            pa_col = cols.get("pa")
            t_col = cols.get("t")
            if not all([tm_col, w_col, l_col, pf_col, pa_col]):
                continue
            for rec in t.to_dict("records"):
                full = clean_team(rec.get(tm_col))
                code = TEAM_NAMES.get(full)
                if not code:
                    continue
                w = pd.to_numeric(rec.get(w_col), errors="coerce")
                l = pd.to_numeric(rec.get(l_col), errors="coerce")
                ti = pd.to_numeric(rec.get(t_col), errors="coerce") if t_col else 0
                pf = pd.to_numeric(rec.get(pf_col), errors="coerce")
                pa = pd.to_numeric(rec.get(pa_col), errors="coerce")
                if pd.isna(w) or pd.isna(l) or pd.isna(pf) or pd.isna(pa):
                    continue
                ti = 0 if pd.isna(ti) else int(ti)
                games = int(w) + int(l) + ti
                found[code] = {
                    "season": season,
                    "team": code,
                    "preseason_games": games,
                    "preseason_wins": int(w),
                    "preseason_losses": int(l),
                    "preseason_ties": ti,
                    "preseason_win_pct": (int(w) + 0.5 * ti) / games if games else np.nan,
                    "preseason_pf": int(pf),
                    "preseason_pa": int(pa),
                    "preseason_point_diff": int(pf) - int(pa),
                    "preseason_avg_point_diff": (int(pf) - int(pa)) / games if games else np.nan,
                    "preseason_source": f"pfr_{season}_preseason_standings",
                }
        if season == 2020 and not found:
            # COVID-19 cancelled the 2020 preseason. Missing is intentional, not 0-0 form.
            source_status[str(season)] = {"ok": True, "teams": 0, "cancelled": True, "source": source}
            continue
        if len(found) < 28:
            raise RuntimeError(f"only parsed {len(found)} teams")
        rows.extend(found.values())
        source_status[str(season)] = {"ok": True, "teams": len(found), "source": source}
    except Exception as exc:
        source_status[str(season)] = {"ok": False, "error": repr(exc)}

out = pd.DataFrame(rows)
if len(out):
    out = out.sort_values(["season", "team"]).drop_duplicates(["season", "team"], keep="last")
    out.to_csv(OUT, index=False)

lac = out[(out.season == 2026) & (out.team == "LAC")] if len(out) else pd.DataFrame()
expected_lac_ok = False
if len(lac) == 1:
    r = lac.iloc[0]
    expected_lac_ok = (
        int(r.preseason_wins) == 1
        and int(r.preseason_losses) == 2
        and int(r.preseason_pf) == 62
        and int(r.preseason_pa) == 68
        and int(r.preseason_point_diff) == -6
    )

status = {
    "rows": int(len(out)),
    "seasons_requested": [min(SEASONS), max(SEASONS)],
    "seasons_with_data": sorted(int(x) for x in out.season.unique()) if len(out) else [],
    "team_seasons_by_year": {str(int(y)): int(len(z)) for y, z in out.groupby("season")} if len(out) else {},
    "chargers_2026_expected_1_2_minus6": expected_lac_ok,
    "source_status": source_status,
    "output": str(OUT),
    "notes": [
        "Preseason records are context features, not automatic betting vetoes.",
        "2020 preseason was cancelled and remains unknown/NA rather than being treated as 0-0.",
        "Source is Pro-Football-Reference year-level preseason standings, cached after first successful historical fetch.",
    ],
}
STATUS.write_text(json.dumps(status, indent=2, default=str))
print(json.dumps(status, indent=2, default=str))
if len(lac):
    print("=== 2026 LAC PRESEASON ===")
    print(lac.to_string(index=False))
if not expected_lac_ok:
    raise RuntimeError("2026 Chargers preseason validation failed; expected 1-2, PF 62, PA 68, PD -6")
