#!/usr/bin/env python3
import json,re,unicodedata
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)
def load(p):
    return json.loads((ROOT/p).read_text())

gap=load(Path('public_reports/PRICED_IDENTITY_GAP.json'))
prof=load(Path('public_phase2/PROFILE_GAP_AUDIT.json'))
wba=load(Path('profile_supplements/wba_profile_index.json')).get('index') or {}
champ=load(Path('supplemental_careers/champinon_profile_index.json')).get('index') or {}

priced=gap.get('priority_missing') or []
def exact_unique(index,name):return len(index.get(nk(name),[]))==1
def exact_any(index,name):return len(index.get(nk(name),[]))>0

report={
 'priced_missing_reported':gap.get('missing_unique_fighters'),
 'priced_priority_rows_audited':len(priced),
 'priced_exact_unique_wba':sum(exact_unique(wba,x['name']) for x in priced),
 'priced_any_wba':sum(exact_any(wba,x['name']) for x in priced),
 'priced_exact_unique_champinon':sum(exact_unique(champ,x['name']) for x in priced),
 'priced_any_champinon':sum(exact_any(champ,x['name']) for x in priced),
 'priced_exact_unique_union':sum(exact_unique(wba,x['name']) or exact_unique(champ,x['name']) for x in priced),
 'top_exact_sources':[
   {'name':x['name'],'priced_bouts':x.get('priced_bouts'),'quote_rows':x.get('quote_rows'),
    'wba_profiles':len(wba.get(nk(x['name']),[])),'champinon_profiles':len(champ.get(nk(x['name']),[]))}
   for x in priced if exact_any(wba,x['name']) or exact_any(champ,x['name'])
 ][:100],
 'profile_gaps':{}
}
for field,items in (prof.get('missing_ranked') or {}).items():
    report['profile_gaps'][field]={
      'missing_fighters':len(items),
      'exact_unique_wba':sum(exact_unique(wba,x['name']) for x in items),
      'ambiguous_wba':sum(len(wba.get(nk(x['name']),[]))>1 for x in items),
      'exact_unique_champinon':sum(exact_unique(champ,x['name']) for x in items),
    }
print(json.dumps(report,indent=2,ensure_ascii=False))
