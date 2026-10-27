import asyncio
import hashlib
import json
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from app import bots, data_archive, experiments, learning, research_plans, strategy, training
from app.validation import cooldown_seconds, fixed_cohort_costs, promotion_allowed, promotion_decision
from test_learning_economics import EconomicDatabaseTests, oos_event


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'registry.sqlite'

    def register(self,stamp=1000):
        with patch.object(experiments.time,'time',return_value=stamp):
            return experiments.register_experiment('test',dict(parameters={'fixed':1},
                costModel={'version':'test-cost-v1','verified':True},trialCount=3,
                executionEquivalent=True,equivalenceVersion='synthetic-test-v1'),db_path=self.path)

    def test_ledger_is_immutable_chained_and_counts_all_trials(self):
        a=self.register();b=self.register(1001)
        self.assertEqual(experiments.accumulated_trials(db_path=self.path),6)
        with closing(sqlite3.connect(self.path)) as con:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute('UPDATE research_experiments SET frozen_at=0')
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute('DELETE FROM research_events')
        self.assertTrue(experiments.verify_ledger(self.path))
        self.assertNotEqual(a['id'],b['id'])

    def test_holdout_overlap_is_not_new_evidence_even_with_another_experiment(self):
        a=self.register()
        data=dict(source='test',symbol='BTC',interval='4h',start=1100,end=1400,dataHash='one')
        with patch.object(experiments.time,'time',return_value=1500):
            first=experiments.claim_holdout(a['id'],data,self.path)
            b=self.register(1500)
            repeated=experiments.claim_holdout(b['id'],dict(data,start=1200,end=1450,dataHash='changed'),self.path)
        self.assertTrue(first['independentFuture']);self.assertTrue(first['allowed'])
        self.assertFalse(repeated['allowed']);self.assertFalse(repeated['independentFuture'])
        decision=experiments.finalize_evidence(a['id'],{},[dict(passed=True)],first,
                                              equivalence=True,costs_verified=True,db_path=self.path)
        self.assertTrue(promotion_allowed(decision))
        with self.assertRaises(ValueError):
            experiments.finalize_evidence(a['id'],{},[dict(passed=True)],first,
                                          equivalence=True,costs_verified=True,db_path=self.path)
        with self.assertRaises(ValueError):
            experiments.finalize_evidence(a['id'],{},[dict(passed=True)],dict(first,id='forged'),db_path=self.path)

    def test_registration_and_ingestion_cannot_backdate_evidence(self):
        with patch.object(experiments.time,'time',return_value=1000):
            with self.assertRaises(ValueError):
                experiments.register_experiment('test',dict(parameters={'x':1},costModel={'version':'x'}),frozen_at=1,db_path=self.path)
        exp=self.register()
        with patch.object(experiments.time,'time',return_value=1500):
            event=experiments.record_observation(exp['id'],'prediction',{'x':1},observed_at=1100,available_at=1100,db_path=self.path)
        self.assertEqual(event['availableAt'],1500)
        self.assertFalse(experiments.observations(exp['id'],1200,db_path=self.path)[-1]['kind']=='prediction')

    def test_owner_database_routing_never_follows_patched_history_to_production(self):
        from app import history
        temporary=Path(self.folder.name)/'account.sqlite'
        with patch.object(history,'DB_PATH',temporary),patch.object(experiments,'DB_PATH',Path('DO-NOT-CREATE-production.sqlite')):
            routed=experiments.registry_path(temporary)
            self.assertEqual(routed,temporary.with_name('research-ledger.sqlite'))
            with patch.object(experiments.time,'time',return_value=1000):
                experiments.register_experiment('isolated',dict(parameters={'x':1},costModel={'version':'x'}),db_path=routed)
            self.assertFalse(Path('DO-NOT-CREATE-production.sqlite').exists())

    def test_readers_never_create_or_mutate_a_registry_and_publish_only_real_evidence(self):
        missing=Path(self.folder.name)/'missing'/'registry.sqlite'
        self.assertEqual(experiments.list_experiments(db_path=missing),[])
        self.assertEqual(experiments.accumulated_trials(db_path=missing),0)
        self.assertEqual(experiments.observations('missing',1500,db_path=missing),[])
        self.assertIsNone(experiments.verify_ledger(missing))
        with self.assertRaises(ValueError):
            experiments.get_experiment('missing',missing)
        self.assertFalse(missing.parent.exists())
        exp=self.register()
        self.assertIsNone(experiments.list_experiments(db_path=self.path)[0]['latestEvidence'])
        with patch.object(experiments.time,'time',return_value=1100):
            event=experiments.record_observation(exp['id'],'training_result',{'historicalPassed':False},db_path=self.path)
        before=self.path.read_bytes()
        self.assertTrue(experiments.verify_ledger(self.path))
        self.assertEqual(experiments.get_experiment(exp['id'],self.path)['id'],exp['id'])
        self.assertEqual(experiments.accumulated_trials(db_path=self.path),3)
        self.assertEqual(len(experiments.observations(exp['id'],1200,db_path=self.path)),2)
        evidence=experiments.list_experiments(db_path=self.path)[0]['latestEvidence']
        self.assertEqual(evidence['eventHash'],event['eventHash'])
        self.assertEqual(evidence['payload']['historicalPassed'],False)
        self.assertEqual(self.path.read_bytes(),before)
        with closing(experiments._connect(self.path,read_only=True)) as con:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("INSERT INTO research_experiments VALUES ('x','x',0,'{}','x')")

    def test_trial_counts_and_all_registered_timestamps_must_be_finite_and_honest(self):
        for trials in (-1,1.5,True,'3'):
            with self.subTest(trials=trials),self.assertRaises(ValueError):
                experiments.register_experiment('test',dict(parameters={'x':1},costModel={'version':'x'},trialCount=trials),db_path=self.path)
        self.assertFalse(self.path.exists())
        exp=self.register()
        for value in (float('nan'),float('inf'),float('-inf'),2000):
            with self.subTest(observed_at=value),patch.object(experiments.time,'time',return_value=1500),self.assertRaises(ValueError):
                experiments.record_observation(exp['id'],'prediction',{},observed_at=value,db_path=self.path)
        for value in (float('nan'),float('inf'),True,'1100'):
            with self.subTest(start=value),self.assertRaises(ValueError):
                experiments.claim_holdout(exp['id'],dict(source='test',symbol='BTC',interval='1h',
                    start=value,end=1400,dataHash='hash'),self.path)
        with self.assertRaises(ValueError):
            experiments.observations(exp['id'],float('inf'),db_path=self.path)
        self.assertEqual(len(experiments.observations(exp['id'],1600,db_path=self.path)),1)

    def test_claim_identity_cannot_be_edited_and_historical_or_scenario_costs_never_promote(self):
        for start,end,equivalent,verified in ((100,900,True,True),(1100,1400,False,True),(2100,2400,True,False)):
            with patch.object(experiments.time,'time',return_value=1000):
                exp=experiments.register_experiment('historical',dict(parameters={'x':1},
                    costModel={'version':'x','verified':verified},executionEquivalent=equivalent,
                    equivalenceVersion='x'),db_path=self.path)
            with patch.object(experiments.time,'time',return_value=2500):
                claim=experiments.claim_holdout(exp['id'],dict(source='test',symbol='BTC',interval='1h',
                    start=start,end=end,dataHash=str(start)),self.path)
                with self.assertRaises(ValueError):
                    experiments.finalize_evidence(exp['id'],{},[dict(passed=True)],dict(claim,dataset=dict(claim['dataset'],start=start+1)),
                        equivalence=True,costs_verified=True,db_path=self.path)
                decision=experiments.finalize_evidence(exp['id'],{},[dict(passed=True)],claim,
                    equivalence=True,costs_verified=True,db_path=self.path)
            self.assertFalse(promotion_allowed(decision))

    def test_attribution_deduplicates_after_process_cache_restart(self):
        with patch.object(experiments.time,'time',return_value=1000):
            first=experiments.log_decision('scalper','BTC',900,.7,{},dict(action='shadow'),
                {'version':'test'},db_path=self.path)
            with patch.dict(experiments._decision_experiments,{},clear=True):
                duplicate=experiments.log_decision('scalper','BTC',900,.7,{},dict(action='shadow'),
                    {'version':'test'},db_path=self.path)
        self.assertIn('eventHash',first)
        self.assertTrue(duplicate['duplicate'])
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM research_events WHERE kind='decision'").fetchone()[0],1)

    def test_promotion_flags_are_strict_and_fixed_cohort_preserves_currency_and_paths(self):
        self.assertFalse(promotion_allowed(promotion_decision(checks_passed=1,independent_future=True,
            execution_equivalent=True,costs_verified=True,experiment_id='x',evidence_id='y')))
        event=dict(direction='long',qty=2,entryMid=100,exitMid=110,entryTime=1000,exitTime=2000,
                   quoteToAccount=.9,accountCurrency='EUR')
        costs=dict(feeRate=0,slippageRate=0,spreadPct=0,carryDailyRate=0)
        result=fixed_cohort_costs([event],costs)
        self.assertEqual(result['pnl'],18)
        self.assertEqual(result['currency'],'EUR')
        for invalid in (dict(event,exitTime=float('nan')),dict(event,exitTime=900),dict(event,quoteToAccount=float('inf'))):
            with self.assertRaises(ValueError):
                fixed_cohort_costs([invalid],costs)
        with self.assertRaises(ValueError):
            fixed_cohort_costs([event,dict(event,accountCurrency='USD')],costs)

    def test_code_provenance_covers_all_python_dependencies_and_lock_but_no_runtime_data(self):
        root=Path(self.folder.name)/'backend'/'app'
        root.mkdir(parents=True)
        module=root/'experiments.py';module.write_text('VERSION = 1',encoding='utf-8')
        with patch.object(experiments,'__file__',str(module)):
            initial=experiments.code_fingerprint()
            (root/'fx_rates.py').write_text('RATE_CONTRACT = 1',encoding='utf-8')
            extended=experiments.code_fingerprint()
            self.assertNotEqual(initial,extended)
            (root.parent/'requirements.lock').write_text('dependency==1.0',encoding='utf-8')
            locked=experiments.code_fingerprint()
            self.assertNotEqual(extended,locked)
            runtime=root/'.runtime';runtime.mkdir()
            (runtime/'data.py').write_text('private runtime content',encoding='utf-8')
            self.assertEqual(experiments.code_fingerprint(),locked)
            exp=self.register()
            self.assertEqual(exp['manifest']['codeHash'],locked)
            self.assertEqual(exp['manifest']['codeScope'],experiments.CODE_SCOPE)


