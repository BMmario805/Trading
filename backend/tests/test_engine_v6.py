import asyncio
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app import desk, gemini_agent as gemini, learning, strategy, training
from app.book_stream import BookStream
from app.desk_maker import valid_lot
from app.execution import signal_exit_enabled
from test_desk import data, add_markets
from test_learning_economics import EconomicDatabaseTests
import test_requested_corrections as requested


class MemoryTests(EconomicDatabaseTests):
    def test_legacy_net_close_previously_marked_skipped_is_recovered_once(self):
        tid=self.trade(pnl=1)
        snap=dict(engineVersion=4,confirmations=[dict(system='MACD',vote=.7)],
                  executionCosts=dict(version='net-costs-v1',feeRate=.001,slippageRate=.0002,spreadPct=.0005,carryDailyRate=.0003),
                  economics=dict(version='net-costs-v1'))
        model=dict(learning._new_model(),engineVersion=5,featureVersion=3,processingVersion=1,n=35,w={'bias':.8})
        with learning._conn() as con:
            con.execute('UPDATE bot_trades SET snapshot=? WHERE id=?',(json.dumps(snap),tid))
            con.execute('INSERT INTO bot_model VALUES (?,?,?)',('scalper',json.dumps(model),1))
            con.execute('INSERT INTO bot_model_processed VALUES (?,?)',('scalper',tid))
        self.assertEqual(learning.meta_learn_new_trades('scalper'),1)
        self.assertEqual(learning._load_model('scalper')['n'],36)
        self.assertEqual(learning.meta_learn_new_trades('scalper'),0)
        self.assertEqual(learning._load_model('scalper')['n'],36)

    def test_old_close_without_cost_contract_is_not_relabelled_as_net(self):
        snap=dict(engineVersion=3,confirmations=[dict(system='MACD',vote=.7)])
        self.assertIsNone(learning._trade_features('scalper',('BTC','long',0,json.dumps(snap))))
    def test_uncalibrated_score_never_increases_risk_even_after_thirty_closes(self):
        self.assertLessEqual(learning.meta_size_multiplier('scalper',.9,True),1)
        learning._load_model('scalper')['n']=35
        self.assertLessEqual(learning.meta_size_multiplier('scalper',.9,False),1)
        self.assertLessEqual(learning.meta_size_multiplier('scalper',.9,True),1)
        self.assertEqual(learning.meta_size_multiplier('scalper',1,True),1)
    def test_compatible_weights_and_receipts_survive_engine_and_additive_feature_upgrade(self):
        old=dict(w={'bias':.8},n=35,lastTradeId=8,updatedAt=1,engineVersion=5,
                 featureVersion=3,processingVersion=learning.META_PROCESSING_VERSION)
        with learning._conn() as con:
            con.execute('INSERT INTO bot_model VALUES (?,?,?)',('scalper',json.dumps(old),1))
            con.execute('INSERT INTO bot_model_processed VALUES (?,?)',('scalper',900))
        self.assertEqual(learning.meta_learn_new_trades('scalper'),0)
        model=learning._load_model('scalper')
        self.assertEqual((model['n'],model['w']),(35,{'bias':.8}))
        self.assertEqual(model['featureVersion'],learning.META_FEATURE_VERSION)
        with learning._conn() as con:
            self.assertIsNotNone(con.execute('SELECT 1 FROM bot_model_processed WHERE trade_id=900').fetchone())

    def test_explicit_feature_schema_accepts_other_engine_but_unknown_schema_is_skipped(self):
        snapshot=dict(metaFeatureVersion=learning.META_FEATURE_VERSION,engineVersion=999,
                      confirmations=[],entryContext=dict(atr_pct=2,extension_atr=1,spread_to_atr=.1))
        feats=learning._trade_features('scalper',('BTC','long',0,json.dumps(snapshot)))
        self.assertAlmostEqual(feats['atr_pct'],.4)
        snapshot['metaFeatureVersion']=99
        self.assertIsNone(learning._trade_features('scalper',('BTC','long',0,json.dumps(snapshot))))

    def test_forgetting_applies_once_per_new_close_and_preserves_unused_feature(self):
        model=learning._new_model();model['w']={'old':1}
        learning._meta_sgd_step(model,{'bias':1},True)
        self.assertAlmostEqual(model['w']['old'],.98-learning.META_LR*learning.META_L1)
        tid=self.trade(pnl=1)
        self.assertEqual(learning.meta_learn_new_trades('scalper'),1)
        before=deepcopy(learning._load_model('scalper'))
        self.assertEqual(learning.meta_learn_new_trades('scalper'),0)
        self.assertEqual(learning._load_model('scalper'),before)


