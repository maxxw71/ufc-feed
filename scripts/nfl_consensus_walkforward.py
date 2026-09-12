from __future__ import annotations

from pathlib import Path
from itertools import product
import numpy as np
import pandas as pd

ROOT = Path.home() / "nfl-predictor-v1"
FEATURES = ROOT / "rolling_roi_discovery" / "pregame_team_sides_2006_2026.parquet"
SCHEDULES = ROOT / "data" / "raw" / "schedules_2006_2026.parquet"
OUT = ROOT / "nfl_consensus_walkforward"
OUT.mkdir(parents=True, exist_ok=True)


def prog(p, msg):
    print(f"[{p:3d}%] {msg}", flush=True)


def implied(o):
    if pd.isna(o) or o == 0:
        return np.nan
    o = float(o)
    return 100.0 / (o + 100.0) if o > 0 else abs(o) / (abs(o) + 100.0)

prog(2, "Loading strict pre-game NFL features")
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
b = b[b.novig_prob > .50].copy().reset_index(drop=True)

# Derived matchup edges.
for a, o, n in [
    ("pre_win", "opp_pre_win", "record_edge"),
    ("last3_win", "opp_last3_win", "recent_win_edge"),
    ("pre_point_diff", "opp_pre_point_diff", "season_pd_edge"),
    ("last3_point_diff", "opp_last3_point_diff", "last3_pd_edge"),
    ("pre_turnover_margin", "opp_pre_turnover_margin", "turnover_edge_raw"),
    ("pre_yards_per_play", "opp_pre_yards_per_play", "ypp_edge_raw"),
]:
    if a in b.columns and o in b.columns:
        b[n] = pd.to_numeric(b[a], errors="coerce") - pd.to_numeric(b[o], errors="coerce")

for name in ["off_epa", "def_epa", "pass_epa", "def_pass_epa", "rush_epa", "def_rush_epa", "scoring", "points_allowed", "turnover", "sacks_made", "def_ypp"]:
    a, o = f"rank_{name}", f"opp_rank_{name}"
    if a in b.columns and o in b.columns:
        b[f"edge_{name}"] = pd.to_numeric(b[o], errors="coerce") - pd.to_numeric(b[a], errors="coerce")

# Strictly prior coach history.
prog(6, "Building prior coach-history fields")
if {"home_coach", "away_coach"}.issubset(s.columns):
    hc = s[["game_id", "season", "week", "home_team", "away_team", "home_score", "away_score", "home_coach", "away_coach", "home_moneyline", "away_moneyline"]].copy()
    h = pd.DataFrame({"game_id": hc.game_id, "season": hc.season, "week": hc.week, "team": hc.home_team,
                      "coach": hc.home_coach, "cw": (hc.home_score > hc.away_score).astype(float),
                      "fav": hc.home_moneyline < hc.away_moneyline})
    a = pd.DataFrame({"game_id": hc.game_id, "season": hc.season, "week": hc.week, "team": hc.away_team,
                      "coach": hc.away_coach, "cw": (hc.away_score > hc.home_score).astype(float),
                      "fav": hc.away_moneyline < hc.home_moneyline})
    ch = pd.concat([h, a], ignore_index=True).sort_values(["coach", "season", "week", "game_id"])
    ch["coach_pre_fav_games"] = ch.groupby("coach")["fav"].transform(lambda x: x.shift(1).expanding().sum())
    ch["fav_win"] = np.where(ch.fav, ch.cw, np.nan)
    ch["coach_pre_fav_win"] = ch.groupby("coach")["fav_win"].transform(lambda x: x.expanding().mean().shift(1))
    b = b.merge(ch[["game_id", "team", "coach_pre_fav_games", "coach_pre_fav_win"]], on=["game_id", "team"], how="left")
else:
    b["coach_pre_fav_games"] = np.nan
    b["coach_pre_fav_win"] = np.nan

