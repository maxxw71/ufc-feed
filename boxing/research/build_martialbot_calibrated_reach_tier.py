#!/usr/bin/env python3
"""Build a calibrated research-only reach tier from MartialBot.

This does NOT write verified_profiles. It creates a separate, explicitly
lower-trust research tier for currently missing strict reaches.

Eligibility:
- exact profile identity;
- plausible numeric reach and height;
- MartialBot reach != MartialBot height (filters its main observed failure mode);
- source calibration on already-strict fighters must have >=90% within 1 cm;
- no existing strict reach.

This tier is for sensitivity/research coverage only, never equivalent to strict
two-source reach.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
INV=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
CAL=ROOT/'profile_supplements'/'martialbot_reach_calibration.json'
OUT=ROOT/'profile_supplements'/'martialbot_calibrated_reach_tier.json'
ROWS=ROOT/'profile_supplements'/'martialbot_calibrated_reach_rows.jsonl'
UA='Mozilla/5.0 AppwizaMartialBotCalibratedReach/1.0'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read(1_500_001)
        if len(raw)>1_500_000:raise ValueError('response too large')
        return r.geturl(),raw

def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1');name=' '.join(h.stripped_strings) if h else ''
    # Strip ranking prefix like "# 5 " for exact identity comparison.
    name_clean=re.sub(r'^#\s*\d+\s*','',name).strip()
    text=' '.join(soup.stripped_strings)
    def val(label):
        m=re.search(r'\b'+label+r'\s+(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
        return float(m.group(1)) if m else None
    return name,name_clean,val('Height'),val('Reach')

def main():
    audit=json.loads(AUDIT.read_text())
    missing={x['id']:x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    inv=json.loads(INV.read_text())
    cal=json.loads(CAL.read_text())
    clean_rows=[x for x in cal.get('rows',[]) if x.get('status')=='usable' and not x.get('martialbot_reach_equals_height')]
    diffs=[abs(float(x['martialbot_reach_cm'])-float(x['strict_reach_cm'])) for x in clean_rows]
    calibration={
      'n':len(diffs),
      'within_1cm_pct':round(100*sum(d<=1 for d in diffs)/len(diffs),2) if diffs else None,
      'within_2cm_pct':round(100*sum(d<=2 for d in diffs)/len(diffs),2) if diffs else None,
      'mean_abs_diff_cm':round(sum(diffs)/len(diffs),3) if diffs else None,
      'gt5cm':sum(d>5 for d in diffs),
    }
    if not diffs or calibration['within_1cm_pct']<90:
        raise SystemExit('calibration below minimum reliability threshold')

    leads=[x for x in inv.get('leads',[]) if x.get('id') in missing]
    def one(x):
        rec={'id':x['id'],'name':x['name'],'url':x['url'],'inventory_reach_cm':x.get('reach_cm'),
             'strict_bout_appearances':x.get('strict_bout_appearances')}
        try:
            final,raw=fetch(x['url']);h,hclean,height,reach=parse(raw)
            rec.update({'final_url':final,'h1':h,'height_cm':height,'reach_cm':reach})
            if nk(hclean)!=nk(x['name']):rec['status']='identity_mismatch'
            elif height is None or reach is None:rec['status']='missing_height_or_reach'
            elif not (120<=height<=250 and 120<=reach<=270):rec['status']='implausible'
            elif abs(reach-height)<0.01:rec['status']='excluded_reach_equals_height'
            elif x.get('reach_cm') is not None and abs(float(x['reach_cm'])-reach)>0.01:rec['status']='inventory_mismatch'
            else:rec['status']='eligible'
        except Exception as e:
            rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:160]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=8) as ex:rows=list(ex.map(one,leads))
    eligible=[r for r in rows if r['status']=='eligible']
    with ROWS.open('w') as f:
        for r in eligible:
            t=missing[r['id']]
            f.write(json.dumps({
              'target_source_id':r['id'],'name':r['name'],'career_source':t.get('career_source'),
              'fields':{'reach_cm':r['reach_cm']},
              'evidence':[{'source':'martialbot_calibrated_single_source','url':r['final_url'],
                           'reported_reach_cm':r['reach_cm'],'reported_height_cm':r['height_cm'],
                           'calibration':calibration}],
              'quality':'research_only_calibrated_single_source_not_strict_verified',
              'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
            },ensure_ascii=False)+'\n')
    counts={}
    for r in rows:counts[r['status']]=counts.get(r['status'],0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'strict_missing_reach_targets':len(missing),'martialbot_missing_reach_leads':len(leads),
      'calibration':calibration,'status_counts':counts,'research_only_eligible':len(eligible),
      'projected_research_usable_strict_plus_calibrated':723-len(missing)+len(eligible),
      'projected_research_usable_pct':round(100*(723-len(missing)+len(eligible))/723,2),
      'policy':'Research-only calibrated tier. Does not alter strict reach coverage and must never be described as independently verified. Exact identity; reach != height; source clean-sample calibration >=90% within 1 cm.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
