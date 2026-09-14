from pathlib import Path
import pandas as pd, json, re
R=Path('/home/anestishkurti92/nfl-predictor-v1/research_v2/home_opener_stress')
OUT=Path('/home/appwiza-runner/nfl-context-data/final_method_expansion'); OUT.mkdir(parents=True,exist_ok=True)
lines=[]
def p(x=''): lines.append(str(x)); print(x,flush=True)
for name in ['sensitivity.csv','fixed_rule_equity.csv','all_losses.csv','espn_comparison.csv']:
 f=R/name
 if not f.exists(): continue
 p(f'=== {name} ===')
 try: d=pd.read_csv(f,low_memory=False)
 except Exception as e: p(e); continue
 p('cols='+','.join(d.columns))
 if name=='sensitivity.csv':
  p(d.to_string(index=True,max_rows=100,max_cols=100))
 else:
  p(d.head(20).to_string(index=False,max_cols=100))
# plain grep of likely directories for 44-7 or 51-bet rule
for f in Path('/home/anestishkurti92/nfl-predictor-v1/research_v2').rglob('*'):
 if f.suffix.lower() not in {'.txt','.md','.csv','.json'}: continue
 try:
  if f.stat().st_size>20_000_000: continue
  txt=f.read_text(errors='ignore')
 except: continue
 for pat in [r'44\s*[-–]\s*7',r'44\s*,\s*7',r'\b51\b.{0,80}\b44\b.{0,80}\b7\b']:
  m=re.search(pat,txt,re.I|re.S)
  if m:
   p(f'HIT {f}: '+txt[max(0,m.start()-300):m.end()+500].replace('\n',' ')[:1000]); break
(OUT/'fortyfour_detail.txt').write_text('\n'.join(lines)+'\n')
