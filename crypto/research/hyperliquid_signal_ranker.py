#!/usr/bin/env python3
"""
Cross-sectional signal ranking for Hyperliquid primary-perp rebound setups.

Goal:
When many qualifying setups arrive together, rank them by ex-ante probability
of reaching +5% before -7.5% and allocate limited 10 x 1,000 USDC slots to the
highest-quality signals.

No future features are used. Model selection is done only on the earliest 60%
training period with time-ordered cross-validation. The latest 40% is untouched
final holdout.
"""
from __future__ import annotations

from pathlib import Path
import json
import math
import numpy as np
import pandas as pd

from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier

SRC = Path("crypto/research/results_hyperliquid_all_pairs/events.csv")
OUT = Path("crypto/research/results_signal_ranking")
OUT.mkdir(parents=True, exist_ok=True)

TARGET = "t50_before_s75_5d"

FEATURES = [
    "daily_ret5","daily_rv20","daily_rsi","trigger_rsi",
    "rsi_peak","rsi_drop_pct","rsi_pct1","rsi_pct3","rsi_accel",
    "price_pct1","price_pct3","price_dd","rsi_price_shock_ratio",
    "daily_4h_rsi_gap","rsi_to_daily_ratio","volume_ratio20",
    "range_ratio20","lower_wick_pct_range","close_location",
    "dist_ema20","dist_ema9","dist_sma9","dist_sma20","dist_sma50",
    "sma9_slope3","sma20_slope3","rsi_vs_sma3","rsi_vs_sma5",
    "rsi_vs_sma9","rsi_vs_ema5","rsi_sma5_slope1","rsi_sma5_slope3",
    "dual_stretch_9",
]

def load():
    e = pd.read_csv(SRC)
    for c in ["arm_time","trigger_time","entry_time"]:
        e[c] = pd.to_datetime(e[c], utc=True, errors="coerce")
    key = ["kind","dex","display_name","arm_time"]
    e = e.sort_values(key+["flush_threshold","trigger_time"]).drop_duplicates(key, keep="first")
    e = e[(e["kind"]=="perp") & (e["dex"]=="primary")].sort_values("entry_time").reset_index(drop=True)
    return e

def usable_features(df):
    out=[]
    for f in FEATURES:
        if f in df.columns and df[f].notna().sum() >= max(30, int(len(df)*0.2)):
            out.append(f)
    return out

def make_models():
    # Linear model for stability/calibration.
    logit = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(C=0.35, max_iter=4000, class_weight="balanced")),
    ])
    # Nonlinear models are included because wick/range/RSI interactions are
    # demonstrably non-monotonic. Complexity is intentionally constrained.
    hgb = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("clf", HistGradientBoostingClassifier(
            learning_rate=0.04, max_iter=180, max_leaf_nodes=10,
            min_samples_leaf=18, l2_regularization=2.0, random_state=7
        )),
    ])
    rf = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("clf", RandomForestClassifier(
            n_estimators=350, max_depth=5, min_samples_leaf=12,
            max_features=0.6, class_weight="balanced_subsample",
            random_state=7, n_jobs=-1
        )),
    ])
    return {"logit":logit,"hgb":hgb,"rf":rf}

def time_cv_scores(train, feats, model):
    # Expanding windows: each validation fold is strictly later than its train fold.
    n=len(train)
    cuts=[int(n*.45), int(n*.60), int(n*.75), int(n*.90), n]
    vals=[]
    start=0
    for i in range(3):
        tr_end=cuts[i]
        va_end=cuts[i+1]
        tr=train.iloc[:tr_end]
        va=train.iloc[tr_end:va_end]
        if len(tr)<80 or len(va)<25: continue
        m=clone(model)
        m.fit(tr[feats], tr[TARGET].astype(int))
        p=m.predict_proba(va[feats])[:,1]
        y=va[TARGET].astype(int).to_numpy()
        auc=roc_auc_score(y,p) if len(np.unique(y))>1 else np.nan
        brier=brier_score_loss(y,p)
        # Cross-sectional top-10 metric by 18h episode.
        tmp=va[["trigger_time",TARGET]].copy()
        tmp["p"]=p
        tmp=assign_episode(tmp,18)
        top=[]
        for _,g in tmp.groupby("episode_id"):
            q=g.sort_values("p",ascending=False).head(10)
            top.extend(q[TARGET].astype(int).tolist())
        top_rate=float(np.mean(top)) if top else np.nan
        vals.append((auc,brier,top_rate,len(va)))
    if not vals:
        return {"auc":np.nan,"brier":np.nan,"top10":np.nan}
    w=np.array([x[3] for x in vals],dtype=float);w=w/w.sum()
    return {
        "auc":float(np.nansum([x[0]*w[i] for i,x in enumerate(vals)])),
        "brier":float(np.nansum([x[1]*w[i] for i,x in enumerate(vals)])),
        "top10":float(np.nansum([x[2]*w[i] for i,x in enumerate(vals)])),
    }

