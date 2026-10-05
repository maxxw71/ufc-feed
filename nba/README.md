# Appwiza NBA Research Dataset

Status: bootstrap in progress.

## Objective
Build a research-grade, point-in-time NBA dataset for Appwiza before method discovery or live promotion.

## Initial coverage
- 2025-26: Regular Season
- 2026-27: Pre Season + Regular Season as games become available
- Expansion target: season-by-season backfill through 2015-16 after integrity checks

## Core tables
1. games
2. team_boxscores
3. player_boxscores
4. playbyplay
5. provenance

The raw authoritative feeds are NBA public data. Normalized outputs are stored as gzip CSV files to keep repository size manageable.

## Source policy
Primary basketball sources:
- NBA LeagueGameFinder for historical game IDs and schedule/result rows
- NBA liveData boxscore feed for team/player game statistics
- NBA liveData play-by-play feed for event-level data

Market data is a separate lane. It must never be used to alter basketball facts. Historical odds snapshots will be added independently and joined only by resolved event identity and pre-tip timestamp.

## Point-in-time rule
Every future model/research feature must be computable using only information available before that game's tipoff. No final-season averages, later injury knowledge, later roster states, or future game outcomes are allowed in features.

## Preseason rule
Preseason is ingested for pipeline validation and contextual research, but is tagged separately and must not be mixed into regular-season method training unless a method explicitly studies preseason.

## Promotion rule
NBA methods follow the same research discipline as the other Appwiza sports lanes:
historical discovery -> holdout validation -> prospective shadow tracking -> live consideration.

No NBA method is live merely because this dataset exists.
