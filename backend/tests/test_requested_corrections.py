from desk_unit_support import fund_unit_desk
import asyncio
from contextlib import ExitStack
import json
import math
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app import bots, desk, gemini_agent, learning, mt5_performance, performance_review, strategy
from app.maker_simulation import PostOnlyOrder
from app.spot_orders import partial_quantity
from test_desk import data, add_markets, ranked_fixture
from test_learning_economics import EconomicDatabaseTests
from test_mt5_performance import deal


class PartialExitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        folder=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.engine=desk.Desk(db_path=Path(folder)/'desk.sqlite')
        fund_unit_desk(self,self.engine)
        self.stack.enter_context(patch.object(gemini_agent,'AGENT',None))
        self.analysis=dict(bar=int(time.time())//3600*3600-3600,close=100,ema20=100,
            ema50=99,atr=2,rsi=55,trend=True,pullback=True,rows=[])
        self.stack.enter_context(patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])))
        self.stack.enter_context(patch.object(desk,'tape',return_value=self.analysis))
        await self.engine.scan(data())

    def quote(self,bid):
        snapshot=data()
        snapshot['books']['SOLUSDT'].update(bidPrice=str(bid),askPrice=str(bid+.01))
        return snapshot

    def test_new_positions_freeze_hourly_partial_policy_and_48h_horizon(self):
        p=self.engine.state['positions'][0]
        self.assertEqual((p['candleInterval'],p['maxHoldSeconds']),('1h',48*3600))
        self.assertEqual(p['exitPolicy'],dict(partialAtR=1.5,partialFraction=1/3,trailAtr=2.8,trailAfterR=2.0,residualStopR=-.5,chandelier4h=True,extendProfitableTrend=True))
        self.assertEqual(self.engine.public()['policy']['version'],desk.POLICY_VERSION)

    def test_partial_then_final_exit_is_one_net_trade_with_all_cash_and_fees(self):
        original=dict(self.engine.state['positions'][0])
        before_cash=self.engine.state['cash']
        self.engine.manage(self.quote(107))
        p=self.engine.state['positions'][0]
        self.assertTrue(p['partialDone'])
        self.assertEqual(len(p['partialExits']),1)
        e=p['partialExits'][0]
        self.assertAlmostEqual(p['qty']+e['qty'],original['qty'])
        self.assertAlmostEqual(self.engine.state['cash'],before_cash+e['proceeds'])
        self.assertAlmostEqual(p['sl'],original['entry']-.5*original['risk']/original['qty'])
        self.engine.close_position(110,'final','SOLUSDT')
        trade=self.engine.public()['trades'][0]
        self.assertAlmostEqual(self.engine.state['cash']-self.engine.initial,trade['pnl'])
        self.assertAlmostEqual(trade['qty'],original['qty'])
        self.assertAlmostEqual(trade['fee'],original['fee'])
        self.assertAlmostEqual(trade['exitFee'],e['fee']+(original['qty']-e['qty'])*110*(1-desk.SLIPPAGE)*desk.FEE)
        self.assertEqual(self.engine.public()['stats']['closed'],1)
        self.assertAlmostEqual(performance_review.desk_trades(self.engine)[0]['fees'],original['fee']+trade['exitFee'])

    def test_restart_and_repeat_quote_do_not_repeat_partial(self):
        self.engine.manage(self.quote(107))
        self.engine.save()
        restored=desk.Desk(db_path=self.engine.db_path)
        cash=restored.state['cash']
        restored.manage(self.quote(109))
        self.assertAlmostEqual(restored.state['cash'],cash)
        self.assertEqual(len(restored.state['positions'][0]['partialExits']),1)
        # Sin una nueva barra 4h cerrada se conserva el stop residual persistido.
        self.assertAlmostEqual(restored.state['positions'][0]['sl'],self.engine.state['positions'][0]['sl'])

    def test_partial_is_not_triggered_by_gross_profit_only_or_stale_quote(self):
        p=self.engine.state['positions'][0]
        gross_trigger=p['entry']+1.5*p['risk']/p['qty']
        self.engine.manage(self.quote(gross_trigger))
        self.assertFalse(p.get('partialDone',False))
        stale=self.quote(120);stale['at']-=121
        self.engine.manage(stale)
        self.assertFalse(p.get('partialDone',False))

    def test_minimum_notional_preserves_full_exit_instead_of_creating_dust(self):
        snapshot=self.quote(107)
        snapshot['metadata']['SOLUSDT']['filters'][1]['minNotional']=str(self.engine.state['positions'][0]['qty']*107*.5)
        self.engine.manage(snapshot)
        p=self.engine.state['positions'][0]
        self.assertTrue(p['partialSkippedByFilters'])
        self.assertFalse(p.get('partialDone',False))
        self.engine.manage(self.quote(114))
        self.assertFalse(self.engine.state['positions'])
        self.assertEqual(self.engine.public()['trades'][0]['exitReason'],'Objetivo')

    def test_pause_keeps_partial_management_and_legacy_position_keeps_fixed_target(self):
        self.engine.state['enabled']=False
        self.engine.manage(self.quote(107))
        self.assertTrue(self.engine.state['positions'][0]['partialDone'])
        p=self.engine.state['positions'][0]
        p.pop('exitPolicy')
        self.engine.manage(self.quote(114))
        self.assertFalse(self.engine.state['positions'])

    def test_time_limit_above_reference_target_is_reported_as_time(self):
        self.engine.manage(self.quote(107))
        self.engine.state['positions'][0]['entryTime']=time.time()-48*3600-1
        self.engine.manage(self.quote(110))
        self.assertEqual(self.engine.public()['trades'][0]['exitReason'],'Tiempo máximo')

    def test_database_failure_rolls_back_partial_cash_and_quantity(self):
        cash,qty=self.engine.state['cash'],self.engine.state['positions'][0]['qty']
        with patch.object(self.engine,'connect',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.engine.manage(self.quote(107))
        self.assertEqual(self.engine.state['cash'],cash)
        self.assertEqual(self.engine.state['positions'][0]['qty'],qty)
        self.assertFalse(self.engine.state['positions'][0].get('partialDone',False))

    def test_newly_excluded_instrument_closes_with_fresh_price_and_keeps_history(self):
        with patch.object(desk,'TOKENIZED_EQUITIES',{'SOL'}):
            stale=self.quote(107);stale['at']-=121
            self.engine.manage(stale)
            self.assertEqual(len(self.engine.state['positions']),1)
            self.engine.manage(self.quote(107))
        self.assertFalse(self.engine.state['positions'])
        trade=self.engine.public()['trades'][0]
        self.assertEqual(trade['exitReason'],'Instrumento fuera del universo cripto')
        self.assertAlmostEqual(self.engine.state['cash']-self.engine.initial,trade['pnl'])

    def test_lot_filters_check_grid_maximum_and_invalid_values(self):
        info=data()['metadata']['SOLUSDT']
        self.assertEqual(partial_quantity(.061,1000,info),0)
        self.assertEqual(partial_quantity(float('nan'),100,info),0)
        self.assertEqual(partial_quantity(.06,100,info),0)
        self.assertGreater(partial_quantity(.2,100,info),0)
        info['filters'][0]['maxQty']='.01'
        self.assertEqual(partial_quantity(.2,100,info),0)


class CatalystTests(unittest.TestCase):
    def decision(self,*,direction='long',symbol='BTC',**changes):
        item=dict(id='news',publishedAt=9990,catalyst='security',direction=-1,
                  confidence=.8,relevant=True,is_noise=False,url='https://example.com')
        item.update(changes)
        result=dict(symbol='BTC',analyzedAt=9995,items=[item])
        agent=Mock()
        agent.snapshot.return_value=dict(data=result)
        with patch.object(gemini_agent,'AGENT',agent):
            decision=gemini_agent.catalyst_gate(symbol,direction,now=10000)
        return decision,agent,result

    def test_only_fresh_relevant_opposing_high_confidence_catalyst_vetoes(self):
        self.assertFalse(self.decision()[0]['allowed'])
        for changes in (dict(direction=1),dict(confidence=.74),dict(catalyst='listing'),
                        dict(relevant=False),dict(is_noise=True),dict(publishedAt=10001),
                        dict(publishedAt=2800),dict(confidence=float('nan'))):
            with self.subTest(changes=changes):self.assertTrue(self.decision(**changes)[0]['allowed'])
        self.assertTrue(self.decision(direction='short')[0]['allowed'])
        self.assertTrue(self.decision(symbol='SOL')[0]['allowed'])

    def test_stale_analysis_does_not_veto_and_never_triggers_generation(self):
        _,agent,result=self.decision()
        result['analyzedAt']=2800
        with patch.object(gemini_agent,'AGENT',agent):
            self.assertTrue(gemini_agent.catalyst_gate('BTC','long',now=10000)['allowed'])
        agent.request.assert_not_called()
        agent.choose_market.assert_not_called()


class FullUniverseSelectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(desk,'rank_relative_strength',side_effect=ranked_fixture))
        folder=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.engine=desk.Desk(db_path=Path(folder)/'desk.sqlite')
        fund_unit_desk(self,self.engine)
        self.snapshot=add_markets(data(),[f'COIN{i:02}' for i in range(80)])
        self.agent=Mock()
        self.agent.snapshot.return_value=dict(data=None)
        self.agent.choose_market=AsyncMock(return_value=dict(symbol='COIN79USDT',reason='Única señal',confidence=.8))
        self.stack.enter_context(patch.object(gemini_agent,'AGENT',self.agent))
        self.downloaded=[]
        async def candles(sym,interval=None):
            self.downloaded.append(sym.source_symbol)
            return [dict(time=int(time.time())//3600*3600-3600,open=100,high=101,low=99,close=100,
                         volume=100,market=sym.source_symbol)]
        self.stack.enter_context(patch.object(self.engine,'candidate_candles',side_effect=candles))
        self.all_ready=False
        def analyze(rows,*args):
            ready=self.all_ready or rows[0]['market']=='COIN79USDT'
            return dict(bar=rows[0]['time'],close=100,ema20=100,ema50=99,atr=2,rsi=55,
                        trend=ready,pullback=ready,rows=rows)
        self.stack.enter_context(patch.object(desk,'tape',side_effect=analyze))

    async def test_signal_beyond_second_batch_is_considered_before_any_entry(self):
        await self.engine.scan(self.snapshot)
        self.assertEqual(set(self.downloaded),set(self.snapshot['metadata']) | {'BTCUSDT'})
        self.assertEqual(self.agent.choose_market.call_args.args[0][0]['symbol'],'COIN79USDT')
        self.assertEqual([p['symbol'] for p in self.engine.state['positions']],['COIN79USDT'])
        self.assertEqual(self.engine.state['selectorCoverage']['evaluated'],81)

    async def test_single_choice_is_exclusive_and_persisted_for_the_entire_bar(self):
        self.all_ready=True
        self.agent.choose_market.return_value=dict(symbol='COIN00USDT',reason='Comparación',confidence=.8)
        await self.engine.scan(self.snapshot)
        await self.engine.scan(self.snapshot)
        self.assertEqual([p['symbol'] for p in self.engine.state['positions']],['COIN00USDT'])
        self.agent.choose_market.assert_awaited_once()
        self.engine.save()
        restored=desk.Desk(db_path=self.engine.db_path)
        self.assertEqual(restored.state['selectorDecision'],self.engine.state['selectorDecision'])
        self.assertEqual(self.engine.state['positions'][0]['evidence']['selection']['role'],'exclusive-setup')
        self.assertEqual(self.engine.state['selectorCoverage']['ready'],80)
        self.assertLessEqual(len(self.agent.choose_market.call_args.args[0]),24)

    async def test_cash_choice_is_not_replaced_with_rotating_entries(self):
        self.all_ready=True
        self.agent.choose_market.return_value=dict(symbol=None,reason='Efectivo',confidence=.8)
        await self.engine.scan(self.snapshot)
        await self.engine.scan(self.snapshot)
        self.assertEqual(self.engine.state['positions'],[])
        self.agent.choose_market.assert_awaited_once()

    async def test_local_fallback_is_one_viable_setup_without_fake_ai_confidence(self):
        self.all_ready=True
        self.agent.choose_market.return_value=None
        await self.engine.scan(self.snapshot)
        self.assertEqual(len(self.engine.state['positions']),1)
        evidence=self.engine.state['positions'][0]['evidence']['selection']
        self.assertEqual(evidence['source'],'rules')
        self.assertNotIn('confidence',evidence)

    async def test_correlation_uses_aligned_closed_returns_and_does_not_send_positions(self):
        self.engine.state['positions']=[dict(symbol='ETHUSDT')]
        now=int(time.time())//3600*3600
        price=100
        rows=[]
        for i in range(73):
            price*=math.exp(.001*(i%7+1))
            rows.append(dict(time=now-(73-i)*3600,open=price,close=price,high=price,low=price,volume=100))
        self.assertIsNone(self.engine.setup_correlation(rows))
        self.engine.candle_cache[('ETHUSDT','1h')]=(now,rows)
        self.assertEqual(self.engine.setup_correlation(rows),1)


