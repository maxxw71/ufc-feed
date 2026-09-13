"""Detailed source-specific features. Historical counts mean observed history.

Physical attributes are current-source snapshots, not timestamp-certified
historical measurements. No career-summary totals are used as past features.
"""
import bisect,collections,datetime as dt,json,re,sqlite3,unicodedata
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def namekey(s):
    s=re.sub(r'\([^)]*\)|\[[^]]*\]','',s)
    return re.sub('[^a-z0-9]','',unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower())
def cm(s):
    s=re.sub(r'\[[^]]*\]','',str(s)).replace('½','.5').replace('¼','.25').replace('¾','.75')
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',s)
    if m:return float(m[1])
    m=re.search(r'(\d+(?:\.\d+)?)\s*m\b',s)
    if m:return float(m[1])*100
    m=re.search(r'(\d+)\s*ft\s*(\d+(?:\.\d+)?)?\s*(?:in)?',s)
    if m:return int(m[1])*30.48+float(m[2] or 0)*2.54
    m=re.search(r'(\d+(?:\.\d+)?)\s*in\b',s)
    return float(m[1])*2.54 if m else None
def age(born,date):
    try:
        value=(dt.date.fromisoformat(date)-dt.date.fromisoformat(born)).days/365.2425
        return round(value,4) if 14<=value<=65 else None
    except (ValueError,TypeError):return None
def summary(history,date):
    # Strict date comparison excludes every same-day result, including tournaments.
    hist=[r for r in history if r['date']<date]
    result={'observed_prior_bouts':len(hist),'history_complete':False,'latest_input_bout_date':hist[-1]['date'] if hist else None}
    for n in [None,3,5,10]:
        rows=hist if n is None else hist[-n:];p='career_observed' if n is None else 'last'+str(n)+'_observed'
        c=collections.Counter(r['winner'] for r in rows)
        result.update({p+'_n':len(rows),p+'_wins':c['BOXER A'],p+'_losses':c['BOXER B'],p+'_draws':c['DRAW'],p+'_no_contests':c['NO CONTEST'],p+'_ko_wins':sum(r['winner']=='BOXER A' and r['method'].upper() in ['KO','TKO','RTD'] for r in rows),p+'_stoppage_losses':sum(r['winner']=='BOXER B' and r['method'].upper() in ['KO','TKO','RTD'] for r in rows)})
        decisive=c['BOXER A']+c['BOXER B']
        result[p+'_win_fraction_decisive']=c['BOXER A']/decisive if decisive else None
    result['rest_days']=(dt.date.fromisoformat(date)-dt.date.fromisoformat(hist[-1]['date'])).days if hist else None
    result['observed_bouts_last_365_days']=sum((dt.date.fromisoformat(date)-dt.date.fromisoformat(r['date'])).days<=365 for r in hist)
    streak=0
    for r in reversed(hist):
        if r['winner']!='BOXER A':break
        streak+=1
    result['observed_win_streak']=streak
    result['previous_result']=hist[-1]['winner'] if hist else None
    result['previous_method']=hist[-1]['method'] if hist else None
    return result
def main():
    db=sqlite3.connect(ROOT/'boxing.sqlite3',timeout=120);db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS normalized_fighters(source_id TEXT PRIMARY KEY,name TEXT,born TEXT,height_cm REAL,reach_cm REAL,stance TEXT,nationality TEXT,weight_class_snapshot TEXT,source_url TEXT,quality TEXT);
