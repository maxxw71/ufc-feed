from __future__ import annotations

from pathlib import Path
from itertools import product
import math
import numpy as np
import pandas as pd

ROOT = Path.home() / "nfl-predictor-v1"
FEATURES = ROOT / "rolling_roi_discovery" / "pregame_team_sides_2006_2026.parquet"
SCHEDULES = ROOT / "data" / "raw" / "schedules_2006_2026.parquet"
OUT = ROOT / "nfl_exhaustive_history"
OUT.mkdir(parents=True, exist_ok=True)


def progress(p: int, msg: str):
    print(f"[{p:3d}%] {msg}", flush=True)


def implied(o):
    if pd.isna(o) or o == 0:
        return np.nan
    o = float(o)
    return 100.0 / (o + 100.0) if o > 0 else abs(o) / (abs(o) + 100.0)


def roi_stats(df: pd.DataFrame):
    n = len(df)
    if n == 0:
        return None
    p = float(df.bet_profit100.sum())
    r = p / (100.0 * n)
    wr = float(df.win.mean())
    old = df[df.season <= 2015]
    new = df[df.season >= 2016]
    old_roi = float(old.bet_profit100.sum() / (100 * len(old))) if len(old) else np.nan
    new_roi = float(new.bet_profit100.sum() / (100 * len(new))) if len(new) else np.nan
    blocks = []
    for lo, hi in [(2006, 2010), (2011, 2015), (2016, 2020), (2021, 2026)]:
        z = df[(df.season >= lo) & (df.season <= hi)]
        blocks.append(float(z.bet_profit100.sum() / (100 * len(z))) if len(z) else np.nan)
    vals = [x for x in blocks if pd.notna(x)]
    return {
        "n": n, "wins": int(df.win.sum()), "losses": int(n - df.win.sum()),
        "win_rate": wr, "roi": r, "profit": p,
        "old_n": len(old), "old_roi": old_roi, "new_n": len(new), "new_roi": new_roi,
        "b1": blocks[0], "b2": blocks[1], "b3": blocks[2], "b4": blocks[3],
        "pos_blocks": int(sum(x > 0 for x in vals)),
        "min_block": float(min(vals)) if vals else np.nan,
        "avg_line": float(df.moneyline.mean()),
        "avg_market": float(df.novig_prob.mean()) if "novig_prob" in df else float(df.market_prob.mean()),
    }


progress(2, "Loading strict pre-game features and schedules")
g = pd.read_parquet(FEATURES)
s = pd.read_parquet(SCHEDULES)

for c in g.columns:
    if c not in {"game_id", "team", "opponent", "roof", "surface", "gameday"}:
        try:
            g[c] = pd.to_numeric(g[c], errors="ignore")
        except Exception:
            pass

# Strict pregame rows only; exact historical moneyline P/L already exists.
b = g[g.moneyline.notna() & g.opp_moneyline.notna() & g.win.isin([0.0, 1.0]) & (g.prior_games >= 3)].copy()
b["team_imp"] = b.moneyline.map(implied)
b["opp_imp"] = b.opp_moneyline.map(implied)
b["novig_prob"] = b.team_imp / (b.team_imp + b.opp_imp)
b["favorite"] = b.novig_prob > .5
b["underdog"] = b.novig_prob < .5

# Common relative features.
pairs = [
    ("pre_win", "opp_pre_win", "record_edge"),
    ("last3_win", "opp_last3_win", "recent_win_edge"),
    ("pre_point_diff", "opp_pre_point_diff", "season_pd_edge"),
    ("last3_point_diff", "opp_last3_point_diff", "last3_pd_edge"),
    ("pre_turnover_margin", "opp_pre_turnover_margin", "turnover_edge_raw"),
    ("pre_off_epa", "opp_pre_off_epa", "off_epa_raw_edge"),
    ("pre_def_epa_allowed", "opp_pre_def_epa_allowed", "def_epa_allowed_raw_edge"),
    ("pre_pass_epa", "opp_pre_pass_epa", "pass_epa_raw_edge"),
    ("pre_rush_epa", "opp_pre_rush_epa", "rush_epa_raw_edge"),
    ("pre_points_for", "opp_pre_points_for", "scoring_edge_raw"),
    ("pre_points_against", "opp_pre_points_against", "points_allowed_edge_raw"),
    ("pre_yards_per_play", "opp_pre_yards_per_play", "ypp_edge_raw"),
    ("pre_def_ypp_allowed", "opp_pre_def_ypp_allowed", "def_ypp_allowed_edge_raw"),
    ("pre_sacks_made", "opp_pre_sacks_made", "sacks_edge_raw"),
]
for a, o, n in pairs:
    if a in b.columns and o in b.columns:
        b[n] = pd.to_numeric(b[a], errors="coerce") - pd.to_numeric(b[o], errors="coerce")

