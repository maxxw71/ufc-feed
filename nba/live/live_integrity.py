"""Prospective NBA input guards and private immutable first-selection ledger.

No historical tables or method thresholds are modified here.
"""
import hashlib,json,math,re,sqlite3,statistics
from collections import defaultdict
from datetime import datetime,timezone,timedelta
from pathlib import Path

VERSION='nba-live-integrity-v1'
MAX_QUOTE_AGE=timedelta(minutes=30)
EAST={1,2,4,5,8,11,14,15,17,18,19,20,27,28,30}
NONBOOK=('live odds','accuscore','consensus','numberfire','teamrankings','betegy','betradar','opening')
def number(v):
    try:
        x=float(v);return x if math.isfinite(x) else None
    except (TypeError,ValueError):return None
def timestamp(v):
    try:
        t=datetime.fromisoformat(str(v).replace('Z','+00:00'))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except (TypeError,ValueError):return None
def truth(v):return str(v).lower() in ('true','1','yes')
def normalized(v):return re.sub('[^a-z0-9]','',str(v).lower())
def clock_seconds(v):
    s=str(v or '')
    try:
        m=re.fullmatch(r'PT(?:(\d+)M)?([\d.]+)S',s)
        if m:return 60*int(m.group(1) or 0)+float(m.group(2))
        if ':' in s:
            mm,ss=s.split(':');return 60*int(mm)+float(ss)
        x=number(s);return x if x is not None and x>=0 else None
    except (ValueError,TypeError):return None
def pregame(g,now):
    tip=timestamp(g.get('game_date'))
    return bool(tip and now<tip and not truth(g.get('completed')) and str(g.get('status_state','')).lower() in ('pre',''))
def observed_completed(g,now):
    seen=timestamp(g.get('ingested_at_utc'));start=timestamp(g.get('game_date'))
    return bool(truth(g.get('completed')) and start and seen and start<seen<=now and number(g.get('home_score')) is not None and number(g.get('away_score')) is not None and number(g.get('home_score'))!=number(g.get('away_score')))
def standings(games,now):
    state=defaultdict(lambda:{'w':0,'l':0,'streak':0});evidence=[]
    for g in sorted(games,key=lambda g:g.get('game_date','')):
        if not observed_completed(g,now):continue
        won=number(g['home_score'])>number(g['away_score'])
        for tid,w in ((str(g['home_team_id']),won),(str(g['away_team_id']),not won)):
            z=state[tid];z['w' if w else 'l']+=1;z['streak']=max(0,z['streak'])+1 if w else min(0,z['streak'])-1
        evidence.append({'game_id':g['game_id'],'observed_final_at':g['ingested_at_utc'],'home_score':g['home_score'],'away_score':g['away_score']})
    result={}
    for tid,z in state.items():
        wp=z['w']/(z['w']+z['l']);east=int(tid) in EAST
        vals=sorted([v['w']/(v['w']+v['l']) for t,v in state.items() if (int(t) in EAST)==east],reverse=True)
        # Compute ranks only from results actually observed before the scan.
        # This avoids the future-dated schedule standings table when H3 is live.
        conf_teams=sorted(
            ((other,s['w']/(s['w']+s['l']),s['w']) for other,s in state.items()
             if (int(other) in EAST)==east and s['w']+s['l']>0),
            key=lambda row:(row[1],row[2],row[0]),reverse=True
        )
        ranks={team:i for i,(team,_,_) in enumerate(conf_teams,1)}
        result[tid]={'current_streak':z['streak'],
                     'winpct_gap_to_seed6':wp-vals[5] if len(vals)>=6 else None,
                     'conference_rank':ranks.get(tid)}
    return result,evidence
def venue(g,catalog):
    matches=[v for v in catalog.values() if normalized(v.get('venue_name'))==normalized(g.get('arena_name')) and g.get('arena_name')]
    coords={(number(v.get('latitude')),number(v.get('longitude'))) for v in matches}
    if len(coords)!=1 or None in next(iter(coords), (None,None)):return None
    return matches[0]
