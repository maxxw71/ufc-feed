from pathlib import Path
import csv, json, sys

ROOT=Path(__file__).resolve().parent
cfg=json.loads((ROOT/"config.json").read_text())
errors=[]

if cfg.get("promotion")!="DWCS":
    errors.append("promotion must be DWCS")
if cfg.get("storage_namespace")!="dwcs":
    errors.append("storage_namespace must be dwcs")
if cfg.get("count_toward_ufc_record") is not False:
    errors.append("DWCS must not count toward UFC record")
if cfg.get("research_status")!="shadow_only":
    errors.append("new DWCS lane must begin shadow_only")

for rel in ["data/events.csv","data/fights.csv"]:
    p=ROOT/rel
    with p.open(newline="",encoding="utf-8") as fh:
        rows=list(csv.DictReader(fh))
    for i,row in enumerate(rows,2):
        if row.get("promotion")!="DWCS":
            errors.append(f"{rel}:{i}: promotion is not DWCS")
        if row.get("event_type")!="contender_series":
            errors.append(f"{rel}:{i}: event_type is not contender_series")

for rel in ["data/method_transfer_registry.csv","data/performance_summary.csv"]:
    p=ROOT/rel
    with p.open(newline="",encoding="utf-8") as fh:
        rows=list(csv.DictReader(fh))
    for i,row in enumerate(rows,2):
        mode=row.get("dwcs_mode",row.get("mode",""))
        if mode not in {"transfer_test","shadow_candidate"}:
            errors.append(f"{rel}:{i}: invalid DWCS mode {mode!r}")
        if row.get("status","shadow_only") not in {"shadow_only",""}:
            errors.append(f"{rel}:{i}: non-shadow method present")

if errors:
    print("\n".join(errors))
    sys.exit(1)

print("DWCS isolation validation passed")
print("UFC official/live records are not modified by this lane")
