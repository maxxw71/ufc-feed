import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "nfl-predictor-v1"
IN = ROOT / "rolling_roi_discovery" / "pregame_team_sides_2006_2026.parquet"
OUT = ROOT / "nfl_ypp_loss_audit"
OUT.mkdir(parents=True, exist_ok=True)


def amer_profit(odds, stake=100.0):
    if pd.isna(odds) or odds == 0:
        return np.nan
    o = float(odds)
    return stake * (o / 100.0) if o > 0 else stake * (100.0 / abs(o))


def metrics(x):
    n = len(x)
    if not n:
        return dict(n=0, wins=0, win=np.nan, roi=np.nan, profit=0.0)
    p = float(x.bet_profit100.sum())
    w = int(x.win.sum())
    return dict(n=n, wins=w, win=float(x.win.mean()), roi=p / (100 * n), profit=p)


def base(df, w, m, loc):
    z = (df.week >= w) & (df.market_prob >= m)
    if loc == "HOME":
        z &= df.home_side.eq(1)
    elif loc == "ROAD":
        z &= df.home_side.eq(0)
    return z


def ypp(df, p):
    return (
        base(df, p["w"], p["m"], p["loc"])
        & (df.pre_yards_per_play >= p["oy"])
        & (df.opp_pre_def_ypp_allowed >= p["dy"])
    )


g = pd.read_parquet(IN)
for c in g.columns:
    if c not in {"game_id", "team", "opponent", "roof", "surface"}:
        try:
            g[c] = pd.to_numeric(g[c], errors="ignore")
        except Exception:
            pass

b = g[
    g.moneyline.notna()
    & g.market_prob.notna()
    & g.win.isin([0.0, 1.0])
    & (g.prior_games >= 3)
    & (g.market_prob > 0.50)
].copy()

for c in ["season", "week", "market_prob", "moneyline", "home_side", "div_game"]:
    if c in b:
        b[c] = pd.to_numeric(b[c], errors="coerce")

# Exact YPP family grid from run_nfl_top_family_walkforward.sh.
grid = []
for w in [4, 5, 6, 7, 8, 9, 10, 11, 12]:
    for m in [0.52, 0.55, 0.60, 0.65]:
        for loc in ["ANY", "HOME", "ROAD"]:
            for oy in [5.5, 5.8, 6.0, 6.2]:
                for dy in [5.5, 5.8, 6.0, 6.2]:
                    grid.append(dict(w=w, m=m, loc=loc, oy=oy, dy=dy))

rows = []
season_summary = []
for year in range(2016, 2027):
    tr = b[b.season < year]
    te = b[b.season == year]
    candidates = []
    for p in grid:
        x = tr[ypp(tr, p).fillna(False)]
        mt = metrics(x)
        if mt["n"] < 50 or mt["win"] < 0.68 or mt["roi"] < 0.03:
            continue
        recent = x[x.season >= max(2006, year - 5)]
        rm = metrics(recent)
        if rm["n"] >= 15 and rm["roi"] <= 0:
            continue
        score = min(mt["roi"], 0.20) * math.sqrt(mt["n"]) + 0.25 * max(0, mt["win"] - 0.68) * math.sqrt(mt["n"])
        candidates.append((score, p, mt))
    if not candidates:
        continue
    candidates.sort(key=lambda q: q[0], reverse=True)
    _, p, train_m = candidates[0]
    z = te[ypp(te, p).fillna(False)].copy()
    test_m = metrics(z)
    season_summary.append({
        "season": year,
        "params": json.dumps(p, sort_keys=True),
        "train_n": train_m["n"],
        "train_win": train_m["win"],
        "train_roi": train_m["roi"],
        "test_n": test_m["n"],
        "test_wins": test_m["wins"],
        "test_win": test_m["win"],
        "test_roi": test_m["roi"],
        "test_profit": test_m["profit"],
    })
    for _, r in z.iterrows():
        d = r.to_dict()
        d["audit_season"] = year
        d["selected_params"] = json.dumps(p, sort_keys=True)
        rows.append(d)

bets = pd.DataFrame(rows)
if len(bets):
    sort_cols = [c for c in ["audit_season", "week", "game_id", "team"] if c in bets.columns]
    bets = bets.sort_values(sort_cols)
    bets.to_csv(OUT / "ypp_walkforward_all_bets.csv", index=False)
    bets[bets.win == 0].to_csv(OUT / "ypp_walkforward_losses.csv", index=False)

pd.DataFrame(season_summary).to_csv(OUT / "ypp_walkforward_by_season.csv", index=False)

print("YPP walk-forward audit complete")
print(f"All qualifying bets: {len(bets)}")
if len(bets):
    losses = bets[bets.win == 0]
    print(f"Losses: {len(losses)}")
    show = [c for c in ["season", "week", "game_id", "team", "opponent", "moneyline", "market_prob", "pre_yards_per_play", "opp_pre_def_ypp_allowed", "home_side", "div_game"] if c in losses.columns]
    print(losses[show].to_string(index=False))
