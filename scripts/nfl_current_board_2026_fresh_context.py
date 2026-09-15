from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd
import numpy as np
import nflreadpy as nfl

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "nfl" / "live_2026_current_snapshot"
OUT.mkdir(parents=True, exist_ok=True)
BOARD = REPO / "nfl" / "live_legacy_full_dissection" / "current_board_deep_context.csv"
SEASON = 2026

TEAM_MAP = {"SD":"LAC","OAK":"LV","STL":"LA","LAR":"LA"}
def canon(x):
    if pd.isna(x): return x
    return TEAM_MAP.get(str(x), str(x))
def num(x): return pd.to_numeric(x, errors="coerce")
def yesno(x):
    if pd.isna(x): return "unknown"
    try: return "yes" if float(x) == 1 else "no"
    except Exception: return str(x)
def to_pd(x): return x.to_pandas() if hasattr(x, "to_pandas") else pd.DataFrame(x)

board = pd.read_csv(BOARD)
board["team"] = board["team"].map(canon)
board["opponent"] = board["opponent"].map(canon)

sched = to_pd(nfl.load_schedules([SEASON]))
sched = sched[(num(sched.get("season")) == SEASON) & sched.get("game_type", "REG").astype(str).eq("REG")].copy()
for c in ["home_team","away_team"]:
    if c in sched: sched[c] = sched[c].map(canon)

team = to_pd(nfl.load_team_stats([SEASON], summary_level="week"))
if "team" in team: team["team"] = team["team"].map(canon)
if "season" in team: team = team[num(team.season).eq(SEASON)].copy()

side=[]
for _,r in sched.iterrows():
    hs=num(pd.Series([r.get("home_score")])).iloc[0]
    aws=num(pd.Series([r.get("away_score")])).iloc[0]
    if pd.isna(hs) or pd.isna(aws): continue
    w=int(num(pd.Series([r.get("week")])).iloc[0])
    for tm,opp,pf,pa,home in [(r.get("home_team"),r.get("away_team"),hs,aws,1),(r.get("away_team"),r.get("home_team"),aws,hs,0)]:
        side.append({"game_id":r.get("game_id"),"week":w,"team":tm,"opponent":opp,"points_for":float(pf),"points_against":float(pa),"point_margin":float(pf-pa),"won":int(pf>pa),"is_home":home})
played=pd.DataFrame(side)

core_candidates = [
    "passing_yards","rushing_yards","receiving_yards","passing_epa","rushing_epa","passing_cpoe",
    "completions","attempts","carries","passing_tds","rushing_tds","interceptions","sacks_suffered",
    "passing_first_downs","rushing_first_downs","fumbles_lost","fantasy_points","pacr","racr"
]
core_cols=[c for c in core_candidates if c in team.columns]
weekly=played.copy()
if len(team) and {"team","week"}.issubset(team.columns):
    t=team.copy(); t["week"]=num(t.week).astype("Int64")
    weekly=weekly.merge(t[["team","week"]+core_cols].drop_duplicates(["team","week"]),on=["team","week"],how="left")

coach_cols=[c for c in [
    "head_coach","offensive_coordinator","defensive_coordinator",
    "head_coach_changed","offensive_coordinator_changed","defensive_coordinator_changed",
    "hc_changed_season","oc_changed_season","dc_changed_season","staff_change_count","major_staff_changes",
    "coachq_hc_prior_quality","coachq_oc_prior_quality","coachq_dc_prior_quality",
    "coachq_hc_recent_quality","coachq_oc_recent_quality","coachq_dc_recent_quality",
    "coachq_staff_quality_mean","coachq_staff_quality_min","coachq_staff_upgrade_count","coachq_staff_downgrade_count",
    "returning_offense_snap_share","returning_defense_snap_share","returning_ol_snap_share","returning_skill_snap_share"
] if c in board.columns]

rows=[]; lines=[]
lines.append("NFL 2026 CURRENT BOARD — FRESH CURRENT-SEASON + COACHING CONTEXT")
lines.append(f"generated_utc={datetime.now(timezone.utc).isoformat()}")
lines.append("Fresh schedule/game_id is authoritative. Completed or week-mismatched board rows are flagged, not treated as upcoming.")
lines.append("Current-season samples before Week 4 are CONTEXT ONLY; mature rolling methods still require >=3 prior completed games.")
lines.append("")