def readiness(root,games,catalog,now):
    future=[g for g in games if timestamp(g.get('game_date')) and timestamp(g['game_date'])>now and not truth(g.get('completed'))]
    missing=[{'game_id':g['game_id'],'arena_name':g.get('arena_name')} for g in future if venue(g,catalog) is None]
    report={'observed_at':now.isoformat(),'scheduled_games':len(future),'matched_venues':len(future)-len(missing),'missing_venues':missing,
            'policy':'Venue coverage is checked even outside the 72-hour scan window. Travel totals require prior observed completed season games; missing inputs stay held.'}
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    temp=root/'readiness.tmp';temp.write_text(json.dumps(report,indent=2)+'\n');temp.replace(root/'readiness.json')
    return report
def miles(a,b):
    lat1,lon1,lat2,lon2=map(math.radians,[float(a['latitude']),float(a['longitude']),float(b['latitude']),float(b['longitude'])])
    z=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 2*3958.7613*math.asin(math.sqrt(min(1,max(0,z))))
def travel(g,tid,games,catalog,now):
    target=timestamp(g.get('game_date'));dest=venue(g,catalog)
    if not target or not dest:return None,{'reason':'missing_or_ambiguous_target_venue'}
    prior=sorted([x for x in games if tid in (str(x.get('home_team_id')),str(x.get('away_team_id'))) and timestamp(x.get('game_date')) and timestamp(x['game_date'])<target],key=lambda x:x['game_date'])
    if any(not observed_completed(x,now) for x in prior):return None,{'reason':'prior_game_not_observed_final'}
    if not prior:return None,{'reason':'no_prior_season_game','target_venue':dest['venue_id']}
    segments=[]
    for a,b in zip(prior,prior[1:]+[g]):
        end=timestamp(b['game_date'])
        if end<target-timedelta(days=7):continue
        va,vb=venue(a,catalog),venue(b,catalog)
        if not va or not vb:return None,{'reason':'missing_prior_venue','game_id':a['game_id']}
        segments.append({'from_game':a['game_id'],'to_game':b['game_id'],'from_venue':va['venue_id'],'to_venue':vb['venue_id'],'miles':miles(va,vb)})
    return sum(s['miles'] for s in segments),{'reason':None,'segments':segments,'target_venue':dest['venue_id']}
def valid_price(v):
    x=number(v);return x if x is not None and abs(x)>=100 else None
def implied(a):return -a/(100-a) if a<0 else 100/(100+a)
def fresh(r,tip,now):
    cap=timestamp(r.get('captured_at_utc'))
    if not cap or not tip or not (tip>now>=cap and now-cap<=MAX_QUOTE_AGE):return False
    update=r.get('bookmaker_last_update')
    if update:
        t=timestamp(update)
        if not t or t>cap or now-t>MAX_QUOTE_AGE:return False
    return True
