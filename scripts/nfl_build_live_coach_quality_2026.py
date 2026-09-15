from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import os
import re

import numpy as np
import pandas as pd
import nflreadpy as nfl

REPO = Path(os.environ.get("GITHUB_WORKSPACE", Path(__file__).resolve().parents[1]))
CTX = Path(os.environ.get("NFL_CONTEXT_ROOT", "/home/appwiza-runner/nfl-context-data"))
SEASON = int(os.environ.get("NFL_SEASON", "2026"))
OUT_CTX = CTX / "coach_quality_live"
OUT_REPO = REPO / "nfl" / "live_coach_quality_2026"
OUT_CTX.mkdir(parents=True, exist_ok=True)
OUT_REPO.mkdir(parents=True, exist_ok=True)

HIST_SIDES = CTX / "coaching_everything" / "coaching_enriched_team_sides_2006_2025.parquet"
RAW_STAFF = CTX / "raw" / "coaching_staff_2006_2026.csv"
AUTH_STAFF = REPO / "data" / "nfl" / "coordinator_history_espn_2019_2026.csv"
ALIASES = {"SD":"LAC","OAK":"LV","STL":"LA","LAR":"LA","WSH":"WAS"}


def num(x): return pd.to_numeric(x, errors="coerce")
def canon_team(x):
    if pd.isna(x): return x
    return ALIASES.get(str(x), str(x))
def norm_name(x):
    if pd.isna(x): return None
    s = str(x).strip().lower()
    if not s: return None
    s = s.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", s)
def to_pd(x): return x.to_pandas() if hasattr(x, "to_pandas") else pd.DataFrame(x)

for p in [HIST_SIDES, RAW_STAFF, AUTH_STAFF]:
    if not p.exists(): raise FileNotFoundError(p)

# Historical performance through 2025 only. These values become pregame-safe inputs for 2026.
d = pd.read_parquet(HIST_SIDES)
d = d[num(d.season).between(2006, SEASON-1)].copy()
d["team"] = d.team.map(canon_team)
d["win"] = num(d.get("win"))
d = d[d.win.isin([0,1])].copy()
last = d.sort_values(["season","week","game_id"]).groupby(["season","team"], as_index=False).tail(1).copy()
season_perf = d.groupby(["season","team"], as_index=False).agg(team_season_win_pct=("win","mean"), team_season_games=("win","size"))
off_col = next((c for c in ["pre_off_epa_per_play","recent_off_epa_per_play","pre_pass_epa_per_dropback"] if c in last.columns), None)
def_col = next((c for c in ["pre_def_allowed_off_epa_per_play","recent_def_allowed_off_epa_per_play","pre_def_allowed_pass_epa_per_dropback"] if c in last.columns), None)
if off_col is None or def_col is None: raise RuntimeError("Historical offense/defense quality columns unavailable")
last["off_value"] = num(last[off_col])
last["def_allowed_value"] = num(last[def_col])
last["off_quality_pct"] = last.groupby("season").off_value.rank(pct=True, method="average")
last["def_quality_pct"] = 1.0 - last.groupby("season").def_allowed_value.rank(pct=True, method="average") + 1.0 / last.groupby("season").def_allowed_value.transform("count")
teamperf = last[["season","team","off_quality_pct","def_quality_pct"]].merge(season_perf, on=["season","team"], how="left")

# Historical long source through 2018; audited ESPN staff source is authoritative for 2019-2026.
raw = pd.read_csv(RAW_STAFF)
raw["team"] = raw.team.map(canon_team)
for c in ["head_coach","offensive_coordinator","defensive_coordinator"]:
    if c not in raw.columns: raw[c] = np.nan
raw = raw[num(raw.season).between(2006,2018)][["season","team","head_coach","offensive_coordinator","defensive_coordinator"]].copy()
auth = pd.read_csv(AUTH_STAFF)
auth["team"] = auth.team.map(canon_team)
auth = auth[num(auth.season).between(2019,SEASON)].copy().rename(columns={"head_coach_espn":"head_coach"})
for c in ["head_coach","offensive_coordinator","defensive_coordinator"]:
    if c not in auth.columns: auth[c] = np.nan
auth = auth[["season","team","head_coach","offensive_coordinator","defensive_coordinator"]]
staff = pd.concat([raw,auth],ignore_index=True).drop_duplicates(["season","team"],keep="last")
staff["season"] = num(staff.season).astype(int)
staff = staff.merge(teamperf,on=["season","team"],how="left")

