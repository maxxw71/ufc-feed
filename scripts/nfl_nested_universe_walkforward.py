from __future__ import annotations

from pathlib import Path
from itertools import product
import math
import numpy as np
import pandas as pd

ROOT = Path.home() / "nfl-predictor-v1"
FEATURES = ROOT / "rolling_roi_discovery" / "pregame_team_sides_2006_2026.parquet"
SCHEDULES = ROOT / "data" / "raw" / "schedules_2006_2026.parquet"
OUT = ROOT / "nfl_nested_universe_walkforward"
OUT.mkdir(parents=True, exist_ok=True)

YEARS = np.arange(2006, 2027)
NY = len(YEARS)


def progress(p: int, msg: str):
    print(f"[{p:3d}%] {msg}", flush=True)


def implied(o):
    if pd.isna(o) or o == 0:
        return np.nan
    o = float(o)
    return 100.0 / (o + 100.0) if o > 0 else abs(o) / (abs(o) + 100.0)


def wilson_lower(wins, n, z=1.2815515655446004):
    n = np.asarray(n, dtype=float)
    wins = np.asarray(wins, dtype=float)
    out = np.full_like(n, np.nan, dtype=float)
    ok = n > 0
    p = np.divide(wins, n, out=np.zeros_like(wins), where=ok)
    denom = 1.0 + z*z/n[ok]
    center = p[ok] + z*z/(2*n[ok])
    adj = z*np.sqrt((p[ok]*(1-p[ok]) + z*z/(4*n[ok]))/n[ok])
    out[ok] = (center - adj) / denom
    return out


progress(2, "Loading strict pre-game features and schedules")
g = pd.read_parquet(FEATURES)
s = pd.read_parquet(SCHEDULES)

for c in g.columns:
    if c not in {"game_id", "team", "opponent", "roof", "surface", "gameday"}:
        try:
            g[c] = pd.to_numeric(g[c], errors="ignore")
        except Exception:
            pass

b = g[g.moneyline.notna() & g.opp_moneyline.notna() & g.win.isin([0.0, 1.0]) & (g.prior_games >= 3)].copy()
b["team_imp"] = b.moneyline.map(implied)
b["opp_imp"] = b.opp_moneyline.map(implied)
b["novig_prob"] = b.team_imp / (b.team_imp + b.opp_imp)
b["favorite"] = b.novig_prob > .5

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

progress(6, "Building strictly prior coach-history fields")
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
    ch["fav_win_contrib"] = np.where(ch.coach_fav, ch.coach_win, np.nan)
    ch["coach_pre_fav_win"] = ch.groupby("coach")["fav_win_contrib"].transform(lambda x: x.expanding().mean().shift(1))
    b = b.merge(ch[["game_id", "team", "coach", "coach_prior_games", "coach_pre_win", "coach_pre_fav_games", "coach_pre_fav_win"]], on=["game_id", "team"], how="left")
else:
    b["coach_prior_games"] = np.nan
    b["coach_pre_win"] = np.nan
    b["coach_pre_fav_games"] = np.nan
    b["coach_pre_fav_win"] = np.nan

fav = b[b.favorite & b.season.between(2006, 2026)].copy().reset_index(drop=True)
progress(9, f"Favorite-side pool: {len(fav):,} games")

season = pd.to_numeric(fav.season, errors="coerce").to_numpy(dtype=int)
yidx = season - 2006
wins_vec = pd.to_numeric(fav.win, errors="coerce").fillna(0).to_numpy(float)
profit_vec = pd.to_numeric(fav.bet_profit100, errors="coerce").fillna(0).to_numpy(float)

families = []
rules_text = []
counts_by_year = []
wins_by_year = []
profit_by_year = []


def add(family, desc, mask):
    if hasattr(mask, "fillna"):
        m = mask.fillna(False).to_numpy(dtype=bool)
    else:
        m = np.asarray(mask, dtype=bool)
    if not m.any():
        return
    idx = yidx[m]
    valid = (idx >= 0) & (idx < NY)
    if not valid.any():
        return
    idx = idx[valid]
    wm = wins_vec[m][valid]
    pm = profit_vec[m][valid]
    families.append(family)
    rules_text.append(desc)
    counts_by_year.append(np.bincount(idx, minlength=NY).astype(np.int16))
    wins_by_year.append(np.bincount(idx, weights=wm, minlength=NY).astype(np.int16))
    profit_by_year.append(np.bincount(idx, weights=pm, minlength=NY).astype(np.float32))


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


progress(12, "Generating exact exhaustive rule universe: record/form")
for w, mp, loc in product(week_floors, market_floors, locations):
    bm = base(w, mp, loc)
    if {"pre_win", "opp_pre_win"}.issubset(fav.columns):
        for fw, ow in product([.55, .60, .65, .70, .75], [.25, .30, .35, .40, .45, .50, .55, .60]):
            add("RECORD MISMATCH", f"week>={w} p>={mp:.2f} {loc} favRecord>={fw:.2f} oppRecord<={ow:.2f}", bm & (fav.pre_win >= fw) & (fav.opp_pre_win <= ow))
    if "last3_pd_edge" in fav.columns:
        for e in [3, 5, 7, 10, 14, 18]:
            add("RECENT FORM", f"week>={w} p>={mp:.2f} {loc} last3PDEdge>={e}", bm & (fav.last3_pd_edge >= e))