class WeightedMetaTests(EconomicDatabaseTests):
    def test_net_r_and_account_conversion_determine_sgd_weight(self):
        tid=self.trade(pnl=9)
        with bots._conn() as con:
            con.execute('UPDATE bot_trades SET economic_risk_dist=2,quote_to_account=1.5 WHERE id=?',(tid,))
        real=learning._meta_sgd_step
        with patch.object(learning,'_meta_sgd_step',wraps=real) as step:
            self.assertEqual(learning.meta_learn_new_trades('scalper'),1)
        self.assertEqual(step.call_args.args[2:],(True,3.0))
        self.assertEqual(learning.meta_summary('scalper')['objective'],'r-weighted-logistic-v1')

    def test_low_win_rate_with_large_winners_is_not_learned_as_high_loss_rate(self):
        model=learning._new_model()
        for i in range(300):
            won=i%3==0
            learning._meta_sgd_step(model,{'bias':1},won,3 if won else 1)
        self.assertGreater(learning._sigmoid(model['w']['bias']),.5)

    def test_missing_initial_risk_is_not_reconstructed_from_a_moved_stop(self):
        tid=self.trade(pnl=5)
        with bots._conn() as con:
            con.execute('UPDATE bot_trades SET initial_risk_dist=NULL,sl=99.99 WHERE id=?',(tid,))
        self.assertEqual(learning.meta_learn_new_trades('scalper'),0)


