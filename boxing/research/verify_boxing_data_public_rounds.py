#!/usr/bin/env python3
"""Normalize and independently verify Boxing Data public round tables.

Only round-total observations whose summed landed/thrown counts agree with an
independent published source for both fighters are emitted as verified rows.
This tier remains weaker than a full CompuBox total+jab+power round chart.
"""
from __future__ import annotations
import json,re,math,statistics
from collections import defaultdict
from datetime import datetime,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"punch_supplements"
SOURCE=P/"boxing_data_public_stats_report.json"
OUT=P/"boxing_data_verified_round_total_observations.jsonl"
REPORT=P/"boxing_data_round_verification_report.json"

def norm(x):
    x=str(x or "").lower()
    x=re.sub(r"\b(jr|jnr|sr|ii|iii|iv)\b"," ",x)
    x=re.sub(r"[^a-z0-9]+"," ",x)
    return re.sub(r"\s+"," ",x).strip()

def core(x):
    return norm(x).replace(" ","")

def pair_key(a,b):return tuple(sorted((core(a),core(b))))

def div(a,b):return (a/b) if b else None
def pct(x):return None if x is None else 100*x

def slope(vals):
    if len(vals)<2:return None
    xs=list(range(1,len(vals)+1));xm=statistics.mean(xs);ym=statistics.mean(vals)
    den=sum((x-xm)**2 for x in xs)
    return sum((x-xm)*(y-ym) for x,y in zip(xs,vals))/den if den else None

def parse_pair(cell):
    m=re.search(r"(\d+)\s*/\s*(\d+)",str(cell or ""))
    if not m:return None
    a,b=map(int,m.groups())
    return (a,b) if 0<=a<=b else None

def token_match(header,name):
    h=norm(header);n=norm(name)
    if not h or not n:return False
    if n in h:return True
    ht=set(h.split());nt=n.split()
    if nt and nt[-1] in ht:return True
    if len(nt)>1 and nt[0] in ht and nt[-1] in ht:return True
    # Allow a unique surname abbreviation such as Hovh. for Hovhannisyan.
    if nt:
        last=nt[-1]
        for tok in ht:
            if len(tok)>=4 and (last.startswith(tok) or tok.startswith(last)):
                return True
    return False

def canonical_names(rec):
    hints=[rec.get("fighter_a_hint"),rec.get("fighter_b_hint")]
    strict=rec.get("strict_fighter_matches") or [None,None]
    out=[]
    for i in range(2):
        out.append(strict[i] if i<len(strict) and strict[i] else hints[i])
    return out

def parse_round_table(rec,table):
    m=table.get("rows") or []
    if len(m)<3:return None
    names=canonical_names(rec)
    if not all(names):return None
    by={names[0]:{},names[1]:{}}

    # Form A: "Key punch stat | Fighter A | Fighter B", rows labeled
    # "Round 1 landed / thrown".
    if len(m[0])>=3 and re.search(r"key punch stat|stat",m[0][0],re.I):
        for row in m[1:]:
            if len(row)<3:continue
            rm=re.search(r"round\s*(\d{1,2}).*landed\s*/\s*thrown",row[0],re.I)
            if not rm:continue
            rnd=int(rm.group(1))
            p1,p2=parse_pair(row[1]),parse_pair(row[2])
            if p1:by[names[0]][rnd]=p1
            if p2:by[names[1]][rnd]=p2
    else:
        header=m[0]
        if not header or not re.search(r"\bround\b",header[0],re.I):return None
        cols={}
        for fi,name in enumerate(names):
            hits=[]
            for ci,h in enumerate(header[1:],1):
                if token_match(h,name) and not re.search(r"power|jab",h,re.I):
                    hits.append(ci)
            if not hits:
                # Direct fighter-name columns (Paul / Chavez Jr.) are caught by
                # token_match; do not guess generic columns.
                continue
            cols[fi]=hits[0]
        if len(cols)<2:return None
        for row in m[1:]:
            if not row:continue
            label=str(row[0]).strip()
            mm=re.fullmatch(r"R?\s*(\d{1,2})",label,re.I) or re.match(r"Round\s*(\d{1,2})\b",label,re.I)
            if not mm:continue
            rnd=int(mm.group(1))
            for fi,ci in cols.items():
                if ci<len(row):
                    p=parse_pair(row[ci])
                    if p:by[names[fi]][rnd]=p

    common=sorted(set(by[names[0]]) & set(by[names[1]]))
    if len(common)<2:return None
    # Require contiguous history from first observed round; fragmented tables
    # like Paul-Chavez can still be useful only if all fight rounds are present.
    if common != list(range(min(common),max(common)+1)):return None
    return {"fighters":names,"rounds":common,"by":by}