class PlanTests(unittest.TestCase):
    setUp=RegistryTests.setUp
    def rows(self,seconds):
        return [dict(time=seconds*(i+1),open=100+i*.1,high=101+i*.1,low=99+i*.1,
                     close=100.5+i*.1,volume=100) for i in range(300)]

    def test_all_seven_plans_are_preregisterable_and_local_evaluation_is_bounded(self):
        self.assertEqual([p['id'] for p in research_plans.list_plans()],['A1','A2','A3','A4','A5','A6','A7'])
        for plan_id in ('A1','A2','A3','A4','A5','A6','A7'):
            symbols=['BTC','ETH'] if plan_id=='A3' else ['BTC']
            exp=research_plans.preregister_plan(plan_id,symbols=symbols,
                   benchmark='BTC' if plan_id=='A3' else None,db_path=self.path)
            if plan_id in ('A5','A6','A7'):
                result=research_plans.evaluate_plan(exp['id'],db_path=self.path)
                self.assertEqual(result['status'],'awaiting_point_in_time_data')
            else:
                seconds=86400 if plan_id in ('A1','A3') else 14400
                interval='1d' if seconds==86400 else '4h'
                datasets=[dict(symbol=s,interval=interval,source='synthetic-test',candles=self.rows(seconds)) for s in symbols]
                result=research_plans.evaluate_plan(exp['id'],datasets,db_path=self.path)
                self.assertFalse(result['automaticPromotion'])
                self.assertTrue(all(not r.get('promotion',{}).get('promotable',False) for r in result['results']))
                repeat=research_plans.evaluate_plan(exp['id'],datasets,db_path=self.path)
                self.assertTrue(all(r['status']=='previously_seen' for r in repeat['results']))
        self.assertTrue(experiments.verify_ledger(self.path))

    def test_paired_evidence_requires_one_finite_matched_opportunity_known_before_decision(self):
        with patch.object(experiments.time,'time',return_value=1000):
            exp=research_plans.preregister_plan('A7',symbols=['BTC'],paired_contract=dict(
                basePolicyHash='fixed-base',overlayPolicyHash='fixed-overlay',currency='USDT',riskBudget=1),db_path=self.path)
        prediction=dict(symbol='BTC',decisionTime=1200,currency='USDT',riskBudget=1,
                        costVersion=exp['manifest']['costModel']['version'],
                        basePolicyHash='fixed-base',overlayPolicyHash='fixed-overlay',publishedAt=1050)
        with patch.object(experiments.time,'time',return_value=1100):
            event=experiments.record_observation(exp['id'],'prediction',prediction,db_path=self.path)
        pair=dict(prediction,predictionEventHash=event['eventHash'],inputAvailableAt=1100,
                  baseRisk=1,overlayRisk=1,baseCostVersion=prediction['costVersion'],
                  overlayCostVersion=prediction['costVersion'],baseNet=-1,overlayNet=.5,
                  baseClosedAt=1250,overlayClosedAt=1250)
        with patch.object(experiments.time,'time',return_value=1300):
            experiments.record_observation(exp['id'],'paired-news',pair,db_path=self.path)
            experiments.record_observation(exp['id'],'paired-news',pair,db_path=self.path)
            experiments.record_observation(exp['id'],'paired-news',dict(pair,symbol='ETH'),db_path=self.path)
            experiments.record_observation(exp['id'],'paired-news',dict(pair,decisionTime=1190),db_path=self.path)
            experiments.record_observation(exp['id'],'paired-news',dict(pair,overlayRisk=2),db_path=self.path)
            result=research_plans.evaluate_plan(exp['id'],db_path=self.path)
        self.assertEqual(result['samples'],1);self.assertEqual(result['rejected'],4)
        self.assertEqual(result['incrementalNet'],1.5)
        events=experiments.observations(exp['id'],1400,db_path=self.path)
        invalid=deepcopy(next(e for e in events if e['kind']=='paired-news'))
        invalid['payload']['baseNet']=float('inf')
        with patch.object(experiments,'observations',return_value=[event,invalid]):
            result=research_plans.evaluate_plan(exp['id'],db_path=self.path)
        self.assertEqual(result['samples'],0)

    def test_paired_policies_are_frozen_and_new_prediction_ids_cannot_duplicate_an_opportunity(self):
        contract=dict(basePolicyHash='base-v1',overlayPolicyHash='overlay-v1',currency='USDT',riskBudget=1)
        for plan_id,kind in (('A5','paired-execution'),('A6','paired-ai'),('A7','paired-news')):
            for frozen_contract in (None,contract):
                with self.subTest(plan=plan_id,contract=frozen_contract):
                    with patch.object(experiments.time,'time',return_value=1000):
                        exp=research_plans.preregister_plan(plan_id,symbols=['BTC'],paired_contract=frozen_contract,db_path=self.path)
                    prediction=dict(contract,symbol='BTC',decisionTime=1200,
                        costVersion=exp['manifest']['costModel']['version'],publishedAt=1050)
                    for at,changed in ((1100,{}),(1101,{}),(1102,dict(overlayPolicyHash='posthoc-policy')),
                                       (1103,dict(currency='EUR')),(1104,dict(costVersion='posthoc-cost'))):
                        frozen=dict(prediction,**changed)
                        with patch.object(experiments.time,'time',return_value=at):
                            event=experiments.record_observation(exp['id'],'prediction',frozen,db_path=self.path)
                        pair=dict(frozen,predictionEventHash=event['eventHash'],inputAvailableAt=at,
                            baseRisk=1,overlayRisk=1,baseCostVersion=frozen['costVersion'],
                            overlayCostVersion=frozen['costVersion'],baseNet=-.5,overlayNet=.5,
                            baseClosedAt=1250,overlayClosedAt=1250)
                        with patch.object(experiments.time,'time',return_value=1300):
                            experiments.record_observation(exp['id'],kind,pair,db_path=self.path)
                    with patch.object(experiments.time,'time',return_value=1400):
                        result=research_plans.evaluate_plan(exp['id'],db_path=self.path)
                    self.assertEqual(result['samples'],1 if frozen_contract else 0)
                    self.assertEqual(result['rejected'],4 if frozen_contract else 5)
                    self.assertEqual(result['pairedContractFrozen'],bool(frozen_contract))


