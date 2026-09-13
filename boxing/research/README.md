# Chronological boxing research

Run on the sports server, not on the Mac. Install these modules into the existing boxing-research directory beside boxing.sqlite3. Dependencies: existing Python environment with BeautifulSoup.

Run `python -m unittest test_chronological_master test_sample_audit`, then `python build_chronological_master.py`, `python scan_chronological_master.py`, and `python audit_chronological_sample.py`. Builds retain a consistent source.sqlite3 snapshot and JSONL master under research_runs; these private datasets are not included here.

All prices remain unverified in quote timing and settlement. Display matching is not independent price verification. No validated ROI, promotion, email or live betting behavior is implemented. The fixed 45-rule grid uses chronological inner validation and outer yearly evaluation; prior research has already examined these years.

Elo is our own reciprocal-graph rating, initialized at 1500 with K32 and date-batched updates. Missing historical physical and punch features remain missing. Internal record-total checks are not independent full-career certification.