def assign_episode(df,hours=18):
    x=df.sort_values("trigger_time").copy()
    ids=[];eid=0;last=None
    for t in x["trigger_time"]:
        if last is None or (t-last)>pd.Timedelta(hours=hours):
            eid+=1
        ids.append(eid);last=t
    x["episode_id"]=ids
    return x

def calibration_table(df):
    x=df.copy()
    try:
        x["score_decile"]=pd.qcut(x["score"],10,duplicates="drop")
    except Exception:
        return pd.DataFrame()
    rows=[]
    for b,g in x.groupby("score_decile",observed=True):
        rows.append({
            "score_bucket":str(b),"n":len(g),
            "mean_pred":float(g["score"].mean()),
            "actual_t5_before_s7p5":float(g[TARGET].mean()),
            "hit5_5d":float(g["hit5_5d"].mean()),
            "hit10_5d":float(g["hit10_5d"].mean()),
            "median_mfe5d":float(g["mfe_5d"].median()),
            "median_mae5d":float(g["mae_5d"].median()),
        })
    return pd.DataFrame(rows)

def topk_episode_table(df):
    x=assign_episode(df,18)
    rows=[]
    for k in [1,2,3,5,10]:
        picked=[]
        for _,g in x.groupby("episode_id"):
            picked.append(g.sort_values("score",ascending=False).head(k))
        p=pd.concat(picked,ignore_index=True) if picked else pd.DataFrame()
        if p.empty: continue
        rows.append({
            "top_k_per_episode":k,
            "signals":len(p),
            "episodes":p["episode_id"].nunique(),
            "t5_before_s7p5":float(p[TARGET].mean()),
            "hit5_5d":float(p["hit5_5d"].mean()),
            "hit10_5d":float(p["hit10_5d"].mean()),
            "median_mfe5d":float(p["mfe_5d"].median()),
            "median_mae5d":float(p["mae_5d"].median()),
        })
    return pd.DataFrame(rows)

def threshold_table(df):
    rows=[]
    for th in [0.55,0.60,0.65,0.70,0.75,0.80,0.85,0.90]:
        g=df[df["score"]>=th]
        if len(g)<5: continue
        rows.append({
            "score_min":th,"n":len(g),
            "t5_before_s7p5":float(g[TARGET].mean()),
            "hit5_5d":float(g["hit5_5d"].mean()),
            "hit10_5d":float(g["hit10_5d"].mean()),
            "median_mfe5d":float(g["mfe_5d"].median()),
            "median_mae5d":float(g["mae_5d"].median()),
        })
    return pd.DataFrame(rows)

