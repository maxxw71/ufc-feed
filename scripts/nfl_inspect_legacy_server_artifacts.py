from pathlib import Path
import json, os
import pandas as pd
# Inventory current server-side legacy method artifacts for robust re-audit.
R=Path('/home/anestishkurti92/nfl-predictor-v1/research_v2')
OUT=Path('/home/appwiza-runner/nfl-context-data/legacy_live_artifact_inspect');OUT.mkdir(parents=True,exist_ok=True)
patterns=['home_opener_stress/*.csv','*home*opener*.csv','late_season*','*late*season*.csv','*pass*rush*.csv','*regular*season*roi*.csv']
files=[]
seen=set()
for pat in patterns:
    for p in R.glob(pat):
        if p in seen or not p.is_file():continue
        seen.add(p)
        rec={'path':str(p),'size':p.stat().st_size}
        try:
            if p.suffix=='.csv':
                df=pd.read_csv(p,nrows=5,low_memory=False)
                rec['columns']=list(df.columns)
                try:
                    with p.open('rb') as f: rec['approx_lines']=sum(1 for _ in f)-1
                except: pass
                rec['head']=df.head(3).to_dict('records')
            elif p.suffix in ['.py','.txt','.json']:
                rec['text']=p.read_text(errors='ignore')[:20000]
        except Exception as e: rec['error']=type(e).__name__+': '+str(e)
        files.append(rec)
(OUT/'inventory.json').write_text(json.dumps(files,indent=2,default=str))
lines=[]
for r in files:
    lines.append('\n### '+r['path'])
    lines.append('size='+str(r.get('size'))+' rows~='+str(r.get('approx_lines')))
    if 'columns' in r: lines.append('columns='+','.join(r['columns']))
    if 'text' in r: lines.append(r['text'][:5000])
    if 'error' in r: lines.append('ERROR '+r['error'])
(OUT/'report.txt').write_text('\n'.join(lines))
print('files',len(files))