def load_jsonl(path):
    if not path.exists():return []
    out=[]
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():continue
        try:out.append(json.loads(line))
        except Exception:pass
    return out

def independent_rows():
    rows=[]
    manual=P/"boxing_data_external_verifications.json"
    if manual.exists():
        try:
            m=json.loads(manual.read_text(encoding="utf-8"))
            for x in m.get("fights",[]):
                if x.get("fighter") and x.get("opponent") and x.get("bout_date"):
                    rows.append({
                      "fighter":x.get("fighter"),"opponent":x.get("opponent"),"bout_date":x.get("bout_date"),
                      "total_landed":x.get("total_landed"),"total_thrown":x.get("total_thrown"),
                      "source_url":x.get("source_url"),"source":x.get("source") or "External verification"
                    })
        except Exception:
            pass
    # Modern Ring published CompuBox summaries.
    for x in load_jsonl(P/"ring_compubox_summaries.jsonl"):
        if x.get("fighter") and x.get("opponent") and x.get("bout_date"):
            rows.append({
              "fighter":x.get("fighter"),"opponent":x.get("opponent"),"bout_date":x.get("bout_date"),
              "total_landed":x.get("total_landed"),"total_thrown":x.get("total_thrown"),
              "source_url":x.get("source_url"),"source":"Ring/CompuBox"
            })
    # BoxingScene CompuBox summaries.
    for x in load_jsonl(P/"boxingscene_compubox_summaries.jsonl"):
        f=x.get("fighter") or x.get("fighter_full_name")
        o=x.get("opponent") or x.get("opponent_full_name")
        d=x.get("bout_date")
        if f and o and d:
            rows.append({
              "fighter":f,"opponent":o,"bout_date":d,
              "total_landed":x.get("total_landed"),"total_thrown":x.get("total_thrown"),
              "source_url":x.get("source_url"),"source":"BoxingScene/CompuBox"
            })
    # Strict archived final-total tables.
    for x in load_jsonl(P/"legacy_compubox_final_totals.jsonl"):
        if x.get("fighter_full_name") and x.get("opponent_full_name") and x.get("bout_date"):
            rows.append({
              "fighter":x["fighter_full_name"],"opponent":x["opponent_full_name"],"bout_date":x["bout_date"],
              "total_landed":x.get("total_landed"),"total_thrown":x.get("total_thrown"),
              "source_url":x.get("report_url"),"source":"Archived CompuBox"
            })
    return rows

def compatible_name(a,b):
    na,nb=norm(a),norm(b)
    if na==nb or na.startswith(nb+" ") or nb.startswith(na+" "):return True
    ta,tb=na.split(),nb.split()
    if len(ta)==1 and tb and ta[0]==tb[-1]:return True
    if len(tb)==1 and ta and tb[0]==ta[-1]:return True
    return False

def match_independent(parsed,rec,ind):
    a,b=parsed["fighters"]
    candidates=[]
    for x in ind:
        if ({core(x["fighter"]),core(x["opponent"])}=={core(a),core(b)}
            or (compatible_name(x["fighter"],a) and compatible_name(x["opponent"],b))
            or (compatible_name(x["fighter"],b) and compatible_name(x["opponent"],a))):
            candidates.append(x)
    # Group by date and source URL, requiring reciprocal rows when possible.
    groups=defaultdict(list)
    for x in candidates:groups[(x["bout_date"],x.get("source_url") or "",x.get("source") or "")].append(x)
    valid=[]
    for key,items in groups.items():
        got={}
        for x in items:
            for name in (a,b):
                if compatible_name(x["fighter"],name):got[name]=x
        if len(got)<2:continue
        sums={name:(sum(parsed["by"][name][r][0] for r in parsed["rounds"]),
                    sum(parsed["by"][name][r][1] for r in parsed["rounds"])) for name in (a,b)}
        ok=True
        for name in (a,b):
            x=got[name]
            try:exp=(int(float(x["total_landed"])),int(float(x["total_thrown"])))
            except Exception:ok=False;break
            if sums[name]!=exp:ok=False;break
        if ok:valid.append((key,got,sums))
    if len(valid)==1:return valid[0]
    # A unique validated-price bout date can disambiguate identical rematches.
    pm=rec.get("validated_price_bout_matches") or []
    if len(pm)==1:
        d=pm[0].get("event_date")
        z=[v for v in valid if v[0][0]==d]
        if len(z)==1:return z[0]
    return None