progress(23, "Generating exact exhaustive rule universe: offense/defense archetypes")
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

progress(35, "Generating exact exhaustive rule universe: rank edges")
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

progress(47, "Generating exact exhaustive rule universe: paired edges")
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
    for aa, cc, fam in combos:
        if aa not in fav.columns or cc not in fav.columns:
            continue
        for x, y in product([4, 8, 12, 16], [4, 8, 12, 16]):
            add(fam, f"week>={w} p>={mp:.2f} {loc} {aa}>={x} {cc}>={y}", bm & (fav[aa] >= x) & (fav[cc] >= y))

progress(57, "Generating exact exhaustive rule universe: rest/division/coach")
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

progress(65, "Generating exact exhaustive rule universe: raw dominance")
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

progress(72, "Stacking per-season sufficient statistics")
C = np.stack(counts_by_year).astype(np.int32)
W = np.stack(wins_by_year).astype(np.int32)
P = np.stack(profit_by_year).astype(np.float64)
R = len(families)
print(f"Rule universe with >=1 historical bet: {R:,}", flush=True)
if R != 125721:
    print(f"NOTE: prior exhaustive run reported 125,721 rules; current non-empty universe is {R:,}. Selection will use every rule generated by the same rule grammar on the current data.", flush=True)

catalog = pd.DataFrame({"rule_id": np.arange(R), "family": families, "rule": rules_text})
catalog.to_csv(OUT / "rule_catalog.csv", index=False)

# Cumulative historical statistics make fully nested yearly selection cheap.
Cc = np.cumsum(C, axis=1)
Wc = np.cumsum(W, axis=1)
Pc = np.cumsum(P, axis=1)


def period_sums(mat, lo_year, hi_year):
    lo = lo_year - 2006
    hi = hi_year - 2006
    z = mat[:, hi].copy()
    if lo > 0:
        z -= mat[:, lo-1]
    return z


def select_rule(test_year: int, policy: str):
    train_hi = test_year - 1
    j = train_hi - 2006
    n = Cc[:, j].astype(float)
    w = Wc[:, j].astype(float)
    p = Pc[:, j].astype(float)
    wr = np.divide(w, n, out=np.full(R, np.nan), where=n > 0)
    roi = np.divide(p, 100*n, out=np.full(R, np.nan), where=n > 0)

    # Require stability in an older block and the five seasons immediately before the test year.
    recent_lo = max(2006, test_year - 5)
    early_hi = recent_lo - 1
    rn = period_sums(Cc, recent_lo, train_hi).astype(float)
    rw = period_sums(Wc, recent_lo, train_hi).astype(float)
    rp = period_sums(Pc, recent_lo, train_hi).astype(float)
    rwr = np.divide(rw, rn, out=np.full(R, np.nan), where=rn > 0)
    rroi = np.divide(rp, 100*rn, out=np.full(R, np.nan), where=rn > 0)

    if early_hi >= 2006:
        en = period_sums(Cc, 2006, early_hi).astype(float)
        ep = period_sums(Pc, 2006, early_hi).astype(float)
        eroi = np.divide(ep, 100*en, out=np.full(R, np.nan), where=en > 0)
    else:
        en = np.zeros(R)
        eroi = np.full(R, np.nan)

    if policy == "STRICT_TARGET":
        ok = (n >= 70) & (wr >= .75) & (roi >= .10) & (rn >= 25) & (rwr >= .70) & (rroi > 0)
        if early_hi >= 2006:
            ok &= (en >= 25) & (eroi > 0)
        # Favor conservative confidence, ROI, then sample size.
        wl = wilson_lower(w, n)
        shrink_roi = p / (100*(n + 40.0))
        score = wl + 2.5*shrink_roi + .012*np.log1p(n)
    elif policy == "CONSERVATIVE":
        ok = (n >= 90) & (wr >= .70) & (roi >= .06) & (rn >= 30) & (rwr >= .68) & (rroi > 0)
        if early_hi >= 2006:
            ok &= (en >= 30) & (eroi > 0)
        wl = wilson_lower(w, n)
        shrink_roi = p / (100*(n + 75.0))
        score = wl + 2.0*shrink_roi + .016*np.log1p(n)
    elif policy == "MAX_SAMPLE_TARGET":
        ok = (n >= 70) & (wr >= .75) & (roi >= .10) & (rn >= 25) & (rroi > 0)
        if early_hi >= 2006:
            ok &= (en >= 25) & (eroi > 0)
        score = n + 25*np.minimum(roi, .20)
    else:
        raise ValueError(policy)

    ids = np.flatnonzero(ok)
    if not len(ids):
        return None
    best = ids[np.nanargmax(score[ids])]
    return int(best), {
        "train_n": int(n[best]), "train_wins": int(w[best]), "train_win": float(wr[best]),
        "train_profit": float(p[best]), "train_roi": float(roi[best]),
        "recent_n": int(rn[best]), "recent_win": float(rwr[best]), "recent_roi": float(rroi[best]),
        "early_n": int(en[best]) if early_hi >= 2006 else 0,
        "early_roi": float(eroi[best]) if early_hi >= 2006 else np.nan,
        "score": float(score[best]),
    }