for name in ["off_epa", "def_epa", "pass_epa", "def_pass_epa", "rush_epa", "def_rush_epa", "scoring", "points_allowed", "turnover", "sacks_made", "def_ypp", "point_diff"]:
    a, o = f"rank_{name}", f"opp_rank_{name}"
    if a in b.columns and o in b.columns:
        b[f"edge_{name}"] = pd.to_numeric(b[o], errors="coerce") - pd.to_numeric(b[a], errors="coerce")

# Coach names, if nflverse schedule contains them. Compute ONLY prior coach history.
progress(7, "Building pre-game team and coach records")
coach_cols = [c for c in ["home_coach", "away_coach"] if c in s.columns]
if len(coach_cols) == 2:
    hc = s[["game_id", "season", "week", "home_team", "away_team", "home_score", "away_score", "home_coach", "away_coach", "home_moneyline", "away_moneyline"]].copy()
    h = pd.DataFrame({
        "game_id": hc.game_id, "season": hc.season, "week": hc.week, "team": hc.home_team,
        "coach": hc.home_coach, "coach_win": (hc.home_score > hc.away_score).astype(float),
        "coach_fav": hc.home_moneyline < hc.away_moneyline,
    })
    a = pd.DataFrame({
        "game_id": hc.game_id, "season": hc.season, "week": hc.week, "team": hc.away_team,
        "coach": hc.away_coach, "coach_win": (hc.away_score > hc.home_score).astype(float),
        "coach_fav": hc.away_moneyline < hc.home_moneyline,
    })
    ch = pd.concat([h, a], ignore_index=True).sort_values(["coach", "season", "week", "game_id"])
    ch["coach_prior_games"] = ch.groupby("coach").cumcount()
    ch["coach_pre_win"] = ch.groupby("coach")["coach_win"].transform(lambda x: x.expanding().mean().shift(1))
    ch["coach_pre_fav_games"] = ch.groupby("coach")["coach_fav"].transform(lambda x: x.shift(1).expanding().sum())
    # historical favorite win rate for coach, only games before target
    ch["fav_win_contrib"] = np.where(ch.coach_fav, ch.coach_win, np.nan)
    ch["coach_pre_fav_win"] = ch.groupby("coach")["fav_win_contrib"].transform(lambda x: x.expanding().mean().shift(1))
    b = b.merge(ch[["game_id", "team", "coach", "coach_prior_games", "coach_pre_win", "coach_pre_fav_games", "coach_pre_fav_win"]], on=["game_id", "team"], how="left")
else:
    b["coach_prior_games"] = np.nan
    b["coach_pre_win"] = np.nan
    b["coach_pre_fav_games"] = np.nan
    b["coach_pre_fav_win"] = np.nan

# Favorite-side pool for manual archetypes.
fav = b[b.favorite].copy()
progress(10, f"Priced favorite-side pool: {len(fav):,} games")

results = []

def add(family, desc, mask):
    z = fav[mask.fillna(False)]
    m = roi_stats(z)
    if m:
        results.append({"family": family, "rule": desc, **m})

# Base dimensions.
week_floors = list(range(4, 14))
market_floors = [.52, .55, .60, .65, .70, .75, .80]
locations = ["ANY", "HOME", "ROAD"]

def base(w, mp, loc):
    z = (fav.week >= w) & (fav.novig_prob >= mp)
    if loc == "HOME" and "home_side" in fav.columns:
        z &= fav.home_side.eq(1)
    elif loc == "ROAD" and "home_side" in fav.columns:
        z &= fav.home_side.eq(0)
    return z

progress(14, "Scanning team-record, favorite-strength and home/away archetypes")
for w, mp, loc in product(week_floors, market_floors, locations):
    bm = base(w, mp, loc)
    if {"pre_win", "opp_pre_win"}.issubset(fav.columns):
        for fw, ow in product([.55, .60, .65, .70, .75], [.25, .30, .35, .40, .45, .50, .55, .60]):
            add("RECORD MISMATCH", f"week>={w} p>={mp:.2f} {loc} favRecord>={fw:.2f} oppRecord<={ow:.2f}", bm & (fav.pre_win >= fw) & (fav.opp_pre_win <= ow))
    if "last3_pd_edge" in fav.columns:
        for e in [3, 5, 7, 10, 14, 18]:
            add("RECENT FORM", f"week>={w} p>={mp:.2f} {loc} last3PDEdge>={e}", bm & (fav.last3_pd_edge >= e))

