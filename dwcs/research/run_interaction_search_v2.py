#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(".")
MASTER = ROOT / "dwcs/frozen/dwcs_historical_master_frozen.csv"
MANIFEST = ROOT / "dwcs/frozen/freeze_manifest.json"
OUT = ROOT / "dwcs/research/interaction_search_v2"
OUT.mkdir(parents=True, exist_ok=True)

DISCOVERY_SEASONS = {1, 2, 3, 4, 5, 6}
VALIDATION_SEASONS = {7, 8, 9}
MIN_DISCOVERY_N = 12
MAX_FROZEN_CANDIDATES = 100


def num(s):
    return pd.to_numeric(s, errors="coerce")


def implied_prob(o):
    o = num(o)
    return np.where(o < 0, (-o) / ((-o) + 100.0), 100.0 / (o + 100.0))


def american_profit(o, won):
    o = float(o)
    if not won:
        return -100.0
    return 10000.0 / abs(o) if o < 0 else o


def make_long(df):
    rows = []
    for side, other, sign in [("a", "b", 1.0), ("b", "a", -1.0)]:
        z = pd.DataFrame(index=df.index)
        z["fight_id"] = df.index
        z["season"] = num(df["season"]).astype("Int64")
        z["event_date"] = df["event_date"]
        z["event_name"] = df["event_name"]
        z["fighter"] = df[f"fighter_{side}"]
        z["opponent"] = df[f"fighter_{other}"]
        z["winner"] = df["winner"]
        z["side"] = side
        z["close_odds"] = num(df.get(f"{side}_close_odds"))
        z["open_odds"] = num(df.get(f"{side}_open_odds"))
        z["won"] = z["fighter"].astype(str).str.strip().eq(z["winner"].astype(str).str.strip())

        # Oriented gaps: positive always favors the selected side.
        z["youth_edge"] = -sign * num(df.get("age_gap_a_minus_b"))
        z["experience_edge"] = sign * num(df.get("experience_gap_a_minus_b"))
        z["win_edge"] = sign * num(df.get("win_pct_gap_a_minus_b"))
        z["finish_edge"] = sign * num(df.get("finish_rate_gap_a_minus_b"))
        z["oppqual_edge"] = sign * num(df.get("opp_quality_gap_a_minus_b"))
        z["elo_edge"] = sign * num(df.get("global_elo_gap_a_minus_b"))
        z["sos_edge"] = sign * num(df.get("sos_avg_elo_gap_a_minus_b"))
        z["height_edge"] = sign * num(df.get("height_gap_a_minus_b"))
        z["reach_edge"] = sign * num(df.get("reach_gap_a_minus_b"))
        z["sigdiff_edge"] = sign * num(df.get("dwcs_sig_diff_gap_a_minus_b"))
        z["tddef_edge"] = sign * num(df.get("dwcs_td_def_gap_a_minus_b"))

        z["prior_fights"] = num(df.get(f"{side}_prior_fights"))
        z["prior_finish_rate"] = num(df.get(f"{side}_prior_finish_rate"))
        z["prior_oppqual"] = num(df.get(f"{side}_prior_avg_opponent_win_pct"))
        z["prefight_elo"] = num(df.get(f"{side}_prefight_global_elo"))
        z["prior_tech_n"] = num(df.get(f"{side}_dwcs_prior_technical_fights"))
        z["prior_sig_diff"] = num(df.get(f"{side}_dwcs_prior_sig_diff"))
        z["prior_td_def"] = num(df.get(f"{side}_dwcs_prior_td_defense"))
        z["prior_ctrl_diff"] = num(df.get(f"{side}_dwcs_prior_control_diff"))

        op = implied_prob(z["open_odds"])
        cp = implied_prob(z["close_odds"])
        z["steam_pp"] = (cp - op) * 100.0

        rows.append(z)
    out = pd.concat(rows, ignore_index=True)
    # Global risk gate agreed for DWCS/UFC research: no four-digit favorites.
    out = out[out["close_odds"].notna() & (out["close_odds"] >= -999)].copy()
    return out


class Cond:
    def __init__(self, name, family, col, op, value):
        self.name = name
        self.family = family
        self.col = col
        self.op = op
        self.value = value

    def mask(self, d):
        x = num(d[self.col])
        if self.op == "ge":
            return x.ge(self.value)
        if self.op == "le":
            return x.le(self.value)
        if self.op == "between":
            lo, hi = self.value
            return x.ge(lo) & x.le(hi)
        raise ValueError(self.op)


ANCHORS = []
MODIFIERS = []


def add_anchor(family, col, thresholds, label=None):
    for t in thresholds:
        nm = f"{label or col}>={t:g}"
        ANCHORS.append(Cond(nm, family, col, "ge", t))


