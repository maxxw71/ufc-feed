#!/usr/bin/env python3
"""Robustness scan for the UFC STRIKING + TD DEFENSE method.

Downloads the same Appwiza research snapshots used by the site and tests
pre-declared veto families against the full historical sample. This is meant
to prevent one-loss retrofitting: filters must improve the broader sample and
remain positive across eras before they are candidates for production use.
"""
from __future__ import annotations
import argparse, io, re, unicodedata, urllib.request, zipfile
from pathlib import Path
import numpy as np
import pandas as pd

NEWCAT_URL = "https://appwiza.com/sports/downloads/ufc/new-category-discovery.zip"
REACH_URL = "https://appwiza.com/sports/downloads/ufc/ufc-reach-method-analysis.zip"


def norm(v):
    x = unicodedata.normalize("NFKD", str(v))
    x = "".join(ch for ch in x if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", x.lower()).split())


def as_bool(s):
    if pd.api.types.is_bool_dtype(s):
        return s.fillna(False).astype(bool)
    return s.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "w", "win"})


def fetch_zip_csv(url, suffix):
    req = urllib.request.Request(url, headers={"User-Agent": "Appwiza-UFC-Research/1.0"})
    with urllib.request.urlopen(req, timeout=120) as response:
        raw = response.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = [n for n in archive.namelist() if n.endswith(suffix)]
        if not names:
            raise RuntimeError(f"{suffix} not found in {url}; members={archive.namelist()[:30]}")
        with archive.open(names[0]) as handle:
            return pd.read_csv(handle, low_memory=False)


def roi(g):
    return float(g["profit100"].sum() / (100 * len(g))) if len(g) else np.nan


def summary(g):
    if not len(g):
        return dict(n=0, wins=0, losses=0, win_rate=np.nan, roi=np.nan,
                    pre_n=0, pre_roi=np.nan, recent_n=0, recent_roi=np.nan,
                    y2022_n=0, y2022_roi=np.nan)
    old = g[g.event_date < pd.Timestamp("2020-01-01")]
    recent = g[g.event_date >= pd.Timestamp("2020-01-01")]
    y22 = g[g.event_date >= pd.Timestamp("2022-01-01")]
    wins = int(g.won.sum())
    return dict(n=len(g), wins=wins, losses=len(g)-wins,
                win_rate=float(g.won.mean()), roi=roi(g),
                pre_n=len(old), pre_roi=roi(old),
                recent_n=len(recent), recent_roi=roi(recent),
                y2022_n=len(y22), y2022_roi=roi(y22))