def market(g,espn,external,now):
    """Consensus probabilities retain existing rule semantics; execution uses a real pair."""
    tip=timestamp(g.get('game_date'));pairs=[];totals=[]
    for r in espn:
        if str(r.get('game_id'))!=str(g['game_id']) or not fresh(r,tip,now):continue
        when=timestamp(r.get('scheduled_utc') or r.get('game_date'))
        if when!=tip:continue
        book=r.get('provider') or ''
        if not book or any(v in book.lower() for v in NONBOOK):continue
        q={'bookmaker':book,'captured_at_utc':r['captured_at_utc'],'source':'espn','game_id':g['game_id'],'scheduled_utc':tip.isoformat()}
        h,a=valid_price(r.get('home_moneyline')),valid_price(r.get('away_moneyline'))
        if h is not None and a is not None:pairs.append(dict(q,home=h,away=a))
        line=number(r.get('over_under'));over=valid_price(r.get('over_odds'));under=valid_price(r.get('under_odds'))
        if line is not None and over is not None and under is not None:totals.append(dict(q,line=line,over=over,under=under))
    groups=defaultdict(list)
    for r in external:
        if normalized(r.get('home_team'))!=normalized(g.get('home_team')) or normalized(r.get('away_team'))!=normalized(g.get('away_team')):continue
        if timestamp(r.get('commence_time'))!=tip or not fresh(r,tip,now):continue
        book=r.get('bookmaker_key') or r.get('bookmaker')
        if book:groups[(r.get('source_event_id'),book,r['captured_at_utc'],r.get('market'))].append(r)
    ep=[];et=[]
    for (eid,book,cap,mk),arr in groups.items():
        q={'bookmaker':book,'captured_at_utc':cap,'source':'the_odds_api','source_event_id':eid,'game_id':g['game_id'],'scheduled_utc':tip.isoformat(),'bookmaker_last_update':arr[0].get('bookmaker_last_update')}
        if mk=='h2h':
            prices={normalized(r.get('outcome')):valid_price(r.get('price')) for r in arr}
            h=prices.get(normalized(g.get('home_team')));a=prices.get(normalized(g.get('away_team')))
            if h is not None and a is not None:ep.append(dict(q,home=h,away=a))
        elif mk=='totals':
            lines=defaultdict(dict)
            for r in arr:lines[number(r.get('point'))][str(r.get('outcome')).lower()]=valid_price(r.get('price'))
            for line,v in lines.items():
                if line is not None and v.get('over') is not None and v.get('under') is not None:et.append(dict(q,line=line,**v))
    # Never mix timestamps within a book or let a partial external market erase an ESPN one.
    def latest(arr,kind):
        by={}
        for q in arr:
            k=(q['source'],q['bookmaker'],q.get('line') if kind=='total' else None)
            if k not in by or timestamp(q['captured_at_utc'])>timestamp(by[k]['captured_at_utc']):by[k]=q
        return list(by.values())
    pairs=latest(ep or pairs,'ml');totals=latest(et or totals,'total')
    hp=ap=None;hq=aq=tq=None
    if pairs:
        hm=statistics.median(q['home'] for q in pairs);am=statistics.median(q['away'] for q in pairs)
        hi,ai=implied(hm),implied(am);hp=hi/(hi+ai);ap=ai/(hi+ai)
        hq=min(pairs,key=lambda q:(abs(q['home']-hm),q['bookmaker']));aq=min(pairs,key=lambda q:(abs(q['away']-am),q['bookmaker']))
    if totals:
        lm=statistics.median(q['line'] for q in totals)
        tq=min(totals,key=lambda q:(abs(q['line']-lm),q['bookmaker'],q['line']))
    return {'home_ml':hq['home'] if hq else None,'away_ml':aq['away'] if aq else None,'home_prob':hp,'away_prob':ap,
            'total':tq['line'] if tq else None,'over_price':tq['over'] if tq else None,'under_price':tq['under'] if tq else None,
            'home_quote':hq,'away_quote':aq,'total_quote':tq,'source':'fresh_provider_quotes','books':len(pairs),
            'captured_at':max([q['captured_at_utc'] for q in pairs+totals],default=None),'quotes':pairs+totals}