def main():
    e=load()
    feats=usable_features(e)
    cut=max(1,int(len(e)*.60))
    train=e.iloc[:cut].copy()
    hold=e.iloc[cut:].copy()

    cv=[]
    models=make_models()
    for name,m in models.items():
        s=time_cv_scores(train,feats,m)
        cv.append({"model":name,**s})
    cvdf=pd.DataFrame(cv)
    # Select with training-only objective: maximize top-10 success first,
    # then AUC, then lower Brier.
    cvdf["select_score"]=cvdf["top10"].fillna(0)*0.60 + cvdf["auc"].fillna(.5)*0.30 + (1-cvdf["brier"].fillna(.25))*0.10
    cvdf=cvdf.sort_values("select_score",ascending=False).reset_index(drop=True)
    chosen=cvdf.iloc[0]["model"]

    model=clone(models[chosen])
    model.fit(train[feats],train[TARGET].astype(int))
    hold["score"]=model.predict_proba(hold[feats])[:,1]
    train["score"]=model.predict_proba(train[feats])[:,1]

    # A simple blend of model score + frozen-rule votes, but weights learned
    # only from train CV would be ideal. For now, report separately rather than
    # letting holdout tune the blend.
    frozen_votes=pd.DataFrame(index=hold.index)
    frozen_votes["ema9_rsiacc"]=((hold["dist_ema9"]<=-0.03649)&(hold["rsi_accel"]<=-7.63088)).astype(int)
    frozen_votes["rsi1_sma9slope"]=((hold["rsi_pct1"]<=-0.21182)&(hold["sma9_slope3"]<=0.02604)).astype(int)
    frozen_votes["rsi1_sma9stretch"]=((hold["rsi_pct1"]<=-0.21182)&(hold["dist_sma9"]<=-0.03517)).astype(int)
    frozen_votes["rsi_sma5_rsi1"]=((hold["rsi_sma5_slope1"]<=-0.05915)&(hold["rsi_pct1"]<=-0.17635)).astype(int)
    frozen_votes["ema9_lowclose"]=((hold["dist_ema9"]<=-0.03649)&(hold["close_location"]<=0.16592)).astype(int)
    hold["rule_votes"]=frozen_votes.sum(axis=1)
    hold["rank_score"]=hold["score"] + 0.03*hold["rule_votes"]

    # Score-first tables.
    cal=calibration_table(hold)
    topk=topk_episode_table(hold)
    thresh=threshold_table(hold)

    # Compare rank_score vs pure model score cross-sectionally without
    # re-fitting. rank_score is only a deterministic frozen-rule tie booster.
    rank_rows=[]
    for score_col in ["score","rank_score"]:
        x=hold.copy()
        x["score_use"]=x[score_col]
        y=assign_episode(x,18)
        for k in [1,3,5,10]:
            picks=[]
            for _,g in y.groupby("episode_id"):
                picks.append(g.sort_values("score_use",ascending=False).head(k))
            p=pd.concat(picks,ignore_index=True)
            rank_rows.append({
                "ranking":score_col,"top_k":k,"signals":len(p),"episodes":p["episode_id"].nunique(),
                "t5_before_s7p5":float(p[TARGET].mean()),
                "hit5_5d":float(p["hit5_5d"].mean()),
                "hit10_5d":float(p["hit10_5d"].mean()),
            })
    rankcmp=pd.DataFrame(rank_rows)

    cvdf.to_csv(OUT/"training_model_selection.csv",index=False)
    hold.to_csv(OUT/"holdout_scored.csv",index=False)
    cal.to_csv(OUT/"holdout_calibration.csv",index=False)
    topk.to_csv(OUT/"holdout_topk_by_episode.csv",index=False)
    thresh.to_csv(OUT/"holdout_score_thresholds.csv",index=False)
    rankcmp.to_csv(OUT/"holdout_ranking_comparison.csv",index=False)

    lines=[
        "HYPERLIQUID CROSS-SECTIONAL SIGNAL RANKING",
        "",
        f"Unique primary-perp setups: {len(e)}",
        f"Train: {len(train)} | untouched holdout: {len(hold)}",
        f"Features: {len(feats)}",
        f"Model selected on training-only time CV: {chosen}",
        "",
        "TRAINING MODEL SELECTION",
        cvdf.to_string(index=False),
        "",
        "HOLDOUT — TOP K PER 18H MARKET EPISODE",
        topk.to_string(index=False),
        "",
        "HOLDOUT — SCORE THRESHOLDS",
        thresh.to_string(index=False),
        "",
        "HOLDOUT — PURE MODEL VS FROZEN-RULE-TIE-BOOST",
        rankcmp.to_string(index=False),
        "",
        "HOLDOUT — SCORE CALIBRATION",
        cal.to_string(index=False),
        "",
        "Guardrail: latest 40% holdout is never used for model choice, feature selection, or threshold fitting.",
    ]
    (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":
    main()