def add_mod(family, col, thresholds, label=None):
    for t in thresholds:
        nm = f"{label or col}>={t:g}"
        MODIFIERS.append(Cond(nm, family, col, "ge", t))


add_anchor("age", "youth_edge", [3, 4, 5, 6], "younger_y")
add_anchor("experience", "experience_edge", [2, 4, 6], "exp_gap")
add_anchor("win", "win_edge", [.10, .15, .20], "win_gap")
add_anchor("oppqual", "oppqual_edge", [.05, .10, .15, .20], "oppqual_gap")
add_anchor("elo", "elo_edge", [25, 50, 75, 100], "elo_gap")
add_anchor("sos", "sos_edge", [25, 50, 75], "sos_gap")
add_anchor("finish", "finish_edge", [.15, .25, .35], "finish_gap")
add_anchor("reach", "reach_edge", [2, 4, 6], "reach_gap")
add_anchor("height", "height_edge", [2, 4], "height_gap")
add_anchor("technical", "sigdiff_edge", [1, 5, 10], "sig_diff_gap")

add_mod("age", "youth_edge", [3, 5, 6], "younger_y")
add_mod("experience", "experience_edge", [0, 2, 4], "exp_gap")
add_mod("win", "win_edge", [.05, .10, .15], "win_gap")
add_mod("oppqual", "oppqual_edge", [.05, .10, .15], "oppqual_gap")
add_mod("elo", "elo_edge", [25, 50, 75], "elo_gap")
add_mod("sos", "sos_edge", [25, 50], "sos_gap")
add_mod("finish", "finish_edge", [.10, .20], "finish_gap")
add_mod("reach", "reach_edge", [2, 4], "reach_gap")
add_mod("height", "height_edge", [2, 4], "height_gap")
add_mod("market", "steam_pp", [5, 10, 15], "steam_pp")
add_mod("technical_history", "prior_tech_n", [1], "prior_dwcs_n")
add_mod("technical_stat", "prior_sig_diff", [0, 5], "prior_sig_diff")
add_mod("td_def", "prior_td_def", [.60, .70], "prior_td_def")

PRICE_CONDS = [
    Cond("dog_100_250", "price", "close_odds", "between", (100, 250)),
    Cond("dog_100_400", "price", "close_odds", "between", (100, 400)),
    Cond("fav_-400_-100", "price", "close_odds", "between", (-400, -100)),
    Cond("fav_-250_-100", "price", "close_odds", "between", (-250, -100)),
    Cond("not_worse_than_-500", "price", "close_odds", "ge", -500),
]
MODIFIERS.extend(PRICE_CONDS)


def signal_rows(d, conds, anchor):
    m = pd.Series(True, index=d.index)
    for c in conds:
        m &= c.mask(d).fillna(False)
    q = d[m].copy()
    if q.empty:
        return q
    # Anchor thresholds are directional; this should normally be one side/fight.
    # Keep the strongest anchor edge if a malformed/missing source creates duplicates.
    q["_anchor"] = num(q[anchor.col])
    q = q.sort_values(["fight_id", "_anchor"], ascending=[True, False]).drop_duplicates("fight_id", keep="first")
    return q.drop(columns=["_anchor"])


def summarize(q):
    if q.empty:
        return None
    q = q.copy()
    q["profit100"] = [american_profit(o, w) for o, w in zip(q.close_odds, q.won)]
    n = len(q)
    wins = int(q.won.sum())
    roi = float(q.profit100.sum() / (100.0 * n))
    by = []
    profitable = 0
    season_rois = []
    for s, g in q.groupby("season"):
        sr = float(g.profit100.sum() / (100.0 * len(g)))
        profitable += int(sr > 0)
        season_rois.append(sr)
        by.append(f"{int(s)}:{len(g)}:{int(g.won.sum())}:{g.won.mean():.3f}:{sr:.3f}")
    return {
        "n": n,
        "wins": wins,
        "win_rate": wins / n,
        "roi": roi,
        "seasons": int(q.season.nunique()),
        "profitable_seasons": profitable,
        "worst_season_roi": min(season_rois) if season_rois else np.nan,
        "season_detail": "|".join(by),
        "signature": hashlib.sha1(",".join(map(str, sorted(q.fight_id.tolist()))).encode()).hexdigest(),
    }


def rule_label(conds):
    return " & ".join(c.name for c in conds)