prog(9, f"Favorite-side pool: {len(b):,} games")

week_floors = [4, 6, 8, 10, 11, 12]
market_floors = [.52, .55, .60, .65, .70]
locations = ["ANY", "HOME", "ROAD"]

rules = []
masks = []

def locmask(loc):
    if loc == "ANY": return np.ones(len(b), dtype=bool)
    if loc == "HOME": return b.home_side.eq(1).to_numpy()
    return b.home_side.eq(0).to_numpy()

def add(family, desc, mask):
    arr = np.asarray(mask, dtype=bool)
    if arr.any():
        rules.append((family, desc))
        masks.append(arr)

# 1) YPP attack vs leaky defense.
prog(12, "Generating consensus families: YPP / EPA / scoring")
if {"pre_yards_per_play", "opp_pre_def_ypp_allowed"}.issubset(b.columns):
    for w, p, loc, oy, dy in product(week_floors, market_floors, locations, [5.5, 5.8, 6.0, 6.2], [5.5, 5.8, 6.0, 6.2]):
        add("YPP_ATTACK", f"w>={w} p>={p:.2f} {loc} offYPP>={oy:.1f} oppDefYPP>={dy:.1f}",
            (b.week >= w).to_numpy() & (b.novig_prob >= p).to_numpy() & locmask(loc) & (b.pre_yards_per_play >= oy).to_numpy() & (b.opp_pre_def_ypp_allowed >= dy).to_numpy())

# 2) Elite offense vs elite defense.
if {"rank_off_epa", "opp_rank_def_epa"}.issubset(b.columns):
    for w, p, loc, fr, od in product(week_floors, market_floors, locations, [5, 8, 10, 12], [5, 8, 10]):
        add("ELITE_OFF_ELITE_DEF", f"w>={w} p>={p:.2f} {loc} offRank<={fr} oppDefRank<={od}",
            (b.week >= w).to_numpy() & (b.novig_prob >= p).to_numpy() & locmask(loc) & (b.rank_off_epa <= fr).to_numpy() & (b.opp_rank_def_epa <= od).to_numpy())

# 3) Balanced scoring and defense.
if {"rank_scoring", "rank_points_allowed"}.issubset(b.columns):
    for w, p, loc, sr, dr in product(week_floors, market_floors, locations, [5, 8, 10, 12], [5, 8, 10, 12]):
        add("SCORING_DEFENSE", f"w>={w} p>={p:.2f} {loc} scoreRank<={sr} ptsAllowedRank<={dr}",
            (b.week >= w).to_numpy() & (b.novig_prob >= p).to_numpy() & locmask(loc) & (b.rank_scoring <= sr).to_numpy() & (b.rank_points_allowed <= dr).to_numpy())

# 4) Pass rush vs weak protection.
prog(25, "Generating consensus families: pass/rush mismatch")
if {"rank_sacks_made", "opp_pre_sacks_suffered"}.issubset(b.columns):
    for w, p, loc, sr, osa in product(week_floors, market_floors, locations, [5, 8, 10, 12], [2.0, 2.5, 3.0]):
        add("PASS_RUSH_PROTECTION", f"w>={w} p>={p:.2f} {loc} sackRank<={sr} oppSacks>={osa:.1f}",
            (b.week >= w).to_numpy() & (b.novig_prob >= p).to_numpy() & locmask(loc) & (b.rank_sacks_made <= sr).to_numpy() & (b.opp_pre_sacks_suffered >= osa).to_numpy())

# 5) Pass offense vs elite pass defense.
if {"rank_pass_epa", "opp_rank_def_pass_epa"}.issubset(b.columns):
    for w, p, loc, pr, dr in product(week_floors, market_floors, locations, [5, 8, 10, 12], [5, 8, 10]):
        add("PASS_OFF_ELITE_PASS_DEF", f"w>={w} p>={p:.2f} {loc} passRank<={pr} oppPassDef<={dr}",
            (b.week >= w).to_numpy() & (b.novig_prob >= p).to_numpy() & locmask(loc) & (b.rank_pass_epa <= pr).to_numpy() & (b.opp_rank_def_pass_epa <= dr).to_numpy())