def observation(fighter,opp,parsed,rec,verification):
    (bout_date,ind_url,ind_source),got,sums=verification
    rounds=parsed["rounds"];fvals=[parsed["by"][fighter][r] for r in rounds];ovals=[parsed["by"][opp][r] for r in rounds]
    fl=sum(x[0] for x in fvals);ft=sum(x[1] for x in fvals);ol=sum(x[0] for x in ovals);ot=sum(x[1] for x in ovals)
    diffs=[x[0]-y[0] for x,y in zip(fvals,ovals)]
    first3=statistics.mean(diffs[:3]) if len(diffs)>=3 else None
    last3=statistics.mean(diffs[-3:]) if len(diffs)>=3 else None
    return {
      "report_url":rec["url"],"report_id":"boxing-data-verified:"+rec["url"],
      "bout_date":bout_date,"available_from_date":rec.get("published_date") or bout_date,
      "report_title":rec.get("title"),"fighter_label":fighter,"fighter_full_name":fighter,"fighter_key":core(fighter),
      "opponent_label":opp,"opponent_full_name":opp,"opponent_key":core(opp),
      "identity_quality":"boxing_data_pair_plus_independent_exact_total_match",
      "rounds_observed":len(rounds),
      "total_landed":fl,"total_thrown":ft,"total_accuracy_pct":pct(div(fl,ft)),
      "opp_total_landed":ol,"opp_total_thrown":ot,"opp_total_accuracy_pct":pct(div(ol,ot)),
      "total_avoidance_pct":pct(1-div(ol,ot)) if ot else None,
      "total_landed_per_round":div(fl,len(rounds)),"total_thrown_per_round":div(ft,len(rounds)),
      "opp_total_landed_per_round":div(ol,len(rounds)),"net_total_landed_per_round":div(fl-ol,len(rounds)),
      "total_landed_diff_slope":slope(diffs),
      "total_round_edge_count":sum(d>0 for d in diffs),"total_round_edge_rate":div(sum(d>0 for d in diffs),len(diffs)),
      "total_first3_net_landed":first3,"total_last3_net_landed":last3,
      "total_late_vs_early_net_delta":(last3-first3) if first3 is not None and last3 is not None else None,
      "source_quality":"boxing_data_public_round_total_verified_independent",
      "independent_verification_source":ind_source,"independent_verification_url":ind_url,
      "round_edge_note":"total-punch landed edge only; not a judge score",
      "avoidance_note":"100 - opponent total connect%; proxy only",
      "raw_rounds":{str(r):list(parsed["by"][fighter][r]) for r in rounds}
    }

def main():
    src=json.loads(SOURCE.read_text(encoding="utf-8"))
    ind=independent_rows()
    verified=[];audits=[];parsed_tables=0
    for rec in src.get("records",[]):
        for table in rec.get("tables",[]):
            if table.get("type")!="round_level_punch_stats":continue
            p=parse_round_table(rec,table)
            if not p:
                audits.append({"url":rec.get("url"),"title":rec.get("title"),"status":"round_table_not_normalizable"})
                continue
            parsed_tables+=1
            v=match_independent(p,rec,ind)
            if not v:
                audits.append({"url":rec.get("url"),"title":rec.get("title"),"fighters":p["fighters"],"rounds":p["rounds"],"status":"no_two_sided_exact_independent_total_match"})
                continue
            a,b=p["fighters"]
            verified += [observation(a,b,p,rec,v),observation(b,a,p,rec,v)]
            audits.append({"url":rec.get("url"),"title":rec.get("title"),"fighters":p["fighters"],"rounds":p["rounds"],"bout_date":v[0][0],"verification_source":v[0][2],"verification_url":v[0][1],"status":"verified"})

    with OUT.open("w",encoding="utf-8") as f:
        for x in verified:f.write(json.dumps(x,ensure_ascii=False,sort_keys=True)+"\n")
    report={
      "generated_at":datetime.utcnow().isoformat()+"Z",
      "source_round_tables":sum(r.get("round_table_count",0) for r in src.get("records",[])),
      "normalizable_round_tables":parsed_tables,
      "verified_fighter_observations":len(verified),
      "verified_fights":len(verified)//2,
      "verified_unique_fighters":len({x["fighter_key"] for x in verified}),
      "source_quality":"boxing_data_public_round_total_verified_independent",
      "admission":"verified supplemental round-total tier; not counted as full total+jab+power CompuBox round chart",
      "audits":audits
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=="__main__":main()
