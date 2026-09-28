#!/usr/bin/env python3
"""Audit strict boxing reach integrity and provenance.

This is a hard quality gate. It audits:
- final strict-sample reach/height values for impossible ranges or static conflicts;
- supplemental reach provenance for first-party evidence or independent consensus;
- known contaminated evidence patterns;
- suspicious but possible reach==height / extreme ape-index cases as warnings.

Critical issues exit non-zero so Phase 2 cannot publish a dataset that silently
accepts invalid reach data.
"""
from __future__ import annotations
import datetime as dt,json,re,unicodedata
from collections import defaultdict
from pathlib import Path

from market_consensus import load_master,canonical_market_rows

ROOT=Path(__file__).resolve().parent
MASTER_POINTER=ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt'
OUT=ROOT/'REACH_INTEGRITY_AUDIT.json'
SUP=ROOT.parent/'profile_supplements'/'verified_profiles.jsonl'
PROBE_DIR=ROOT.parent/'profile_supplements'
EXTERNAL_PROBES=[
 ('martialbot_missing_reach_inventory.json','martialbot'),
 ('ready_to_fight_reach_probe.json','ready_to_fight'),
 ('boxnow_reach_probe.json','boxnow'),
 ('boxingdata_reach_probe.json','boxingdata'),
 ('wba_direct_reach_probe.json','wba'),
 ('boxlive_wayback_reach_probe.json','boxlive_wayback'),
]

OFFICIAL_MARKERS=(
    'official','wba_','wbc_','bkfc','matchroom','top_rank','toprank','pbc_',
    'queensberry','salita','world_boxing_council','ibf_','wbo_'
)
MULTI_MARKERS=('consensus','two_independent','three_source','multi_source')
BANNED_OUTER_EVIDENCE={'boxerlist_martialbot_reach_consensus'}

def norm(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9_]+','',x)

def parse_measure(v):
    if v is None:return None
    if isinstance(v,(int,float)):
        x=float(v)
        return x if 120<=x<=270 else None
    s=str(v).replace('½','.5').replace('¼','.25').replace('¾','.75')
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',s,re.I)
    if m:
        x=float(m.group(1));return x if 120<=x<=270 else None
    m=re.search(r'(\d+(?:\.\d+)?)\s*(?:["″]|in(?:ches)?)\b',s,re.I)
    if m:
        x=round(float(m.group(1))*2.54,2);return x if 120<=x<=270 else None
    return None

def readjsonl(path):
    out=[]
    if not path.exists():return out
    for line in path.read_text().splitlines():
        if not line.strip():continue
        try:out.append(json.loads(line))
        except Exception:pass
    return out

def evidence_summary(x):
    evidence=x.get('evidence') or []
    quality=str(x.get('quality') or '').casefold()
    families=set();numeric=[];official=False;banned=[]
    exact_false=[]
    for e in evidence:
        if not isinstance(e,dict):continue
        src=str(e.get('source') or '')
        low=src.casefold()
        if low in BANNED_OUTER_EVIDENCE:banned.append(src)
        if e.get('exact_identity') is False:exact_false.append(src)
        if src:families.add(low)
        if any(m in low or m in quality for m in OFFICIAL_MARKERS):
            official=True
        sources=e.get('sources')
        if isinstance(sources,list):
            for s in sources:
                if not isinstance(s,dict):continue
                fam=str(s.get('source') or '').casefold()
                if fam:families.add(fam)
                val=parse_measure(s.get('reported_reach_cm'))
                if val is None:val=parse_measure(s.get('reported_reach'))
                if val is not None:numeric.append((fam,val))
        # Some direct evidence stores the accepted field itself.
        val=parse_measure((e.get('fields') or {}).get('reach_cm') if isinstance(e.get('fields'),dict) else None)
        if val is not None and src:numeric.append((low,val))

    distinct_numeric_families={f for f,_ in numeric if f}
    vals=[v for _,v in numeric]
    numeric_consensus=(len(distinct_numeric_families)>=2 and len(vals)>=2 and max(vals)-min(vals)<=1.01)
    claimed_multi=any(m in quality for m in MULTI_MARKERS)
    legacy_multi=(claimed_multi and len(families)>=2)
    return {
      'official':official,'numeric_consensus':numeric_consensus,'legacy_multi':legacy_multi,
      'families':sorted(families),'numeric_values_cm':vals,'banned':banned,
      'exact_identity_false':exact_false
    }

