from __future__ import annotations

from pathlib import Path
import json
import os
import traceback

import nflreadpy as nfl

SOURCE_ROOT = Path(os.environ.get("NFL_SOURCE_ROOT", "/home/anestishkurti92/nfl-predictor-v1"))
CONTEXT_ROOT = Path(os.environ.get("NFL_CONTEXT_ROOT", "/home/appwiza-runner/nfl-context-data"))
RAW = CONTEXT_ROOT / "raw"
RAW.mkdir(parents=True, exist_ok=True)

SEASONS = list(range(2006, 2027))
STATUS = {"source_root": str(SOURCE_ROOT), "context_root": str(CONTEXT_ROOT), "datasets": {}}


def save(name, loader):
    path = RAW / name
    try:
        print(f"[NFL CONTEXT] loading {name}", flush=True)
        df = loader()
        df.write_parquet(path, compression="zstd")
        rec = {"ok": True, "rows": df.height, "columns": df.columns, "path": str(path)}
        if "season" in df.columns and df.height:
            rec["season_min"] = df.select("season").min().item()
            rec["season_max"] = df.select("season").max().item()
        STATUS["datasets"][name] = rec
        print(f"[NFL CONTEXT] {name}: {df.height:,} rows x {df.width}", flush=True)
    except Exception as exc:
        STATUS["datasets"][name] = {"ok": False, "error": repr(exc)}
        print(f"[NFL CONTEXT] WARNING {name}: {exc}", flush=True)
        traceback.print_exc()


save("players.parquet", nfl.load_players)
save("player_stats_weekly_2006_2026.parquet", lambda: nfl.load_player_stats(SEASONS, summary_level="week"))
save("rosters_2006_2026.parquet", lambda: nfl.load_rosters(SEASONS))
save("weekly_rosters_2006_2026.parquet", lambda: nfl.load_rosters_weekly(SEASONS))
save("depth_charts_2006_2026.parquet", lambda: nfl.load_depth_charts(SEASONS))
save("snap_counts_2012_2026.parquet", lambda: nfl.load_snap_counts(list(range(2012, 2027))))

# nflverse's historical injury source currently ends after 2024. Keep this explicitly
# bounded instead of pretending that 2025-26 injury reports exist in this feed.
save("injuries_2009_2024.parquet", lambda: nfl.load_injuries(list(range(2009, 2025))))

for kind in ("passing", "rushing", "receiving"):
    save(f"ngs_{kind}_2016_2026.parquet", lambda kind=kind: nfl.load_nextgen_stats(list(range(2016, 2027)), kind))

for kind in ("pass", "rush", "rec", "def"):
    save(f"pfr_adv_{kind}_2018_2026.parquet", lambda kind=kind: nfl.load_pfr_advstats(list(range(2018, 2027)), kind, "week"))

save("ftn_charting_2022_2026.parquet", lambda: nfl.load_ftn_charting(list(range(2022, 2027))))
save("trades.parquet", nfl.load_trades)
save("combine.parquet", nfl.load_combine)
save("draft_picks.parquet", nfl.load_draft_picks)

status_path = CONTEXT_ROOT / "NFL_CONTEXT_DATA_STATUS.json"
status_path.write_text(json.dumps(STATUS, indent=2, default=str))
summary = {k: {kk: vv for kk, vv in v.items() if kk != "columns"} for k, v in STATUS["datasets"].items()}
print(json.dumps(summary, indent=2, default=str))
