# UFC Age × Height × Reach × Wrestling vs. Striker — Research Audit

**Research only.** Run on October 10, 2026; excludes all fights dated October 10, 2026 or later. No U-methods were added, removed, or retuned. Outcomes and moneyline prices are historical reconstructions, **not prospective live fills**.

## Input and evaluation design
- Source: `/home/anestishkurti92/ufc-predictor-v1/feature_expansion/prefight_favorite_features_v6.csv`
- 3,349 raw favorite-side rows; 3,279 eligible after event-date, outcome, quote and consistency filters.
- Discovery before 2024-01-01: 2,929 records; later holdout: 350.
- Favorite needs >=2 prior UFC bouts and opponent >=2 for primary matchup cohorts.
- Younger means favorite >=4 years younger, active wrestler >=2.5 takedown attempts and >=0.5 control minutes per 15. Striking-oriented opponent proxy: opponent <=3 takedown attempts and >=2.5 significant strikes landed per minute.
- Physical mismatches assessed at any deficit, >=2-inch height / >=4-inch reach, and more extreme thresholds. These strata are observational; they are not independent calibrated model predictions.

## Headline results (historical per-unit ROI)

| Cohort | N | W–L | Win rate | ROI | 2010–23 ROI (n) | 2024–26 ROI (n) |
|---|---:|---:|---:|---:|---:|---:|
| All eligible price favorites | 3,279 | 2143–1136 | 65.36% | −3.37% | −4.02% (2929) | +2.04% (350) |
| 4+ years younger favorites | 809 | 594–215 | 73.42% | +6.27% | +6.63% (686) | +4.30% (123) |
| Younger active wrestlers | 500 | 372–128 | 74.40% | +6.73% | +6.52% (421) | +7.81% (79) |
| Younger wrestlers versus low-TD striking opponents | 180 | 146–34 | 81.11% | +16.77% | +14.25% (141) | +25.88% (39) |
| Same, younger wrestler shorter AND shorter reach | 54 | 48–6 | 88.89% | +30.35% | +29.65% (41) | +32.58% (13) |
| Same, >=2in shorter AND >=4in reach deficit | 7 | 6–1 | 85.71% | +31.63% | +28.35% (6) | +51.28% (1) |
| Same, >=3in shorter AND >=6in reach deficit | 2 | 2–0 | 100% | +43.82% | +43.82% (2) | no matches |

For the **54-fight** cohort, conventional resampling within this *selected* retrospective subgroup estimates an ROI interval of **+16.26% to +43.01%**. This does not include uncertainty from choosing the subgroup after noticing Camilo–Herbert, selection bias, historical pricing availability, or model deployment.

For a strict **Camilo-like** definition (age advantage 6+ years, shorter 2+ inches, reach disadvantage 6+, wrestler >=4 takedown attempts per 15 against striking-oriented opponent), only **two** historical matches qualified:
- Cory McKenna over Miranda Granger, August 6, 2022.
- Jared Gordon over Leonardo Santos, August 20, 2022.

Both won; **zero** examples were present in the 2024–26 holdout. This is too small to infer Camilo's individual win probability or promote a method.

## Opponent takedown defense: surprisingly not a simple veto

In the 54-fight younger/shorter wrestler cohort:
- Opponent prior TD defense below 60%: **15/20**, +6.22% ROI. Two additional fights lacked TD-defense data.
- Opponent prior TD defense 60–79%: **20/20**, +45.37% ROI.
- Opponent prior TD defense 80%+: **12/12**, +55.24% ROI.

The simple theory *higher opponent TD defense necessarily hurts the shorter wrestler* is **not supported by this historical slice**. This association is counterintuitive and not controlled for opponent selection, striking danger, market expectations, sample size, TD-defense attempt denominators, or correlated features. Do not invert it into a live strategy.

## Six actual historical losses and how the fight finished

| Younger wrestling favorite lost | Opponent | Fight date | Actual finish |
|---|---|---|---|
| Kenny Robertson | Sean Pierson | 2013-06-15 | Majority decision (R3) |
| Devin Clark | Jan Błachowicz | 2017-10-21 | Submission (R2) |
| Andrew Sanchez | Ryan Janes | 2017-12-01 | KO/TKO (R3) |
| Mackenzie Dern | Marina Rodriguez | 2021-10-09 | Unanimous decision (R5) |
| Jimmy Crute | Jamahal Hill | 2021-12-04 | KO/TKO (R1) |
| Ricardo Ramos | Julian Erosa | 2024-03-23 | Submission (R1) |

**Interpretation:** two submissions, two KO/TKOs, two decisions. The failure modes were not exclusively takedown defense or reach. Striking risk, submission danger during grappling, and distance/control failure deserve separate study.

Median opponent pre-fight significant strikes landed per minute:
- Against losing younger wrestlers: **5.04**.
- Against winning younger wrestlers: **3.86**.

Post-hoc descriptive stratification by opponent striking rate among the 54 fights:
- Under 3.5/min: **15–1**, +37.40% ROI.
- 3.5–4.49/min: **16–1**, +34.69%.
- 4.5–5.49/min: **11–2**, +25.71%.
- 5.5+/min: **6–2**, +14.61%.

This is an interesting pattern but was identified **after inspecting the losses**. It must be validated on separate future fights before being a veto.

## Candidate screen and decision

**0 of 36** fixed candidate filters met all predeclared training/era/holdout sample requirements and positive early/late discovery ROI. **No production method and no live filter promotion.**

The initial next research priorities are: (1) replace simple TD defense with pre-fight defended-attempt denominator and opponent-adjusted successful takedown entries; (2) explicitly model KO and submission traps from verified regional + UFC histories; (3) assess distance-striking volume and quality against young wrestlers with reach deficits; (4) prospectively shadow-log all candidate matchups and their source coverage before outcome.

## Provenance

- [Chronological interaction study code](https://github.com/maxxw71/ufc-feed/blob/main/scripts/ufc_age_reach_wrestling_interactions.py)
- [Successful original study](https://github.com/maxxw71/ufc-feed/actions/runs/38098879578)
- [Case-level follow-up and bootstrap checks](https://github.com/maxxw71/ufc-feed/actions/runs/38098927528)
- [Loss finishes and striking/TDD bands](https://github.com/maxxw71/ufc-feed/actions/runs/38098973669)
