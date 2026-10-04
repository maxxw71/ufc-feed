# Boxing DEEP_STATS source audit

Updated: 2026-10-04

Purpose: identify a lawful, structured, repeatable source of fight-level and round-level boxing performance data that can support the DEEP_STATS lane. This file does not authorize ingestion. Every source must pass provenance, identity, point-in-time, licensing/terms, and cross-source accuracy checks before entering strict research.

## Spend policy

**No paid boxing-data subscription is approved from research claims alone.** Before any paid tier is considered, the source must prove value at zero cost or via a vendor-provided sample.

Required proof before spend:
1. confirm that round-level punch fields are actually returned for multiple historical fights, not just demo fights;
2. measure historical depth across a stratified sample of years and promotions;
3. cross-check sampled totals/rounds against independently published CompuBox or another authoritative source;
4. measure overlap against our 719 strict-profile fighters and 443 validated-price bouts;
5. estimate how many pre-fight snapshots would reach >=1 and >=3 prior punch fights per fighter;
6. document source/licensing terms suitable for Appwiza research use.

If these checks cannot be completed on the free tier, public demos, or a vendor-provided evaluation sample, do not pay merely to discover whether the product is useful.

## Priority 1 — CompuBox official data feed

Status: **best schema match / licensing required for Appwiza use**

Public CompuBox pages currently expose structured round-stat reports with total punches, jabs, power punches, landed/thrown, percentages, body-landed totals and opponent statistics. This is the closest direct analogue to the performance layer we want.

Important restriction: CompuBox's public site states that displayed data is for personal/non-commercial use and that commercial/production/sportsbook/fantasy data feeds require approval. Do not bulk-harvest the public site into Appwiza strict research without appropriate permission.

Action:
1. pursue approved data-feed/licensing access;
2. request historical depth/count and bulk/export options;
3. if access is obtained, map exact bout identity into the existing boxing graph;
4. independently cross-check a sample against published CompuBox tables already in our warehouse;
5. ingest only observations whose bout date and source availability are known.

## Priority 2 — Boxing Data API (boxing-data.com)

Status: **high-priority structured API lead / historical depth must be measured**

The service advertises fight endpoints with round-by-round total, jab and power landed/thrown statistics, fighter profiles, schedules, results and historical fights. A free Basic tier currently advertises 100 requests/month with no credit card required; use that or a vendor-provided sample for evaluation. The paid unlimited-history tier is not approved unless the zero-cost proof gate above is satisfied.

Why it matters:
- API-native JSON rather than article/PDF parsing;
- fields align closely with our DEEP_STATS schema;
- designed for application/research use rather than an HTML-only archive.

Before strict use:
1. obtain API access under terms appropriate for Appwiza;
2. enumerate actual historical fight coverage rather than relying on marketing claims;
3. measure how many of our 719 strict-profile fighters and 443 validated-price bouts are covered;
4. compare at least a stratified sample of fight/round totals against independently published CompuBox;
5. reject transformed/estimated statistics if source definitions cannot be reconciled.

## Priority 3 — BoxRec API / BoxRec

Status: **BROAD-lane priority; not currently a CompuBox replacement**

BoxRec currently exposes an API documentation endpoint that requires a key. BoxRec remains extremely valuable for fighter identity, bout history, schedules, results, ratings, scorecards and career context.

Use:
- strengthen identity resolution and complete career chronology;
- cross-check dates/opponents/results;
- potentially improve schedules, ratings and event linkage.

Do not assume:
- that BoxRec provides systematic round-by-round landed/thrown/jab/power statistics;
- that a fight-wiki page containing occasional punch totals constitutes uniform historical punch coverage.

If API credentials become available, audit available endpoints and fields before integration.

## Priority 4 — FightFax / Pro Boxing Records

Status: **BROAD-lane institutional verification lead**

FightFax states it maintains commission-verified professional records and offers paid data licensing/API access to professional users. This could materially improve result/identity/official-record verification and perhaps resolve career gaps.

It is not currently identified as a round-by-round punch-stat source, so its primary role is BROAD integrity rather than DEEP_STATS.

## Priority 5 — Jabbr DeepStrike

Status: **high-information independent stats source / archive-access path unclear**

DeepStrike uses computer vision to produce landed/thrown, high-impact, pressure, aggression, combinations and other metrics. Jabbr reports analysis of thousands of professional rounds and has supplied live broadcast statistics.

Potential value:
- independent validation against CompuBox;
- richer pressure/impact/combination features unavailable from conventional punch counts;
- future DEEP_STATS enrichment.

Open question:
- no public bulk historical API/archive suitable for our warehouse has yet been verified.

Treat as partnership/API lead, not a source to scrape.

## Lower-priority commercial feeds

Other vendors advertise boxing fighter/fight/round/punch/odds feeds. These may be useful, but marketing claims must not be treated as verified coverage. Any such provider must first pass a sample-based source audit against known fights and documented field definitions.

## Integration order

1. Keep BROAD research expanding immediately.
2. Seek approved CompuBox feed access.
3. Audit Boxing Data API historical depth with a small licensed/API sample.
4. Audit BoxRec API for BROAD enrichment.
5. Explore FightFax for official career verification.
6. Explore Jabbr/DeepStrike for independent or richer performance statistics.
7. Only then consider lower-priority commercial feeds.

## DEEP_STATS admission rule

A source is not admitted to strict DEEP_STATS because it is convenient or large. It must satisfy:
- exact fighter/bout identity;
- known bout date and availability date;
- reproducible field definitions;
- no post-fight leakage into prefight snapshots;
- licensing/terms compatible with intended use;
- spot-check accuracy against an independent authoritative/published source.