ROLE={"hc":("head_coach","team_season_win_pct"),"oc":("offensive_coordinator","off_quality_pct"),"dc":("defensive_coordinator","def_quality_pct")}

# Role history entering each season. Use explicit temporary columns so HC's quality field cannot
# duplicate the win-pct column and silently become a DataFrame/Series object.
role_frames=[]
for role,(name_col,quality_col) in ROLE.items():
    cols=["season","team",name_col,"team_season_win_pct","team_season_games"]
    if quality_col not in cols: cols.append(quality_col)
    work=staff[cols].copy()
    work["role_quality_input"]=num(work[quality_col])
    work["role_win_input"]=num(work["team_season_win_pct"])
    work["role_games_input"]=num(work["team_season_games"])
    work["name_key"]=work[name_col].map(norm_name)
    out=[]
    for key,g in work[work.name_key.notna()].groupby("name_key"):
        g=g.sort_values(["season","team"]); hist=[]
        for _,r in g.iterrows():
            prior=[h for h in hist if h["season"] < int(r.season)]
            qvals=[float(h["quality"]) for h in prior if pd.notna(h["quality"])]
            wvals=[float(h["win"]) for h in prior if pd.notna(h["win"])]
            games=[float(h["games"]) for h in prior if pd.notna(h["games"])]
            out.append({
                "season":int(r.season),"team":r.team,
                f"{role}_prior_role_seasons":len(set(h["season"] for h in prior)),
                f"{role}_prior_role_games":float(np.sum(games)) if games else 0.0,
                f"{role}_prior_quality":float(np.mean(qvals)) if qvals else np.nan,
                f"{role}_recent_quality":float(qvals[-1]) if qvals else np.nan,
                f"{role}_prior_win_pct":float(np.mean(wvals)) if wvals else np.nan,
            })
            hist.append({"season":int(r.season),"quality":r["role_quality_input"],"win":r["role_win_input"],"games":r["role_games_input"]})
    role_frames.append(pd.DataFrame(out))

q=staff[["season","team","head_coach","offensive_coordinator","defensive_coordinator"]].copy()
for rf in role_frames: q=q.merge(rf,on=["season","team"],how="left")

# Consecutive current-team tenure entering target season.
for role,(name_col,_) in ROLE.items():
    lookup={(int(r.season),r.team):norm_name(r[name_col]) for _,r in staff.iterrows()}
    vals=[]
    for _,r in q.iterrows():
        key=norm_name(r[name_col]); tenure=0; y=int(r.season)-1
        while key and lookup.get((y,r.team))==key:
            tenure+=1; y-=1
        vals.append(tenure)
    q[f"{role}_current_team_tenure_seasons"]=vals

prev=q[["season","team","head_coach","offensive_coordinator","defensive_coordinator"]].copy(); prev["season"]+=1
prev=prev.rename(columns={"head_coach":"prev_head_coach","offensive_coordinator":"prev_offensive_coordinator","defensive_coordinator":"prev_defensive_coordinator"})
q=q.merge(prev,on=["season","team"],how="left")

def end_quality_for(name,role,cutoff):
    key=norm_name(name)
    if not key: return np.nan
    name_col,quality_col=ROLE[role]
    h=staff[(staff[name_col].map(norm_name)==key)&(num(staff.season)<cutoff)]
    vals=num(h[quality_col]).dropna()
    return float(vals.mean()) if len(vals) else np.nan

for role,(name_col,_) in ROLE.items():
    prev_col={"hc":"prev_head_coach","oc":"prev_offensive_coordinator","dc":"prev_defensive_coordinator"}[role]
    changed=[]; deltas=[]
    for _,r in q.iterrows():
        cur,old=r.get(name_col),r.get(prev_col)
        if pd.isna(cur) or pd.isna(old): changed.append(np.nan); deltas.append(np.nan); continue
        ischg=norm_name(cur)!=norm_name(old); changed.append(float(ischg))
        if not ischg: deltas.append(0.0); continue
        curq=r.get(f"{role}_prior_quality"); oldq=end_quality_for(old,role,int(r.season))
        deltas.append(float(curq-oldq) if pd.notna(curq) and pd.notna(oldq) else np.nan)
    q[f"{role}_changed_quality"]=changed
    q[f"{role}_quality_delta_vs_departed"]=deltas

