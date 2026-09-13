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