# 6) Rush defense vs weak rushing offense.
if {"rank_def_rush_epa", "opp_rank_rush_epa"}.issubset(b.columns):
    for w, p, loc, dr, oo in product(week_floors, market_floors, locations, [5, 8, 10, 12], [18, 22, 25, 28]):
        add("RUSH_DEF_WEAK_OFF", f"w>={w} p>={p:.2f} {loc} rushDefRank<={dr} oppRushRank>={oo}",
            (b.week >= w).to_numpy() & (b.novig_prob >= p).to_numpy() & locmask(loc) & (b.rank_def_rush_epa <= dr).to_numpy() & (b.opp_rank_rush_epa >= oo).to_numpy())

# 7) Points allowed strength vs weak scoring offense.
prog(38, "Generating consensus families: defense / record / form")
if {"rank_points_allowed", "opp_rank_scoring"}.issubset(b.columns):
    for w, p, loc, dr, oo in product(week_floors, market_floors, locations, [5, 8, 10, 12], [18, 22, 25, 28]):
        add("POINTS_ALLOWED_WEAK", f"w>={w} p>={p:.2f} {loc} ptsAllowedRank<={dr} oppScoreRank>={oo}",
            (b.week >= w).to_numpy() & (b.novig_prob >= p).to_numpy() & locmask(loc) & (b.rank_points_allowed <= dr).to_numpy() & (b.opp_rank_scoring >= oo).to_numpy())

# 8) Record mismatch.
if {"pre_win", "opp_pre_win"}.issubset(b.columns):
    for w, p, loc, fw, ow in product(week_floors, market_floors, locations, [.55, .60, .65, .70, .75], [.30, .35, .40, .45, .50]):
        add("RECORD_MISMATCH", f"w>={w} p>={p:.2f} {loc} favRec>={fw:.2f} oppRec<={ow:.2f}",
            (b.week >= w).to_numpy() & (b.novig_prob >= p).to_numpy() & locmask(loc) & (b.pre_win >= fw).to_numpy() & (b.opp_pre_win <= ow).to_numpy())

# 9) Recent-form point differential.
if "last3_pd_edge" in b.columns:
    for w, p, loc, e in product(week_floors, market_floors, locations, [3, 5, 7, 10, 14]):
        add("RECENT_FORM", f"w>={w} p>={p:.2f} {loc} last3PDEdge>={e}",
            (b.week >= w).to_numpy() & (b.novig_prob >= p).to_numpy() & locmask(loc) & (b.last3_pd_edge >= e).to_numpy())

# 10) Turnover + EPA balance.
prog(50, "Generating consensus families: turnover / balance / coach")
if {"edge_turnover", "edge_off_epa"}.issubset(b.columns):
    for w, p, loc, te, oe in product(week_floors, market_floors, locations, [4, 8, 12, 16], [4, 8, 12, 16]):
        add("TURNOVER_OFFENSE", f"w>={w} p>={p:.2f} {loc} TOedge>={te} offEdge>={oe}",
            (b.week >= w).to_numpy() & (b.novig_prob >= p).to_numpy() & locmask(loc) & (b.edge_turnover >= te).to_numpy() & (b.edge_off_epa >= oe).to_numpy())

# 11) Balanced offense + defense EPA edge.
if {"edge_off_epa", "edge_def_epa"}.issubset(b.columns):
    for w, p, loc, oe, de in product(week_floors, market_floors, locations, [4, 8, 12, 16], [4, 8, 12, 16]):
        add("BALANCED_EPA", f"w>={w} p>={p:.2f} {loc} offEdge>={oe} defEdge>={de}",
            (b.week >= w).to_numpy() & (b.novig_prob >= p).to_numpy() & locmask(loc) & (b.edge_off_epa >= oe).to_numpy() & (b.edge_def_epa >= de).to_numpy())

