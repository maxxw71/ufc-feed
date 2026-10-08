# NBA loss-first research standard

Effective 2026-10-08. Applies to every new NBA candidate, all 10 live methods, and all future NBA method-family searches.

## Goal
Discover *pregame, repeatable* situations where an otherwise qualifying method tends to lose money, not simply exclude historical losing games. Seek improvement in realized ROI, execution prices, risk, independent opportunities, and net unit return. A decline in historical win count is not automatically acceptable, and a rise in ROI with fewer total profit units must be disclosed.

## Default review for every method
1. Freeze the parent selection rule, exact full-precision thresholds, timestamps, bookmaker quote and the entire parent game/side/market selection set.
2. Produce a **complete loss ledger**: losing game, selected team/market/line, price, game date, season, opponent, pre-tip feature snapshot and later settlement. Record pushes and late price/missing-input exclusions separately.
3. Compare winning vs losing selections across **market probability/odds, pre-tip injury-weighted absences, starter and rotation continuity, starter minutes/PM, recent form, opponent strength, rest, travel, playing style, officiating and transaction/coaching change**. Tag missing data as unknown, never as zero/healthy.
4. Show results by season, phase, odds bucket, favorite/underdog, venue, rest/travel burden and individual feature bucket. Look specifically for money-losing regimes and why the parent selection remains profitable outside those regimes.
5. Build *hypothetical* filters exclusively on a reserved early training window. Never choose, widen, narrow or rank thresholds with the validation or holdout samples. Predetermined economically interpretable zero and market-probability thresholds may be used for diagnostics, but not promoted after observing later outcomes.
6. For each filter, report **kept bets, eliminated wins, avoided losses, pushes, win rate, ROI at median and worst executable price, total unit profit change, absolute drawdown and sample reduction** separately. Evaluate true incremental value against the original parent—not a filtered winner-only benchmark. Check overlap and correlation with existing live methods.
7. Run untouched confirmation and validation; check every holdout season independently; stress nearby cutoff values, leave-one-season-out samples and adverse bookmaker prices. Reject narrow, unstable or small-sample filters even if the combined holdout ROI is positive.
8. Keep the original method frozen and report every experimental filter as research/shadow-only until a separate independent prospective audit justifies a promotion. No retuning on 2026-27 outcomes and no retroactive signal entries.

## Current chronological splits
- H5 loss filters: **2018-19 to 2020-21 discovery**, 2021-22 to 2022-23 confirmation, 2023-24 validation, 2024-25 to 2025-26 untouched holdout.
- Official injury sensitivity uses a separate chronological track because 2018-21 historical reports are not yet available: 2021-22 discovery, 2022-23 confirmation, 2023-24 validation and 2024-26 holdout only after full archived report coverage. Missing-report games are held out, not counted as fully healthy.
- Scripts: `nba/research/audit_live_arsenal_losses.py` and `nba/research/audit_loss_descendants.py` (original live parents); `nba/research/forensic_h5_losses.py` and `nba/research/hunt_v5/h5_loss_forensics.json` (H5 candidate family).

## Initial findings (keep hypotheses distinct from decisions)
- **H5_ML_002:** 26 historical losses across 203 qualifying bets. No discovery-trained additional filter passed the initial loss-aware gate. Removing all >=80%-implied-probability bets has interesting later economics but sacrifices many winning signals and was not a selected discovery-only rule. Do not promote based on retrospectively viewed holdout.
- **H5_ATS_001:** 185 historical losses in 428 bets, with 2024-25 weak. Eight train-qualified exploratory filters failed to improve ROI in *all* later phases. Travel disadvantage is a plausible failure mechanism in some seasons but was not robust across the 2023-24 validation. Do not force it into the live rule.
- **H5_ML_001:** A rest filter improved ROI in each subsequent phase, but removed profitable winners and reduced flat-stake total profit in the holdout. Flag as an ROI-efficiency lead requiring prospective evaluation rather than automatic live promotion.
- Additional injury rows recovered for 2023-24 and 2024-25; recheck all injury hypotheses as 2025-26 official text is repaired. No claim of causal injury effects from winner/loser correlations.

No automated ChatGPT research-watch messages. Run/inspect GitHub research only as part of the Appwiza project workflow; user-facing updates only on request.