def forward(root,evaluations,games,now,context):
    """Append evaluations; freeze first pre-tip qualification; record outcome revisions separately."""
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    protocol={'version':VERSION,'selection_policy':'First qualified observation per game/method/selection within the scanner 72-hour window, strictly before scheduled tip. Later prices never replace it.',
              'price_policy':'Fresh paired bookmaker observation, maximum 30 minutes old. Fixed original method thresholds; consensus probabilities used only for qualification.',
              'outcome_policy':'Append observed game-result revisions. Hold schedule changes/cancellations. Hypothetical flat-one-unit P/L, before fees; bookmaker settlement unverified.'}
    try:
        with (root/'protocol.json').open('x') as f:f.write(json.dumps(protocol,indent=2)+'\n')
    except FileExistsError:pass
    payload=json.dumps({'version':VERSION,'observed_at':now.isoformat(),'context':context,'evaluations':evaluations},sort_keys=True,separators=(',',':'))
    sha=hashlib.sha256(payload.encode()).hexdigest()
    snap=root/'snapshots'/now.strftime('%Y-%m-%d');snap.mkdir(parents=True,exist_ok=True)
    dest=snap/(sha+'.json')
    if not dest.exists():dest.write_text(payload+'\n')
    db=sqlite3.connect(root/'forward.sqlite3',timeout=30)
    with db:
        db.execute('CREATE TABLE IF NOT EXISTS selections (key TEXT PRIMARY KEY, observed_at TEXT NOT NULL, snapshot TEXT NOT NULL, payload TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS outcomes (key TEXT NOT NULL, evidence_hash TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(key,evidence_hash))')
        for ev in evaluations:
            tip=timestamp(ev.get('scheduled_utc'));q=ev.get('quote')
            if not ev.get('qualified') or not tip or now>=tip or not q or valid_price(ev.get('price')) is None:continue
            if not fresh(q,tip,now) or timestamp(q.get('scheduled_utc') or q.get('game_date'))!=tip:continue
            key='|'.join([str(ev['game_id']),ev['method_id'],str(ev.get('selection_team_id') or 'OVER')])
            db.execute('INSERT OR IGNORE INTO selections VALUES(?,?,?,?)',(key,now.isoformat(),str(dest.relative_to(root)),json.dumps(ev,sort_keys=True)))
        by={str(g['game_id']):g for g in games}
        for key,raw in db.execute('SELECT key,payload FROM selections').fetchall():
            ev=json.loads(raw);g=by.get(str(ev['game_id']))
            if not g:continue
            result={'game_id':g['game_id'],'scheduled_utc':g.get('game_date'),'source_observed_at':g.get('ingested_at_utc'),'bookmaker_settlement':'unverified','status':'pending'}
            if timestamp(g.get('game_date'))!=timestamp(ev['scheduled_utc']):result['status']='held_schedule_changed'
            elif any(s in str(g.get('status','')).lower() for s in ('cancel','postpon','suspend')):result['status']='held_event_status'
            elif observed_completed(g,now):
                h,a=number(g['home_score']),number(g['away_score']);result.update(home_score=h,away_score=a,status='game_result_observed')
                if ev['market']=='total_over':outcome='push' if h+a==ev['line'] else ('win' if h+a>ev['line'] else 'loss')
                else:outcome='win' if (h>a)==(str(ev['selection_team_id'])==str(g['home_team_id'])) else 'loss'
                price=ev['price'];result.update(outcome=outcome,hypothetical_profit_units=0 if outcome=='push' else -1 if outcome=='loss' else price/100 if price>0 else 100/abs(price))
            if result['status']=='pending':continue
            encoded=json.dumps(result,sort_keys=True);eh=hashlib.sha256(encoded.encode()).hexdigest()
            db.execute('INSERT OR IGNORE INTO outcomes VALUES(?,?,?,?)',(key,eh,now.isoformat(),encoded))
        count=db.execute('SELECT count(*) FROM selections').fetchone()[0]
        latest={}
        for key,raw in db.execute('SELECT key,payload FROM outcomes ORDER BY rowid'):latest[key]=json.loads(raw)
        methods={}
        for key,raw in db.execute('SELECT key,payload FROM selections'):
            ev=json.loads(raw);m=methods.setdefault(ev['method_id'],{'frozen':0,'wins':0,'losses':0,'pushes':0,'pending_or_held':0,'hypothetical_profit_units':0.0})
            m['frozen']+=1;out=latest.get(key,{})
            if out.get('status')!='game_result_observed':m['pending_or_held']+=1;continue
            m[{'win':'wins','loss':'losses','push':'pushes'}[out['outcome']]]+=1;m['hypothetical_profit_units']+=out['hypothetical_profit_units']
        for m in methods.values():
            decided=m['wins']+m['losses'];scored=decided+m['pushes']
            m['hit_rate']=m['wins']/decided if decided else None;m['hypothetical_roi']=m['hypothetical_profit_units']/scored if scored else None
    db.close()
    status={'version':VERSION,'observed_at':now.isoformat(),'evaluations':len(evaluations),'frozen_selections':count,'methods':methods,'snapshot':str(dest.relative_to(root)),'settlement':'Game outcomes and hypothetical flat-stake P/L; bookmaker settlement not assumed.'}
    temp=root/'latest.tmp';temp.write_text(json.dumps(status,indent=2)+'\n');temp.replace(root/'latest.json')
    return status