class GateIntegrationTests(EconomicDatabaseTests):
    def test_unreplayed_catalyst_is_stored_in_shadow_without_changing_base_policy(self):
        bot=bots.TradingBot(bots.SCALPER)
        with patch.object(gemini_agent,'catalyst_gate',return_value=dict(allowed=False,reason='Veto contextual')):
            bot._open(bots.SYMBOLS_BY_ID['BTC'],'long',100,2,.8,
                      dict(reasons=[],entryQuote=dict(price=100,time=bots.time.time()),candles=[dict(time=0,open=100,high=101,low=99,close=100,volume=100)],confirmations=[]),2,4,1)
        self.assertIn('BTC',bot.positions)
        self.assertEqual(bot.balance,bot.cfg.initial_balance)
        with bots._conn() as con:
            snap=json.loads(con.execute('SELECT snapshot FROM bot_trades').fetchone()[0])
        self.assertEqual(snap['catalystGate']['mode'],'shadow')
        self.assertFalse(snap['catalystGate']['shadowDecision']['allowed'])

    def test_probation_limits_apply_to_all_seven_bots_and_slow_frames_are_explicit(self):
        self.assertEqual((learning.PROBATION_SIZE,learning.MAX_PROBATION_POSITIONS),(.25,2))
        self.assertEqual((bots.TREND.work_interval,bots.TREND.trend_interval),('1h','4h'))
        self.assertEqual((bots.SQUEEZE.work_interval,bots.SQUEEZE.trend_interval),('1h','4h'))


