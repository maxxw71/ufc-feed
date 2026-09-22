#!/usr/bin/env python3
"""Fill missing nationality from official WBA/IBF ranking-country evidence.

Acceptance is intentionally conservative:
- exact normalized fighter name;
- all usable official country observations resolve to one ISO country;
- AND either at least two sanctioning bodies agree, or one body repeats the
  same country across at least three dated official snapshots;
- existing nationality is never overwritten.

This treats sanctioning-body country labels as an official nationality proxy,
kept with explicit provenance.
"""
from __future__ import annotations
import datetime as dt,json,os,re,unicodedata
from collections import defaultdict
from pathlib import Path
import pycountry

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'verified_profiles.jsonl'
REPORT=ROOT/'profile_supplements'/'ranking_nationality_backfill_report.json'
IBF_BOUTS=ROOT/'official_bouts'/'ibf_bouts.json'

FILES=[
 ('WBC',ROOT/'rankings'/'wbc_monthly_rankings.json'),
 ('WBA',ROOT/'rankings'/'wba_monthly_rankings.json'),
 ('WBA',ROOT/'rankings'/'wba_monthly_champions.json'),
 ('IBF',ROOT/'rankings'/'ibf_monthly_rankings.json'),
 ('IBF',ROOT/'rankings'/'ibf_monthly_champions.json'),
]

ALIASES={
 'UK':'GBR','GB':'GBR','ENG':'GBR','SCO':'GBR','WAL':'GBR','NIR':'GBR',
 'US':'USA','U.S.':'USA','U.S.A.':'USA',
 'DOM':'DOM','DR':'DOM','D.R.':'DOM','DOM. R.':'DOM','DOM R':'DOM',
 'RSA':'ZAF','SA':'ZAF',
 'KOR':'KOR','PR':'PRI','PUR':'PRI',
 'CZE':'CZE','GER':'DEU','DEN':'DNK','NED':'NLD','GRE':'GRC',
 'PHI':'PHL','URU':'URY','VEN':'VEN','BUL':'BGR','CRO':'HRV',
 'CHI':'CHL','ARG':'ARG','MEX':'MEX','CAN':'CAN','AUS':'AUS',
 'NZL':'NZL','JPN':'JPN','THA':'THA','CHN':'CHN','CUB':'CUB',
 'NIC':'NIC','PAN':'PAN','COL':'COL','BRA':'BRA','ESP':'ESP',
 'FRA':'FRA','ITA':'ITA','POL':'POL','RUS':'RUS','UKR':'UKR',
 'KAZ':'KAZ','UZB':'UZB','KGZ':'KGZ','MNG':'MNG','IRL':'IRL',
 'GHA':'GHA','NGA':'NGA','TZA':'TZA','UGA':'UGA','NAM':'NAM',
 'ANG':'AGO','COD':'COD','CMR':'CMR','MAR':'MAR','TUN':'TUN',
 'CRC':'CRI','HON':'HND','GUA':'GTM','SLV':'SLV','JAM':'JAM',
 'HAI':'HTI','BAH':'BHS','TTO':'TTO','BAR':'BRB'
}

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def canon_country(value):
    s=re.sub(r'\s+',' ',str(value or '')).strip(' .')
    if not s:return None
    code=ALIASES.get(s.upper(),s.upper())
    try:
        if len(code)==2:
            c=pycountry.countries.get(alpha_2=code)
        elif len(code)==3:
            c=pycountry.countries.get(alpha_3=code)
        else:
            c=pycountry.countries.lookup(s)
    except LookupError:
        # common human names
        remap={
          'RUSSIA':'RUS','SOUTH KOREA':'KOR','NORTH KOREA':'PRK',
          'UNITED STATES':'USA','UNITED KINGDOM':'GBR',
          'DOMINICAN REPUBLIC':'DOM','CZECH REPUBLIC':'CZE',
          'BOLIVIA':'BOL','VENEZUELA':'VEN','TANZANIA':'TZA',
          'MOLDOVA':'MDA','IRAN':'IRN','SYRIA':'SYR','VIETNAM':'VNM'
        }.get(s.upper())
        c=pycountry.countries.get(alpha_3=remap) if remap else None
    if not c:return None
    return {'alpha3':c.alpha_3,'name':c.name}

