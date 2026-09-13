"""Fixed, disclosed diagnostics; no exclusions selected from known losses."""
import collections,json
from pathlib import Path
from scan_chronological_master import metrics
ROOT=Path(__file__).resolve().parent
run=Path((ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').read_text().strip())
audit=json.loads((run/'sample_audit.json').read_text());bad={tuple(r['key']) for r in audit if not r['structural_pass']}
rows=[r for r in audit if tuple(r['key']) not in bad and r['favorite'] and r['price']>=1.2 and r['younger_by'] is not None and r['younger_by']>=3]
# Predeclared coarse diagnostic splits, shown on both sides, not optimized.
factors={'elo_gap_at_least_100':lambda r:r['elo_gap']>=100,'experience_gap_at_least_10':lambda r:r['experience_gap']>=10,
'last5_wins_better':lambda r:r['form_gap']>=1,'own_rest_over_180':lambda r:r['rest']>180,
'opponent_rest_over_180':lambda r:r['opp_rest']>180,'opponent_stoppage_loss_last5':lambda r:r['opp_stoppage_losses']>=1,
'price_at_least_1_5':lambda r:r['price']>=1.5}
report={'rule':'favorite; age advantage >=3 years; decimal price >=1.20','status':'exploratory_unverified_prices',
'diagnostics':{name:{period:{str(flag):metrics([r for r in rows if lo<=r['year']<=hi and fn(r)==flag]) for flag in (True,False)} for period,lo,hi in [('development',2021,2023),('later',2024,2025),('partial_2026',2026,2026)]} for name,fn in factors.items()},
'losses':[r for r in rows if not r['win']],
'limitations':['These splits describe associations, not reasons or causes of defeats.','No filter promoted or bad season removed.','Dated quote verification remains required before validated ROI testing.']}
(run/'age_rule_diagnostics.json').write_text(json.dumps(report,indent=2))
print(json.dumps({'age_rule_bets':len(rows),'losses':len(report['losses'])}))
