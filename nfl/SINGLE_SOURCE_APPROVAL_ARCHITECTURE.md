# NFL single-source approved-bet architecture

Production invariant:

`research candidates -> production_match_guard.filter_records() -> approved_records -> email + Appwiza + bet tracker`

No downstream consumer is allowed to read the unguarded candidate list.

## Fail-closed behavior

A record is rejected before publication if matchup identity cannot be proven against the authoritative schedule and ESPN bookmaker payload. The guard verifies game ID, season/week, home/away teams, selected side/team, ESPN event identity, kickoff, bookmaker, odds timestamp/source, raw sportsbook team IDs, opponent ID, and selected price.

Rejected records must produce all three outcomes:

- no email selection
- no Appwiza NFL card
- no tracked/ledger bet

The owner-run installer `nfl/install_production_match_guard.sh` now validates this single-source invariant before restarting the live service. It aborts rather than allowing a partial deployment.

## Current production deployment caveat

The GitHub Appwiza runner does not have write permission to `/home/anestishkurti92/nfl-predictor-v1/research_v2`, so the live scanner cannot be changed by the runner. The installer must be executed by the owner account `anestishkurti92` (or an equivalent privileged deployment path). Until that succeeds and the live page is regenerated, repository protection is not equivalent to live protection.