class TrendTests(unittest.TestCase):
    def test_squeeze_waits_for_closed_four_hour_confirmation(self):
        rows=[dict(time=i*3600,open=100+i*.2,close=100.1+i*.2,
                   high=100.15+i*.2,low=99.9+i*.2,volume=100) for i in range(280)]
        higher=strategy.resample(rows,14400)
        with patch.object(strategy,'_htf_trend_votes',wraps=strategy._htf_trend_votes) as votes:
            full=strategy.squeeze_scores(rows,higher)
        self.assertEqual(votes.call_args.args[-1],14400)
        for length in (201,202,203,241):
            self.assertEqual(full[:length],strategy.squeeze_scores(rows[:length],strategy.resample(rows[:length],14400)))

    def test_three_blocks_are_causal_require_breakout_and_match_confirmations(self):
        rows=[dict(time=i*3600,open=100+i*.2,close=100.1+i*.2,
                   high=100.15+i*.2,low=99.9+i*.2,volume=100) for i in range(260)]
        higher=strategy.resample(rows,14400)
        factors=strategy.trend_factor_series(rows,higher)
        self.assertEqual(factors[:-1],strategy.trend_factor_series(rows[:-1],strategy.resample(rows[:-1],14400)))
        self.assertTrue(any(f['score']>0 for f in factors[200:]))
        confirmations=strategy.confirmations('trend',rows,higher)
        self.assertEqual(len(confirmations),3)
        self.assertAlmostEqual(sum(c['vote']*w for c,w in zip(confirmations,(.4,.4,.2))),factors[-1]['score'])
        flat=[dict(c,open=100,high=101,low=99,close=100) for c in rows]
        self.assertFalse(any(strategy.trend_scores(flat,strategy.resample(flat,14400))))


