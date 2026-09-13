# Boxing data readiness

Scope: professional boxing, 1990 onward, all available promotions and non-title bouts. Earlier career bouts are retained when needed to reconstruct a fighter's pre-1990 experience. Processing and the database remain on Google.

## New historical statistics

`enriched_pre_bout` supplements the original feature table. Each row preserves its source bout ID, date, fighter identity, verified opponent identity when available, earlier input bout IDs, and build timestamp.

| Family | Statistics | Qualification |
|---|---|---|
| Activity | Bouts in previous 90/180/365/730 days; median recent gap; maximum prior gap; days since first observed bout | Observed history, not proof of complete career |
| Results | Decision wins/losses, split/majority decision wins/losses, stoppage wins/losses, no contests | Career and previous 3/5/10 bouts |
| Durability | Days since prior stoppage loss; loss and draw streaks | Strictly earlier bouts |
| Round experience | Terminal rounds reached, average terminal round, reached round nine, explicitly scheduled 12+ rounds, early stoppage wins | Known-round denominator retained; missing stays unknown; terminal rounds are not minutes or completed rounds |
| Rematches | Prior verified meetings and their wins/losses | Reciprocal opponent links only |
| Opponent quality | Mean observed win fraction of previous opponents; wins over opponents with at least 75% observed win fraction | Opponent records frozen before that earlier meeting; at least five decisive prior bouts; observed careers may be incomplete |
| Audit | Earlier input dates, duplicate date/opponent rows, input IDs and coverage counts | Coverage flags are not automatic research eligibility |

Current height, reach, stance and profile totals remain separate. They are not certified historical values. Birth-date sources still need identity checks. Historical punch reports remain outside model features until identities, dates and coverage are validated.

Earlier-bout cutoffs prevent future fights entering the calculations. They do not certify when a source published or revised an old result; retrospective disqualifications and no-contest changes still require effective-date evidence.

## Completion gates

1. Reconcile aliases and same-bout source observations; retain disagreements.
2. Audit reconstructed records against dated source records. A large number of rows does not establish complete history.
3. Publish year-by-year counts of observations, verified identities, prior-history availability, known rounds, punch coverage and matched prices.
4. Preserve actual bookmaker identity and quote provenance; identify timing and settlement gaps explicitly.
5. Freeze a versioned research subset with input hashes and documented exclusions before further rule searches.

We cannot honestly call the entire 1990–present archive complete yet. The strict first price/history scan had only 195 eligible bouts, much smaller than raw bout observations. Historical odds and representative punch data remain the largest external gaps.

## Sources requiring further access or validation

- [CompuBox](https://beta.compuboxdata.com/): public reports and historical links in collected articles. Its home page exposes recent reports and current leaders; those leaders must not be backdated. CompuBox lists a contact for production/data-feed access. No message or purchase has been authorized or made.
- [BoxRec API documentation](https://boxrec.com/api/docs): broad record source; working authorized API access has not been established.
- [Boxing Data API documentation](https://boxing-data.com/docs/): describes fighter profiles, fight results, events and rankings with RapidAPI authentication. Historical depth and point-in-time suitability have not been verified; not counted as imported data.

`finish_data_batch.sh` is a single bounded server job, behind the current expansion lock. It processes up to 80 external evidence pages, follows observed historical CompuBox report links (up to 80 unparsed reports), and rebuilds identity links, features, joins and coverage. It does not install a recurring timer or send emails.