for _,b in board.iterrows():
    tm,opp=canon(b.team),canon(b.opponent)
    board_week=int(num(pd.Series([b.get("week")])).iloc[0])
    gid=b.get("game_id")
    sm=sched[sched.game_id.astype(str).eq(str(gid))] if "game_id" in sched else pd.DataFrame()
    schedule_week=board_week; completed=False; target_pf=np.nan; target_pa=np.nan; target_status="UPCOMING"
    if len(sm):
        sr=sm.iloc[0]
        sw=num(pd.Series([sr.get("week")])).iloc[0]
        if pd.notna(sw): schedule_week=int(sw)
        hs=num(pd.Series([sr.get("home_score")])).iloc[0]; aws=num(pd.Series([sr.get("away_score")])).iloc[0]
        completed=pd.notna(hs) and pd.notna(aws)
        if completed:
            target_status="COMPLETED_STALE_BOARD"
            if tm==canon(sr.get("home_team")): target_pf,target_pa=float(hs),float(aws)
            else: target_pf,target_pa=float(aws),float(hs)
    else:
        target_status="GAME_ID_NOT_FOUND"
    week_mismatch=(board_week!=schedule_week)
    if week_mismatch and not completed: target_status="WEEK_MISMATCH_REVIEW"

    target_week=schedule_week
    prior=weekly[(weekly.team.eq(tm)) & (num(weekly.week)<target_week)].sort_values("week")
    oppprior=weekly[(weekly.team.eq(opp)) & (num(weekly.week)<target_week)].sort_values("week")

    rec={
        "team":tm,"selection":b.get("selection"),"opponent":opp,"game_id":gid,"board_week":board_week,"schedule_week":schedule_week,
        "week_mismatch":week_mismatch,"target_status":target_status,"target_completed":completed,"target_points_for":target_pf,"target_points_against":target_pa,
        "moneyline":b.get("moneyline"),"method":b.get("method"),"preseason_record":b.get("preseason_record_equiv"),
        "preseason_policy_pass":b.get("preseason_policy_pass"),"price_bucket_win_pct":b.get("price_bucket_win_pct"),
        "price_bucket_roi":b.get("price_bucket_roi"),"price_bucket_recent_roi":b.get("price_bucket_recent_roi"),
        "current_games_available":int(len(prior)),"current_wins":int(prior.won.sum()) if len(prior) else 0,
        "current_losses":int(len(prior)-prior.won.sum()) if len(prior) else 0,"current_point_margin_avg":float(prior.point_margin.mean()) if len(prior) else np.nan,
        "opp_current_games_available":int(len(oppprior)),"opp_current_wins":int(oppprior.won.sum()) if len(oppprior) else 0,
        "opp_current_losses":int(len(oppprior)-oppprior.won.sum()) if len(oppprior) else 0,"opp_current_point_margin_avg":float(oppprior.point_margin.mean()) if len(oppprior) else np.nan,
    }
    for c in coach_cols: rec[c]=b.get(c)
    for c in core_cols:
        rec[f"current_{c}_avg"] = float(num(prior[c]).mean()) if len(prior) and num(prior[c]).notna().any() else np.nan
        rec[f"opp_current_{c}_avg"] = float(num(oppprior[c]).mean()) if len(oppprior) and num(oppprior[c]).notna().any() else np.nan
    rows.append(rec)

    changes=[]
    for label,cands in [("HC",["hc_changed_season","head_coach_changed"]),("OC",["oc_changed_season","offensive_coordinator_changed"]),("DC",["dc_changed_season","defensive_coordinator_changed"])]:
        val=np.nan
        for c in cands:
            if c in b.index and pd.notna(b.get(c)): val=b.get(c); break
        changes.append(f"{label} change={yesno(val)}")
    lines.append(f"{tm} ({b.get('selection')}) vs {opp} | board W{board_week}, schedule W{schedule_week} | ML={b.get('moneyline')} | method={b.get('method')} | status={target_status}")
    if week_mismatch: lines.append(f"  IDENTITY WARNING: board week={board_week} but fresh schedule game_id says week={schedule_week}.")
    if completed: lines.append(f"  TARGET ALREADY FINAL: {tm} {int(target_pf)}-{int(target_pa)} {opp}. This is not an upcoming bet.")
    lines.append("  Coaching: " + ", ".join(changes))
    names=[f"HC={b.get('head_coach')}" if pd.notna(b.get('head_coach')) else None,f"OC={b.get('offensive_coordinator')}" if pd.notna(b.get('offensive_coordinator')) else None,f"DC={b.get('defensive_coordinator')}" if pd.notna(b.get('defensive_coordinator')) else None]
    if any(names): lines.append("  Staff: " + "; ".join(x for x in names if x))
    if len(prior):
        lines.append(f"  Preseason={b.get('preseason_record_equiv')} pass={b.get('preseason_policy_pass')} | prior 2026 games={len(prior)} record={int(prior.won.sum())}-{int(len(prior)-prior.won.sum())} avg margin={prior.point_margin.mean():+.1f}")
        for _,g in prior.iterrows():
            extras=[]
            for c in core_cols:
                v=num(pd.Series([g.get(c)])).iloc[0]
                if pd.notna(v): extras.append(f"{c}={v:.3g}")
            lines.append(f"    W{int(g.week)} vs {g.opponent}: {'W' if g.won else 'L'} {int(g.points_for)}-{int(g.points_against)} margin={g.point_margin:+.0f}" + ((" | "+", ".join(extras[:9])) if extras else ""))
    else:
        lines.append(f"  Preseason={b.get('preseason_record_equiv')} pass={b.get('preseason_policy_pass')} | no completed 2026 games before authoritative target week")
    if len(oppprior): lines.append(f"  Opponent prior-2026 record={int(oppprior.won.sum())}-{int(len(oppprior)-oppprior.won.sum())}, avg margin={oppprior.point_margin.mean():+.1f}")
    lines.append("")

out=pd.DataFrame(rows)
out.to_csv(OUT/"current_board_fresh_context.csv",index=False)
weekly.to_csv(OUT/"completed_2026_team_week_context.csv",index=False)
(OUT/"report.txt").write_text("\n".join(lines)+"\n")
meta={"generated_utc":datetime.now(timezone.utc).isoformat(),"season":SEASON,"board_rows":len(board),"completed_team_game_rows":len(weekly),"team_weekly_fields_used":core_cols,"actionable_rows":int((out.target_status=="UPCOMING").sum()),"stale_completed_rows":int((out.target_status=="COMPLETED_STALE_BOARD").sum()),"week_mismatch_rows":int(out.week_mismatch.sum()),"policy":"Fresh schedule/game_id overrides page week. Weeks 1-3 current stats are context only; >=3 prior games required for mature rolling methods. Coaching unknown stays unknown."}
(OUT/"summary.json").write_text(json.dumps(meta,indent=2,default=str))
print("\n".join(lines))
