#!/usr/bin/env python3
from __future__ import annotations
import json,re,os,hashlib
from pathlib import Path
import numpy as np,pandas as pd

ROOT=Path(".")
OUT=ROOT/"dwcs/frozen"
OUT.mkdir(parents=True,exist_ok=True)

def norm(s):
    s=str(s or "").lower().replace("’","'").replace("-"," ")
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",s)
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def dtcol(df,c):
    df[c]=pd.to_datetime(df[c],errors="coerce").dt.normalize()
    return df

def fmap(df,date_col,fighter_col):
    return {(pd.Timestamp(x[date_col]),norm(x[fighter_col])):x for _,x in df.iterrows()}

def side_prefix(d,prefix,skip):
    out={}
    for k,v in d.items():
        if k in skip: continue
        out[prefix+k]=v
    return out

def main():
    fights=dtcol(pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False),"event_date")
    age=dtcol(pd.read_csv(ROOT/"dwcs/research/age_enrichment/fight_age_features.csv",low_memory=False),"event_date")
    odds=dtcol(pd.read_csv(ROOT/"dwcs/research/historical_odds/historical_odds.csv",low_memory=False),"event_date")

    phys_path=ROOT/"dwcs/research/static_profile_merge/dwcs_fighter_physicals_merged.csv"
    phys=pd.read_csv(phys_path if phys_path.exists() else ROOT/"dwcs/research/enrichment_probe/dwcs_fighter_physicals.csv",low_memory=False)
    phys["_n"]=phys.fighter.map(norm)
    pmap={x._n:x for _,x in phys.iterrows()}

    reg_path=ROOT/"dwcs/research/regional_history_v2/dwcs_prefight_regional_features_v2.csv"
    reg=dtcol(pd.read_csv(reg_path if reg_path.exists() else ROOT/"dwcs/research/regional_history/dwcs_prefight_regional_features.csv",low_memory=False),"dwcs_date")
    sos=dtcol(pd.read_csv(ROOT/"dwcs/research/point_in_time_sos/dwcs_prefight_global_elo_sos.csv",low_memory=False),"dwcs_date")
    tech=dtcol(pd.read_csv(ROOT/"dwcs/research/historical_technical/prefight_technical_snapshots.csv",low_memory=False),"event_date")
    off=dtcol(pd.read_csv(ROOT/"dwcs/research/official_ufc_history/prefight_official_ufcstats_snapshots.csv",low_memory=False),"event_date")

    amap={(x.event_date,tuple(sorted((norm(x.fighter_a),norm(x.fighter_b))))):x for _,x in age.iterrows()}
    omap={(x.event_date,tuple(sorted((norm(x.fighter_a),norm(x.fighter_b))))):x for _,x in odds.iterrows()}
    rmap=fmap(reg,"dwcs_date","fighter")
    smap=fmap(sos,"dwcs_date","fighter")
    tmap=fmap(tech,"event_date","fighter")
    fmapoff=fmap(off,"event_date","fighter")

    rows=[]
    for _,x in fights.iterrows():
        fa,fb=x.fighter_a,x.fighter_b
        key=(x.event_date,tuple(sorted((norm(fa),norm(fb)))))
        a=amap.get(key); o=omap.get(key)
        z={
          "season":int(x.season),"event_name":x.event_name,"event_date":x.event_date.date().isoformat(),
          "fighter_a":fa,"fighter_b":fb,"winner":x.winner,
          "research_split":"discovery" if int(x.season)<=6 else "locked_validation",
          "outcome_target_only":True
        }

        # Point-in-time age.
        if a is not None:
            if norm(a.fighter_a)==norm(fa):
                z.update({"a_age":a.a_age,"b_age":a.b_age,"a_dob":a.a_dob,"b_dob":a.b_dob})
            else:
                z.update({"a_age":a.b_age,"b_age":a.a_age,"a_dob":a.b_dob,"b_dob":a.a_dob})
            z["age_gap_abs"]=abs(float(z["a_age"])-float(z["b_age"])) if pd.notna(z.get("a_age")) and pd.notna(z.get("b_age")) else np.nan

        # Historical market orientation normalized to A/B.
        if o is not None:
            sel_is_a=norm(o.selected_profile)==norm(fa)
            z.update({
              "a_open_odds":o.selected_open if sel_is_a else o.opponent_open,
              "a_close_odds":o.selected_close if sel_is_a else o.opponent_close,
              "b_open_odds":o.opponent_open if sel_is_a else o.selected_open,
              "b_close_odds":o.opponent_close if sel_is_a else o.selected_close,
              "market_source":"BestFightOdds_historical",
              "bfo_event_url":o.get("bfo_event_url",""),
              "odds_recovery_source":o.get("recovery_source","")
            })

        for side,name in [("a_",fa),("b_",fb)]:
            pm=pmap.get(norm(name))
            if pm is not None:
                for c in ["height_in","reach_in","stance","height_source","reach_source","ufc_com_verified_profile","ufc_com_url"]:
                    if c in pm:z[side+c]=pm.get(c)

            rg=rmap.get((x.event_date,norm(name)))
            if rg is not None:
                for c in ["prior_fights","prior_wins","prior_losses","prior_draws","prior_win_pct","prior_ko_wins",
                          "prior_sub_wins","prior_decision_wins","prior_finish_rate","prior_first_round_finish_rate",
                          "prior_distinct_promotions","prior_avg_opponent_win_pct","prior_days_since_last_fight",
                          "regional_history_matched","history_source"]:
                    if c in rg:z[side+c]=rg.get(c)

            sg=smap.get((x.event_date,norm(name)))
            if sg is not None:
                for c in ["prior_global_fights","prefight_global_elo","avg_opponent_pre_elo","median_opponent_pre_elo",
                          "max_opponent_pre_elo","strong_opponents_1550","elite_opponents_1600","wins_vs_1550",
                          "wins_vs_1600","quality_residual_sum","quality_residual_avg","last3_avg_opponent_elo",
                          "last5_avg_opponent_elo","days_since_last_global_fight","sos_matched"]:
                    if c in sg:z[side+c]=sg.get(c)

            tg=tmap.get((x.event_date,norm(name)))
            if tg is not None:
                for c in ["prior_technical_fights","prior_sig_landed","prior_sig_attempted","prior_sig_accuracy",
                          "prior_sig_absorbed","prior_sig_defense","prior_sig_diff","prior_td_landed",
                          "prior_td_attempted","prior_td_accuracy","prior_td_allowed","prior_td_defense",
                          "prior_control_seconds","prior_control_diff","prior_knockdowns","prior_sub_attempts"]:
                    if c in tg:z[side+"dwcs_"+c]=tg.get(c)

            og=fmapoff.get((x.event_date,norm(name)))
            if og is not None and pd.notna(og.get("prior_ufcstats_fights",np.nan)):
                for c in ["prior_ufcstats_fights","prior_minutes","sig_l_pm","sig_abs_pm","sig_diff_pm","sig_acc","sig_def",
                          "kd15","kd_abs15","td_l15","td_a15","td_acc","td_def","ctrl15","ctrl_diff15","sub15"]:
                    if c in og:z[side+"official_"+c]=og.get(c)

        # Common matchup differences used by discovery, all pre-fight.
        diffs=[
          ("age", "a_age","b_age"),
          ("height","a_height_in","b_height_in"),
          ("reach","a_reach_in","b_reach_in"),
          ("experience","a_prior_fights","b_prior_fights"),
          ("win_pct","a_prior_win_pct","b_prior_win_pct"),
          ("finish_rate","a_prior_finish_rate","b_prior_finish_rate"),
          ("opp_quality","a_prior_avg_opponent_win_pct","b_prior_avg_opponent_win_pct"),
          ("global_elo","a_prefight_global_elo","b_prefight_global_elo"),
          ("sos_avg_elo","a_avg_opponent_pre_elo","b_avg_opponent_pre_elo"),
          ("dwcs_sig_diff","a_dwcs_prior_sig_diff","b_dwcs_prior_sig_diff"),
          ("dwcs_td_def","a_dwcs_prior_td_defense","b_dwcs_prior_td_defense")
        ]
        for label,ca,cb in diffs:
            va=pd.to_numeric(pd.Series([z.get(ca)]),errors="coerce").iloc[0]
            vb=pd.to_numeric(pd.Series([z.get(cb)]),errors="coerce").iloc[0]
            z[label+"_gap_a_minus_b"]=float(va-vb) if pd.notna(va) and pd.notna(vb) else np.nan
        rows.append(z)

    master=pd.DataFrame(rows).sort_values(["event_date","event_name","fighter_a"]).reset_index(drop=True)

    # Guard against current UFC.com dynamic aggregates entering the frozen historical table.
    forbidden=["sig_str_landed_per_min","sig_str_absorbed_per_min","sig_str_defense_pct","takedown_avg_15",
               "takedown_defense_pct","submission_avg_15","knockdown_avg","record_current","age_current"]
    leaked=[c for c in master.columns if c in forbidden or c.startswith("current_")]
    if leaked:
        raise SystemExit(f"Leakage guard: forbidden current UFC.com columns in historical master: {leaked}")

    master.to_csv(OUT/"dwcs_historical_master_frozen.csv",index=False)

    # Season 10 is not allowed into discovery or locked validation.
    s10=pd.read_csv(ROOT/"dwcs/data/season10_2026_master.csv",low_memory=False)
    s10["research_split"]="prospective_locked"
    s10["eligible_for_method_tuning"]=False
    s10.to_csv(OUT/"season10_prospective_registry.csv",index=False)

    prov=[
      {"feature_family":"age_at_fight","classification":"POINT_IN_TIME","historical_use":"allowed","source":"DOB + exact fight date"},
      {"feature_family":"historical_odds","classification":"POINT_IN_TIME","historical_use":"allowed","source":"BestFightOdds historical pages"},
      {"feature_family":"height_reach_stance","classification":"STATIC_OK","historical_use":"allowed","source":"BoutMetrics + verified UFC.com static gap fill"},
      {"feature_family":"regional_career","classification":"POINT_IN_TIME","historical_use":"allowed","source":"global MMA archive strictly before fight date"},
      {"feature_family":"global_elo_sos","classification":"POINT_IN_TIME","historical_use":"allowed","source":"global MMA archive chronological Elo strictly before fight date"},
      {"feature_family":"prior_dwcs_technical","classification":"POINT_IN_TIME","historical_use":"allowed","source":"historical DWCS technical rows strictly before fight date"},
      {"feature_family":"prior_official_ufcstats","classification":"POINT_IN_TIME","historical_use":"allowed","source":"UFCStats rows strictly before fight date"},
      {"feature_family":"current_ufc_com_dynamic_metrics","classification":"CURRENT_ONLY","historical_use":"FORBIDDEN","source":"UFC.com current athlete profile"},
      {"feature_family":"fight_outcome","classification":"POST_FIGHT_TARGET","historical_use":"target_only","source":"historical result"}
    ]
    pd.DataFrame(prov).to_csv(OUT/"feature_provenance_registry.csv",index=False)

    manifest={
      "historical_rows":len(master),
      "discovery_rows":int((master.research_split=="discovery").sum()),
      "locked_validation_rows":int((master.research_split=="locked_validation").sum()),
      "season10_prospective_rows":len(s10),
      "discovery_seasons":[1,2,3,4,5,6],
      "locked_validation_seasons":[7,8,9],
      "prospective_seasons":[10],
      "leakage_guard_passed":True,
      "current_ufc_com_dynamic_metrics_in_historical_master":False,
      "master_sha256":hashlib.sha256((OUT/"dwcs_historical_master_frozen.csv").read_bytes()).hexdigest(),
      "git_sha":os.environ.get("GITHUB_SHA","")
    }
    (OUT/"freeze_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(json.dumps(manifest,indent=2))

if __name__=="__main__":main()