progress(80, "Running fully nested season-by-season selection across the entire rule universe")
policies = ["STRICT_TARGET", "CONSERVATIVE", "MAX_SAMPLE_TARGET"]
rows = []
for policy in policies:
    for test_year in range(2016, 2027):
        sel = select_rule(test_year, policy)
        if sel is None:
            rows.append({"policy": policy, "test_year": test_year, "rule_id": np.nan, "family": "NONE", "rule": "NONE", "test_n": 0, "test_wins": 0, "test_profit": 0.0, "test_roi": np.nan, "test_win": np.nan})
            continue
        rid, tr = sel
        j = test_year - 2006
        tn = int(C[rid, j]); tw = int(W[rid, j]); tp = float(P[rid, j])
        rows.append({
            "policy": policy, "test_year": test_year, "rule_id": rid,
            "family": families[rid], "rule": rules_text[rid], **tr,
            "test_n": tn, "test_wins": tw, "test_profit": tp,
            "test_roi": tp/(100*tn) if tn else np.nan,
            "test_win": tw/tn if tn else np.nan,
        })
    print(f"Nested policy {policy} complete", flush=True)

wf = pd.DataFrame(rows)
wf.to_csv(OUT / "nested_by_year.csv", index=False)

progress(88, "Summarizing true out-of-sample results")
summ = []
for policy in policies:
    z = wf[(wf.policy == policy) & (wf.test_n > 0)].copy()
    n = int(z.test_n.sum()) if len(z) else 0
    wins = int(z.test_wins.sum()) if len(z) else 0
    profit = float(z.test_profit.sum()) if len(z) else 0.0
    summ.append({
        "policy": policy, "oos_bets": n, "oos_wins": wins,
        "oos_win_rate": wins/n if n else np.nan,
        "oos_profit100": profit, "oos_roi": profit/(100*n) if n else np.nan,
        "profitable_seasons": int((z.test_profit > 0).sum()),
        "seasons_with_bets": int(len(z)),
        "distinct_families": int(z.family.nunique()),
        "target_hit": bool(n >= 30 and wins/n >= .75 and profit/(100*n) >= .10) if n else False,
    })
summary = pd.DataFrame(summ)
summary.to_csv(OUT / "nested_summary.csv", index=False)

# Also summarize OOS performance by selected family, without using this table for selection.
family_rows = []
for policy in policies:
    z = wf[(wf.policy == policy) & (wf.test_n > 0)]
    for fam, q in z.groupby("family"):
        n = int(q.test_n.sum()); wins = int(q.test_wins.sum()); profit = float(q.test_profit.sum())
        family_rows.append({"policy": policy, "family": fam, "oos_bets": n, "oos_wins": wins, "oos_win_rate": wins/n if n else np.nan, "oos_profit100": profit, "oos_roi": profit/(100*n) if n else np.nan, "seasons_selected": len(q)})
pd.DataFrame(family_rows).to_csv(OUT / "selected_family_oos.csv", index=False)

print("\nFULLY NESTED NFL RULE-UNIVERSE WALK-FORWARD", flush=True)
print("="*125, flush=True)
print(f"Rule universe evaluated each year: {R:,}", flush=True)
for _, r in summary.iterrows():
    hit = " *** TARGET HIT ***" if r.target_hit else ""
    print(f"{r.policy:20s} OOS n={int(r.oos_bets):4d} {int(r.oos_wins):4d}-{int(r.oos_bets-r.oos_wins):4d} win={100*r.oos_win_rate:5.1f}% ROI={100*r.oos_roi:+6.2f}% P/L=${r.oos_profit100:+,.2f} profitable seasons={int(r.profitable_seasons)}/{int(r.seasons_with_bets)} families={int(r.distinct_families)}{hit}", flush=True)

print("\nYEAR-BY-YEAR SELECTED RULES", flush=True)
print("="*125, flush=True)
for policy in policies:
    print(f"\n{policy}", flush=True)
    z = wf[wf.policy == policy]
    for _, r in z.iterrows():
        if int(r.test_n) == 0:
            print(f"  {int(r.test_year)}: no qualifying selection / no test bets", flush=True)
        else:
            print(f"  {int(r.test_year)}: {r.family} | test {int(r.test_wins)}-{int(r.test_n-r.test_wins)} win={100*r.test_win:.1f}% ROI={100*r.test_roi:+.2f}% | TRAIN-ONLY SELECTED: {r.rule}", flush=True)

progress(100, "Fully nested NFL universe walk-forward complete")
