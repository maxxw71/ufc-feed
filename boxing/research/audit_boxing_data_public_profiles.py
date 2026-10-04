#!/usr/bin/env python3
"""Audit Boxing Data BROAD physical/profile candidates against strict profiles."""
from __future__ import annotations
import csv,json,re,statistics
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CAND=ROOT/"profile_supplements"/"boxing_data_public_profile_candidates.csv"
GAP=ROOT/"public_phase2"/"PROFILE_GAP_AUDIT.json"
OUT=ROOT/"profile_supplements"/"boxing_data_public_profile_agreement.json"

def n(x):
    x=str(x or "").strip().lower()
    return re.sub(r"\s+"," ",x)

def num(x):
    try:return float(x)
    except:return None

def main():
    strict=json.loads(GAP.read_text())
    sm={x["name"]:x for x in strict.get("fighters",[]) if x.get("name")}
    rows=list(csv.DictReader(CAND.open(encoding="utf-8")))
    comparisons=[]
    per=defaultdict(lambda:{"n":0,"agree":0,"conflict":0,"diffs":[]})
    conflicts=[]
    for r in rows:
        f=r["fighter"];field=r["field"]
        s=(sm.get(f,{}).get("present") or {}).get(field)
        if s is None:continue
        c=r["candidate_value"]
        ok=None;diff=None
        if field in ("reach_cm","height_cm"):
            a,b=num(c),num(s)
            if a is None or b is None:continue
            diff=abs(a-b)
            ok=diff<=1.1
        elif field=="stance":
            ok=n(c)==n(s)
        elif field=="nationality":
            # Nationality labels vary (British/United Kingdom/English); report
            # exact-label agreement only and avoid treating terminology variance
            # as a hard data error.
            ok=n(c)==n(s)
        else:
            continue
        d=per[field];d["n"]+=1;d["agree"]+=int(bool(ok));d["conflict"]+=int(not ok)
        if diff is not None:d["diffs"].append(diff)
        comparisons.append({"fighter":f,"field":field,"candidate":c,"strict":s,"difference":diff,"agree":ok,"source_url":r["source_url"]})
        if not ok:conflicts.append(comparisons[-1])
    summary={}
    for field,d in per.items():
        summary[field]={
          "comparisons":d["n"],"agreements":d["agree"],"conflicts":d["conflict"],
          "agreement_pct":round(100*d["agree"]/d["n"],2) if d["n"] else None,
          "median_abs_diff_cm":round(statistics.median(d["diffs"]),2) if d["diffs"] else None,
          "max_abs_diff_cm":round(max(d["diffs"]),2) if d["diffs"] else None
        }
    out={
      "generated_at":datetime.now(timezone.utc).isoformat(),
      "summary":summary,
      "conflicts":conflicts,
      "policy":"Physical conflicts do not overwrite strict values. They are queued for source review; nationality wording differences are not automatically errors."
    }
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=="__main__":main()
