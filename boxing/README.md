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