def add_candidate(rows, base, family, rule, keep, complexity=1):
    keep = keep.fillna(False)
    kept = base[keep].copy()
    removed = base[~keep].copy()
    s = summary(kept)
    rem = summary(removed)
    rows.append({
        "family": family, "rule": rule, "complexity": complexity, **s,
        "removed_n": rem["n"], "removed_wins": rem["wins"],
        "removed_losses": rem["losses"], "removed_roi": rem["roi"],
        "loss_capture_rate": rem["losses"] / max(1, int((~base.won).sum())),
        "winner_removal_rate": rem["wins"] / max(1, int(base.won.sum())),
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("ufc_striking_td_postmortem"))
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    print("[10%] Downloading Appwiza pre-fight feature snapshot", flush=True)
    t = fetch_zip_csv(NEWCAT_URL, "prefight_favorite_features.csv")
    t["event_date"] = pd.to_datetime(t["event_date"], errors="coerce").dt.normalize()
    for c in t.columns:
        if c not in {"event_date", "favorite", "opponent"}:
            t[c] = pd.to_numeric(t[c], errors="coerce")
    t["won"] = t["won"].fillna(0).astype(bool)
    t = t.dropna(subset=["event_date", "favorite", "opponent", "market_prob", "profit100"]).copy()

    print("[25%] Joining division context", flush=True)
    r = fetch_zip_csv(REACH_URL, "reach_market_sample.csv")
    r["event_date"] = pd.to_datetime(r["event_date"], errors="coerce").dt.normalize()
    r["market_fav_is_p1"] = as_bool(r["market_fav_is_p1"])
    r["favorite"] = np.where(r.market_fav_is_p1, r.player1, r.player2)
    r["opponent"] = np.where(r.market_fav_is_p1, r.player2, r.player1)
    r["_favn"] = pd.Series(r["favorite"]).map(norm)
    r["_oppn"] = pd.Series(r["opponent"]).map(norm)
    divcol = "division" if "division" in r.columns else ("weightclass" if "weightclass" in r.columns else None)
    dimcols = ["event_date", "_favn", "_oppn"] + ([divcol] if divcol else [])
    dim = r[dimcols].drop_duplicates(["event_date", "_favn", "_oppn"])
    t["_favn"] = t.favorite.map(norm)
    t["_oppn"] = t.opponent.map(norm)
    t = t.merge(dim, on=["event_date", "_favn", "_oppn"], how="left")
    if divcol:
        t["division_label"] = t[divcol].astype(str)
        t["is_womens"] = t.division_label.str.contains("Women", case=False, na=False)
    else:
        t["division_label"] = ""
        t["is_womens"] = False

    baseline_mask = (
        (t.market_prob >= .70) &
        (t.f_fights >= 4) & (t.o_fights >= 4) &
        ((t.f_sig_diff_pm - t.o_sig_diff_pm) >= 1.00) &
        ((t.f_td_def - t.o_td_def) >= .10)
    )
    b = t[baseline_mask].copy().sort_values(["event_date", "favorite", "opponent"]).reset_index(drop=True)
    b["sig_diff_gap"] = b.f_sig_diff_pm - b.o_sig_diff_pm
    b["td_def_gap"] = b.f_td_def - b.o_td_def
    b["pace_defense_support"] = (
        (b.market_prob >= .65) &
        ((b.f_sig_l_pm - b.o_sig_l_pm) >= .25) &
        ((b.f_sig_def - b.o_sig_def) >= .15)
    )
    b["striking_diff_support"] = (
        (b.market_prob >= .75) &
        (b.sig_diff_gap >= 1.00) &
        (b.f_sig_diff_pm >= .50)
    )
    b["second_striking_method"] = b.pace_defense_support | b.striking_diff_support

    bs = summary(b)
    print(f"[35%] Baseline {bs['n']} bets, {bs['wins']}-{bs['losses']}, "
          f"{bs['win_rate']*100:.1f}% win, {bs['roi']*100:+.2f}% ROI", flush=True)
    b.to_csv(out / "baseline_bets.csv", index=False)

    subrows = []
    def subgroup(label, mask):
        subrows.append({"subgroup": label, **summary(b[mask.fillna(False)])})
    subgroup("Men", ~b.is_womens)
    subgroup("Women", b.is_womens)
    subgroup("Favorite younger/same age", b.age_adv >= 0)
    subgroup("Favorite older", b.age_adv < 0)
    subgroup("Favorite >=2y older", b.age_adv <= -2)
    subgroup("Market 70-74.99%", (b.market_prob >= .70) & (b.market_prob < .75))
    subgroup("Market >=75%", b.market_prob >= .75)
    subgroup("Has second striking method", b.second_striking_method)
    subgroup("TD method only", ~b.second_striking_method)
    pd.DataFrame(subrows).to_csv(out / "subgroup_breakdown.csv", index=False)

    rows = []

    for floor in [.71, .72, .725, .73, .74, .75, .76, .77, .78, .80]:
        add_candidate(rows, b, "MARKET FLOOR", f"keep market_prob >= {floor:.3f}", b.market_prob >= floor)

    for min_age_adv in [-4, -3, -2, -1, 0, 1]:
        add_candidate(rows, b, "AGE CONFLICT", f"keep age_adv >= {min_age_adv:+.0f} years", b.age_adv >= min_age_adv)

    for lag in [0, .15, .25, .34, .50]:
        keep = b.f_last3.isna() | b.o_last3.isna() | (b.f_last3 >= b.o_last3 - lag)
        add_candidate(rows, b, "LAST3 FORM", f"veto if favorite last3 trails opponent by > {lag:.2f}", keep)
    for lag in [0, .10, .20, .25, .40]:
        keep = b.f_last5.isna() | b.o_last5.isna() | (b.f_last5 >= b.o_last5 - lag)
        add_candidate(rows, b, "LAST5 FORM", f"veto if favorite last5 trails opponent by > {lag:.2f}", keep)

    for extra in [90, 180, 270, 365, 540]:
        keep = b.f_layoff.isna() | b.o_layoff.isna() | ((b.f_layoff - b.o_layoff) <= extra)
        add_candidate(rows, b, "LAYOFF", f"veto if favorite layoff exceeds opponent by > {extra} days", keep)

    for opp_kd in [.25, .50, .75, 1.00, 1.25]:
        keep = b.o_kd15.isna() | (b.o_kd15 < opp_kd)
        add_candidate(rows, b, "OPPONENT POWER", f"veto opponent KD/15 >= {opp_kd:.2f}", keep)
    for abs_kd in [.25, .50, .75, 1.00]:
        keep = b.f_kd_abs15.isna() | (b.f_kd_abs15 < abs_kd)
        add_candidate(rows, b, "FAVORITE DURABILITY", f"veto favorite KD-absorbed/15 >= {abs_kd:.2f}", keep)
    for danger in [.25, .50, .75]:
        veto = (b.o_kd15 >= danger) & (b.f_kd_abs15 >= danger / 2)
        add_candidate(rows, b, "POWER INTERACTION",
                      f"veto opp KD/15 >= {danger:.2f} AND fav KD-abs/15 >= {danger/2:.3f}", ~veto)

    # Replace the naive TDD-vs-TDD interpretation with actual wrestling interaction.
    # Anti-wrestler: opponent really attempts takedowns. Offensive exploitation:
    # favorite really wrestles, has control, and opponent TDD is exploitable.
    for opp_att in [1.5, 2, 3, 4, 5]:
        for fav_att in [1.5, 2, 3, 4, 5]:
            offensive = (b.f_td_a15 >= fav_att) & (b.f_ctrl15 >= 1.0) & (b.o_td_def <= .75)
            anti = b.o_td_a15 >= opp_att
            add_candidate(rows, b, "MATCHUP-RELEVANT WRESTLING",
                          f"keep if opp TDatt/15 >= {opp_att:g} OR "
                          f"(fav TDatt/15 >= {fav_att:g} & fav ctrl/15 >=1 & opp TDD<=75%)",
                          anti | offensive, complexity=3)

    for tdd in [.60, .65, .70, .75, .80]:
        for ctrl in [.5, 1.0, 1.5, 2.0]:
            anti = b.o_td_a15 >= 2.0
            offensive = (b.f_td_a15 >= 2.0) & (b.f_ctrl15 >= ctrl) & (b.o_td_def <= tdd)
            add_candidate(rows, b, "WRESTLING RELEVANCE SENSITIVITY",
                          f"keep oppTDatt>=2 OR (favTDatt>=2 & ctrl>={ctrl:g} & oppTDD<={tdd:.2f})",
                          anti | offensive, complexity=3)

    add_candidate(rows, b, "CONSENSUS SUPPORT", "require Pace+Defense OR Striking Differential",
                  b.second_striking_method)
    for floor in [.72, .73, .74, .75]:
        keep = (b.market_prob >= floor) | b.second_striking_method
        add_candidate(rows, b, "LOW-CONFIDENCE CONSENSUS",
                      f"if market < {floor:.2f}, require a second striking method", keep, complexity=2)
    for older in [1, 2, 3, 4]:
        risky_old = b.age_adv <= -older
        keep = (~risky_old) | b.second_striking_method
        add_candidate(rows, b, "OLDER-FAVORITE CONSENSUS",
                      f"if favorite >= {older}y older, require a second striking method", keep, complexity=2)
    for older in [2, 3]:
        for floor in [.72, .73, .75]:
            risk = (b.age_adv <= -older) | (b.market_prob < floor)
            keep = (~risk) | b.second_striking_method
            add_candidate(rows, b, "AGE/PRICE CONSENSUS",
                          f"if fav >= {older}y older OR market < {floor:.2f}, require second method",
                          keep, complexity=3)

    res = pd.DataFrame(rows)
    base_roi = bs["roi"]
    base_wr = bs["win_rate"]
    res["roi_change_pp"] = (res.roi - base_roi) * 100
    res["win_change_pp"] = (res.win_rate - base_wr) * 100
    res["sample_retained"] = res.n / len(b)
    res["both_eras_positive"] = (res.pre_roi > 0) & (res.recent_roi > 0)
    res["recent_2022_positive"] = res.y2022_roi > 0
    res["promotion_screen"] = (
        (res.n >= max(30, int(.55 * len(b)))) &
        (res.pre_n >= 10) & (res.recent_n >= 10) &
        res.both_eras_positive &
        (res.roi >= base_roi) &
        (res.loss_capture_rate >= 2 * res.winner_removal_rate)
    )
    res = res.sort_values(["promotion_screen", "roi", "n"], ascending=[False, False, False])
    res.to_csv(out / "candidate_filters.csv", index=False)
    stable = res[res.promotion_screen].copy()
    stable.to_csv(out / "stable_candidates.csv", index=False)

    lines = [
        "UFC STRIKING + TD DEFENSE — POST-MORTEM / ROBUSTNESS",
        "=" * 104,
        "",
        "Baseline deployed rule:",
        "market>=70%, >=4 prior fights each, sig-strike differential gap>=1.0/min, TD-defense gap>=10pp",
        f"Baseline: {bs['n']} bets | {bs['wins']}-{bs['losses']} | {bs['win_rate']*100:.1f}% wins | ROI {bs['roi']*100:+.2f}%",
        f"Pre-2020 ROI {bs['pre_roi']*100:+.2f}% | 2020+ ROI {bs['recent_roi']*100:+.2f}% | 2022+ ROI {bs['y2022_roi']*100:+.2f}%",
        "",
        "Subgroups",
        "-" * 104,
    ]
    for x in subrows:
        lines.append(f"{x['subgroup']:<34} n={x['n']:>3} {x['wins']}-{x['losses']} "
                     f"win={x['win_rate']*100:5.1f}% ROI={x['roi']*100:+6.2f}%")
    lines += ["", "Candidates clearing conservative promotion screen", "-" * 104]
    if stable.empty:
        lines.append("None. Do not change the production rule from this post-mortem alone.")
    else:
        for _, x in stable.head(30).iterrows():
            lines.append(f"{x.family:<28} n={int(x.n):>3} {int(x.wins)}-{int(x.losses)} "
                         f"win={x.win_rate*100:5.1f}% ROI={x.roi*100:+6.2f}% "
                         f"(dROI {x.roi_change_pp:+5.2f}pp) | pre={x.pre_roi*100:+6.2f}% "
                         f"recent={x.recent_roi*100:+6.2f}% | removed {int(x.removed_wins)}W/{int(x.removed_losses)}L | {x.rule}")
    lines += [
        "", "Interpretation rules", "-" * 104,
        "* A filter is not promoted merely because it would exclude Fiorot-Grasso.",
        "* Prefer simple gates that improve ROI while retaining >=55% of the sample and staying positive in both eras.",
        "* Women/men splits are descriptive unless sample sizes are independently adequate.",
        "* Matchup-relevant wrestling means opponent TD volume OR favorite offensive wrestling into weak opponent TDD.",
        "* If no filter passes, retain the original rule and downgrade confidence rather than retrofitting.",
    ]
    (out / "report.txt").write_text("\n".join(lines))
    print("[100%] Complete. Results:", out, flush=True)


if __name__ == "__main__":
    main()
