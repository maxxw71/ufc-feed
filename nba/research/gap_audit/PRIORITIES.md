# NBA dataset gap / method-expansion audit — 2026-10-07

## Newly filled in this research pass

- **Historical officials / referee crews:** 9,519 games fetched successfully; 9,441 games with officials; 28,479 official-game rows. Point-in-time crew tendency features built.
- **Travel / time-zone context:** 43/43 unique historical venues geocoded and assigned a timezone. Team-game travel coverage ~98.7%. Features include prior-venue miles, 3/5/7/10-day cumulative miles, timezone shift, east/west shift and long-trip flags.
- **Starter vs bench role production:** prior 3/5/10 starter and bench minutes, points, assists, rebounds, plus-minus, shot volume, turnover volume, scoring share and starter usage proxy.
- **Quarter / scoring-shape context:** prior 3/5/10 Q1-Q4 margins, first/second-half margin, Q3 response, Q4 margin and scoreboard-delta clutch margin.
- **Dated roster transactions:** 7,726 Basketball-Reference transaction rows across 2018-19 through 2025-26, including 460 trades, 4,736 signings and 2,419 waivers. Same-day events are excluded from target-game pregame features.
- **Conference standings / seed pressure:** point-in-time conference rank, top-6/play-in state, gaps to 6th/10th seed, streak, last-10 and season stage.
- **Five-man lineup quality:** exact historical lineup stints already cover 98.5% of 2018-19 and 100% thereafter (535,735 stints). Added target confirmed-five prior shared minutes/PM48 and rolling starter/nonstarter/top-unit quality.

## Strong new leads

### NBA_TRAVEL_001 — highest priority
Away side; market probability >= 70.23%; opponent has traveled at least 576.9 more miles than selected team over the prior 7 days.
- Overall: 232-32 (87.9%), +7.25% ROI
- Holdout 2024-26: 67-9 (88.2%), +5.07%
- Worst historical price: +5.88%
- Same-price-gate baseline without travel filter: 80.1%, -2.24% ROI
- Unique vs existing live NBA families: 223 / 264 signals (~84.5%)
- Status: shadow / promotion candidate pending prospective policy decision.

### NBA_ROTSHAPE_001 — useful but less stable
Starter-heavy scoring profile + broader bench usage + recent clutch-margin advantage.
- Overall: 152-47 (76.4%), +7.52%
- Holdout: 47-10 (82.5%), +8.28%
- Worst-price ROI: +5.40%
- 182 / 199 signals unique vs current live families
- Caveat: multiple individual seasons have negative ROI, including 2025-26; keep shadow.

### NBA_STYLE_TOT_001 — totals shadow lead
High combined paint scoring share + lower combined foul rate + short total rest.
- Overall: 55.9%, +6.56% median ROI
- Holdout: 55.5%, +5.85%
- Worst-price ROI: +2.82%
- Status: shadow only; totals edge is materially thinner than TRAVEL_001.

### NBA_LQ_003 — strong exact-lineup-quality candidate
Confirmed starting-five prior shared PM48 gap >= 10.55, selected team has materially lower recent lineup-stint volatility, and market probability >= 59.68%.
- Overall: 310-69 (81.8%), +7.75% ROI
- Holdout 2024-26: 61-14 (81.3%), +4.81%
- Worst historical price: +5.67%
- Same-price-gate baseline without lineup filters: 73.1%, -3.89% ROI
- Unique vs existing live NBA families: 340 / 379 signals (~89.7%)
- Caveat: negative ROI in 2018-19 and 2024-25; keep shadow pending prospective evidence.

### NBA_STAND_002 — strong standings/market disagreement candidate
Opponent at-or-above #6-seed win-percentage pace, selected team has a materially worse recent streak, but market still prices selected side >= 59.68%.
- Overall: 304-85 (78.1%), +6.09% ROI
- Holdout: 81-21 (79.4%), +7.12%
- Worst historical price: +4.01%
- Same-price favorite baseline: 73.1%, -3.89% ROI
- Unique vs existing live NBA families: 365 / 389 signals (~93.8%)
- Remains positive after 20, 40 and 55 prior games; two individual seasons are slightly negative.
- Status: shadow / promotion candidate.

## Searches rejected at current standards

- Pure starter-core production concentration: 37,760 rules, 0 candidates.
- Flow/workload-only lane: 0 candidates.
- Broad high-probability search: 0 elite survivors.
- Value-mispricing lane: 0 survivors.
- Market microstructure / book disagreement: 5,026 rules, 0 candidates.
- Playing-style moneyline: 36,036 rules, 0 candidates.
- Referee-only lanes: 12,192 rules, 0 candidates.
- Altitude/elevation interactions: 11,136 rules, 0 candidates.
- Roster-transaction moneyline lane: 27,712 rules, 0 candidates.

## Data-quality rejections / leakage guards

- **Historical injury status from ESPN game-summary pages: REJECTED.** Old game pages return current 2026 injury blocks, creating future leakage. ESPN injuries remain prospective/current only.
- **PBP shot-distance / direct FGA semantics: REJECTED for method use.** shot_distance is 0% populated and PBP is_field_goal overcounts official FGA by ~24-27%. Do not create shot-location or shot-efficiency methods until a validated source/parser exists.
- **Exact coaching-change dates:** Basketball-Reference transaction pages provide many dated coach events but recent-season coach labeling needs a separate completeness validation before research use.

## Highest-priority remaining gaps

1. **True historical availability / injuries** from timestamped or archived reports, not rewritten current-state pages.
2. **Validated exact coaching changes for 2024-25 and 2025-26** to complete game-level coaching context.
3. **Historical salary / contract / roster-value features** (team salary, top-player salary concentration, value lost/added around trades). Basketball-Reference contract query parameters were tested and rejected because they always returned the current 2026-27 table; HoopsHype returned HTTP 402; ESPN HTML returned only anti-bot 202 responses. Continue only with a genuinely season-addressable source.
4. **Validated shot-location / shot-type data** from a source with official-attempt reconciliation.
5. **Advanced player impact / on-off** derived from exact lineup stints or a timestamp-safe historical source.
6. **Referee interactions rather than referee-only rules**, especially totals + foul/FTA style.
7. **Travel interactions** with workload, altitude, rest and lineup continuity after TRAVEL_001 is frozen.
8. **Roster transaction interactions** with player impact / salary value; transaction counts alone produced no method.

## Research policy

All target-game features must be knowable before tipoff. Current availability is never retroactively injected into historical backtests. Rules selected from historical discovery are frozen before prospective 2026-27 tracking.