class ValidationTests(unittest.TestCase):
    def test_trend_signal_exits_are_disabled_in_live_and_optimizer_contract(self):
        for bot in ('swing','trend','squeeze'): self.assertFalse(signal_exit_enabled(bot))
        for bot in ('scalper','meanrev','orb','flow'): self.assertTrue(signal_exit_enabled(bot))

    def test_dsr_penalizes_trials_and_rejects_short_or_zero_variance_samples(self):
        rng=random.Random(12)
        series=[rng.gauss(.15,1) for _ in range(500)]
        small=training.deflated_sharpe(series,[0,.1])
        large=training.deflated_sharpe(series,[0,.1]*50)
        self.assertLess(large['probability'],small['probability'])
        self.assertFalse(training.deflated_sharpe([1]*100,[0,1])['passed'])
        self.assertFalse(training.deflated_sharpe([1,-1]*4,[0,1])['passed'])

    def test_two_positive_blocks_can_pass_but_holdout_double_cost_still_cannot_fail(self):
        rows=[dict(time=i*3600,open=100,high=102,low=98,close=100,volume=100) for i in range(2000)]
        protocol=training.temporal_protocol(len(rows),10)
        rng=random.Random(3)
        series=[rng.gauss(.4,1) for _ in range(900)]
        fail_holdout=False
        def replay(*args,**kwargs):
            lo,hi=args[12:14]
            costs=args[16]
            pnl=6
            if (lo,hi) in protocol['folds']:
                idx=protocol['folds'].index((lo,hi))
                pnl=(4,4,-1)[idx]
                if costs['feeRate']>.001: pnl=(2,2,-1)[idx]
            if (lo,hi)==protocol['holdout'] and costs['feeRate']>=.002 and fail_holdout: pnl=-1
            return dict(pnl=pnl,trades=12,maxDrawdown=1,profitFactor=2,tradePnls=[pnl/12]*12,periodPnls=series)
        with patch.object(training,'simulate',side_effect=replay) as mock,patch.object(training,'GRID_THRESHOLD',(.4,.5)),patch.object(training,'GRID_SL',(2,)),patch.object(training,'GRID_TP',(4,)),patch.object(training,'GRID_TRAIL',(1.2,)):
            args=('trend',rows,[.8]*2000,[2]*2000,.001,10,2,1,100)
            result=training.optimize(*args,cost_model=dict(feeRate=.001))
            self.assertTrue(result['historicalPassed']); self.assertFalse(result['applied'])
            self.assertTrue(all(call.kwargs['exit_on_signal'] is False for call in mock.call_args_list))
            self.assertEqual(result['holdoutStressMultiplier'],2)
            fail_holdout=True
            self.assertFalse(training.optimize(*args,cost_model=dict(feeRate=.001))['applied'])


class ContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=patch.object(gemini,'DB_PATH',Path(self.tmp.name)/'gemini.sqlite');self.db.start();self.addCleanup(self.db.stop)
        self.agent=gemini.Agent(gemini.Settings(key='test',min_request_seconds=1800))
        self.addAsyncCleanup(self.agent.close)

    async def test_cooldown_separates_selector_from_observer_but_hour_and_day_remain_global(self):
        now=10000
        self.assertIsNotNone(self.agent.reserve('BTC',now))
        self.assertIsNotNone(self.agent.reserve('DESK',now+1))
        self.assertIsNone(self.agent.reserve('ETH',now+2))
        self.assertIsNone(self.agent.reserve('DESK',now+2))
        for i in range(4):
            with gemini.connection() as con:
                con.execute('INSERT INTO attempts(ts,symbol,status) VALUES (?,?,?)',(now+3+i,'BTC','success'))
        self.assertIsNone(self.agent.reserve('DESK',now+1801))

    async def test_on_demand_candidate_is_not_limited_to_initial_three_symbols(self):
        now=time.time()
        sources=[dict(source='test',items=[dict(text='Solana upgrade',time=now-1,url='https://example.org')])]
        async def answer(symbol,items):
            result=dict(summary='Neutral',items=[dict(id=i['id'],direction=0,confidence=.8,
                relevant=True,is_noise=False,catalyst='other',explanation='Neutral') for i in items])
            return json.dumps(result),{},'test'
        with patch.object(self.agent,'request',side_effect=answer),patch('app.sentiment.candidate_sources',AsyncMock(return_value=dict(sources=sources))) as collect:
            result=await self.agent.pretrade(SimpleNamespace(id='SOL'))
            self.assertEqual(result['symbol'],'SOL')
            await self.agent.pretrade(SimpleNamespace(id='SOL'))
            collect.assert_awaited_once()

    async def test_macro_veto_requires_explicit_factor_direction_not_btc_news(self):
        now=time.time()
        item=dict(id='one',direction=-1,confidence=.9,relevant=True,is_noise=False,
                  catalyst='security',publishedAt=now-1,factor_impacts=[])
        observed=dict(symbol='BTC',analyzedAt=now,items=[item])
        agent=SimpleNamespace(snapshot=lambda symbol:dict(data=None),macro_observations=lambda:[observed])
        with patch.object(gemini,'AGENT',agent):
            self.assertTrue(gemini.catalyst_gate('SOL','long')['allowed'])
            item.update(catalyst='monetary_policy',factor_impacts=[dict(factor='crypto',direction=-1,confidence=.9)])
            self.assertFalse(gemini.catalyst_gate('SOL','long')['allowed'])
            self.assertTrue(gemini.catalyst_gate('XAUUSD','long')['allowed'])

    async def test_slow_source_does_not_discard_available_pretrade_texts(self):
        from app import sentiment
        from app.config import SYMBOLS_BY_ID
        cancelled=[]
        async def slow(*args):
            try: await asyncio.Event().wait()
            finally: cancelled.append(True)
        fast=dict(source='fast',items=[dict(text='Fresh',time=time.time())])
        with patch.object(sentiment,'peek_cached',return_value=None),patch.object(sentiment,'_stocktwits',AsyncMock(return_value=fast)),patch.object(sentiment,'_reddit',side_effect=slow),patch.object(sentiment,'ideas_source',AsyncMock(return_value=None)),patch.object(sentiment,'minds_source',AsyncMock(return_value=None)),patch.object(sentiment,'official_news',AsyncMock(return_value=None)):
            result=await sentiment.candidate_sources(SYMBOLS_BY_ID['SOL'],timeout=.01)
        self.assertEqual(result['sources'],[fast])
        self.assertTrue(cancelled)


class RankedSelectionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=requested.FullUniverseSelectorTests.asyncSetUp
    async def test_failed_first_choice_uses_second_and_never_opens_the_third(self):
        self.all_ready=True
        self.agent.choose_market.return_value=dict(symbol='COIN00USDT',ranked_symbols=['COIN00USDT','COIN01USDT','COIN02USDT'],reason='Prioridades',confidence=.8)
        original=self.engine.review_candidate
        reviewed=[]
        async def verify(candidate,*args):
            if not any(p['symbol']==candidate['symbol'] for p in self.engine.state['positions']):
                reviewed.append(candidate['symbol'])
            if candidate['symbol']=='COIN00USDT':
                self.engine.evaluated(candidate,'blocked','Libro insuficiente al verificar')
                return
            return await original(candidate,*args)
        with patch.object(self.engine,'review_candidate',side_effect=verify):
            await self.engine.scan(self.snapshot)
            await self.engine.scan(self.snapshot)
        self.assertEqual(reviewed,['COIN00USDT','COIN01USDT'])
        self.assertEqual([p['symbol'] for p in self.engine.state['positions']],['COIN01USDT'])
        self.agent.choose_market.assert_awaited_once()


