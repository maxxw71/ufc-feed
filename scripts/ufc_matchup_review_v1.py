#!/usr/bin/env python3
"""Point-in-time UFC matchup review. Conservative safety gate, not a new winning method.

Uses prior UFCStats bouts only. Missing regional/DWCS records are *not* treated
as evidence of zero submission or finish risk. Research verdicts are versioned;
do not rewrite settled picks or pretend a postmortem rule is validated ROI.
"""
from __future__ import annotations
import csv
import json
import math
import re
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

VERSION = "UFC_MATCHUP_REVIEW_V1_20261010"
MIN_UFC_FIGHTS = 2
HISTORY_FILENAME = Path("raw/competitions.csv")

def norm(name):
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()

def as_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None

def number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None

def clock_minutes(row):
    rnd = number(row.get("round"))
    t = re.match(r"^(\d+):(\d+)$", str(row.get("time") or "").strip())
    if rnd is None or not t:
        return None
    return max(0.1, (max(1, int(rnd)) - 1)*5 + int(t[1]) + int(t[2])/60)

def attempt_count(row, side, field):
    # Per-round UFCStats columns are optional; missing must stay unknown, not zero.
    total = 0.0
    found = False
    for rnd in range(1,6):
        val = row.get(f"{side}_rd{rnd}_{field}")
        if val is None or not str(val).strip() or str(val).lower() == "nan":
            continue
        match = re.search(r"(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)",str(val),re.I)
        if match:
            total += float(match[2])
            found = True
    return total if found else None