q["staff_change_count"]=q[["hc_changed_quality","oc_changed_quality","dc_changed_quality"]].fillna(0).sum(axis=1)
q["full_staff_stable"]=(q.staff_change_count.eq(0)&q[["head_coach","offensive_coordinator","defensive_coordinator"]].notna().all(axis=1)).astype(int)
q["full_staff_overhaul"]=q.staff_change_count.ge(3).astype(int)
q["staff_quality_mean"]=q[["hc_prior_quality","oc_prior_quality","dc_prior_quality"]].mean(axis=1,skipna=True)
q["staff_quality_min"]=q[["hc_prior_quality","oc_prior_quality","dc_prior_quality"]].min(axis=1,skipna=True)
up=pd.Series(0,index=q.index,dtype=int); down=pd.Series(0,index=q.index,dtype=int)
for role in ["hc","oc","dc"]:
    delta=num(q[f"{role}_quality_delta_vs_departed"])
    up += delta.gt(0).fillna(False).astype(int)
    down += delta.lt(0).fillna(False).astype(int)
q["staff_upgrade_count"]=up; q["staff_downgrade_count"]=down

q.to_csv(OUT_CTX/"coach_quality_team_seasons_dynamic.csv",index=False)
q2026=q[num(q.season).eq(SEASON)].copy()
q2026.to_csv(OUT_REPO/"coach_quality_team_seasons_2026.csv",index=False)

sched=to_pd(nfl.load_schedules([SEASON]))
if "game_type" in sched.columns: sched=sched[sched.game_type.astype(str).eq("REG")].copy()
parts=[]
for side,opp in [("home","away"),("away","home")]:
    parts.append(pd.DataFrame({"game_id":sched.game_id,"season":num(sched.season).astype(int),"week":num(sched.week).astype("Int64"),"gameday":sched.gameday,"team":sched[f"{side}_team"].map(canon_team),"opponent":sched[f"{opp}_team"].map(canon_team),"is_home":1 if side=="home" else 0}))
sides=pd.concat(parts,ignore_index=True)
team_cols=[c for c in q2026.columns if c not in ["season","team"]]
teamq=q2026.rename(columns={c:f"coachq_{c}" for c in team_cols})
oppq=q2026.rename(columns={"team":"opponent",**{c:f"opp_coachq_{c}" for c in team_cols}})
sides=sides.merge(teamq,on=["season","team"],how="left").merge(oppq,on=["season","opponent"],how="left")
numeric_base=[]
for c in team_cols:
    tc,oc=f"coachq_{c}",f"opp_coachq_{c}"
    if tc not in sides.columns or oc not in sides.columns: continue
    tv,ov=num(sides[tc]),num(sides[oc])
    if tv.notna().any() or ov.notna().any():
        sides[f"adv_{c}"]=tv-ov; numeric_base.append(c)

sides.to_parquet(OUT_CTX/"coach_quality_2026_team_sides.parquet",index=False,compression="zstd")
sides.to_csv(OUT_REPO/"coach_quality_2026_team_sides.csv",index=False)
summary={
    "generated_utc":datetime.now(timezone.utc).isoformat(),"season":SEASON,
    "team_seasons":int(len(q2026)),"teams_with_hc":int(q2026.head_coach.notna().sum()),"teams_with_oc":int(q2026.offensive_coordinator.notna().sum()),"teams_with_dc":int(q2026.defensive_coordinator.notna().sum()),
    "game_side_rows":int(len(sides)),"games":int(sides.game_id.nunique()),"opponent_relative_numeric_features":len(numeric_base),
    "runtime_parquet":str(OUT_CTX/"coach_quality_2026_team_sides.parquet"),
    "staff_source":"data/nfl/coordinator_history_espn_2019_2026.csv overlaid on historical staff <=2018",
    "quality_policy":"All coach quality/experience inputs use seasons strictly before 2026; no 2026 outcomes are used in coach quality scores."
}
(OUT_REPO/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
lines=["NFL 2026 DYNAMIC COACH QUALITY LAYER","",json.dumps(summary,indent=2,default=str),"","2026 STAFF SNAPSHOT"]
show=["team","head_coach","offensive_coordinator","defensive_coordinator","hc_prior_role_seasons","oc_prior_role_seasons","dc_prior_role_seasons","hc_current_team_tenure_seasons","oc_current_team_tenure_seasons","dc_current_team_tenure_seasons","hc_prior_quality","oc_prior_quality","dc_prior_quality","staff_quality_mean","staff_quality_min","staff_upgrade_count","staff_downgrade_count"]
for _,r in q2026.sort_values("team")[show].iterrows(): lines.append(" | ".join(f"{c}={r.get(c)}" for c in show))
(OUT_REPO/"report.txt").write_text("\n".join(lines)+"\n")
print("\n".join(lines[:40]))