# 12) Coach favorite history.
if {"coach_pre_fav_games", "coach_pre_fav_win"}.issubset(b.columns):
    for w, p, loc, cg, cw in product(week_floors, market_floors, locations, [15, 25, 40], [.60, .65, .70, .75]):
        add("COACH_HISTORY", f"w>={w} p>={p:.2f} {loc} coachFavN>={cg} coachFavWin>={cw:.2f}",
            (b.week >= w).to_numpy() & (b.novig_prob >= p).to_numpy() & locmask(loc) & (b.coach_pre_fav_games >= cg).to_numpy() & (b.coach_pre_fav_win >= cw).to_numpy())

rule_df = pd.DataFrame(rules, columns=["family", "rule"])
M = np.vstack(masks).astype(np.uint8)
prog(58, f"Consensus candidate universe: {len(rule_df):,} rules across {rule_df.family.nunique()} families")

season = b.season.to_numpy(dtype=int)
win = b.win.to_numpy(dtype=float)
profit = b.bet_profit100.to_numpy(dtype=float)
family_to_idx = {fam: np.flatnonzero(rule_df.family.to_numpy() == fam) for fam in rule_df.family.unique()}

# Policies select at most ONE rule per family using training history only.
def select_rule(idxs, train_mask, recent_mask, policy):
    X = M[idxs]
    tr = train_mask.astype(np.uint8)
    rc = recent_mask.astype(np.uint8)
    n = X @ tr
    wins = X @ (tr * win)
    pnl = X @ (tr * profit)
    rn = X @ rc
    rpnl = X @ (rc * profit)
    wr = np.divide(wins, n, out=np.zeros_like(wins, dtype=float), where=n > 0)
    roi = np.divide(pnl, 100*n, out=np.zeros_like(pnl, dtype=float), where=n > 0)
    rroi = np.divide(rpnl, 100*rn, out=np.zeros_like(rpnl, dtype=float), where=rn > 0)
    if policy == "STRICT":
        ok = (n >= 50) & (wr >= .75) & (roi >= .08) & (rn >= 15) & (rroi > 0)
        score = roi * np.sqrt(n)
    else:
        ok = (n >= 70) & (wr >= .70) & (roi >= .04) & (rn >= 20) & (rroi >= -.02)
        se = np.sqrt(np.maximum(wr*(1-wr), 1e-9) / np.maximum(n, 1))
        win_lb = wr - 1.64*se
        roi_shrunk = pnl / (100*(n + 50))
        score = win_lb + 2.0*roi_shrunk + 0.01*np.log1p(n)
    if not ok.any():
        return None
    local = int(np.argmax(np.where(ok, score, -1e9)))
    ridx = int(idxs[local])
    return ridx, int(n[local]), float(wr[local]), float(roi[local]), int(rn[local]), float(rroi[local])

selected_rows = []
bet_rows = []
summary_rows = []

