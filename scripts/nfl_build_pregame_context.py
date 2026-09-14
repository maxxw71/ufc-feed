from __future__ import annotations

from pathlib import Path
from collections import Counter
import json
import os
import numpy as np
import pandas as pd

SOURCE_ROOT = Path(os.environ.get("NFL_SOURCE_ROOT", "/home/anestishkurti92/nfl-predictor-v1"))
CTX = Path(os.environ.get("NFL_CONTEXT_ROOT", "/home/appwiza-runner/nfl-context-data"))
RAW = CTX / "raw"
OUT = CTX / "derived"
OUT.mkdir(parents=True, exist_ok=True)
SCHEDULES = SOURCE_ROOT / "data" / "raw" / "schedules_2006_2026.parquet"

TEAM_MAP = {"SD":"LAC","OAK":"LV","STL":"LA","LAR":"LA"}
def canon(x):
    if pd.isna(x): return x
    return TEAM_MAP.get(str(x), str(x))

def primary(values):
    vals=[str(v) for v in values if pd.notna(v) and str(v)]
    return Counter(vals).most_common(1)[0][0] if vals else None

def safe_num(s): return pd.to_numeric(s, errors="coerce")

# ---- Schedule -> one row per team-game. Everything used below is known before kickoff. ----
s = pd.read_parquet(SCHEDULES)
s = s[s.game_type.eq("REG")].copy()
s["gameday_dt"] = pd.to_datetime(s.gameday, errors="coerce")
parts=[]
for side, opp in [("home","away"),("away","home")]:
    d=pd.DataFrame({
        "game_id":s.game_id,
        "season":safe_num(s.season).astype(int),
        "week":safe_num(s.week).astype(int),
        "gameday":s.gameday,
        "gameday_dt":s.gameday_dt,
        "team":s[f"{side}_team"],
        "opponent":s[f"{opp}_team"],
        "home_side":1 if side=="home" else 0,
        "qb_id":s.get(f"{side}_qb_id"),
        "qb_name":s.get(f"{side}_qb_name"),
        "head_coach":s.get(f"{side}_coach"),
        "moneyline":s.get(f"{side}_moneyline"),
        "opp_moneyline":s.get(f"{opp}_moneyline"),
        "rest":s.get(f"{side}_rest"),
        "opp_rest":s.get(f"{opp}_rest"),
    })
    parts.append(d)
g=pd.concat(parts,ignore_index=True)
g["team_canon"]=g.team.map(canon)
g["opponent_canon"]=g.opponent.map(canon)
g["early_season_context_only"]=(g.week <= 3).astype(int)
g["validated_rolling_methods_ready"]=(g.week >= 4).astype(int)

# ---- Primary prior-season QB and head coach continuity. ----
team_season = g.groupby(["season","team_canon"],as_index=False).agg(
    primary_qb_id=("qb_id",primary),
    primary_qb_name=("qb_name",primary),
    primary_head_coach=("head_coach",primary),
)
prev=team_season.copy()
prev["season"] += 1
prev=prev.rename(columns={
    "primary_qb_id":"prior_season_primary_qb_id",
    "primary_qb_name":"prior_season_primary_qb_name",
    "primary_head_coach":"prior_season_head_coach",
})
g=g.merge(prev,on=["season","team_canon"],how="left")
g["qb_changed_from_prior_season"] = np.where(
    g.qb_id.notna() & g.prior_season_primary_qb_id.notna(),
    g.qb_id.astype(str).ne(g.prior_season_primary_qb_id.astype(str)).astype(float), np.nan)
g["head_coach_changed"] = np.where(
    g.head_coach.notna() & g.prior_season_head_coach.notna(),
    g.head_coach.astype(str).ne(g.prior_season_head_coach.astype(str)).astype(float), np.nan)

# Prior NFL starts for this QB, excluding current game.
q=g[g.qb_id.notna()].sort_values(["gameday_dt","game_id","team_canon"]).copy()
q["qb_prior_starts"] = q.groupby("qb_id").cumcount()
g=g.merge(q[["game_id","team_canon","qb_prior_starts"]],on=["game_id","team_canon"],how="left")