progress(22, "Scanning elite offense/defense vs opponent archetypes")
for w, mp, loc in product(week_floors, market_floors, locations):
    bm = base(w, mp, loc)
    for metric in ["off_epa", "def_epa", "pass_epa", "def_pass_epa", "rush_epa", "def_rush_epa", "scoring", "points_allowed", "turnover", "sacks_made"]:
        r = f"rank_{metric}"; orr = f"opp_rank_{metric}"
        if r not in fav.columns or orr not in fav.columns:
            continue
        for fr, opp in product([5, 8, 10, 12], [5, 8, 10, 18, 22, 25, 28]):
            relation = "ELITE" if opp <= 10 else "WEAK"
            cond = (fav[r] <= fr) & ((fav[orr] <= opp) if relation == "ELITE" else (fav[orr] >= opp))
            add(f"{metric.upper()} vs {relation}", f"week>={w} p>={mp:.2f} {loc} favRank<={fr} oppRank {'<=' if relation=='ELITE' else '>='}{opp}", bm & cond)

progress(32, "Scanning rank-edge and multi-category superiority")
edge_cols = [c for c in ["edge_off_epa", "edge_def_epa", "edge_pass_epa", "edge_def_pass_epa", "edge_rush_epa", "edge_def_rush_epa", "edge_scoring", "edge_points_allowed", "edge_turnover", "edge_sacks_made"] if c in fav.columns]
for w, mp, loc in product(week_floors, market_floors, locations):
    bm = base(w, mp, loc)
    for c in edge_cols:
        for e in [4, 6, 8, 10, 12, 14, 16, 18]:
            add("SINGLE RANK EDGE", f"week>={w} p>={mp:.2f} {loc} {c}>={e}", bm & (fav[c] >= e))
    if edge_cols:
        for e in [4, 6, 8, 10, 12]:
            cnt = sum((pd.to_numeric(fav[c], errors="coerce") >= e).astype(int) for c in edge_cols)
            for k in range(2, min(8, len(edge_cols)) + 1):
                add("MULTI-EDGE", f"week>={w} p>={mp:.2f} {loc} >= {k}/{len(edge_cols)} rank edges >= {e}", bm & (cnt >= k))

progress(44, "Scanning balanced offense/defense, scoring, turnovers and protection")
for w, mp, loc in product(week_floors, market_floors, locations):
    bm = base(w, mp, loc)
    combos = [
        ("edge_off_epa", "edge_def_epa", "EPA BOTH"),
        ("edge_pass_epa", "edge_def_pass_epa", "PASS BOTH"),
        ("edge_rush_epa", "edge_def_rush_epa", "RUSH BOTH"),
        ("edge_scoring", "edge_points_allowed", "SCORING BOTH"),
        ("edge_turnover", "edge_def_epa", "TURNOVER + DEF"),
        ("edge_turnover", "edge_off_epa", "TURNOVER + OFF"),
        ("edge_sacks_made", "edge_def_pass_epa", "PASS RUSH + PASS DEF"),
    ]
    for a, c, fam in combos:
        if a not in fav.columns or c not in fav.columns:
            continue
        for x, y in product([4, 8, 12, 16], [4, 8, 12, 16]):
            add(fam, f"week>={w} p>={mp:.2f} {loc} {a}>={x} {c}>={y}", bm & (fav[a] >= x) & (fav[c] >= y))

progress(54, "Scanning rest, divisional context, roof/weather and coach-history filters")
for w, mp, loc in product(week_floors, market_floors, locations):
    bm = base(w, mp, loc)
    if "rest_edge" in fav.columns:
        for r in [3, 5, 7, 10]:
            if "edge_off_epa" in fav.columns:
                add("REST + OFFENSE", f"week>={w} p>={mp:.2f} {loc} restEdge>={r} offEdge>=8", bm & (fav.rest_edge >= r) & (fav.edge_off_epa >= 8))
            if "edge_def_epa" in fav.columns:
                add("REST + DEFENSE", f"week>={w} p>={mp:.2f} {loc} restEdge>={r} defEdge>=8", bm & (fav.rest_edge >= r) & (fav.edge_def_epa >= 8))
    if "div_game" in fav.columns and "edge_off_epa" in fav.columns and "edge_def_epa" in fav.columns:
        for dval, dname in [(0, "NON-DIV"), (1, "DIV")]:
            add("DIVISION CONTEXT", f"week>={w} p>={mp:.2f} {loc} {dname} bothEPAedge>=8", bm & fav.div_game.eq(dval) & (fav.edge_off_epa >= 8) & (fav.edge_def_epa >= 8))
    if "coach_pre_fav_win" in fav.columns:
        for cg, cw in product([15, 25, 40], [.60, .65, .70, .75]):
            add("COACH FAVORITE HISTORY", f"week>={w} p>={mp:.2f} {loc} coachFavGames>={cg} coachFavWin>={cw:.2f}", bm & (fav.coach_pre_fav_games >= cg) & (fav.coach_pre_fav_win >= cw))

