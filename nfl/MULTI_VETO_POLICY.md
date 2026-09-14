# NFL multi-veto policy

A method is not limited to one veto. The objective is to maximize robust prospective ROI without fitting historical noise.

## Selection protocol

1. Discover the base signal without using the holdout period.
2. Generate veto candidates using train + validation only.
3. Additional vetoes are allowed only when each added veto produces material incremental improvement in both pre-holdout eras, not merely in the combined sample.
4. Penalize complexity and prefer the simpler rule whenever the incremental improvement is small.
5. Require adequate sample retention after every added veto. A prettier ROI from a tiny surviving sample is not enough.
6. Freeze the full stacked rule before opening holdout results.
7. The entire stack must then confirm in holdout. If the selected stack fails holdout, reject the stack rather than tuning it on holdout.
8. Timing exclusions and feature vetoes compete under the same framework; neither category has special priority.
9. Report both the base method and filtered method: record, win percentage, ROI, holdout ROI, sample retained, positive-season rate, and number of vetoes.
10. For live deployment, fail closed when a required veto feature is unavailable.

## Default anti-overfit guardrails

- Maximum search depth: 3 vetoes unless a later dedicated audit justifies more.
- Each extra veto should improve the average train/validation ROI by at least 3 percentage points after the first veto.
- The selected stack should improve pre-holdout ROI by at least 5 percentage points over the base method.
- Neither train nor validation ROI may materially collapse versus the base method.
- Prefer retaining at least 60% of pre-holdout bets and at least 50% of holdout bets.
- Holdout ROI must confirm the stack; otherwise keep the simpler previously approved version.
- Never select or tune thresholds using the holdout period.

This policy supersedes the previous blanket one-veto maximum. One veto remains preferable when it captures nearly all of the robust improvement, but multiple vetoes are allowed when the evidence supports them.

_Optimizer trigger: 2026-09-14._