def evaluate_discovery(disc):
    records = []
    seen_rules = set()
    for anchor in ANCHORS:
        eligible_mods = [m for m in MODIFIERS if m.family != anchor.family]

        # Anchor + one modifier and anchor + two modifiers. Repeated feature families
        # in one conjunction are prohibited so thresholds remain interpretable.
        combos = [(m,) for m in eligible_mods]
        combos += [
            pair for pair in itertools.combinations(eligible_mods, 2)
            if pair[0].family != pair[1].family
        ]

        for mods in combos:
            conds = (anchor,) + tuple(mods)
            label = rule_label(conds)
            if label in seen_rules:
                continue
            seen_rules.add(label)
            q = signal_rows(disc, conds, anchor)
            if len(q) < MIN_DISCOVERY_N:
                continue
            z = summarize(q)
            if z is None or z["seasons"] < 4:
                continue
            # Broad enough to retain probability and value lanes without selecting on validation.
            if z["win_rate"] < .55 or z["roi"] <= 0:
                continue
            lane = "high_probability" if z["win_rate"] >= .68 else ("value" if z["roi"] >= .20 else "hybrid")
            score = (
                z["roi"]
                + 1.35 * (z["win_rate"] - .50)
                + .10 * (z["profitable_seasons"] / max(1, z["seasons"]))
                + .02 * math.log1p(z["n"])
            )
            records.append({
                "anchor_family": anchor.family,
                "anchor": anchor.name,
                "rule": label,
                "lane": lane,
                "selection_score": score,
                **{k: v for k, v in z.items() if k != "signature"},
                "signal_signature": z["signature"],
            })
    return pd.DataFrame(records)


def freeze_candidates(all_rules):
    if all_rules.empty:
        return all_rules
    # Dedupe exact discovery selections; keep the simplest/highest-scoring explanation.
    d = all_rules.copy()
    d["condition_count"] = d["rule"].str.count("&") + 1
    d = d.sort_values(["selection_score", "condition_count"], ascending=[False, True])
    d = d.drop_duplicates("signal_signature", keep="first")

    picked = []
    # Preserve breadth across lanes/families before filling by score.
    for lane in ["high_probability", "value", "hybrid"]:
        for fam, g in d[d.lane.eq(lane)].groupby("anchor_family"):
            picked.extend(g.head(3).index.tolist())
    picked = list(dict.fromkeys(picked))
    for idx in d.index:
        if len(picked) >= MAX_FROZEN_CANDIDATES:
            break
        if idx not in picked:
            picked.append(idx)
    return d.loc[picked].sort_values("selection_score", ascending=False).head(MAX_FROZEN_CANDIDATES)


def parse_rule(label):
    lookup = {c.name: c for c in ANCHORS + MODIFIERS}
    names = [x.strip() for x in label.split("&")]
    conds = [lookup[n] for n in names]
    anchor = conds[0]
    return conds, anchor


def validate(cands, val):
    rows = []
    picks = []
    for _, c in cands.iterrows():
        conds, anchor = parse_rule(c.rule)
        q = signal_rows(val, conds, anchor)
        z = summarize(q) if len(q) else {
            "n": 0, "wins": 0, "win_rate": np.nan, "roi": np.nan,
            "seasons": 0, "profitable_seasons": 0, "worst_season_roi": np.nan,
            "season_detail": "", "signature": ""
        }
        status = "insufficient_holdout_sample"
        if z["n"] >= 10:
            if c.lane == "high_probability":
                status = "passed_locked_validation" if z["win_rate"] >= .65 and z["roi"] > 0 and z["profitable_seasons"] >= 2 else "failed_locked_validation"
            elif c.lane == "value":
                status = "passed_locked_validation" if z["win_rate"] >= .50 and z["roi"] >= .15 and z["profitable_seasons"] >= 2 else "failed_locked_validation"
            else:
                status = "passed_locked_validation" if z["win_rate"] >= .58 and z["roi"] > 0 and z["profitable_seasons"] >= 2 else "failed_locked_validation"
        rows.append({
            "anchor_family": c.anchor_family,
            "anchor": c.anchor,
            "rule": c.rule,
            "lane": c.lane,
            "discovery_n": int(c.n),
            "discovery_win_rate": c.win_rate,
            "discovery_roi": c.roi,
            "discovery_profitable_seasons": int(c.profitable_seasons),
            "validation_n": int(z["n"]),
            "validation_wins": int(z["wins"]),
            "validation_win_rate": z["win_rate"],
            "validation_roi": z["roi"],
            "validation_profitable_seasons": int(z["profitable_seasons"]),
            "validation_seasons": int(z["seasons"]),
            "validation_worst_season_roi": z["worst_season_roi"],
            "validation_season_detail": z["season_detail"],
            "validation_status": status,
            "thresholds_frozen_before_validation": True,
        })
        if len(q):
            qq = q.copy()
            qq["rule"] = c.rule
            qq["lane"] = c.lane
            picks.append(qq[["rule", "lane", "fight_id", "season", "event_date", "event_name", "fighter", "opponent", "close_odds", "won"]])
    return pd.DataFrame(rows), (pd.concat(picks, ignore_index=True) if picks else pd.DataFrame())