# ---- Player master: QB age / experience / draft pedigree. ----
players_path=RAW/"players.parquet"
if players_path.exists():
    p=pd.read_parquet(players_path)
    cols=[c for c in ["gsis_id","birth_date","years_of_experience","rookie_season","draft_year","draft_round","draft_pick","height","weight"] if c in p.columns]
    p=p[cols].drop_duplicates("gsis_id")
    p=p.rename(columns={c:f"qb_{c}" for c in cols if c!="gsis_id"}).rename(columns={"gsis_id":"qb_id"})
    g=g.merge(p,on="qb_id",how="left")
    if "qb_birth_date" in g.columns:
        bd=pd.to_datetime(g.qb_birth_date,errors="coerce")
        g["qb_age"]=(g.gameday_dt-bd).dt.days/365.2425

# ---- Returning snap continuity from previous season, using the FIRST regular-season
# weekly roster snapshot of the target season. This avoids using later-season roster moves. ----
snaps_path=RAW/"snap_counts_2012_2026.parquet"
roster_path=RAW/"weekly_rosters_2006_2026.parquet"
if snaps_path.exists() and roster_path.exists():
    sn=pd.read_parquet(snaps_path)
    sn=sn[sn.game_type.eq("REG")].copy() if "game_type" in sn.columns else sn.copy()
    sn["team_canon"]=sn.team.map(canon)
    sn["pfr_player_id"]=sn.pfr_player_id.astype(str)
    sn["offense_snaps"]=safe_num(sn.offense_snaps).fillna(0)
    sn["defense_snaps"]=safe_num(sn.defense_snaps).fillna(0)
    sn["position_norm"]=sn.position.fillna("").astype(str).str.upper()
    sn["ol_snaps"]=np.where(sn.position_norm.isin(["C","G","LG","RG","T","LT","RT","OL","OT"]),sn.offense_snaps,0)
    sn["skill_snaps"]=np.where(sn.position_norm.isin(["WR","RB","FB","TE"]),sn.offense_snaps,0)
    prior=sn.groupby(["season","team_canon","pfr_player_id"],as_index=False).agg(
        offense_snaps=("offense_snaps","sum"), defense_snaps=("defense_snaps","sum"),
        ol_snaps=("ol_snaps","sum"), skill_snaps=("skill_snaps","sum"))
    totals=prior.groupby(["season","team_canon"],as_index=False).agg(
        prior_offense_snaps=("offense_snaps","sum"),prior_defense_snaps=("defense_snaps","sum"),
        prior_ol_snaps=("ol_snaps","sum"),prior_skill_snaps=("skill_snaps","sum"))
    prior["target_season"]=prior.season+1
    totals["target_season"]=totals.season+1

    wr=pd.read_parquet(roster_path)
    if "game_type" in wr.columns:
        wr=wr[wr.game_type.eq("REG")].copy()
    wr["team_canon"]=wr.team.map(canon)
    wr["week_num"]=safe_num(wr.week)
    minw=wr.groupby(["season","team_canon"])["week_num"].transform("min")
    wr=wr[wr.week_num.eq(minw)].copy()
    wr=wr[wr.pfr_id.notna()].copy()
    wr["pfr_player_id"]=wr.pfr_id.astype(str)
    wr=wr[["season","team_canon","pfr_player_id"]].drop_duplicates()
    m=wr.merge(prior,left_on=["season","team_canon","pfr_player_id"],right_on=["target_season","team_canon","pfr_player_id"],how="left")
    for c in ["offense_snaps","defense_snaps","ol_snaps","skill_snaps"]:
        m[c]=safe_num(m[c]).fillna(0)
    ret=m.groupby(["season_x","team_canon"],as_index=False).agg(
        returning_offense_snaps=("offense_snaps","sum"),returning_defense_snaps=("defense_snaps","sum"),
        returning_ol_snaps=("ol_snaps","sum"),returning_skill_snaps=("skill_snaps","sum"))
    ret=ret.rename(columns={"season_x":"season"})
    den=totals[["target_season","team_canon","prior_offense_snaps","prior_defense_snaps","prior_ol_snaps","prior_skill_snaps"]].rename(columns={"target_season":"season"})
    ret=ret.merge(den,on=["season","team_canon"],how="left")
    for num,denom,outc in [
        ("returning_offense_snaps","prior_offense_snaps","returning_offense_snap_share"),
        ("returning_defense_snaps","prior_defense_snaps","returning_defense_snap_share"),
        ("returning_ol_snaps","prior_ol_snaps","returning_ol_snap_share"),
        ("returning_skill_snaps","prior_skill_snaps","returning_skill_snap_share")]:
        ret[outc]=np.where(ret[denom]>0,ret[num]/ret[denom],np.nan)
    g=g.merge(ret[["season","team_canon","returning_offense_snap_share","returning_defense_snap_share","returning_ol_snap_share","returning_skill_snap_share"]],on=["season","team_canon"],how="left")