CREATE TABLE IF NOT EXISTS pre_bout_features(source_id TEXT PRIMARY KEY,bout_date TEXT,fighter_id TEXT,opponent_id TEXT,pre_fight_json TEXT,outcome_json TEXT);
CREATE TABLE IF NOT EXISTS feature_builds(built_at TEXT,rows INTEGER,opponent_links INTEGER,physical_profiles INTEGER);''')
    history=collections.defaultdict(list)
    for row in db.execute("SELECT * FROM bouts WHERE source='wikipedia' AND status='FINISHED' ORDER BY date,source_id"):
        if row['method'].upper() not in ['KO','TKO','UD','SD','MD','PTS','RTD','DQ','TD','NC','NWS','DRAW','D']:continue
        history[row['url']].append(dict(row))
    profiles={};names=collections.defaultdict(set)
    for row in db.execute("SELECT * FROM fighters WHERE source='wikipedia'").fetchall():
        if row['source_id'] not in history:continue # Exclude unrelated biographies/unparsed sport pages.
        attr=json.loads(row['snapshot']).get('attributes',{})
        h=cm(attr.get('Height',''));reach=cm(attr.get('Reach',''))
        h=h if h is not None and 120<=h<=250 else None
        reach=reach if reach is not None and 120<=reach<=270 else None
        stance=attr.get('Stance');stance=next((x for x in ['orthodox','southpaw','switch'] if x in str(stance).lower()),None)
        profile=dict(name=row['name'],born=row['born'],height_cm=h,reach_cm=reach,stance=stance)
        profiles[row['source_id']]=profile;names[namekey(row['name'])].add(row['source_id'])
        db.execute('INSERT OR REPLACE INTO normalized_fighters VALUES(?,?,?,?,?,?,?,?,?,?)',(row['source_id'],row['name'],row['born'],h,reach,stance,attr.get('Nationality'),attr.get('Weight'),row['source_id'],'current_snapshot_static_attributes_require_verification'))
    db.commit()
    count=linked=0
    verified={}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='opponent_links'").fetchone():
        verified={r['bout_id']:r['opponent_id'] for r in db.execute("SELECT * FROM opponent_links WHERE evidence='reciprocal_result_confirmed'").fetchall() if r['opponent_id'] in history and r['opponent_id'] in profiles}
    for url,hist in history.items():
        own=profiles.get(url,{})
        for row in hist:
            date=row['date']
            if date<'1990-01-01':continue
            candidates=names[namekey(row['boxer_b'])];oppid=next(iter(candidates)) if len(candidates)==1 else None
            if row['source_id'] in verified:oppid=verified[row['source_id']]
            opponent=profiles.get(oppid,{})
            pre={'fighter':summary(hist,date),'opponent':summary(history[oppid],date) if oppid else None,'opponent_identity_status':'unique_normalized_name_candidate' if oppid else 'unresolved','static_attributes_historically_verified':False}
            if row['source_id'] in verified:pre['opponent_identity_status']='reciprocal_source_link_and_result'
            for side,p in [('fighter',own),('opponent',opponent)]:
                pre[side+'_age']=age(p.get('born'),date);pre[side+'_height_cm']=p.get('height_cm');pre[side+'_reach_cm']=p.get('reach_cm');pre[side+'_stance']=p.get('stance')
            for metric in ['age','height_cm','reach_cm']:
                a,b=pre['fighter_'+metric],pre['opponent_'+metric]
                pre[metric+'_advantage']=a-b if a is not None and b is not None else None
            pre['opposite_stances']=pre['fighter_stance']!=pre['opponent_stance'] if pre['fighter_stance'] in ['orthodox','southpaw'] and pre['opponent_stance'] in ['orthodox','southpaw'] else None
            outcome={'winner':row['winner'],'method':row['method'],'rounds_raw':row['rounds'],'venue':row['venue'],'source_url':row['url']}
            assert pre['fighter']['latest_input_bout_date'] is None or pre['fighter']['latest_input_bout_date']<date
            db.execute('INSERT OR REPLACE INTO pre_bout_features VALUES(?,?,?,?,?,?)',(row['source_id'],date,url,oppid,json.dumps(pre),json.dumps(outcome)))
            count+=1;linked+=bool(oppid)
        db.commit()
    stamp=dt.datetime.now(dt.timezone.utc).isoformat();db.execute('INSERT INTO feature_builds VALUES(?,?,?,?)',(stamp,count,linked,len(profiles)));db.commit()
    result={'built_at':stamp,'feature_rows':count,'opponent_candidate_links':linked,'normalized_profiles':len(profiles),'missing_height':sum(p['height_cm'] is None for p in profiles.values()),'missing_reach':sum(p['reach_cm'] is None for p in profiles.values()),'missing_stance':sum(p['stance'] is None for p in profiles.values()),'notes':['Source-specific observations, not globally deduplicated bets.','Observed history is not certified complete.','No odds-based predictions or ROI computed.','Opponent name links are candidates, not verified identities.']}
    (ROOT/'FEATURE_COVERAGE.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
