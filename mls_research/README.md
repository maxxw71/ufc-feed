# MLS research layer

This directory is intentionally isolated from the NFL/UFC publishers and official bet feeds.

## What it adds

1. **Point-in-time player strength** from American Soccer Analysis (ASA): rolling xG+xA, xPass/touch share, Goals Added, minutes, and salary releases.
2. **Official MLS availability snapshots** from Player Status Reports, preserving OUT / QUESTIONABLE and the reason category.
3. **Suspension separation** so discipline is not mislabeled as injury.
4. **Missing-player value features**: weighted impact, minutes, g+, xG+xA, touches, salary share and unresolved-name count.
5. **Lineup continuity** using only earlier matches.

## Anti-leakage rules

- Every availability report carries an observed/publication timestamp.
- A match may only use a snapshot whose observation time is before the feature cutoff.
- An MLS report is treated as a complete team snapshot. If a club says "None", the snapshot is explicitly empty; old injuries never carry forward.
- ASA player metrics use a date-bounded trailing window ending on the requested snapshot date.
- MLSPA salary data is filtered to releases published on/before the snapshot date.
- Historical player values must be rebuilt by cutoff date (or cached snapshots), never joined from final-season totals.

## Example

```bash
python -m pip install -r mls_research/requirements.txt

python -m mls_research.collect_mls_status \
  --output data/mls/availability_snapshots.jsonl \
  --raw-dir data/mls/raw_status

python -m mls_research.build_player_strength \
  --as-of 2026-09-18 \
  --lookback-days 365 \
  --output data/mls/player_strength/2026-09-18.parquet
```

For historical status reports, provide an official-URL file via `--url-file`. Raw HTML and parsed JSONL are append-only so later page edits do not rewrite what the model knew at the time.

## Promotion rule

These features are research/shadow only. No MLS method should enter an official live tracker, public "Available selections", or email until it has passed the same stability and holdout checks used for the other Appwiza sports.
