# UFC: opponent-adjusted takedown defense × striking × submission-entry risk

**Research-only historical study — 2026-10-10.** No changes to live U1–U12, betting recommendations, bet ledger, or scanner approval. The full reproducible analysis is in [the research script](https://github.com/maxxw71/ufc-feed/blob/main/scripts/ufc_opponent_adjusted_interaction_research.py), with stdout evidence in [successful workflow run](https://github.com/maxxw71/ufc-feed/actions/runs/38099283506).

## Reconstructed prior-fight data

- **3,279/3,279** usable historical favorite-side price/outcome rows matched to UFCStats prior-bout state; duplicate matches **0**. Full v6 base has more rows, but fights on or after October 10, 2026 and invalid/non-reconcilable moneylines/outcomes are excluded.
- **Cohort reconciliation:** the earlier study had 180 qualifying wrestler-vs-striker matches. All **180** are also in the new 202-fight cohort; the new reconstruction adds **22**, with **zero** old-only cases. The differences arise from previous v6 base `f_fights/o_fights` sometimes undercounting a fighter's earlier UFCStats fights compared with our chronological raw-history reconstruction (e.g., Jeremy Stephens prior fights: 1 in base versus 10 in raw). This means **180 and 202 are NOT identical cohorts**, and the different ROI values should not be mistaken for the impact of a risk filter. Audit evidence: [cohort reconciliation](https://github.com/maxxw71/ufc-feed/actions/runs/38099455395).
- **3,761 regional fighter-fight records** available for optional submission-risk flags from the regional archive (543 fighters). Not all are independently checked. Missing regional history remains **unknown**, not "zero threat."
- Opponent-adjusted takedown defense: for each fighter's *past* defended bout, compare takedowns conceded against the number expected from the attacker's **pre-that-bout** smoothed takedown conversion rate. Aggregate before the next fight, using a 12-attempt smoothing denominator. The comparison excludes contemporaneous and future fights. This is an experimental opponent-quality proxy rather than a validated individual probability.
- Raw prior defended-takedown attempts are separately counted. A historical 80% takedown-defense percentage based on 5 attempts is not treated as equivalent to one based on 40 attempts.
- Discovery before 2024; report 2024 separately and 2025–Oct 9, 2026 separately. **Neither is now a genuinely untouched holdout**, because prior research has already examined those years. Historical prices are retrospective reconstructions, not prospectively frozen execution odds.

## Main risk-interaction comparisons

Younger = favorite at least 4 years younger; wrestler = at least 2.5 historical takedown attempts/15 minutes and 0.5 control min/15; striking opponent proxy = at least 2.5 significant strikes landed/min and at most 3 takedown attempts/15. Both sides need at least 2 historical UFCStats bouts.

| Historical group | N | W–L | Win rate | ROI |
|---|---:|---:|---:|---:|
| Younger wrestlers vs. striking-oriented opponent | 202 | 161–41 | 79.70% | +14.07% |
| Exclude high opponent striking volume **and** positive strike differential | 172 | 139–33 | 80.81% | +15.35% |
| Exclude favorite submission vulnerability colliding with opponent submission history | 171 | 137–34 | 80.12% | +14.06% |
| Exclude poor favorite TD conversion paired with reliably strong opponent TD defense | 191 | 152–39 | 79.58% | +12.75% |
| Exclude **any** of the three flags | 136 | 111–25 | 81.62% | +14.59% |
| Shorter wrestler with shorter reach: unchanged | 57 | 51–6 | 89.47% | +29.87% |
| Same shorter-wrestler subgroup, exclude any flags | 36 | 32–4 | 88.89% | +26.16% |

**Important:** These hypotheses are NOT improvements worthy of production. The 202-fight combined veto removes **50 winners and 16 losers**, dropping total historical net profit from **+28.41u to +19.84u** at flat 1u per wager. In the 57-fight shorter-wrestler subset, it removes **19 winners and 2 losers**, also reducing ROI. A small apparent win-rate increase in one cohort cannot establish generality.

### Denominator-adjusted takedown findings

- The broader 202-fight group has **168 fights** whose opponent had faced 12+ UFCStats takedown attempts before the bout. That documented-coverage subset is **135–33**, **+15.78% ROI**.
- Only **127/202** have sufficient opponent-adjusted TD-defense history under the provisional requirement of 12+ defended attempts across at least two bouts against attackers with established prior takedown statistics.
- The shorter-wrestler group has **30/57** such opponent-adjusted cases: **28–2**, **+41.20% ROI**; coverage is missing for 27, so this is NOT a standalone approved trading method.
- Opponents with shrunk TD-defense >=68% and 12+ past attempts faced are associated with **18–0** outcomes for the shorter wrestler, +50.63% ROI, but **none** were observed in the later 2025–26 slice. It is especially inappropriate to turn this counterintuitive pattern into a blanket live selection or extrapolate 100%.
- Opponent defense at least 8 percentage points stronger than expected from prior opponent-quality-adjusted TD conversion was **15–0** within the short-wrestler subgroup, but also **zero** in the later 2025–26 slice. Insufficient prospective support.

### Combined submission, striking and wrestling dangers

Three research flags were computed from data available before each fight:
1. **Striking danger:** opponent landed >=5 significant strikes/min historically and significant striking differential >=0.5/min.
2. **Submission collision:** favorite prior UFCStats or regional submission loss and opponent UFCStats submission finish or multiple archive-documented regional submissions. Regional source incompleteness is retained.
3. **Defended takedown collision:** favorite at least 3 takedown attempts/15 but conversion <=30%, opposing defense with 12+ previous attempted takedowns faced and shrunk success >=68%.

The striker danger flag identifies lower-return *historical* fights in the broader group: flagged **22–8**, +6.70% ROI, versus unflagged **139–33**, +15.35%. However **2025–26** comparison is worse after removing such flags: baseline **16–2**, +24.73% versus surviving **13–2**, +18.47%. The submission filter likewise does not improve later ROI.

Risk flags should remain descriptive diagnostics until separately validated. Earlier examination of the same matchups means a chronological label alone is not truly independent.

## Recommended next research, not promotion

1. Compare the adjusted-defense proxy against equal-odds, equal-experience controls, and test sensitivity to the 12-attempt denominator and 68% shrinkage cutoff. Low-sample defense measures can otherwise be misleading.
2. Separate losing mechanisms: submission during wrestling entry, early KO vs extended distance exchanges, and losing on the scorecards from insufficient control. Record verified pre-fight regional history with source reliability separately.
3. Log all new prospectively qualifying fight matchups **before outcomes** in an isolated frozen research ledger. Group multiple fights involving the same fighter and event when validating rates.
4. Test fresh observations outside the previously analyzed 2010–2026 period. Do not promote to U1–U12 or modify bankroll exposure on the basis of these results.

**Decision: zero new official methods, no new live vetoes.** This pass identified possible mechanisms worth studying but did not demonstrate a robust, transferable improvement to high-probability betting results.