progress(62, "Scanning raw-stat dominance thresholds")
raws = [
    ("season_pd_edge", [3, 5, 7, 10, 14]),
    ("last3_pd_edge", [3, 5, 7, 10, 14]),
    ("record_edge", [.10, .15, .20, .25, .30]),
    ("turnover_edge_raw", [.5, 1.0, 1.5, 2.0]),
    ("ypp_edge_raw", [.2, .4, .6, .8, 1.0]),
    ("sacks_edge_raw", [.5, 1.0, 1.5, 2.0]),
]
for w, mp, loc in product(week_floors, market_floors, locations):
    bm = base(w, mp, loc)
    for c, vals in raws:
        if c not in fav.columns:
            continue
        for v in vals:
            add("RAW DOMINANCE", f"week>={w} p>={mp:.2f} {loc} {c}>={v}", bm & (fav[c] >= v))

progress(68, "Saving manual-rule discovery table")
rules = pd.DataFrame(results)
rules.to_csv(OUT / "all_manual_rules.csv", index=False)

# Strong historical candidates: user target + basic anti-overfit screens.
elig = rules[(rules.n >= 100) & (rules.win_rate >= .75) & (rules.roi >= .10) & (rules.old_roi > 0) & (rules.new_roi > 0)].copy()
robust = elig[(elig.pos_blocks >= 3) & (elig.min_block >= -.05)].copy().sort_values(["roi", "n"], ascending=[False, False])
elig = elig.sort_values(["roi", "n"], ascending=[False, False])
elig.to_csv(OUT / "target_75win_10roi_n100.csv", index=False)
robust.to_csv(OUT / "robust_target_rules.csv", index=False)

# Walk-forward machine learning. Predict outcome from PRE-GAME stats and market, then bet only when
# the model's estimated probability exceeds the no-vig market probability by a threshold chosen from PRIOR seasons only.
progress(72, "Preparing walk-forward ML feature matrix")
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier

numeric_candidates = [
    "novig_prob", "week", "home_side", "rest_edge", "pre_win", "opp_pre_win", "last3_win", "opp_last3_win",
    "season_pd_edge", "last3_pd_edge", "record_edge", "recent_win_edge",
    "pre_off_epa", "opp_pre_off_epa", "pre_def_epa_allowed", "opp_pre_def_epa_allowed",
    "pre_pass_epa", "opp_pre_pass_epa", "pre_rush_epa", "opp_pre_rush_epa",
    "pre_turnover_margin", "opp_pre_turnover_margin", "pre_yards_per_play", "opp_pre_yards_per_play",
    "pre_def_ypp_allowed", "opp_pre_def_ypp_allowed", "pre_sacks_made", "opp_pre_sacks_made",
    "rank_off_epa", "opp_rank_off_epa", "rank_def_epa", "opp_rank_def_epa",
    "rank_pass_epa", "opp_rank_pass_epa", "rank_def_pass_epa", "opp_rank_def_pass_epa",
    "rank_rush_epa", "opp_rank_rush_epa", "rank_def_rush_epa", "opp_rank_def_rush_epa",
    "rank_scoring", "opp_rank_scoring", "rank_points_allowed", "opp_rank_points_allowed",
    "rank_turnover", "opp_rank_turnover", "rank_sacks_made", "opp_rank_sacks_made",
    "coach_pre_win", "coach_pre_fav_win", "coach_prior_games",
]
features = [c for c in numeric_candidates if c in b.columns]
ml = b[(b.week >= 4) & b.season.notna()].copy()
for c in features:
    ml[c] = pd.to_numeric(ml[c], errors="coerce")

models = {
    "logit": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=2000, C=0.5)),
    "hgb": make_pipeline(SimpleImputer(strategy="median"), HistGradientBoostingClassifier(max_depth=3, learning_rate=.06, max_iter=160, l2_regularization=1.0, random_state=7)),
}

