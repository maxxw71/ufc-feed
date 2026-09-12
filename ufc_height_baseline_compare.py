import pandas as pd
from pathlib import Path

SRC=Path('ufc_height_method_analysis/height_market_sample.csv')
OUT=Path('ufc_height_method_analysis/height_vs_market_baseline.txt')

df=pd.read_csv(SRC,low_memory=False)
df['market_prob']=pd.to_numeric(df['market_prob'],errors='coerce')
df['height_gap']=pd.to_numeric(df['height_gap'],errors='coerce')
df['market_fav_won']=df['market_fav_won'].astype(str).str.lower().map({'true':True,'false':False})
df['market_fav_taller']=df['market_fav_taller'].astype(str).str.lower().map({'true':True,'false':False})

lines=['HEIGHT FILTER VS MARKET-FAVORITE BASELINE','='*76,'']
for p in [.55,.60,.65,.70,.75]:
    base=df[df.market_prob>=p]
    lines.append(f'MARKET >= {p*100:.0f}%')
    lines.append(f'  all favorites: n={len(base)} win={base.market_fav_won.mean()*100:.2f}%')
    for gap in [1,2,3,4,5]:
        g=base[(base.market_fav_taller)&(base.height_gap>=gap)]
        lift=(g.market_fav_won.mean()-base.market_fav_won.mean())*100 if len(g) else float('nan')
        lines.append(f'  taller by >={gap}in: n={len(g)} win={g.market_fav_won.mean()*100:.2f}% lift={lift:+.2f}pp')
    lines.append('')

# Division-level comparisons at a practical 60/65/70 threshold and >=1 inch.
for p in [.60,.65,.70]:
    lines += [f'DIVISION LIFT @ MARKET >= {p*100:.0f}% + TALLER >=1in','-'*76]
    for div,d in df.groupby('division'):
        base=d[d.market_prob>=p]
        filt=base[(base.market_fav_taller)&(base.height_gap>=1)]
        if len(base)>=50 and len(filt)>=25:
            lift=(filt.market_fav_won.mean()-base.market_fav_won.mean())*100
            lines.append(f'{div:<24} base n={len(base):4d} {base.market_fav_won.mean()*100:5.1f}% | height n={len(filt):3d} {filt.market_fav_won.mean()*100:5.1f}% | lift={lift:+5.1f}pp')
    lines.append('')

OUT.write_text('\n'.join(lines)+'\n')
print(OUT.read_text())