def main():
    if not MASTER.exists() or not MANIFEST.exists():
        raise SystemExit("Frozen master/manifest missing")

    manifest = json.loads(MANIFEST.read_text())
    actual_hash = hashlib.sha256(MASTER.read_bytes()).hexdigest()
    expected_hash = manifest.get("master_sha256")
    if expected_hash and actual_hash != expected_hash:
        raise SystemExit(f"Frozen master hash mismatch: {actual_hash} != {expected_hash}")

    df = pd.read_csv(MASTER, low_memory=False)
    seasons = set(num(df["season"]).dropna().astype(int).unique())
    if any(s >= 10 for s in seasons):
        raise SystemExit(f"Leakage guard: Season 10+ found in historical search master: {sorted(seasons)}")

    disc_df = df[num(df["season"]).isin(DISCOVERY_SEASONS)].copy()
    val_df = df[num(df["season"]).isin(VALIDATION_SEASONS)].copy()
    disc = make_long(disc_df)
    val = make_long(val_df)

    all_rules = evaluate_discovery(disc)
    all_rules = all_rules.sort_values("selection_score", ascending=False)
    cands = freeze_candidates(all_rules)
    validation, validation_picks = validate(cands, val)

    all_rules.to_csv(OUT / "discovery_all_interactions.csv", index=False)
    cands.to_csv(OUT / "frozen_interaction_candidates.csv", index=False)
    validation.to_csv(OUT / "locked_validation_results.csv", index=False)
    validation_picks.to_csv(OUT / "locked_validation_picks.csv", index=False)

    passed = validation[validation.validation_status.eq("passed_locked_validation")].copy()
    promising = validation[
        validation.validation_status.eq("insufficient_holdout_sample")
        & validation.validation_roi.fillna(-9).gt(0)
        & validation.validation_win_rate.fillna(0).ge(.60)
    ].copy()

    meta = {
        "source_master_sha256": actual_hash,
        "immutable_source_master": True,
        "discovery_seasons": sorted(DISCOVERY_SEASONS),
        "locked_validation_seasons": sorted(VALIDATION_SEASONS),
        "season10_used_for_rule_generation": False,
        "season10_used_for_threshold_selection": False,
        "four_digit_favorites_rejected": True,
        "min_discovery_n": MIN_DISCOVERY_N,
        "rules_retained_after_discovery_filters": int(len(all_rules)),
        "frozen_candidates": int(len(cands)),
        "passed_locked_validation": int(len(passed)),
        "promising_small_holdout": int(len(promising)),
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    lines = [
        "DWCS BROAD INTERACTION SEARCH V2",
        "=" * 112,
        f"Immutable frozen master SHA256: {actual_hash}",
        "Discovery only: Seasons 1-6",
        "Locked validation only: Seasons 7-9",
        "Season 10: EXCLUDED from all rule generation and threshold selection",
        "Global gate: four-digit favorites rejected",
        "",
        f"Discovery interaction rules retained: {len(all_rules)}",
        f"Frozen before validation: {len(cands)}",
        f"Passed locked validation: {len(passed)}",
        f"Promising but small holdout: {len(promising)}",
        "",
        "TOP PASSED LOCKED-VALIDATION INTERACTIONS",
        "-" * 112,
    ]
    if passed.empty:
        lines.append("No interaction met the configured locked-validation promotion screen.")
    else:
        show = passed.sort_values(["validation_roi", "validation_win_rate"], ascending=False).head(30)
        for _, x in show.iterrows():
            lines.append(
                f"{x.lane:<16} VAL n={int(x.validation_n):>3} win={x.validation_win_rate*100:5.1f}% "
                f"ROI={x.validation_roi*100:+6.1f}% | DISC n={int(x.discovery_n):>3} "
                f"win={x.discovery_win_rate*100:5.1f}% ROI={x.discovery_roi*100:+6.1f}% | {x.rule}"
            )
    lines += ["", "PROMISING SMALL-HOLDOUT INTERACTIONS", "-" * 112]
    if promising.empty:
        lines.append("None.")
    else:
        show = promising.sort_values(["validation_win_rate", "validation_roi"], ascending=False).head(25)
        for _, x in show.iterrows():
            lines.append(
                f"{x.lane:<16} VAL n={int(x.validation_n):>3} win={x.validation_win_rate*100:5.1f}% "
                f"ROI={x.validation_roi*100:+6.1f}% | {x.rule}"
            )
    (OUT / "report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
