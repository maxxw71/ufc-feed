#!/usr/bin/env python3
"""Second-pass validation for UFC Striking + TD Defense.

Repairs age from DOB data, tests finer power-risk stability, and reconstructs
Fiorot-Grasso strictly from fights before 2026-09-12.
"""
from __future__ import annotations
import io, json, re, unicodedata, urllib.request, zipfile
from pathlib import Path
import numpy as np
import pandas as pd

OUT=Path("ufc_striking_td_postmortem")
BASE=OUT/"baseline_bets.csv"
REACH_URL="https://appwiza.com/sports/downloads/ufc/ufc-reach-method-analysis.zip"
IND_URL="https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/individuals.csv"
COMP_URL="https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/competitions.csv"
PREFIGHT_FEED_URL="https://raw.githubusercontent.com/maxxw71/ufc-feed/70dd69d0e58fc61d99d6854fe7917bdbaa341b09/upcoming.json"


def norm(v):
    x=unicodedata.normalize("NFKD",str(v))
    x="".join(ch for ch in x if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+"," ",x.lower()).split())


def as_bool(s):
    if pd.api.types.is_bool_dtype(s): return s.fillna(False).astype(bool)
    return s.astype(str).str.strip().str.lower().isin({"1","true","yes","w","win"})


def fetch_bytes(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Appwiza-UFC-Research/1.0"})
    with urllib.request.urlopen(req,timeout=120) as r:return r.read()


def fetch_csv(url):
    return pd.read_csv(io.BytesIO(fetch_bytes(url)),low_memory=False)


def fetch_zip_csv(url,suffix):
    with zipfile.ZipFile(io.BytesIO(fetch_bytes(url))) as z:
        names=[n for n in z.namelist() if n.endswith(suffix)]
        if not names: raise RuntimeError(f"missing {suffix}")
        with z.open(names[0]) as f:return pd.read_csv(f,low_memory=False)


def roi(g):
    return float(g.profit100.sum()/(100*len(g))) if len(g) else np.nan


def summ(g):
    old=g[g.event_date<pd.Timestamp("2020-01-01")]
    rec=g[g.event_date>=pd.Timestamp("2020-01-01")]
    wins=int(g.won.sum()) if len(g) else 0
    return dict(n=len(g),wins=wins,losses=len(g)-wins,
                win_rate=float(g.won.mean()) if len(g) else np.nan,roi=roi(g),
                pre_n=len(old),pre_roi=roi(old),recent_n=len(rec),recent_roi=roi(rec))


def add(rows,b,family,rule,keep):
    keep=keep.fillna(False)
    k=b[keep];rm=b[~keep]
    s=summ(k);rr=summ(rm)
    rows.append({"family":family,"rule":rule,**s,
                 "removed_n":rr["n"],"removed_wins":rr["wins"],"removed_losses":rr["losses"],
                 "removed_roi":rr["roi"],
                 "loss_capture_rate":rr["losses"]/max(1,int((~b.won).sum())),
                 "winner_removal_rate":rr["wins"]/max(1,int(b.won.sum()))})


def parse_of(v):
    m=re.search(r"(\d+)\s+of\s+(\d+)",str(v),re.I)
    return (float(m.group(1)),float(m.group(2))) if m else (0.,0.)


def parse_ctrl(v):
    m=re.match(r"\s*(\d+):(\d+)\s*$",str(v))
    return (60*int(m.group(1))+int(m.group(2)))/60 if m else 0.


def parse_num(v):
    m=re.search(r"-?\d+(?:\.\d+)?",str(v))
    return float(m.group()) if m else 0.


def fight_minutes(r):
    try:rnd=max(1,int(float(r.get("round",1) or 1)))
    except Exception:rnd=1
    m=re.match(r"(\d+):(\d+)",str(r.get("time","0:00")))
    sec=int(m.group(1))*60+int(m.group(2)) if m else 0
    return max(1,(rnd-1)*300+sec)/60


def fighter_profile(comp,name,cutoff):
    nk=norm(name);fights=[]
    for _,r in comp[comp.event_date<cutoff].iterrows():
        n1,n2=norm(r.get("player1","")),norm(r.get("player2",""))
        if nk==n1:fights.append((r,1,2))
        elif nk==n2:fights.append((r,2,1))
    q=dict(fights=0,mins=0.,sl=0.,sa=0.,abs=0.,absa=0.,kd=0.,kdabs=0.,
           tdl=0.,tda=0.,tdallowed=0.,tdfaced=0.,ctrl=0.)
    for r,side,opp in fights:
        mins=fight_minutes(r);q["fights"]+=1;q["mins"]+=mins
        for rd in range(1,6):
            a,c=parse_of(r.get(f"p{side}_rd{rd}_Sig_str"));q["sl"]+=a;q["sa"]+=c
            a,c=parse_of(r.get(f"p{opp}_rd{rd}_Sig_str"));q["abs"]+=a;q["absa"]+=c
            q["kd"]+=parse_num(r.get(f"p{side}_rd{rd}_KD"));q["kdabs"]+=parse_num(r.get(f"p{opp}_rd{rd}_KD"))
            a,c=parse_of(r.get(f"p{side}_rd{rd}_Td"));q["tdl"]+=a;q["tda"]+=c
            a,c=parse_of(r.get(f"p{opp}_rd{rd}_Td"));q["tdallowed"]+=a;q["tdfaced"]+=c
            q["ctrl"]+=parse_ctrl(r.get(f"p{side}_rd{rd}_Ctrl"))
    if not q["fights"] or not q["mins"]:return None
    m=q["mins"];sc15=15/m
    return {"fights":q["fights"],"sig_l_pm":q["sl"]/m,"sig_abs_pm":q["abs"]/m,
            "sig_diff_pm":(q["sl"]-q["abs"])/m,
            "sig_def":1-q["abs"]/q["absa"] if q["absa"] else np.nan,
            "kd15":q["kd"]*sc15,"kd_abs15":q["kdabs"]*sc15,
            "td_l15":q["tdl"]*sc15,"td_a15":q["tda"]*sc15,
            "td_def":1-q["tdallowed"]/q["tdfaced"] if q["tdfaced"] else np.nan,
            "ctrl15":q["ctrl"]*sc15}


def main():
    if not BASE.exists():raise SystemExit(f"Missing {BASE}")
    b=pd.read_csv(BASE,low_memory=False)
    b["event_date"]=pd.to_datetime(b.event_date,errors="coerce").dt.normalize()
    b["won"]=as_bool(b.won)

    print("[15%] Repairing age from DOB data",flush=True)
    reach=fetch_zip_csv(REACH_URL,"reach_market_sample.csv")
    reach["event_date"]=pd.to_datetime(reach.event_date,errors="coerce").dt.normalize()
    reach["market_fav_is_p1"]=as_bool(reach.market_fav_is_p1)
    reach["favorite"]=np.where(reach.market_fav_is_p1,reach.player1,reach.player2)
    reach["opponent"]=np.where(reach.market_fav_is_p1,reach.player2,reach.player1)
    reach["fav_url"]=np.where(reach.market_fav_is_p1,reach.player1_url,reach.player2_url)
    reach["opp_url"]=np.where(reach.market_fav_is_p1,reach.player2_url,reach.player1_url)
    reach["_favn"]=reach.favorite.map(norm);reach["_oppn"]=reach.opponent.map(norm)
    dim=reach[["event_date","_favn","_oppn","fav_url","opp_url"]].drop_duplicates(["event_date","_favn","_oppn"])
    b["_favn"]=b.favorite.map(norm);b["_oppn"]=b.opponent.map(norm)
    b=b.drop(columns=[c for c in ["fav_url","opp_url"] if c in b],errors="ignore").merge(dim,on=["event_date","_favn","_oppn"],how="left")

    ind=fetch_csv(IND_URL)
    ind["dob_dt"]=pd.to_datetime(ind.dob,errors="coerce")
    dob=dict(zip(ind.url.astype(str).str.strip(),ind.dob_dt))
    b["fav_dob"]=b.fav_url.astype(str).str.strip().map(dob)
    b["opp_dob"]=b.opp_url.astype(str).str.strip().map(dob)
    b["fav_age"]=(b.event_date-b.fav_dob).dt.days/365.2425
    b["opp_age"]=(b.event_date-b.opp_dob).dt.days/365.2425
    b["age_adv_repaired"]=b.opp_age-b.fav_age

    rows=[]
    for minadv in [-5,-4,-3,-2,-1,0,1,2]:
        add(rows,b,"AGE CONFLICT",f"keep repaired age_adv >= {minadv:+d}y",b.age_adv_repaired>=minadv)
    for older in [1,2,3,4,5]:
        risk=b.age_adv_repaired<=-older
        add(rows,b,"OLDER FAVORITE + CONSENSUS",
            f"if favorite >= {older}y older require second striking method",
            (~risk)|b.second_striking_method.astype(bool))

    print("[40%] Fine-grid power-risk stability",flush=True)
    b["power_risk_product"]=pd.to_numeric(b.o_kd15,errors="coerce")*pd.to_numeric(b.f_kd_abs15,errors="coerce")
    for threshold in [.04,.05,.06,.07,.08,.09,.10,.11,.12,.13,.14,.15,.16,.18,.20,.25]:
        add(rows,b,"POWER RISK PRODUCT",f"veto opponent KD/15 * favorite KDabs/15 >= {threshold:.2f}",
            b.power_risk_product<threshold)
    for okd in [.15,.20,.25,.30,.35,.40,.45,.50]:
        for fabs in [.05,.10,.125,.15,.20,.25]:
            add(rows,b,"POWER TWO-FACTOR",f"veto oppKD15>={okd:.3f} AND favKDabs15>={fabs:.3f}",
                ~((b.o_kd15>=okd)&(b.f_kd_abs15>=fabs)))

    base=summ(b);res=pd.DataFrame(rows)
    res["roi_change_pp"]=(res.roi-base["roi"])*100
    res["win_change_pp"]=(res.win_rate-base["win_rate"])*100
    res["sample_retained"]=res.n/len(b)
    res["promotion_screen"]=(res.n>=30)&(res.pre_n>=10)&(res.recent_n>=10)&(res.pre_roi>0)&(res.recent_roi>0)&(res.roi>=base["roi"])&(res.loss_capture_rate>=2*res.winner_removal_rate)
    res=res.sort_values(["promotion_screen","roi","n"],ascending=[False,False,False])
    res.to_csv(OUT/"age_power_extension.csv",index=False)
    b.to_csv(OUT/"baseline_bets_with_age.csv",index=False)

    losses=b[~b.won].copy()
    losscols=["event_date","favorite","opponent","market_prob","fav_age","opp_age","age_adv_repaired",
              "o_kd15","f_kd_abs15","power_risk_product","f_td_a15","f_ctrl15","o_td_a15","o_td_def","second_striking_method"]
    losses[losscols].to_csv(OUT/"historical_losses_context.csv",index=False)

    print("[65%] Reconstructing Fiorot-Grasso from pre-fight-only history",flush=True)
    comp=fetch_csv(COMP_URL);comp["event_date"]=pd.to_datetime(comp.event_date,errors="coerce").dt.normalize()
    cutoff=pd.Timestamp("2026-09-12")
    fp=fighter_profile(comp,"Manon Fiorot",cutoff);op=fighter_profile(comp,"Alexa Grasso",cutoff)
    feed=json.loads(fetch_bytes(PREFIGHT_FEED_URL))
    market=np.nan
    for e in feed.get("events",[]):
        for bout in e.get("bouts",[]):
            if {norm(bout.get("fighter_a","")),norm(bout.get("fighter_b",""))}=={norm("Manon Fiorot"),norm("Alexa Grasso")}:
                m=bout.get("market") or {};pa=m.get("consensus_no_vig_a");pb=m.get("consensus_no_vig_b")
                market=float(pa if norm(bout.get("fighter_a",""))==norm("Manon Fiorot") else pb)
    imeta=ind.copy();imeta["_n"]=imeta.name.map(norm) if "name" in imeta.columns else ""
    def getdob(name):
        z=imeta[imeta["_n"]==norm(name)]
        return pd.to_datetime(z.iloc[0].dob,errors="coerce") if len(z) else pd.NaT
    fage=(cutoff-getdob("Manon Fiorot")).days/365.2425
    oage=(cutoff-getdob("Alexa Grasso")).days/365.2425
    case={"market_prob":market,"favorite_age":fage,"opponent_age":oage,"age_adv":oage-fage}
    if fp and op:
        case.update({f"f_{k}":v for k,v in fp.items()});case.update({f"o_{k}":v for k,v in op.items()})
        case["sig_diff_gap"]=fp["sig_diff_pm"]-op["sig_diff_pm"]
        case["td_def_gap"]=fp["td_def"]-op["td_def"]
        case["power_risk_product"]=op["kd15"]*fp["kd_abs15"]
        case["pace_support"]=bool(market>=.65 and fp["sig_l_pm"]-op["sig_l_pm"]>=.25 and fp["sig_def"]-op["sig_def"]>=.15)
        case["diff_support"]=bool(market>=.75 and case["sig_diff_gap"]>=1 and fp["sig_diff_pm"]>=.5)
        case["second_striking_method"]=case["pace_support"] or case["diff_support"]
        case["baseline_qualified"]=bool(market>=.70 and fp["fights"]>=4 and op["fights"]>=4 and case["sig_diff_gap"]>=1 and case["td_def_gap"]>=.10)
        case["wrestling_relevance_2x2"]=bool(op["td_a15"]>=2 or (fp["td_a15"]>=2 and fp["ctrl15"]>=1 and op["td_def"]<=.75))
        case["older3_needs_consensus_pass"]=bool(not(case["age_adv"]<=-3) or case["second_striking_method"])
    pd.DataFrame([case]).to_csv(OUT/"fiorot_grasso_prefight_case.csv",index=False)

    stable=res[res.promotion_screen]
    lines=["UFC STRIKING + TD DEFENSE — AGE/POWER EXTENSION","="*104,"",
           f"Baseline {base['n']} bets | {base['wins']}-{base['losses']} | win {base['win_rate']*100:.1f}% | ROI {base['roi']*100:+.2f}%","",
           "Historical losses context","-"*104]
    for _,x in losses.iterrows():
        lines.append(f"{x.event_date.date()} {x.favorite} vs {x.opponent} | ageAdv={x.age_adv_repaired:+.1f}y | oppKD15={x.o_kd15:.3f} | favKDabs15={x.f_kd_abs15:.3f} | product={x.power_risk_product:.3f}")
    lines+=["","Stable extension candidates","-"*104]
    for _,x in stable.head(30).iterrows():
        lines.append(f"{x.family:<27} n={int(x.n):>3} {int(x.wins)}-{int(x.losses)} ROI={x.roi*100:+6.2f}% dROI={x.roi_change_pp:+5.2f}pp | pre={x.pre_roi*100:+6.2f}% recent={x.recent_roi*100:+6.2f}% | removed {int(x.removed_wins)}W/{int(x.removed_losses)}L | {x.rule}")
    lines+=["","Fiorot-Grasso reconstructed pre-fight case","-"*104]
    for k,v in case.items():lines.append(f"{k}: {v}")
    (OUT/"extension_report.txt").write_text("\n".join(lines))
    print("[100%] Extension complete",flush=True)


if __name__=="__main__":main()
