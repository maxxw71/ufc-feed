#!/usr/bin/env python3
"""Cross-corroborate explicit reach leads from independent boxing sources.

This is a coordination layer, not a scraper. It consumes already-audited probe
outputs and creates a strict addition only when:
- the fighter is currently missing reach;
- at least two independent source families explicitly report reach;
- an agreement cluster exists within 1 cm;
- exact source URLs / identities were already verified by their collectors.

It never treats height as reach and never invents values. All explicit
disagreements remain visible in the report.
"""
from __future__ import annotations
import csv,datetime as dt,json,re,statistics,unicodedata
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
SUP=ROOT/'profile_supplements'
OUT=SUP/'cross_probe_reach_additions.jsonl'
REPORT=SUP/'cross_probe_reach_report.json'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def load(name):
    p=SUP/name
    try:return json.loads(p.read_text()) if p.exists() else {}
    except Exception:return {}

def plausible(v):
    try:x=float(v)
    except Exception:return None
    return x if 120<=x<=270 else None

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[])
             if 'reach_cm' in set(x.get('missing') or [])}
    vals=defaultdict(list)

    def add(name,source,reach,url=None,extra=None):
        key=nk(name);r=plausible(reach)
        if key not in targets or r is None:return
        vals[key].append({'source':source,'reach_cm':r,'url':url,**(extra or {})})

    # MartialBot is lead-tier and must always be corroborated.
    for x in load('martialbot_missing_reach_inventory.json').get('leads') or []:
        add(x.get('name'),'martialbot',x.get('reach_cm'),x.get('url'))

    # Ready To Fight exact-H1 lead rows.
    for x in load('ready_to_fight_reach_probe.json').get('rows') or []:
        if x.get('status')=='lead':
            add(x.get('name'),'ready_to_fight',x.get('reach_cm'),x.get('url'))

    # Current BoxNow exact-H1 lead rows.
    for x in load('boxnow_reach_probe.json').get('rows') or []:
        if x.get('status')=='lead':
            add(x.get('h1') or x.get('name'),'boxnow',x.get('reach_cm'),x.get('url'))

    # Corrected BoxerList parser: only true explicit Reach fields survive.
    for x in load('boxerlist_reach_probe.json').get('rows') or []:
        if x.get('status')=='lead':
            add(x.get('h1') or x.get('name'),'boxerlist',x.get('reach_cm'),x.get('url'))

    # Boxing Data fighter-specific preview sections.
    for x in load('boxingdata_reach_probe.json').get('leads') or []:
        add(x.get('name'),'boxingdata',x.get('reach_cm'),x.get('url'))

    # Cleaned public Boxing Data article tale-of-tape candidates. These are a
    # corroborating source family only; they can never stand alone.
    public_csv=SUP/'boxing_data_public_profile_candidates.csv'
    if public_csv.exists():
        try:
            with public_csv.open(encoding='utf-8') as fh:
                for x in csv.DictReader(fh):
                    if x.get('field')!='reach_cm':continue
                    if str(x.get('currently_missing','')).strip().lower() not in ('true','1','yes'):continue
                    add(x.get('fighter'),'boxing_data_public_articles',x.get('candidate_value'),x.get('source_url'),
                        {'raw_display':x.get('raw_value'),'quality':'public_article_explicit_reach_candidate'})
        except Exception:
            pass

    # Archived Box.Live exact identity.
    for x in load('boxlive_wayback_reach_probe.json').get('rows') or []:
        if x.get('status')=='lead':
            add(x.get('name'),'boxlive_wayback',x.get('reach_cm'),
                x.get('snapshot_url') or x.get('live_url'))

    # Official WBA exact-profile accepted rows.
    for x in load('wba_direct_reach_probe.json').get('rows') or []:
        if x.get('status')=='accepted':
            add(x.get('target_name') or x.get('name'),'wba_official',
                x.get('reach_cm'),x.get('url'))

    # Manually discovered public leads are persisted so web-indexed tale-of-
    # the-tape/profile evidence is not lost between runs. Group by source_family:
    # multiple disagreeing values from the same publisher are an internal
    # conflict and can never count as two independent sources.
    manual=SUP/'manual_public_reach_leads.jsonl'
    if manual.exists():
        for line in manual.read_text().splitlines():
            if not line.strip():continue
            try:x=json.loads(line)
            except Exception:continue
            add(x.get('name'),x.get('source_family') or x.get('source') or 'manual_public',
                x.get('reported_reach_cm'),x.get('url'),
                {'source_detail':x.get('source'),'raw_display':x.get('raw_display'),
                 'quality':x.get('quality')})

    # Additional public probe families when they expose raw lead rows.
    for fname,source in [
      ('ringside24_reach_probe.json','ringside24'),
      ('boxingshowtimes_reach_probe.json','boxingshowtimes'),
      ('sofascore_reach_probe.json','sofascore'),
      ('ring_reach_probe.json','the_ring'),
      ('sportsbetlistings_reach_probe.json','sportsbetlistings'),
    ]:
        obj=load(fname)
        candidates=(obj.get('rows') or []) + (obj.get('leads') or [])
        seen=set()
        for x in candidates:
            sig=(str(x.get('name') or x.get('h1')),str(x.get('url') or x.get('detail_url')),str(x.get('reach_cm')))
            if sig in seen:continue
            seen.add(sig)
            if x.get('status') not in (None,'lead','accepted'):continue
            add(x.get('name') or x.get('h1'),source,x.get('reach_cm'),
                x.get('url') or x.get('detail_url') or x.get('search_url'))

    additions=[];agreements=[];conflicts=[];singletons=[]
    for key,t in sorted(targets.items(),key=lambda kv:(-int(kv[1].get('strict_bout_appearances') or 0),kv[1]['name'])):
        # One explicit value per source family. If one source emitted multiple
        # different values, quarantine that source for this fighter.
        bysrc=defaultdict(list)
        for x in vals.get(key,[]):bysrc[x['source']].append(x)
        clean=[]
        source_internal_conflicts=[]
        for src,items in bysrc.items():
            uniq=sorted({round(float(x['reach_cm']),2) for x in items})
            if len(uniq)!=1:
                source_internal_conflicts.append({'source':src,'values':uniq})
                continue
            clean.append(items[0])
        if source_internal_conflicts:
            conflicts.append({'name':t['name'],'target_source_id':t['id'],
                              'reason':'source_internal_conflict',
                              'conflicts':source_internal_conflicts})
        if len(clean)<2:
            if clean:singletons.append({'name':t['name'],'sources':clean})
            continue

        best=[]
        for seed in clean:
            cluster=[x for x in clean if abs(float(x['reach_cm'])-float(seed['reach_cm']))<=1]
            if len(cluster)>len(best):best=cluster
        if len(best)<2:continue

        best_sources={x['source'] for x in best}
        outside=[x for x in clean if x['source'] not in best_sources]
        far=[x for x in outside if min(abs(float(x['reach_cm'])-float(y['reach_cm'])) for y in best)>2]
        item={'name':t['name'],'target_source_id':t['id'],
              'strict_bout_appearances':t.get('strict_bout_appearances'),
              'agreeing_sources':best,'other_explicit_sources':outside}
        agreements.append(item)
        # Keep strong conflicts visible and do not auto-promote a split source
        # picture. Two agreeing sources with no >2 cm contradictor is enough.
        if far:
            conflicts.append({**item,'reason':'external_source_conflict_gt_2cm','conflicting_sources':far})
            continue

        reach=round(statistics.median([float(x['reach_cm']) for x in best]),2)
        if float(reach).is_integer():reach=int(reach)
        additions.append({
          'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'cross_probe_reach_consensus','fields':{'reach_cm':reach},
            'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':best}],
          'conflicts':{},'quality':'cross_probe_two_source_reach_consensus_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
        })

    with OUT.open('w',encoding='utf-8') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'missing_targets':len(targets),'targets_with_any_explicit_lead':len(vals),
      'agreement_candidates':len(agreements),'accepted_additions':len(additions),
      'conflicts_quarantined':len(conflicts),'singleton_leads':len(singletons),
      'additions':[{'name':x['name'],'reach_cm':x['fields']['reach_cm'],
                    'sources':[s['source'] for s in x['evidence'][0]['sources']]} for x in additions],
      'agreements':agreements,'conflicts':conflicts,'singletons':singletons,
      'policy':'Explicit reach only; no height-as-reach inference; exact identities inherited from source probes; >=2 independent source families within 1 cm; any explicit third-source contradiction >2 cm quarantines automatic promotion.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in (
      'missing_targets','targets_with_any_explicit_lead','agreement_candidates',
      'accepted_additions','conflicts_quarantined','singleton_leads')},indent=2))

if __name__=='__main__':main()