class BudgetTests(unittest.TestCase):
    def test_virtual_topup_is_audited_idempotent_and_excluded_from_pnl(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(mt5_performance,'DB_PATH',Path(folder)/'mt5.sqlite'):
            owner='demo'
            settings=dict(initial=100,currency='EUR',startedAt=1,cutoffMsc=1000)
            with mt5_performance.connection() as con:
                con.execute('INSERT INTO mt5_sessions VALUES (?,?)',(owner,json.dumps(settings)))
            account=NS(currency='EUR',balance=9995,equity=9995,credit=0)
            deals=[deal(1,0,10000,type=2,position_id=0),deal(2,2,-5,entry=1)]
            mt5_performance.ingest(owner,account,[],deals,990,full=True)
            self.assertEqual(mt5_performance.ensure_virtual_budget(owner,1000,'EUR',1000),900)
            self.assertEqual(mt5_performance.ensure_virtual_budget(owner,1000,'EUR',1001),0)
            historical=mt5_performance.performance(owner,[])
            self.assertEqual(historical['account']['reconciliationDifference'],0)
            mt5_performance.ingest(owner,account,[],deals,1001,full=True)
            capital=mt5_performance.capital(owner,account,[])
            self.assertEqual((capital['allocatedCapital'],capital['equity']),(1000,995))
            result=mt5_performance.performance(owner,[])
            self.assertEqual(result['account']['totalPnl'],-5)
            self.assertEqual(result['account']['netFunding'],1000)
            self.assertEqual(result['account']['reconciliationDifference'],0)
            self.assertEqual(mt5_performance.session(owner)['cutoffMsc'],1000)


class MakerTests(unittest.TestCase):
    def order(self,side='buy'):
        return PostOnlyOrder.place('BTCUSDT',side,1,dict(at=100,bidPrice=100,askPrice=101,bidQty=2,askQty=2),now=100,ttl=60,fee_rate=.001)

    def trade(self,order,identifier=1,at=101,price=100,qty=3,maker=True):
        order.trade(dict(symbol='BTCUSDT',id=identifier,at=at,price=price,quantity=qty,isBuyerMaker=maker))

    def test_no_trade_or_wrong_aggressor_does_not_fill_at_touch(self):
        order=self.order()
        self.assertEqual(order.filled,0)
        self.trade(order,maker=False)
        self.assertEqual(order.filled,0)
        self.trade(order,identifier=2,qty=2)
        self.assertEqual(order.filled,0)
        self.trade(order,identifier=3,qty=.5)
        self.assertEqual(order.filled,.5)
        self.assertAlmostEqual(order.public()['netCashFlow'],-50.05)

    def test_duplicates_stale_events_expiry_and_disconnect_never_fill(self):
        order=self.order()
        self.trade(order,qty=2)
        self.trade(order,qty=100)
        self.trade(order,identifier=2,at=99,qty=100)
        self.assertEqual(order.filled,0)
        self.trade(order,identifier=3,at=160,qty=100)
        self.assertEqual(order.status,'expired')
        order=self.order();order.disconnect();self.trade(order)
        self.assertEqual(order.filled,0)

    def test_sell_is_posted_at_ask_with_explicit_fees_and_bounded_fills(self):
        order=self.order('sell')
        self.trade(order,price=102,qty=.25,maker=False)
        self.assertEqual(order.filled,.25)
        self.trade(order,identifier=2,price=101,qty=20,maker=False)
        self.assertEqual(order.filled,1)
        self.assertAlmostEqual(order.fees,.101)
        self.assertEqual(order.status,'filled')
