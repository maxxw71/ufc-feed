# Appwiza NBA dataset and method research

## Current operating state (2026-10-08)
- Historical regular-season research window: **2018-19 through 2025-26**, including shortened COVID seasons. Older seasons remain enrichment targets where verified sources exist.
- **2026-27** prospective capture/scanner: regular season only, no preseason picks.
- NBA production scanner is independent of GitHub Actions and owns `/sports/nba/current.json`; the systemd timer operates every 10 minutes. Deployment of a repository feed does not replace the production scanner state.
- **10 frozen live methods:** NBA_ML001_DEF_TS, NBA_PHYSCOACH_001, NBA_INTERACT_003, NBA_TRAVEL_001, NBA_LQ_003, NBA_STAND_002, NBA_ROTSHAPE_001, NBA_STYLE_TOT_001, NBA_H3_OVER_002, NBA_STAND_002_STARTPM.
- The final two methods were promoted with explicit user authorization on 2026-10-08. The original eight rules remain unchanged. STAND_002_STARTPM is a strict subset of STAND_002; treat overlapping same-side wagers as concentrated exposure.
- Frozen prospective first-selection and immutable evidence: `nba/live/live_integrity.py` and server-side `nba/forward` snapshots.

## Historical injury coverage
- Official pre-tip report PDFs for 2021-22 and 2022-23 have been preserved and converted to point-in-time player/team features.
- The 2023-24 / 2024-25 / 2025-26 independent archive expansion is configured in `.github/workflows/nba-injury-2023-26-backfill.yml`. Verify produced coverage summaries before claiming completion. The 2020-21 recovery is a separate workflow.
- Source texts, exact chosen report URLs and failures are stored under `nba/availability/historical_by_season/<season>`. Missing text is not interpreted as a healthy player.
- `nba/build_historical_injury_features.py` extracts status and prior player exposure; verify identity and pre-tip time before using any row in method research.

## Research lanes
- Canonical historical live-method bankroll simulation: `nba/research/bankroll/live_arsenal_50k_1pct_compound.json` (2018-19 to 2025-26).
- Live and shadow method registry: `nba/live/arsenal.json`, `nba/research/shadow_arsenal_2026_27.json`.
- Current cross-domain totals audit: `nba/research/hunt_v3/`.
- New moneyline and ATS interaction search: `nba/research/discover_method_hunt_v5.py` and `nba/research/hunt_v5/`. Results are discovery/research-only until separate review, not automatically live.
- Injury/rotation search: `nba/research/discover_injury_rotation.py` and `nba/research/injury_rotation/`. Later-season official injury rows must exist before any true 2024-26 holdout claim.
- Other research families: lineup, starter, rotation, travel, coaching, market, officials, transaction value, standings, Elo/SOS and shot profile.

## Guardrails
Use strictly point-in-time data and archived executable prices for backtests; prefer independently verified snapshots. Always distinguish discovery from holdout and forward results. Preserve historical thresholds and avoid outcome leakage. When a method overlaps another, report both incremental exposure and independent signal contribution. Do not automatically promote weak or duplicate candidates. No automated ChatGPT research notifications: updates only when requested.