prog(65, "Running family-consensus walk-forward 2016-2025")
for policy in ["STRICT", "CONSERVATIVE"]:
    for y in range(2016, 2026):
        train = season < y
        recent = (season >= y-5) & (season < y)
        test = season == y
        chosen = []
        for fam, idxs in family_to_idx.items():
            z = select_rule(idxs, train, recent, policy)
            if z is None:
                continue
            ridx, n, wr, roi, rn, rroi = z
            chosen.append((fam, ridx))
            selected_rows.append({"policy": policy, "test_year": y, "family": fam, "rule": rule_df.iloc[ridx].rule,
                                  "train_n": n, "train_win": wr, "train_roi": roi, "recent_n": rn, "recent_roi": rroi})
        test_idx = np.flatnonzero(test)
        votes = np.zeros(len(test_idx), dtype=int)
        fam_lists = [[] for _ in range(len(test_idx))]
        for fam, ridx in chosen:
            hit = M[ridx, test_idx].astype(bool)
            votes += hit.astype(int)
            for j in np.flatnonzero(hit):
                fam_lists[j].append(fam)
        for k in [2, 3, 4, 5]:
            pick = votes >= k
            ix = test_idx[pick]
            if len(ix) == 0:
                continue
            n = len(ix); w = int(win[ix].sum()); pnl = float(profit[ix].sum())
            summary_rows.append({"policy": policy, "vote_threshold": k, "test_year": y, "bets": n, "wins": w,
                                 "win_rate": w/n, "profit100": pnl, "roi": pnl/(100*n), "families_selected": len(chosen)})
            for pos, gi in zip(np.flatnonzero(pick), ix):
                bet_rows.append({"policy": policy, "vote_threshold": k, "test_year": y, "game_id": b.iloc[gi].game_id,
                                 "team": b.iloc[gi].team, "opponent": b.iloc[gi].opponent, "moneyline": b.iloc[gi].moneyline,
                                 "novig_prob": b.iloc[gi].novig_prob, "votes": int(votes[pos]), "families": ";".join(fam_lists[pos]),
                                 "win": int(win[gi]), "profit100": float(profit[gi])})
    prog(76 if policy == "STRICT" else 86, f"Consensus policy {policy} complete")

sel = pd.DataFrame(selected_rows)
byyear = pd.DataFrame(summary_rows)
bets_out = pd.DataFrame(bet_rows)
sel.to_csv(OUT / "selected_family_rules_by_year.csv", index=False)
byyear.to_csv(OUT / "consensus_by_year.csv", index=False)
bets_out.to_csv(OUT / "consensus_bets.csv", index=False)

agg = []
for (policy, k), z in byyear.groupby(["policy", "vote_threshold"]):
    n = int(z.bets.sum()); w = int(z.wins.sum()); pnl = float(z.profit100.sum())
    agg.append({"policy": policy, "vote_threshold": int(k), "oos_bets": n, "oos_wins": w,
                "oos_win_rate": w/n if n else np.nan, "oos_profit100": pnl, "oos_roi": pnl/(100*n) if n else np.nan,
                "profitable_years": int((z.profit100 > 0).sum()), "years_with_bets": len(z)})
agg = pd.DataFrame(agg).sort_values(["oos_roi", "oos_bets"], ascending=[False, False])
agg.to_csv(OUT / "consensus_summary.csv", index=False)

prog(92, "Consensus OOS summary")
print("\nNFL FAMILY-CONSENSUS WALK-FORWARD — TRUE OOS")
print("="*120)
for _, r in agg.iterrows():
    hit = " *** TARGET HIT ***" if r.oos_bets >= 30 and r.oos_win_rate >= .75 and r.oos_roi >= .10 else ""
    print(f"{r.policy:13s} votes>={int(r.vote_threshold)} | n={int(r.oos_bets):3d} {int(r.oos_wins):3d}-{int(r.oos_bets-r.oos_wins):3d} | win={100*r.oos_win_rate:5.1f}% | ROI={100*r.oos_roi:+6.2f}% | P/L=${r.oos_profit100:+,.2f} | profitable years={int(r.profitable_years)}/{int(r.years_with_bets)}{hit}")

hits = agg[(agg.oos_bets >= 30) & (agg.oos_win_rate >= .75) & (agg.oos_roi >= .10)]
print("\nTARGET HITS (>=30 OOS bets, >=75% wins, >=10% ROI)")
print("="*120)
if len(hits):
    for _, r in hits.iterrows():
        print(f"{r.policy} votes>={int(r.vote_threshold)}: n={int(r.oos_bets)} win={100*r.oos_win_rate:.1f}% ROI={100*r.oos_roi:+.2f}%")
else:
    print("None. Consensus did not clear the full target in this test.")

prog(100, "NFL consensus walk-forward complete")