def main():
    audit=json.loads(AUDIT.read_text())
    targets={}
    for x in (audit.get('missing_ranked') or {}).get('nationality',[]):
        key=nk(x.get('name'))
        if key:targets[key]=x

    obs=defaultdict(list)
    for body,path in FILES:
        if not path.exists():continue
        try:rows=json.loads(path.read_text())
        except Exception:continue
        for r in rows:
            key=nk(r.get('name'))
            if key not in targets:continue
            cc=canon_country(r.get('country'))
            if not cc:continue
            obs[key].append({
              'body':body,'alpha3':cc['alpha3'],'country_name':cc['name'],
              'country_raw':r.get('country'),'source_url':r.get('source_url'),
              'safe_effective_date':r.get('safe_effective_date'),
              'division':r.get('division'),'rank':r.get('rank'),
              'status':r.get('status')
            })

    # Official IBF bout rows explicitly carry participant country codes.
    if IBF_BOUTS.exists():
        try:ibf_bouts=json.loads(IBF_BOUTS.read_text())
        except Exception:ibf_bouts=[]
        for r in ibf_bouts:
            for name_field,country_field in (('fighter_a','fighter_a_country'),('fighter_b','fighter_b_country')):
                key=nk(r.get(name_field))
                if key not in targets:continue
                cc=canon_country(r.get(country_field))
                if not cc:continue
                obs[key].append({
                  'body':'IBF_BOUT','alpha3':cc['alpha3'],'country_name':cc['name'],
                  'country_raw':r.get(country_field),'source_url':r.get('source_url'),
                  'safe_effective_date':r.get('date'),'division':r.get('weight_class'),
                  'rank':None,'status':r.get('bout_type')
                })

    existing={}
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            if not line.strip():continue
            try:
                x=json.loads(line);existing[x['target_source_id']]=x
            except Exception:pass

    accepted=[];rejected=[]
    for key,t in targets.items():
        rows=obs.get(key,[])
        countries={x['alpha3'] for x in rows}
        bodies={x['body'] for x in rows}
        if len(countries)!=1:
            rejected.append({'name':t['name'],'reason':'country_conflict_or_missing','countries':sorted(countries),'bodies':sorted(bodies)})
            continue
        alpha3=next(iter(countries))
        # One official source is acceptable only when it repeats the exact
        # country at least three times. Multiple independent official source
        # families may agree with fewer total observations.
        source_families={('IBF' if b=='IBF_BOUT' else b) for b in bodies}
        if len(source_families)<2 and len(rows)<3:
            rejected.append({'name':t['name'],'reason':'insufficient_repeated_official_evidence','observations':len(rows),'bodies':sorted(bodies)})
            continue
        country_name=rows[0]['country_name']
        sid=t['id']
        cur=existing.get(sid)
        if cur and 'nationality' in (cur.get('fields') or {}):
            continue
        evidence_by_body={}
        for body in sorted(bodies):
            samples=[x for x in rows if x['body']==body][:4]
            evidence_by_body[body]=samples
        evidence={'source':'official_ranking_country_consensus','bodies':sorted(bodies),
                  'observations':len(rows),'alpha3':alpha3,'samples':evidence_by_body}
        if cur:
            fields=dict(cur.get('fields') or {});fields['nationality']=country_name;cur['fields']=fields
            ev=list(cur.get('evidence') or []);ev.append(evidence);cur['evidence']=ev
            cur['quality']='official_profile_or_ranking_consensus_missing_fields_only'
            existing[sid]=cur
        else:
            existing[sid]={
              'target_source_id':sid,'name':t['name'],'career_source':t.get('career_source'),
              'fields':{'nationality':country_name},'evidence':[evidence],'conflicts':{},
              'quality':'official_ranking_country_consensus_exact_identity',
              'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
            }
        accepted.append({'name':t['name'],'nationality':country_name,'alpha3':alpha3,
                         'bodies':sorted(bodies),'observations':len(rows)})

    tmp=OUT.with_suffix('.jsonl.tmp')
    with tmp.open('w',encoding='utf-8') as fh:
        for x in sorted(existing.values(),key=lambda z:(z.get('name') or '',z.get('target_source_id') or '')):
            fh.write(json.dumps(x,ensure_ascii=False)+'\n')
    os.replace(tmp,OUT)

    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'missing_nationality_targets':len(targets),'targets_with_official_country_observations':len(obs),
      'accepted':len(accepted),'rejected':len(rejected),
      'accepted_items':accepted,'rejected_sample':rejected[:120],
      'policy':'Exact identity; unanimous canonical country across official WBC/WBA/IBF ranking or IBF bout evidence; two independent official source families or >=3 repeated observations from one family; existing nationality never overwritten.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ['missing_nationality_targets','targets_with_official_country_observations','accepted','rejected']},indent=2))

if __name__=='__main__':main()