class CausalPolicyTests(EconomicDatabaseTests):
    def test_training_archives_the_complete_long_input_in_verifiable_ordered_chunks(self):
        source=dict(symbol='BTCUSDT',provider='binance')
        rows=[dict(time=i*3600,open=100,high=101,low=99,close=100,volume=100,dataSource=source)
              for i in range(10001)]
        cfg=replace(bots.FLOW,universe=('BTC',))
        sentinel=Path(training.DB_PATH).parent/'MUST-NOT-USE-GLOBAL-ARCHIVE'
        with patch.object(training,'fetch_history',AsyncMock(return_value=rows)), \
             patch.object(training,'score_series',return_value=[0.0]*len(rows)), \
             patch.object(training,'optimize',return_value=dict(ok=False,applied=False,params={},engineVersion=training.ENGINE_VERSION)) as optimize, \
             patch.object(data_archive,'ROOT',sentinel):
            result=asyncio.run(training._train_symbol(bots.TradingBot(cfg),bots.SYMBOLS_BY_ID['BTC']))
        self.assertEqual(len(optimize.call_args.args[1]),len(rows))
        ledger=experiments.registry_path(training.DB_PATH)
        exp=experiments.get_experiment(result['experimentId'],ledger)
        events=experiments.observations(exp['id'],training.time.time(),db_path=ledger)
        receipts=[e['payload'] for e in events if e['kind']=='dataset_snapshot']
        self.assertEqual(len(receipts),2)
        restored=[]
        for index,receipt in enumerate(receipts):
            self.assertEqual(receipt['chunkIndex'],index)
            self.assertEqual(receipt['chunkCount'],2)
            self.assertEqual(receipt['rowOffset'],len(restored))
            self.assertEqual(receipt['totalBars'],len(rows))
            self.assertEqual(receipt['inputDataHash'],exp['manifest']['dataset']['dataHash'])
            raw=(ledger.parent/'research-data'/receipt['artifact']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),receipt['sha256'])
            payload=json.loads(raw)
            self.assertEqual(payload['source'],experiments.canonical(source))
            self.assertLessEqual(len(payload['candles']),10000)
            restored.extend(payload['candles'])
        self.assertEqual(restored,[{key:row[key] for key in ('time','open','high','low','close','volume')} for row in rows])
        self.assertFalse(sentinel.exists())

    def test_training_snapshot_identity_failure_is_visible_before_optimization(self):
        source=dict(provider='binance',symbol='BTCUSDT',extra='x'*400)
        rows=[dict(time=i*3600,open=100,high=101,low=99,close=100,volume=100,dataSource=source)
              for i in range(500)]
        with patch.object(training,'fetch_history',AsyncMock(return_value=rows)), \
             patch.object(training,'optimize') as optimize:
            with self.assertRaisesRegex(ValueError,'Dataset identity'):
                asyncio.run(training._train_symbol(bots.TradingBot(bots.FLOW),bots.SYMBOLS_BY_ID['BTC']))
        optimize.assert_not_called()

    def test_warm_start_deduplicates_overlapping_operations_not_only_batches(self):
        a,b,c=[oos_event(k,{'bias':1},1) for k in ('a','b','c')]
        self.assertEqual(learning.warm_start_from_oos('scalper',[a,b],namespace='BTC'),2)
        self.assertEqual(learning.warm_start_from_oos('scalper',[b,c],namespace='BTC'),1)
        self.assertEqual(learning._load_model('scalper')['n'],0)
        self.assertEqual(learning._load_model('scalper')['oosSamples'],3)
        self.assertEqual(learning.warm_start_from_oos('scalper',[({'bias':1},1)]),0)

    def test_flow_training_uses_the_same_four_hour_aggregation_as_live_config(self):
        cfg=replace(bots.FLOW,universe=('BTC',))
        rows=[dict(time=i*3600,open=100,high=101,low=99,close=100,volume=100) for i in range(600)]
        seen=[]
        def scores(bot_id,work,higher):
            seen.append(higher);return [0.0]*len(work)
        with patch.object(training,'fetch_history',AsyncMock(return_value=rows)), \
             patch.object(training,'score_series',side_effect=scores), \
             patch.object(training,'optimize',return_value=dict(ok=False,applied=False,params={},engineVersion=training.ENGINE_VERSION)):
            asyncio.run(training._train_symbol(bots.TradingBot(cfg),bots.SYMBOLS_BY_ID['BTC']))
        self.assertEqual(strategy.TREND_STEP['flow'],14400)
        self.assertEqual(seen[0],strategy.resample(rows,14400))

    def test_cooldown_clock_matches_losses_and_reentry_after_a_win(self):
        self.assertEqual([cooldown_seconds(120,-1,n) for n in (1,2,3,4)],[240,480,960,960])
        self.assertEqual(cooldown_seconds(120,1,0),30)
        rows=[dict(time=i*60,open=100,high=100.1,low=99.9,close=100,volume=100) for i in range(21)]
        for i in (2,7): rows[i]['low']=98
        rows[16]['high']=104
        scores=[.8 if i in (1,6,15,17) else 0 for i in range(21)]
        result=training.simulate(rows,scores,[1]*21,0,.5,1,3,10,100,2,1,100,0,21,
            exit_on_signal=False,meta_builder=lambda i,d:{'bias':1},cooldown_seconds_base=120)
        self.assertEqual([r['entryTime'] for r in result['oosTrades']],[120,420,960,1080])

    def test_signal_consumption_survives_restart_and_empty_or_stale_inputs_cannot_open(self):
        bot=bots.TradingBot(bots.SCALPER);bot.load_state()
        empty=dict(reasons=[],candles=[],confirmations=[])
        bot._open(bots.SYMBOLS_BY_ID['BTC'],'long',100,2,.8,empty,2,4,1)
        self.assertFalse(bot.positions)
        rows=[dict(time=0,open=100,high=101,low=99,close=100,volume=100)]
        signal=dict(reasons=[],candles=rows,confirmations=[],entryQuote=dict(price=100,time=1))
        bot._open(bots.SYMBOLS_BY_ID['BTC'],'long',100,2,.8,signal,2,4,1)
        self.assertFalse(bot.positions)
        signal['entryQuote']=dict(price=100,time=bots.time.time())
        with patch.object(bots.systems,'chart_overlays',return_value={}):
            bot._open(bots.SYMBOLS_BY_ID['BTC'],'long',100,2,.8,signal,2,4,1)
        self.assertIn('BTC',bot.positions)
        with bots._conn() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM bot_signal_receipts').fetchone()[0],1)
        restored=bots.TradingBot(bots.SCALPER);restored.load_state()
        self.assertIn('BTC',restored.positions)
        with patch.object(bots.systems,'chart_overlays',return_value={}):
            restored._open(bots.SYMBOLS_BY_ID['BTC'],'long',100,2,.8,signal,2,4,1)
        with bots._conn() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM bot_trades').fetchone()[0],1)