wf_rows = []
for model_name, model in models.items():
    progress(76 if model_name == "logit" else 84, f"Walk-forward ML: {model_name}")
    for test_year in range(2016, 2027):
        train = ml[ml.season < test_year].copy()
        test = ml[ml.season == test_year].copy()
        if len(train) < 1000 or len(test) == 0:
            continue
        model.fit(train[features], train.win.astype(int))
        train["pred"] = model.predict_proba(train[features])[:, 1]
        test["pred"] = model.predict_proba(test[features])[:, 1]
        train["edge"] = train.pred - train.novig_prob
        test["edge"] = test.pred - test.novig_prob

        # Select threshold from historical data only. Require meaningful volume and user-style target on training.
        candidates = []
        for pmin in [.60, .65, .70, .75, .80, .85]:
            for emin in [.02, .04, .06, .08, .10, .12, .15]:
                for favonly in [True, False]:
                    z = train[(train.pred >= pmin) & (train.edge >= emin)]
                    if favonly:
                        z = z[z.novig_prob > .50]
                    if len(z) < 120:
                        continue
                    st = roi_stats(z)
                    # score rewards target behavior and sample size; doesn't require 10% train ROI to allow honest OOS.
                    score = (min(st["roi"], .20) * 3 + min(st["win_rate"], .90)) * math.sqrt(len(z))
                    candidates.append((score, pmin, emin, favonly, st))
        if not candidates:
            continue
        candidates.sort(key=lambda x: x[0], reverse=True)
        _, pmin, emin, favonly, trst = candidates[0]
        z = test[(test.pred >= pmin) & (test.edge >= emin)]
        if favonly:
            z = z[z.novig_prob > .50]
        tst = roi_stats(z)
        wf_rows.append({
            "model": model_name, "test_year": test_year, "pmin": pmin, "edge_min": emin, "favorite_only": favonly,
            "train_n": trst["n"], "train_win": trst["win_rate"], "train_roi": trst["roi"],
            "test_n": 0 if tst is None else tst["n"], "test_wins": 0 if tst is None else tst["wins"],
            "test_profit": 0.0 if tst is None else tst["profit"], "test_roi": np.nan if tst is None else tst["roi"],
            "test_win": np.nan if tst is None else tst["win_rate"],
        })

wf = pd.DataFrame(wf_rows)
wf.to_csv(OUT / "ml_walkforward_by_year.csv", index=False)
ml_summary = []
for name in models:
    z = wf[(wf.model == name) & (wf.test_n > 0)]
    n = int(z.test_n.sum()) if len(z) else 0
    wins = int(z.test_wins.sum()) if len(z) else 0
    profit = float(z.test_profit.sum()) if len(z) else 0.0
    ml_summary.append({
        "model": name, "oos_bets": n, "oos_wins": wins, "oos_win_rate": wins / n if n else np.nan,
        "oos_profit100": profit, "oos_roi": profit / (100 * n) if n else np.nan,
        "profitable_seasons": int((z.test_profit > 0).sum()), "seasons_with_bets": len(z),
    })
mls = pd.DataFrame(ml_summary)
mls.to_csv(OUT / "ml_walkforward_summary.csv", index=False)

progress(93, "Building final summary")
lines = []
lines.append("NFL EXHAUSTIVE HISTORY SEARCH — 2006-2026")
lines.append("Target: >=75% wins AND >=10% ROI with stability; all features strictly pre-game.")
lines.append(f"Priced team-side rows: {len(b):,}; favorite-side rows: {len(fav):,}")
lines.append(f"Manual rules tested: {len(rules):,}")
lines.append(f"Historical target rules n>=100: {len(elig):,}")
lines.append(f"Robust historical target rules: {len(robust):,}")
lines.append("")
lines.append("TOP ROBUST MANUAL RULES")
for _, r in robust.head(30).iterrows():
    lines.append(f"{r.family} | n={int(r.n)} | {int(r.wins)}-{int(r.losses)} | win={100*r.win_rate:.1f}% | ROI={100*r.roi:+.2f}% | old={100*r.old_roi:+.2f}% | new={100*r.new_roi:+.2f}% | blocks+={int(r.pos_blocks)}/4 | {r.rule}")
lines.append("")
lines.append("TRUE WALK-FORWARD ML")
for _, r in mls.iterrows():
    lines.append(f"{r.model} | OOS bets={int(r.oos_bets)} | win={100*r.oos_win_rate:.1f}% | ROI={100*r.oos_roi:+.2f}% | P/L=${r.oos_profit100:+.2f} | profitable seasons={int(r.profitable_seasons)}/{int(r.seasons_with_bets)}")

(OUT / "summary.txt").write_text("\n".join(lines))
print("\n" + "\n".join(lines[:45]))
progress(100, "Exhaustive NFL history search complete")