class BtcRegimeTests(unittest.TestCase):
    def test_stressed_btc_requires_positive_aligned_relative_strength_for_alt_breakout(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=desk.Desk(db_path=Path(folder)/'desk.sqlite')
            engine.task=object()
            last=int(time.time())//3600*3600-3600
            def rows(end):
                return [dict(time=last-(29-i)*3600,open=100+(end-100)*i/29,
                             high=101+(end-100)*i/29,low=99+(end-100)*i/29,
                             close=100+(end-100)*i/29,volume=100) for i in range(30)]
            engine.btc_rows=rows(90)
            engine.master_regime=dict(stressed=True)
            self.assertFalse(engine.master_gate('SOLUSDT',dict(strategy='breakout'),rows(80))['allowed'])
            self.assertTrue(engine.master_gate('SOLUSDT',dict(strategy='breakout'),rows(95))['allowed'])
            shifted=rows(95)
            for row in shifted: row['time']-=3600
            self.assertFalse(engine.master_gate('SOLUSDT',dict(strategy='breakout'),shifted)['allowed'])
            self.assertTrue(engine.master_gate('SOLUSDT',dict(strategy='trend-pullback'),rows(80))['allowed'])
            engine.master_regime=None
            self.assertFalse(engine.master_gate('SOLUSDT',dict(strategy='breakout'),rows(95))['allowed'])


class MakerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.engine=desk.Desk(db_path=root/'desk.sqlite')
        self.now=1000000.0
        self.stack.enter_context(patch('time.time',side_effect=lambda:self.now))
        self.stream=BookStream();self.stream.connected=True;self.stream.watch(['SOLUSDT'])
        self.stack.enter_context(patch('app.desk_maker.BOOKS',self.stream))
        self.stack.enter_context(patch.object(desk,'BOOKS',self.stream))
        self.snapshot=data();self.snapshot['at']=self.now
        self.book(100,100.01,2)
        order=desk.size_order(1000,1000,100.01,2,self.snapshot['metadata']['SOLUSDT'])
        self.template=dict(order,symbol='SOLUSDT',entryKey='SOLUSDT:999000',entryTime=self.now,quoteAt=self.now,
            atr=2,initialDist=order['entry']-order['sl'],best=order['entry'],lastBid=100,status='open',
            marketFilters=self.snapshot['metadata']['SOLUSDT']['filters'],evidence={})

    def book(self,bid,ask,queue):
        self.stream.books['SOLUSDT']=dict(symbol='SOLUSDT',bidPrice=str(bid),askPrice=str(ask),bidQty=str(queue),askQty=str(queue),
                receivedAt=self.now,monotonicAt=time.monotonic())
        self.snapshot['at']=self.now
        self.snapshot['books']['SOLUSDT'].update(bidPrice=str(bid),askPrice=str(ask))

    def trade(self,identifier,quantity,price=100,maker=True):
        self.now+=.1
        self.assertTrue(self.stream.ingest(dict(e='trade',s='SOLUSDT',t=identifier,T=self.now*1000,p=str(price),q=str(quantity),m=maker)))

    def test_quote_touch_never_fills_and_reservation_survives_restart(self):
        self.assertTrue(self.engine.maker.post(self.template,'buy',self.template['qty']))
        self.engine.maker.reconcile(self.snapshot)
        self.assertFalse(self.engine.state['positions'])
        self.assertEqual(self.engine.state['cash'],1000)
        restored=desk.Desk(db_path=self.engine.db_path)
        self.assertTrue(restored.maker.pending_entry())
        self.assertGreater(restored.maker.orders[0]['reservedRisk'],0)
        # El nuevo proceso no conoce operaciones omitidas: cancela, no rellena.
        self.stream.generation='new-process'
        restored.maker.reconcile(self.snapshot)
        self.assertFalse(restored.maker.orders)
        self.assertFalse(restored.state['positions'])

    def test_observed_partial_fill_updates_cash_and_risk_atomically_and_only_once(self):
        self.engine.maker.post(self.template,'buy',self.template['qty'])
        self.trade(1,2.1)
        self.engine.maker.reconcile(self.snapshot)
        p=self.engine.state['positions'][0]
        self.assertAlmostEqual(p['qty'],.1)
        self.assertAlmostEqual(self.engine.state['cash'],989.99)
        self.assertAlmostEqual(p['entry'],100)
        self.assertGreater(self.engine.maker.orders[0]['reservedRisk'],0)
        self.engine.maker.reconcile(self.snapshot)
        self.assertAlmostEqual(self.engine.state['cash'],989.99)
        self.assertAlmostEqual(self.engine.public()['maker']['reservedCash'],self.engine.maker.orders[0]['reservedCash'])

    def test_database_failure_replays_observed_fill_without_double_debit(self):
        self.engine.maker.post(self.template,'buy',self.template['qty'])
        self.trade(1,2.1)
        with patch.object(self.engine,'connect',side_effect=OSError('disk')):
            with self.assertRaises(OSError): self.engine.maker.reconcile(self.snapshot)
        self.assertEqual(self.engine.state['cash'],1000)
        self.assertFalse(self.engine.state['positions'])
        self.engine.maker.reconcile(self.snapshot)
        self.assertAlmostEqual(self.engine.state['cash'],989.99)

    def test_stop_closes_actual_partial_fill_and_cancels_remaining_entry(self):
        self.engine.maker.post(self.template,'buy',self.template['qty'])
        self.trade(1,2.1)
        self.engine.maker.reconcile(self.snapshot)
        self.book(95,95.01,2)
        self.engine.manage(self.snapshot)
        self.assertFalse(self.engine.state['positions'])
        self.assertFalse(self.engine.maker.orders)
        trade=self.engine.public()['trades'][0]
        self.assertAlmostEqual(trade['pnl'],self.engine.state['cash']-1000)

    def test_expiry_does_not_invent_fill_and_arms_normal_taker_revalidation(self):
        self.engine.maker.post(self.template,'buy',self.template['qty'])
        self.now+=61
        self.engine.maker.reconcile(self.snapshot)
        self.assertFalse(self.engine.maker.orders)
        self.assertFalse(self.engine.state['positions'])
        self.assertIn(self.template['entryKey'],self.engine.state['makerFallback'])
        self.assertEqual(self.engine.state['cash'],1000)

    def test_partial_maker_and_final_taker_reconcile_one_trade_with_exact_fees(self):
        self.engine.maker.post(self.template,'buy',self.template['qty'])
        self.trade(1,100)
        self.engine.maker.reconcile(self.snapshot)
        p=self.engine.state['positions'][0]
        self.book(107,107.01,.1)
        qty=desk.partial_quantity(p['qty'],107.01,dict(filters=p['marketFilters']))
        self.assertTrue(self.engine.maker.post(p,'sell',qty))
        self.trade(2,100,107.01,False)
        self.engine.maker.reconcile(self.snapshot)
        p=self.engine.state['positions'][0]
        self.assertTrue(p['partialDone'])
        self.assertEqual(p['partialExits'][0]['slippageRate'],0)
        self.engine.close_position(110,'final','SOLUSDT')
        trade=self.engine.public()['trades'][0]
        self.assertAlmostEqual(trade['pnl'],self.engine.state['cash']-1000)
        self.assertAlmostEqual(trade['exitFee'],sum(e['fee'] for e in trade['partialExits'])+trade['finalExitQty']*trade['exitPrice']*.001)


class SignalArchitectureTests(unittest.TestCase):
    def test_meanrev_and_squeeze_are_causal_and_explain_exactly_three_blocks(self):
        rows=[dict(time=i*3600,open=100+i*.03,close=100+i*.03+.01,high=101+i*.03,low=99+i*.03,volume=100) for i in range(280)]
        higher=strategy.resample(rows,14400)
        for bot in ('meanrev','squeeze'):
            full=strategy.score_series(bot,rows,higher)
            for length in (201,223,241):
                self.assertEqual(full[:length],strategy.score_series(bot,rows[:length],higher))
            self.assertEqual([c['system'] for c in strategy.confirmations(bot,rows,higher)],['Régimen','Disparador de precio','Flujo CMF'])