def external_leads():
    out=defaultdict(list)
    def add(name,source,value,url=None):
        v=parse_measure(value)
        if not name or v is None:return
        key=norm(name)
        sig=(source,round(v,2),str(url or ''))
        if sig not in {(x['source'],round(x['reach_cm'],2),str(x.get('url') or '')) for x in out[key]}:
            out[key].append({'source':source,'reach_cm':round(v,2),'url':url})

    for filename,source in EXTERNAL_PROBES:
        path=PROBE_DIR/filename
        if not path.exists():continue
        try:j=json.loads(path.read_text())
        except Exception:continue
        if filename.startswith('martialbot_'):
            for x in j.get('leads') or []:add(x.get('name'),source,x.get('reach_cm'),x.get('url'))
        elif filename.startswith('ready_to_fight_'):
            for x in j.get('rows') or []:
                if x.get('status')=='lead':add(x.get('name'),source,x.get('reach_cm'),x.get('url'))
        elif filename.startswith('boxnow_'):
            for x in j.get('rows') or []:
                if x.get('status')=='lead':add(x.get('name') or x.get('h1'),source,x.get('reach_cm'),x.get('url'))
        elif filename.startswith('boxingdata_'):
            for x in j.get('leads') or []:add(x.get('name'),source,x.get('reach_cm'),x.get('url'))
        elif filename.startswith('wba_direct_'):
            for x in j.get('rows') or []:
                if x.get('status')=='accepted':add(x.get('target_name') or x.get('name'),source,x.get('reach_cm'),x.get('url'))
        elif filename.startswith('boxlive_wayback_'):
            for x in j.get('rows') or []:
                if x.get('status')=='lead':add(x.get('name'),source,x.get('reach_cm'),x.get('snapshot_url') or x.get('live_url'))
    return out

