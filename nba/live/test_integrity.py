import csv,gzip,json,os,sqlite3,subprocess,sys,tempfile,unittest,ast
from datetime import datetime,timedelta,timezone
from pathlib import Path
import live_integrity as x

class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc);self.tip=self.now+timedelta(hours=2)
        self.g={'game_id':'g','game_date':self.tip.isoformat(),'home_team':'Home','away_team':'Away','home_team_id':'1','away_team_id':'2','status_state':'pre','completed':'False','arena_name':'A'}
        self.q={'game_id':'g','game_date':self.tip.isoformat(),'captured_at_utc':self.now.isoformat(),'provider':'Book','home_moneyline':-150,'away_moneyline':130,'over_under':210,'over_odds':-110,'under_odds':-110}
    def test_clock_formats(self):
        for value,expected in [('55.3',55.3),('0.0',0),('2:03',123),('PT2M3.00S',123),('PT5S',5),('bad',None),('-1',None)]:self.assertEqual(x.clock_seconds(value),expected)
    def test_tip_is_strict(self):
        self.assertTrue(x.pregame(self.g,self.now));self.assertFalse(x.pregame(self.g,self.tip));self.assertFalse(x.pregame(dict(self.g,status_state='in'),self.now))
    def test_no_unobserved_final(self):
        g=dict(self.g,game_date=(self.now-timedelta(hours=3)).isoformat(),completed=True,home_score=110,away_score=100,ingested_at_utc=(self.now+timedelta(seconds=1)).isoformat())
        self.assertEqual(x.standings([g],self.now)[0],{})
        g['ingested_at_utc']=self.now.isoformat();self.assertEqual(x.standings([g],self.now)[0]['1']['current_streak'],1)
    def test_new_live_promotions_have_bookmaker_and_observed_state_guards(self):
        root=Path(__file__).resolve().parents[1]
        arsenal=json.loads((root/'live/arsenal.json').read_text())
        ids={m['method_id'] for m in arsenal['methods']}
        self.assertIn('NBA_H3_OVER_002',ids)
        self.assertIn('NBA_STAND_002_STARTPM',ids)
        scanner=(root/'live/scan_live_methods.py').read_text()
        for mid in ('NBA_H3_OVER_002','NBA_STAND_002_STARTPM'):
            self.assertIn('"method_id":"'+mid+'"',scanner) if mid=='NBA_H3_OVER_002' else self.assertIn('"'+mid+'":[',scanner)
        self.assertIn('h_rank=val(live_stand.get(hid,{}),"conference_rank")',scanner)
        self.assertIn('h3quote=ms.get("total_quote")',scanner)
        self.assertIn('integrity.valid_price(ms["over_price"])',scanner)
        # Prospective selections require an actual accepted quote; the frozen
        # forward ledger must receive the primary live evaluations, not shadows.
        self.assertIn('evaluations.append(h3ev)',scanner)
        self.assertIn('evaluations.append(ev)',scanner)

    def test_observed_only_standings_ranks(self):
        t=self.now
        completed=[]
        for i,(home,away,hs,aw) in enumerate([
            ('1','2',111,99),('4','5',97,102),('8','11',112,93)]):
            completed.append({
                'game_id':str(i),'home_team_id':home,'away_team_id':away,
                'game_date':(t-timedelta(days=2+i)).isoformat(),
                'ingested_at_utc':(t-timedelta(days=1)).isoformat(),
                'completed':True,'home_score':hs,'away_score':aw
            })
        future=dict(completed[0],game_id='future',home_team_id='1',away_team_id='4',
                    game_date=(t+timedelta(hours=1)).isoformat(),
                    ingested_at_utc=(t+timedelta(hours=2)).isoformat(),home_score=0,away_score=125)
        states,_=x.standings(completed+[future],t)
        self.assertEqual(states['1']['conference_rank'],1)
        self.assertEqual(states['4']['conference_rank'],2)
        self.assertTrue(all(s['conference_rank']>=1 for s in states.values()))

    def test_stale_and_future_quotes(self):
        for age in (-1,31):
            q=dict(self.q,captured_at_utc=(self.now-timedelta(minutes=age)).isoformat())
            self.assertIsNone(x.market(self.g,[q],[],self.now)['home_ml'])
    def test_rescheduled_quote_rejected(self):
        q=dict(self.q,game_date=(self.tip+timedelta(days=1)).isoformat())
        self.assertIsNone(x.market(self.g,[q],[],self.now)['home_ml'])
    def test_no_posttip_price(self):self.assertIsNone(x.market(self.g,[self.q],[],self.tip)['home_ml'])
    def test_nonbook_and_invalid_price(self):
        for q in (dict(self.q,provider='Live Odds'),dict(self.q,home_moneyline=12),dict(self.q,home_moneyline='NaN')):
            self.assertIsNone(x.market(self.g,[q],[],self.now)['home_ml'])
    def test_real_prices_not_synthetic_median(self):
        m=x.market(self.g,[self.q,dict(self.q,provider='Other',home_moneyline=-180)],[],self.now)
        self.assertIn(m['home_ml'],(-150,-180));self.assertNotEqual(m['home_ml'],-165)
    def test_total_line_price_stay_paired(self):
        m=x.market(self.g,[self.q,dict(self.q,provider='Other',over_under=214,over_odds=-120)],[],self.now)
        self.assertIn((m['total'],m['over_price']),((210,-110),(214,-120)))
    def test_partial_external_keeps_espn_total(self):
        ext=[{'home_team':'Home','away_team':'Away','commence_time':self.tip.isoformat(),'captured_at_utc':self.now.isoformat(),'source_event_id':'e','bookmaker_key':'E','market':'h2h','outcome':name,'price':p} for name,p in [('Home',-160),('Away',140)]]
        m=x.market(self.g,[self.q],ext,self.now);self.assertEqual(m['home_ml'],-160);self.assertEqual(m['total'],210)
        for r in ext:r['commence_time']=(self.tip+timedelta(days=1)).isoformat()
        self.assertIsNone(x.market(self.g,[],ext,self.now)['home_ml'])
    def test_provider_update_freshness(self):
        q=dict(self.q,bookmaker_last_update=(self.now-timedelta(hours=2)).isoformat())
        self.assertFalse(x.fresh(q,self.tip,self.now))
    def test_travel_and_missing_inputs(self):
        cat={'1':{'venue_id':'1','venue_name':'A','latitude':0,'longitude':0},'2':{'venue_id':'2','venue_name':'B','latitude':0,'longitude':10}}
        past=dict(self.g,game_id='past',arena_name='B',game_date=(self.now-timedelta(days=2)).isoformat(),completed=True,home_score=100,away_score=90,ingested_at_utc=(self.now-timedelta(days=1)).isoformat())
        distance,e=x.travel(self.g,'1',[past,self.g],cat,self.now);self.assertGreater(distance,600)
        self.assertIsNone(x.travel(self.g,'1',[dict(past,completed=False),self.g],cat,self.now)[0])
        self.assertIsNone(x.travel(dict(self.g,arena_name='unknown'),'1',[past],cat,self.now)[0])
    def test_immutable_selection_outcome_revision_and_schedule_hold(self):
        with tempfile.TemporaryDirectory() as d:
            ev={'game_id':'g','method_id':'M','selection_team_id':'1','qualified':True,'price':-150,'quote':self.q,'scheduled_utc':self.tip.isoformat(),'market':'moneyline'}
            x.forward(d,[ev],[self.g],self.now,{})
            x.forward(d,[dict(ev,price=-200)],[self.g],self.now+timedelta(minutes=1),{})
            with sqlite3.connect(Path(d)/'forward.sqlite3') as db:
                self.assertEqual(json.loads(db.execute('SELECT payload FROM selections').fetchone()[0])['price'],-150)
            now=self.tip+timedelta(hours=4)
            final=dict(self.g,completed=True,home_score=100,away_score=90,ingested_at_utc=now.isoformat())
            x.forward(d,[],[final],now,{})
            x.forward(d,[],[dict(final,home_score=80)],now+timedelta(minutes=1),{})
            x.forward(d,[],[dict(final,game_date=(self.tip+timedelta(days=1)).isoformat())],now+timedelta(minutes=2),{})
            with sqlite3.connect(Path(d)/'forward.sqlite3') as db:
                outcomes=[json.loads(r[0]) for r in db.execute('SELECT payload FROM outcomes ORDER BY rowid')]
            self.assertEqual([o.get('outcome') for o in outcomes],['win','loss',None]);self.assertEqual(outcomes[-1]['status'],'held_schedule_changed')
    def test_scanner_integration_and_stale_quote_hold(self):
        def gz(p,rows):
            p.parent.mkdir(parents=True,exist_ok=True)
            with gzip.open(p,'wt') as f:
                w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);data=root/'data/2026_27/regular_season';f=root/'features'
            g=dict(self.g,season='2026-27');gz(data/'games.csv.gz',[g])
            gz(f/'team_rolling.csv.gz',[{'game_id':'g','team_id':t,'_net_rating_est_last5_avg':v,'_def_rating_est_last5_avg':100,'_true_shooting_est_last5_avg':ts,'prior_games_available':10} for t,v,ts in [('1',10,.65),('2',0,.5)]])
            gz(root/'team_game_context.csv.gz',[{'game_id':'g','team_id':t,'days_since_prev_game':v} for t,v in [('1',3),('2',1)]])
            (root/'live').mkdir();(root/'live/arsenal.json').write_text('{}');(root/'travel').mkdir();(root/'travel/venue_geocode_cache.json').write_text('{}')
            gz(root/'market/market_snapshots.csv.gz',[self.q])
            env=dict(os.environ,APPWIZA_NBA_ROOT=str(root),APPWIZA_NBA_SCANNER_OUT=str(root/'out'),APPWIZA_NBA_FORWARD_ROOT=str(root/'forward'),APPWIZA_NBA_PROSPECTIVE_ROOT=str(root/'prospective'))
            script=Path(__file__).with_name('scan_live_methods.py')
            subprocess.run([sys.executable,'-B',str(script),'--force'],env=env,check=True,capture_output=True)
            picks=json.loads((root/'out/active_picks.json').read_text())['picks']
            self.assertEqual([r['method_id'] for r in picks],['NBA_ML001_DEF_TS']);self.assertEqual(picks[0]['quote']['bookmaker'],'Book')
            self.q['captured_at_utc']=(self.now-timedelta(hours=1)).isoformat();gz(root/'market/market_snapshots.csv.gz',[self.q])
            subprocess.run([sys.executable,'-B',str(script),'--force'],env=env,check=True,capture_output=True)
            self.assertEqual(json.loads((root/'out/active_picks.json').read_text())['picks'],[])
            with sqlite3.connect(root/'forward/forward.sqlite3') as db:self.assertEqual(db.execute('SELECT count(*) FROM selections').fetchone()[0],1)

if __name__=='__main__':unittest.main()
