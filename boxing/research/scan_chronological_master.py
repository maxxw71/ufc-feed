"""Nested historical selection. All returns remain unverified-price arithmetic."""
import collections,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def metrics(rows):
    n=len(rows);w=sum(x['win'] for x in rows);p=sum(x['profit'] for x in rows)
    return {'bets':n,'wins':w,'win_pct':100*w/n if n else None,'profit_units':p,'roi_pct':100*p/n if n else None}

def main():
    run=Path((ROOT/'LATEST_CHRONOLOGICAL_MASTER.txt').read_text().strip())
    groups=collections.defaultdict(lambda:collections.defaultdict(dict))
    audit=collections.Counter()
    for line in (run/'boxing_prefight_master.jsonl').open():
        r=json.loads(line);a,b=r['fighter'],r['opponent']
        if not r['canonical_verified_pair'] or not b:continue
        if not all(x['record_totals_match'] and x['summary']['observed_prior_bouts']>=5 for x in (a,b)):continue
        for q in r['quotes']:
            if q['result'] not in ('WIN','LOSS') or q['decimal_price']<=1:continue
            key=(r['bout_date'],*sorted([a['id'],b['id']]))
            groups[key][q['bookmaker']].setdefault(a['id'],[]).append((r,q))
    rows=[]
    for key,books in groups.items():
        for book,sides in sorted(books.items()):
            if len(sides)!=2:continue
            # Ambiguous multiple quotes or aliases are excluded, never chosen by returns.
            if any(len(v)!=1 for v in sides.values()):continue
            pair=[v[0] for v in sides.values()]
            if {q['result'] for r,q in pair}!={'WIN','LOSS'}:continue
            for r,q in pair:
                a,b=r['fighter'],r['opponent'];s,t=a['summary'],b['summary']
                price=q['decimal_price'];other=next(x[1]['decimal_price'] for x in pair if x[0]['fighter']['id']!=a['id'])
                rows.append({'key':key,'bookmaker':book,'quote_rowid':q['quote_rowid'],'feature_bout_id':r['source_id'],'year':int(key[0][:4]),'selection':r['fighter_name'],'price':price,'favorite':price<other,
                'win':q['result']=='WIN','profit':price-1 if q['result']=='WIN' else -1,
                'elo_gap':a['elo']-b['elo'],'elo_depth':min(a['elo_prior_verified_bouts'],b['elo_prior_verified_bouts']),
                'younger_by':b['age_from_biography']-a['age_from_biography'] if a['age_from_biography'] and b['age_from_biography'] else None,
                'experience_gap':s['observed_prior_bouts']-t['observed_prior_bouts'],
                'form_gap':s['last5_observed_wins']-t['last5_observed_wins'],
                'rest':s['rest_days'],'opp_rest':t['rest_days'],
                'opp_stoppage_losses':b['extended']['last5_stoppage_losses'],
                'ko_rate':s['career_observed_ko_wins']/s['observed_prior_bouts']})
            break
    rules={}
    for floor in (1.2,1.4,1.6):
        for family,field,thresholds in [('quality','elo_gap',(50,100,150)),('age','younger_by',(3,5,7)),('experience','experience_gap',(5,10,20)),('momentum','form_gap',(1,2,3)),('power_chin','ko_rate',(.4,.6,.8))]:
            for cutoff in thresholds:
                rules[f'{family}_price{floor}_{field}{cutoff}']={'family':family,'field':field,'threshold':cutoff,'min_price':floor}
    def match(r,s):
        value=r[s['field']]
        return r['favorite'] and r['price']>=s['min_price'] and value is not None and value>=s['threshold'] and (s['family']!='quality' or r['elo_depth']>=5) and (s['family']!='power_chin' or r['opp_stoppage_losses']>=1)
    selected={name:[r for r in rows if match(r,s)] for name,s in rules.items()}
    years=sorted({r['year'] for r in rows}); outer=[]
    # Rank on preceding validation years, each requiring earlier training support.
    for year in years:
        candidates=[]
        for name,rs in selected.items():
            valid=[]
            for inner in years:
                if inner>=year:continue
                train=[r for r in rs if r['year']<inner]
                if len(train)>=20:valid.extend(r for r in rs if r['year']==inner)
            m=metrics(valid)
            if m['bets']>=20 and m['roi_pct']>0:candidates.append((m['roi_pct'],name,m))
        candidates.sort(key=lambda x:(-x[0],x[1]))
        chosen={}
        for _,name,m in candidates:chosen.setdefault(rules[name]['family'],name)
        votes=collections.Counter()
        for name in chosen.values():
            for r in selected[name]:
                if r['year']==year:votes[(tuple(r['key']),r['selection'])]+=1
        consensus={str(n):metrics([r for r in rows if r['year']==year and votes[(tuple(r['key']),r['selection'])]>=n]) for n in (2,3,4,5)}
        best=candidates[0][1] if candidates else None
        outer.append({'year':year,'selected_from_earlier_years':best,'test':metrics([r for r in selected.get(best,[]) if r['year']==year]),'family_choices':chosen,'consensus':consensus})
    report={'eligible_bouts':len({tuple(r['key']) for r in rows}),'eligible_selection_rows':len(rows),'rules_tested':len(rules),
        'status':'EXPLORATORY_ONLY_UNVERIFIED_PRICES','outer_walk_forward':outer,
        'all_rules':{name:{'definition':rules[name],'all_years':metrics(rs),'by_year':{str(y):metrics([r for r in rs if r['year']==y]) for y in range(1990,2027)}} for name,rs in selected.items()},
        'limitations':['No rule promoted. Historical quotes have no verified pre-fight timestamp or settlement rules.',
        'Nested selection requires 20 earlier training bets and 20 accumulated inner-validation bets; otherwise abstains.',
        'Prior research has examined these years; these are not pristine holdouts.',
        'Punch, sex, weight, promoter and source robustness families require additional dated coverage.']}
    (run/'rule_research.json').write_text(json.dumps(report,indent=2))
    (run/'priced_selections.json').write_text(json.dumps(rows,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='all_rules'}))
if __name__=='__main__':main()