def _index_from_file(path):
    rows = {}
    with path.open(newline="",encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            dt=as_date(r.get("event_date"))
            a,b=norm(r.get("player1")),norm(r.get("player2"))
            if not dt or not a or not b or a==b:
                continue
            result=str(r.get("result") or "").upper().strip()
            if not (result.startswith("W") or result.startswith("L")):
                # No contest/draw is still a fight, but no winner and no loss.
                winner=-1
            else:
                winner=0 if result.startswith("W") else 1
            method=str(r.get("method") or "").lower()
            finish_kind=("SUB" if "submission" in method else
                         "KO" if ("ko" in method or "tko" in method) else None)
            rnd=number(r.get("round"))
            minutes=clock_minutes(r)
            for side,(fighter,opponent) in enumerate(((a,b),(b,a))):
                label="p1" if side==0 else "p2"
                event={
                    "date":dt,
                    "opponent":opponent,
                    "win":int(side==winner),
                    "loss":int(winner>=0 and side!=winner),
                    "submission_win":int(side==winner and finish_kind=="SUB"),
                    "submission_loss":int(winner>=0 and side!=winner and finish_kind=="SUB"),
                    "ko_win":int(side==winner and finish_kind=="KO"),
                    "ko_loss":int(winner>=0 and side!=winner and finish_kind=="KO"),
                    "first_round_finish_win":int(side==winner and bool(finish_kind) and rnd==1),
                    "first_round_finish_loss":int(winner>=0 and side!=winner and bool(finish_kind) and rnd==1),
                    "first_round_sub_win":int(side==winner and finish_kind=="SUB" and rnd==1),
                    "minutes":minutes,
                    "td_attempts":attempt_count(r,label,"Td")
                }
                rows.setdefault(fighter,[]).append(event)
    return {k:sorted(v,key=lambda x:x["date"]) for k,v in rows.items()}

@lru_cache(maxsize=4)
def _cached_index(file_path, modified_ns, size):
    return _index_from_file(Path(file_path))

def history(root):
    path=Path(root)/HISTORY_FILENAME
    stat=path.stat()
    return _cached_index(str(path),stat.st_mtime_ns,stat.st_size)

def snap(events, event_date):
    prior=[e for e in events if e["date"] < event_date]
    recent=prior[-5:]
    mins=sum(e["minutes"] for e in prior if e["minutes"] is not None)
    tds=[e for e in prior if e["td_attempts"] is not None and e["minutes"] is not None]
    td_minutes=sum(e["minutes"] for e in tds)
    td_attempts=sum(e["td_attempts"] for e in tds)
    return {
        "fights":len(prior),
        "wins":sum(e["win"] for e in prior),
        "losses":sum(e["loss"] for e in prior),
        "submission_wins":sum(e["submission_win"] for e in prior),
        "submission_losses":sum(e["submission_loss"] for e in prior),
        "ko_wins":sum(e["ko_win"] for e in prior),
        "ko_losses":sum(e["ko_loss"] for e in prior),
        "first_round_finish_wins":sum(e["first_round_finish_win"] for e in prior),
        "first_round_finish_losses":sum(e["first_round_finish_loss"] for e in prior),
        "first_round_submission_wins":sum(e["first_round_sub_win"] for e in prior),
        "recent5_submission_losses":sum(e["submission_loss"] for e in recent),
        "recent5_finish_losses":sum(e["submission_loss"]+e["ko_loss"] for e in recent),
        "td_attempts_per15":round(15*td_attempts/td_minutes,3) if td_minutes>0 else None,
        "td_coverage_fights":len(tds),
        "first_prior_fight":prior[0]["date"].isoformat() if prior else None,
        "last_prior_fight":prior[-1]["date"].isoformat() if prior else None
    }

def _market_for(p, favorite):
    a=norm(p.get("fighter_a"))
    side="a" if norm(favorite)==a else "b"
    return {
        "no_vig_prob":number(p.get("market_"+side)),
        "book_odds":number(p.get("best_odds_"+side)),
        "model_prob":number(p.get("p_"+side)),
        "model_ev_pct":number(p.get("ev_"+side+"_pct"))
    }

def assess_selection(pred, favorite, event_date, root):
    a,b=str(pred.get("fighter_a") or "").strip(),str(pred.get("fighter_b") or "").strip()
    fav=str(favorite or "").strip()
    opp=(b if norm(fav)==norm(a) else a if norm(fav)==norm(b) else "")
    info={
        "version":VERSION,"favorite":fav,"opponent":opp,"event_date":str(event_date)[:10],
        "status":"HOLD","reasons":[],"warnings":[],"coverage":"ufcstats_prior_bouts_only",
        "favorite_history":{},"opponent_history":{},"market":_market_for(pred,fav)
    }
    ed=as_date(event_date)
    if not ed or not opp:
        info["reasons"].append("UNRESOLVED_FIGHTER_OR_DATE")
        return info
    try:
        ix=history(root)
    except (OSError,ValueError) as exc:
        info["reasons"].append("VERIFIED_UFC_HISTORY_UNAVAILABLE")
        info["warnings"].append(type(exc).__name__)
        return info
    fp=snap(ix.get(norm(fav),[]),ed)
    op=snap(ix.get(norm(opp),[]),ed)
    info["favorite_history"]=fp
    info["opponent_history"]=op
    if fp["fights"] < MIN_UFC_FIGHTS or op["fights"] < MIN_UFC_FIGHTS:
        info["reasons"].append("INSUFFICIENT_PRIOR_UFC_HISTORY_REQUIRES_REGIONAL_DWCS_REVIEW")
    # Independent of U1-U12: a documented submission history on both sides
    # is a specific matchup trap. Hold for corroboration rather than assuming
    # the striking/age/market edge neutralizes the guillotine risk.
    if op["submission_wins"]>=1 and fp["submission_losses"]>=1:
        info["reasons"].append("SUBMISSION_TRAP_OPPONENT_SUB_WINS_AND_FAVORITE_SUB_LOSSES")
    if op["first_round_finish_wins"]>=2 and fp["first_round_finish_losses"]>=1:
        info["warnings"].append("EARLY_FINISH_COLLISION")
    if op["ko_wins"]>=2 and fp["ko_losses"]>=1:
        info["warnings"].append("KNOCKOUT_STYLE_COLLISION")
    if op["submission_wins"]>=1 and fp["td_attempts_per15"] is not None and fp["td_attempts_per15"]>=1.0:
        info["warnings"].append("TAKEDOWN_ENTRY_VS_SUBMISSION_OPPONENT")
    if fp["td_coverage_fights"]==0 or op["td_coverage_fights"]==0:
        info["warnings"].append("TAKEDOWN_STAT_COVERAGE_INCOMPLETE")
    if op["submission_wins"]==0:
        info["warnings"].append("ZERO_UFC_SUBMISSIONS_DOES_NOT_EXCLUDE_REGIONAL_SUBMISSIONS")
    # Model EV is an independent warning, not a retrospectively tuned hard
    # rejection; missing/uncalibrated probabilities cannot prove a positive EV.
    ev=info["market"]["model_ev_pct"]
    if ev is None:
        info["warnings"].append("MODEL_EXPECTED_VALUE_NOT_AVAILABLE")
    elif ev<=0:
        info["warnings"].append("NONPOSITIVE_MODEL_EXPECTED_VALUE")
    info["status"]="HOLD" if info["reasons"] else "PASS"
    return info

def check_line(review):
    f,o=review["favorite_history"],review["opponent_history"]
    if not f or not o:
        return "Matchup review "+review["status"]+" — verified prior-fight context missing"
    def rate(x):
        return "unknown" if x is None else f"{float(x):.2f}"
    return (
        "Matchup "+review["status"]+": favorite UFCStats history "
        +f"{f['fights']} fights / {f['submission_losses']} submission losses / "
        +f"{f['ko_losses']} KO losses / {rate(f['td_attempts_per15'])} TD attempts per 15; "
        +"opponent "+f"{o['fights']} fights / {o['submission_wins']} submission wins / "
        +f"{o['first_round_finish_wins']} first-round finish wins"
    )
