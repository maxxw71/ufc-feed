# Boxing collectors and upcoming-bouts feed

This folder adds the core boxing collection code and a compact schedule snapshot. The research database and large datasets remain on appwiza. Downloads: https://appwiza.com/sports/boxing/#downloads

## Collector code

- `collectors/collect.py`: base archive, source provenance and initial bout collection.
- `collectors/careers.py`: professional fighter histories.
- `collectors/odds_collect.py`: archived price observations.
- `collectors/punch_collect.py`: published punch reports.
- `collectors/wiki_record_tables.py`: professional-record parser used by careers.
- `collectors/public_feed_ipv6.py`: legacy network helper used by the collectors.
- `collectors/export_upcoming.py`: read-only schedule exporter.

Use Python 3.12 and install `requirements.txt`. Collectors store their database and raw captures beside the code. Run them on the research server, not GitHub Actions. Start with `collect.py --pbc-pages 20`; other collection commands have independent batch limits. Source access failures must be respected; no guarantee of historical completeness is made.

## Feed

`upcoming.json` contains only explicitly scheduled or announced future bouts with two named fighters and no winner. It is a schedule, not betting picks. The initial snapshot is empty because the current historical database contains no verified future bouts; this is a coverage gap, not a claim that no boxing events are scheduled. No boxing method has been promoted to live picks.

Regenerate on appwiza:

```sh
python collectors/export_upcoming.py --database /path/to/boxing.sqlite3 --output upcoming.json
```

This commit is a snapshot; no automatic GitHub publishing schedule is installed. The existing UFC feed and its workflows are unchanged. No credentials, raw evidence, database or research-analysis output is included.


## Authoritative server research releases (2026-10-07)

Boxing workflows execute on the appwiza self-hosted runner. The server release pointer is /srv/appwiza-sports/boxing-releases/CURRENT.json; aggregate coverage is public_phase2/CURRENT_DATASET_STATUS.json; detailed manifests and ledgers remain private in the server release. Older public_reports files describe retained raw/legacy populations and must not override this manifest. Releases preserve database/master hashes, executed code and supplement hashes, compressed snapshots, priced-bout exclusion ledgers and frozen hypothesis versions.

The release driver uses isolated work directories, a publication lock, a disk-headroom gate and atomic success-only publication. Identical inputs skip redundant builds. Existing boxing snapshots are retained. Failed builds preserve the prior current release.

The exclusion ledger uses the same gates as market selection. The history repair queue prioritizes fighters associated with excluded priced bouts: a target may require opponent-history or identity review rather than a change to its own career. Broad and deep-punch research remain separate.

Prospective outcome review fails closed on conflicting results and keeps nearby-date candidates for manual review. Two URLs from one publisher are not independent sources. Verified fight outcomes do not certify bookmaker-specific draw, cancellation or no-contest settlement. Methods remain research-only. The hypothesis registry retains the first freeze timestamp for an unchanged method version.

Recovered careers are stored separately in supplemental_careers/recovered_priced_careers.jsonl. The importer reuses existing exact source/date/opponent identities and hashes new event identities instead of assigning row-position IDs. Conflicting outcomes are held for review. Raw recovery evidence is retained on appwiza under /srv/appwiza-sports/boxing-maintenance/targeted-repairs.