# ---- Historical final injury designation (available through 2024 only). ----
inj_path=RAW/"injuries_2009_2024.parquet"
if inj_path.exists():
    inj=pd.read_parquet(inj_path)
    inj=inj[inj.game_type.eq("REG")].copy() if "game_type" in inj.columns else inj.copy()
    inj["team_canon"]=inj.team.map(canon)
    status=inj.report_status.fillna("").astype(str).str.lower()
    weights=np.select([status.eq("out"),status.eq("doubtful"),status.eq("questionable")],[1.0,.75,.25],default=0.0)
    inj["injury_burden"]=weights
    inj["out_player"]=(status.eq("out")).astype(int)
    pos=inj.position.fillna("").astype(str).str.upper()
    inj["out_qb"]=(status.eq("out") & pos.eq("QB")).astype(int)
    inj["out_ol"]=(status.eq("out") & pos.isin(["C","G","OG","OT","T","OL"])).astype(int)
    ia=inj.groupby(["season","week","team_canon"],as_index=False).agg(
        injury_burden=("injury_burden","sum"),out_players=("out_player","sum"),out_qbs=("out_qb","sum"),out_ol=("out_ol","sum"))
    g=g.merge(ia,on=["season","week","team_canon"],how="left")
    g["injury_data_available"]=(g.season<=2024).astype(int)

# ---- Coaching staff (HC from schedules; OC/DC from audited team-season source when available). ----
staff_path=RAW/"coaching_staff_2006_2026.csv"
if staff_path.exists():
    st=pd.read_csv(staff_path)
    st["team_canon"]=st.team.map(canon)
    take=[c for c in ["season","team_canon","offensive_coordinator","defensive_coordinator","offensive_coordinator_changed","defensive_coordinator_changed","coordinator_changes","major_staff_changes","staff_source"] if c in st.columns]
    g=g.merge(st[take].drop_duplicates(["season","team_canon"]),on=["season","team_canon"],how="left")

# Descriptive flags only; thresholds are NOT production vetoes until historical validation.
g["low_offense_continuity_flag"]=(safe_num(g.get("returning_offense_snap_share"))<.65).astype("Int64") if "returning_offense_snap_share" in g else pd.Series(pd.NA,index=g.index,dtype="Int64")
g["low_defense_continuity_flag"]=(safe_num(g.get("returning_defense_snap_share"))<.65).astype("Int64") if "returning_defense_snap_share" in g else pd.Series(pd.NA,index=g.index,dtype="Int64")
g["low_ol_continuity_flag"]=(safe_num(g.get("returning_ol_snap_share"))<.65).astype("Int64") if "returning_ol_snap_share" in g else pd.Series(pd.NA,index=g.index,dtype="Int64")
g["context_only_reason"] = np.where(g.week<=3,"WEEK_1_3_CURRENT_SEASON_SAMPLE_NOT_MATURE","")

g=g.sort_values(["season","week","game_id","home_side"],ascending=[True,True,True,False])
out_path=OUT/"team_game_pregame_context_2006_2026.parquet"
g.to_parquet(out_path,index=False)
g[g.season.eq(2026)].to_csv(OUT/"team_game_pregame_context_2026.csv",index=False)

coverage={c:int(g[c].notna().sum()) for c in [
    "qb_id","qb_prior_starts","qb_changed_from_prior_season","head_coach_changed",
    "returning_offense_snap_share","returning_defense_snap_share","returning_ol_snap_share","returning_skill_snap_share",
    "offensive_coordinator","defensive_coordinator","offensive_coordinator_changed","defensive_coordinator_changed"
] if c in g.columns}
status={"rows":len(g),"games":int(g.game_id.nunique()),"season_min":int(g.season.min()),"season_max":int(g.season.max()),"coverage_non_null":coverage,"output":str(out_path),"notes":["Weeks 1-3 are context-only for rolling team methods requiring >=3 prior current-season games.","Returning snap shares use the target season's first regular-season roster snapshot and PRIOR-season snaps only.","Injury feed is intentionally marked unavailable after 2024.","Low-continuity thresholds are candidate flags, not validated betting vetoes."]}
(CTX/"NFL_PREGAME_CONTEXT_STATUS.json").write_text(json.dumps(status,indent=2))
print(json.dumps(status,indent=2))
