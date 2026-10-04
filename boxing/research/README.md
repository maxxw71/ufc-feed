# Chronological boxing research

Run the database-backed research on the sports/Appwiza server beside `boxing.sqlite3`. GitHub CI only runs the static leakage/audit/market-policy tests because the private research database is not stored in the repository.

## Baseline chronological build

Run:

```bash
python -m unittest test_chronological_master test_sample_audit test_market_consensus
python build_chronological_master.py
python scan_chronological_master.py
python audit_chronological_sample.py
python age_rule_diagnostics.py
```

Builds retain a consistent `source.sqlite3` snapshot and chronological JSONL master under `research_runs`; these private datasets are not included here.

## Phase 2 interaction research

After the chronological master and audit exist, run:

```bash
python phase2_interaction_scan.py
```

Phase 2 removes the original arbitrary single-book selection policy. `market_consensus.py` requires at least two clean two-sided bookmaker observations for a bout, identifies the favorite using the median no-vig probability across books, uses the median displayed decimal price as the primary exploratory ROI price, and records best/worst displayed prices separately for sensitivity.

The fixed Phase-2 universe tests age × Elo, age × form, age × experience, age × power, Elo × experience, Elo × prior schedule strength, Elo × form, Elo × activity, and power/chin interactions. Rules are selected in nested year-by-year fashion using only earlier-year evidence; cross-family consensus is also reported.

Outputs are written into the current `research_runs/<timestamp>/` directory as `phase2_interaction_research.json`, `phase2_target_candidates.json`, and `phase2_report.txt`.

## Interpretation limits

All archived prices remain unverified in quote timing and settlement. Reproducing a displayed price from cached HTML is not independent evidence that the quote was available pre-fight. Therefore all ROI remains exploratory arithmetic and no rule is promoted to live betting behavior from these files alone.

Elo is our own reciprocal-graph rating, initialized at 1500 with K32 and date-batched updates. Current biography DOB is treated as stable identity data for age; current height/reach/stance are excluded from historical qualification. Missing dated punch features remain missing until CompuBox identity/date coverage passes separate validation. Internal record-total checks are not independent full-career certification.


## Two-lane research architecture (effective 2026-10-04)

Boxing research is now intentionally split into two independent lanes so sparse punch-stat history can no longer block the broader research program.

### Lane A — BROAD

Purpose: large-sample method discovery and betting-probability research using fields that already have strong historical coverage.

Primary inputs:
- chronological career record and opponent graph
- age/DOB
- height/reach/stance when verified
- recent form and activity/layoff
- KO/TKO and decision history
- reciprocal opponent strength / Elo-style context
- official WBC/WBA/WBO/IBF rankings and title context
- division/weight movement and other dated career context
- independently verified historical prices where available
- prospective verified prices going forward

The existing Phase 2/3/4 and full method-discovery sweep belong to the BROAD lane. Punch data must not be required for a BROAD observation. BROAD candidates remain research/shadow only until their own validation gates are met.

### Lane B — DEEP_STATS

Purpose: smaller-sample, high-information research using actual pre-fight punch-performance history.

Required inputs should come from verified fight-level or round-level punch evidence and may include:
- total punches landed/thrown
- jab landed/thrown
- power landed/thrown
- accuracy
- opponent landed/thrown
- round-by-round pace
- early-vs-late output and accuracy decay
- offense/defense and opponent-adjusted punch efficiency
- last-1/3/5 punch-history windows
- volatility and damage/pressure proxies when source definitions are explicit

DEEP_STATS must remain separate from BROAD until the relevant fighter snapshot has sufficient pre-fight punch depth. A fight can participate in BROAD while being ineligible for DEEP_STATS. As punch history expands, fighters/fights automatically become eligible for deeper research without changing the BROAD record.

### Source policy

BROAD and DEEP_STATS share the same identity graph and strict point-in-time policy, but source roles differ:

- BoxRec/FightFax-style record sources: identity, career, schedule, result, rating/context candidates; not assumed to provide CompuBox-equivalent punch history.
- CompuBox: preferred direct punch-stat source when a report can be tied to the exact bout.
- Publisher reproductions of CompuBox: accepted only under existing provenance/identity rules.
- Third-party structured punch APIs/computer-vision sources: research candidates until sampled against independently published CompuBox or another authoritative source.
- No source is promoted into strict DEEP_STATS merely because it has a convenient API.

The promotion rule is unchanged: retrospective discovery can create shadow candidates, never automatic live methods.