def main():
    critical=[];warnings=[]

    # Final strict sample static consistency.
    run=Path(MASTER_POINTER.read_text().strip())
    master=load_master(run/'boxing_prefight_master.jsonl')
    strict=canonical_market_rows(master,min_books=2)
    vals=defaultdict(lambda:{'names':set(),'reach':set(),'height':set()})
    for bout in strict:
        for row in (bout['favorite_row'],bout['opponent_row']):
            fighter=row.get('fighter') or {};fid=fighter.get('id')
            if not fid:continue
            name=row.get('fighter_name') or fid
            vals[fid]['names'].add(str(name))
            for raw,key in (('reach','reach_cm_static_proxy'),('height','height_cm_static_proxy')):
                v=fighter.get(key)
                if v in (None,''):continue
                try:x=round(float(v),4)
                except Exception:
                    critical.append({'type':'non_numeric_final_static_value','fighter_id':fid,'name':name,'field':raw,'value':v})
                    continue
                vals[fid][raw].add(x)

    for fid,d in vals.items():
        name=sorted(d['names'])[0] if d['names'] else fid
        if len(d['reach'])>1:
            critical.append({'type':'conflicting_final_reach_values','fighter_id':fid,'name':name,'values':sorted(d['reach'])})
        if len(d['height'])>1:
            critical.append({'type':'conflicting_final_height_values','fighter_id':fid,'name':name,'values':sorted(d['height'])})
        for r in d['reach']:
            if not 120<=r<=270:
                critical.append({'type':'implausible_final_reach','fighter_id':fid,'name':name,'reach_cm':r})
        for h in d['height']:
            if not 120<=h<=250:
                critical.append({'type':'implausible_final_height','fighter_id':fid,'name':name,'height_cm':h})
        if len(d['reach'])==1 and len(d['height'])==1:
            r=next(iter(d['reach']));h=next(iter(d['height']));delta=round(r-h,2)
            if abs(delta)<0.01:
                warnings.append({'type':'reach_equals_height','fighter_id':fid,'name':name,'height_cm':h,'reach_cm':r})
            elif delta < -20 or delta > 30:
                warnings.append({'type':'extreme_reach_height_delta','fighter_id':fid,'name':name,'height_cm':h,'reach_cm':r,'delta_cm':delta})

    # Supplemental provenance.
    supplements=readjsonl(SUP)
    supplement_reach=0;official_count=0;numeric_consensus_count=0;legacy_multi_count=0
    for x in supplements:
        r=(x.get('fields') or {}).get('reach_cm')
        if r in (None,''):continue
        supplement_reach+=1
        name=x.get('name');sid=x.get('target_source_id')
        try:rv=float(r)
        except Exception:
            critical.append({'type':'non_numeric_supplement_reach','target_source_id':sid,'name':name,'value':r});continue
        if not 120<=rv<=270:
            critical.append({'type':'implausible_supplement_reach','target_source_id':sid,'name':name,'reach_cm':rv})
        ev=evidence_summary(x)
        if ev['banned']:
            critical.append({'type':'banned_contaminated_evidence','target_source_id':sid,'name':name,
                             'reach_cm':rv,'sources':ev['banned']})
        if ev['exact_identity_false']:
            critical.append({'type':'non_exact_identity_evidence','target_source_id':sid,'name':name,
                             'reach_cm':rv,'sources':ev['exact_identity_false']})
        if ev['official']:
            official_count+=1
        elif ev['numeric_consensus']:
            numeric_consensus_count+=1
        elif ev['legacy_multi']:
            legacy_multi_count+=1
            warnings.append({'type':'legacy_multi_source_without_stored_numeric_values','target_source_id':sid,
                             'name':name,'reach_cm':rv,'families':ev['families']})
        else:
            critical.append({'type':'weak_reach_provenance','target_source_id':sid,'name':name,'reach_cm':rv,
                             'quality':x.get('quality'),'families':ev['families']})

    # Compare accepted supplemental reaches against every persisted numeric
    # external lead we currently hold. A discrepancy is a warning, not an
    # automatic deletion: official/two-source consensus can legitimately
    # overrule a weaker outlier. This makes those cases permanently visible.
    ext=external_leads()
    for x in supplements:
        r=(x.get('fields') or {}).get('reach_cm')
        if r in (None,''):continue
        try:rv=float(r)
        except Exception:continue
        leads=ext.get(norm(x.get('name'))) or []
        bad=[z for z in leads if abs(float(z['reach_cm'])-rv)>2.0]
        if bad:
            warnings.append({'type':'external_reach_disagreement','target_source_id':x.get('target_source_id'),
                             'name':x.get('name'),'verified_reach_cm':rv,'quality':x.get('quality'),
                             'conflicting_leads':bad})

    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'strict_consensus_bouts':len(strict),'strict_fighters_seen':len(vals),
      'supplement_reach_rows':supplement_reach,
      'supplement_provenance':{
        'official_or_first_party':official_count,
        'independent_numeric_consensus':numeric_consensus_count,
        'legacy_multi_source_claim_warning':legacy_multi_count,
      },
      'critical_issue_count':len(critical),'warning_count':len(warnings),
      'critical_issues':critical,'warnings':warnings,
      'policy':'Critical failure if final strict reach/height is impossible or inconsistent, if contaminated BoxerList evidence remains, if identity evidence is explicitly non-exact, or if a non-official supplement lacks defensible multi-source provenance. Reach==height is a warning only because it can be legitimate; it requires provenance review rather than automatic deletion.'
    }
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:v for k,v in report.items() if k not in ('critical_issues','warnings')},indent=2))
    if critical:
        raise SystemExit(2)

if __name__=='__main__':main()
