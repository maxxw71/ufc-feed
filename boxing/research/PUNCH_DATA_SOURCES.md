# Modern boxing round/punch data sources

This file tracks sources suitable for expanding the chronological boxing research database. The goal is to preserve only source-linked observations that can later be converted into leakage-safe pre-fight features.

## 1. CompuBox public round reports — primary observed source

Useful fields seen in public round reports:

- total punches landed / thrown by round
- jab landed / thrown by round
- power punches landed / thrown by round
- connect percentages
- fight-total body punches landed in many reports
- opponent connect rates can be derived from the other fighter's rows
- round-to-round output, accuracy, net landed, power share and late-fight trends can be derived

Derived defensive fields must be labelled carefully. `100 - opponent connect %` is an **avoidance proxy**, not a literal count of slips, blocks, parries or evasions.

CompuBox states on its public site that displayed data is for personal use only and commercial use requires approval. Therefore raw CompuBox data and derived row-level datasets are for private research and should not be republished through Appwiza without permission.

Collector: `boxing/collectors/punch_collect.py`

Chronological feature builder: `boxing/research/build_punch_profiles.py`

## 2. Boxing Data API — structured secondary source

The provider advertises round-by-round fight stats with, for each fighter and round:

- total punches landed / thrown / accuracy
- jabs landed / thrown / accuracy
- power punches landed / thrown / accuracy

It also exposes fighter profiles, historical fights, results, scores, divisions, titles and events through RapidAPI. This is attractive for identity coverage and for filling fights absent from the public CompuBox crawl, but a paid plan/API key is required for useful historical scale and source lineage/redistribution rights must be reviewed before production use.

A future integration should ingest this as a **separate source table**, never overwrite CompuBox rows, and compare duplicate fights for agreement.

## 3. ESPN/major-media CompuBox tables — cross-check/source recovery

ESPN and other fight-preview/recap pages often reproduce CompuBox tables, including round-by-round total/jab/power landed and thrown. These are useful for:

- recovering exact reports that are no longer linked from the CompuBox home page
- cross-checking parsed values
- finding dates/fighter spellings

They are not statistically independent from CompuBox when explicitly credited to CompuBox.

## 4. Official commission scorecards — scoring context, not punch counts

Commission/judge scorecards can provide true judge-by-round scoring when available. These should be stored separately from punch counts because a 10-9 round is not equivalent to 'fighter landed more punches'. They are useful for:

- judge agreement/disagreement
- score margin by round
- close-fight/decision-risk features
- comparing punch-count edge with actual judging

## 5. Broadcaster/promotion fight pages

Some broadcaster and promoter pages publish fight statistics or quote CompuBox totals. These are useful as source-linked supplements where the original report is missing. Each observation should retain the page URL, fetch timestamp and whether the numbers are original or attributed to another provider.

## 6. Video/computer-vision estimates — experimental only

Open-source projects exist that attempt punch detection and landed/missed classification from fight video. This can potentially estimate:

- punch attempts
- landed/missed punches
- event timestamps
- round-level activity

This should be treated as a separate experimental source because accuracy depends heavily on camera angle, occlusion and model training. Only use legally available/owned footage, and never mix model-estimated counts with human-coded CompuBox counts without a source flag and validation study.

## Recommended schema/feature policy

For every punch observation retain:

- source name + exact URL
- event/fight date
- fighter and opponent source labels
- round number
- total/jab/power landed and thrown
- body landed where available
- raw capture hash and fetch timestamp
- source-quality/verification state

Derived pre-fight features should include prior-only rolling 1/3/5-fight and career-weighted values for:

- landed/round and thrown/round
- total/jab/power accuracy
- opponent landed/round
- total/jab/power avoidance proxy
- net landed/round
- power-punch net differential
- punch-count round-edge rate (not judge score)
- output and net-landed slope across rounds
- late-vs-early fight delta
- body-punch share

Same-date/current-fight observations must never enter that fight's own pre-fight feature row.
