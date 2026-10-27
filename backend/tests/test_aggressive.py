from dataclasses import replace
import json
import math
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from app import aggressive,bots,desk,learning,strategy,training
from app.mt5_gateway import DemoGateway
from app.spot_orders import partial_quantity
from test_desk import data
from test_learning_economics import EconomicDatabaseTests
from test_mt5_demo import setup_api


class CausalityTests(unittest.TestCase):
    def rows(self,step=3600,n=300):
        result=[]
        for i in range(n):
            price=100+i*.08+math.sin(i/6)*.5
            result.append(dict(time=1700000000//step*step+i*step,open=price-.05,close=price,
                               high=price+.2,low=price-.3,volume=100+i%8*10))
        return result

    def test_all_seven_profiles_keep_prefix_unchanged_with_future_higher_bars(self):
        for cfg in bots.ALL_BOTS:
            step=training.WORK_SECONDS[cfg.work_interval]
            rows=self.rows(step)
            higher=strategy.resample(rows,aggressive.HIGHER_SECONDS[cfg.id])
            full=strategy.score_series(cfg.id,rows,higher)
            for cut in (69,201,241,281):
                self.assertEqual(full[:cut],strategy.score_series(cfg.id,rows[:cut],higher),cfg.id)

    def test_flow_waits_for_actual_four_hour_close(self):
        from app.flow import flow_scores
        rows=self.rows()
        higher=strategy.resample(rows,14400)
        prefix=rows[:242]
        known=[c for c in higher if c['time']+14400<=prefix[-1]['time']]
        self.assertEqual(flow_scores(prefix,higher),flow_scores(prefix,known))

    def test_desk_discards_unclosed_bar_and_requires_higher_regime(self):
        rows=self.rows()
        now=rows[-1]['time']+3600
        base=desk.tape(rows,now)
        self.assertTrue(base['higherRegimeConfirmed'])
        future=dict(rows[-1],time=now,close=99999,high=100000)
        self.assertEqual(base,desk.tape(rows+[future],now))
        short=desk.tape(rows[-80:],now)
        self.assertFalse(short['entryAllowed'])
        self.assertFalse(short['higherRegimeConfirmed'])


class AggressiveSizingTests(unittest.TestCase):
    def test_cash_desk_keeps_full_target_and_waits_two_r_before_trailing(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=desk.Desk('stocks',Path(folder)/'stocks.sqlite')
            engine.last_data=None
            now=time.time()
            info=data()['metadata']['SOLUSDT']
            order=desk.size_order(100,100,100,2,info,partial=False,fee=engine.fee,slippage=engine.slippage,min_net_rr=2)
            p=dict(order,symbol='AAPL',entryKey='AAPL:one',entryTime=now,quoteAt=now,atr=2,
                   initialDist=4,best=order['entry'],lastBid=100,status='open',marketFilters=info['filters'])
            engine.state['positions']=[p];engine.state['cash']=100-p['notional']-p['fee']
            with engine.connect() as con:
                con.execute('INSERT INTO trades(entry_key,payload) VALUES (?,?)',(p['entryKey'],json.dumps(p)))
            def quote(bid):
                return dict(at=time.time(),metadata={},books={'AAPL':dict(bidPrice=bid,askPrice=bid+.01,referenceAt=time.time())})
            initial=p['sl']
            engine.manage(quote(106))
            self.assertEqual(p['sl'],initial)
            self.assertFalse(p.get('partialDone',False))
            engine.manage(quote(109))
            self.assertGreater(p['sl'],p['entry'])
            engine.manage(quote(114))
            self.assertFalse(engine.state['positions'])
            self.assertEqual(engine.public()['trades'][0]['exitReason'],'Objetivo')
            self.assertAlmostEqual(engine.state['cash']-100,engine.public()['trades'][0]['pnl'])
            self.assertEqual(engine.public()['policy']['minNetRewardRisk'],2)

    def test_more_capacity_still_obeys_cash_economic_risk_and_filters(self):
        for cash in (10,100,1000):
            order=desk.size_order(1000,cash,100,2,data()['metadata']['SOLUSDT'])
            if order:
                self.assertLessEqual(order['notional'],350)
                self.assertLessEqual(order['notional']+order['fee'],cash)
                self.assertLessEqual(order['risk'],15)
                self.assertEqual(order['entry']-order['sl'],4)
                self.assertEqual(order['tp']-order['entry'],12)
        self.assertIsNone(desk.size_order(1,1,100,2,data()['metadata']['SOLUSDT']))

    def test_partial_third_and_legacy_half_preserve_negotiable_remainders(self):
        info=data()['metadata']['SOLUSDT']
        self.assertEqual(partial_quantity(3,100,info),1.5)
        self.assertEqual(partial_quantity(3,100,info,fraction=1/3),.99)
        self.assertEqual(partial_quantity(.1,100,info,fraction=1/3),0)

    def test_probation_is_limited_to_quarter_and_two_slots_without_global_cap_change(self):
        from app.portfolio_risk import POLICY
        self.assertEqual((learning.PROBATION_SIZE,learning.MAX_PROBATION_POSITIONS),(.25,2))
        self.assertEqual((POLICY['maxRiskPct'],POLICY['maxGrossMultiple']),(3,2))
        self.assertEqual(2500*.005*learning.PROBATION_SIZE,3.125)

    def test_adverse_reversal_and_trend_experiments_are_not_applied(self):
        self.assertFalse(aggressive.BOT_UPDATES['meanrev'])
        self.assertFalse(aggressive.BOT_UPDATES['trend'])
        self.assertFalse(aggressive.BOT_UPDATES['flow'])
        self.assertEqual(bots.SCALPER.work_interval,'5m')
        self.assertEqual(bots.SWING.trend_interval,'4h')


class FrozenPositionsTests(EconomicDatabaseTests):
    def test_old_scalper_position_keeps_old_timeout_after_switch_to_five_minutes(self):
        tid=self.trade(status='open')
        with learning._conn() as con:
            con.execute('UPDATE bot_trades SET snapshot=? WHERE id=?',
                        (json.dumps(dict(engineVersion=6,confirmations=[])),tid))
        bot=bots.TradingBot(bots.SCALPER);bot.load_state()
        self.assertEqual(bot.positions['BTC'].max_hold_seconds,2700)
        self.assertIsNone(bot.positions['BTC'].exit_policy)
        self.assertEqual(bot.cfg.max_hold_seconds,14400)

    def test_new_entry_freezes_policy_and_restarts_without_losing_it(self):
        bot=bots.TradingBot(bots.SWING)
        with patch.object(bots.systems,'chart_overlays',return_value={}):
            bot._open(bots.SYMBOLS_BY_ID['BTC'],'long',100,2,.8,
                      dict(reasons=[],entryQuote=dict(price=100,time=bots.time.time()),candles=[dict(time=0,open=100,high=101,low=99,close=100,volume=100)],confirmations=[]),2,7,2.6,probation_why='Sin validar')
        self.assertEqual(bot.positions['BTC'].exit_policy['trailStartR'],2)
        restored=bots.TradingBot(bots.SWING);restored.load_state()
        self.assertEqual(restored.positions['BTC'].exit_policy,bot.positions['BTC'].exit_policy)
        self.assertEqual(restored.positions['BTC'].max_hold_seconds,bot.cfg.max_hold_seconds)


class BrokerExposureTests(unittest.TestCase):
    def setUp(self):
        self.api,self.config,_,_=setup_api()
        self.config['symbols']={'EURUSD':'EURUSD'}
        self.infos={
            'EURUSD':NS(currency_base='EUR',currency_profit='USD',trade_contract_size=100000,trade_calc_mode=0),
            'GBPUSD':NS(currency_base='GBP',currency_profit='USD',trade_contract_size=100000,trade_calc_mode=0),
            'USDJPY':NS(currency_base='USD',currency_profit='JPY',trade_contract_size=100000,trade_calc_mode=0),
            'XAUUSD':NS(currency_base='XAU',currency_profit='USD',trade_contract_size=100,trade_calc_mode=4),
            'NVDA':NS(currency_base='USD',currency_profit='USD',trade_contract_size=1,trade_calc_mode=32)}
        self.api.symbol_info.side_effect=lambda symbol:self.infos.get(symbol)
        self.api.symbol_info_tick.return_value=NS(time=1000,bid=1.1,ask=1.1001)
        self.gateway=DemoGateway(self.api,self.config)

    def test_forex_gold_and_stocks_use_same_verified_usd_to_eur_conversion(self):
        self.assertEqual(self.gateway.notional_eur('EURUSD',.01,1.1,1000),1000)
        self.assertAlmostEqual(self.gateway.notional_eur('GBPUSD',.01,1.3,1000),1300/1.1)
        self.assertAlmostEqual(self.gateway.notional_eur('USDJPY',.01,150,1000),1000/1.1)
        self.assertAlmostEqual(self.gateway.notional_eur('XAUUSD',.01,2000,1000),2000/1.1)
        self.assertAlmostEqual(self.gateway.notional_eur('NVDA',1,200,1000),200/1.1)
        self.api.order_send.assert_not_called()

    def test_missing_stale_or_wrong_currency_conversion_remains_unknown(self):
        self.api.symbol_info_tick.return_value.time=800
        self.assertIsNone(self.gateway.notional_eur('GBPUSD',.01,1.3,1000))
        self.api.symbol_info_tick.return_value.time=1000
        self.infos['EURUSD'].currency_base='GBP'
        self.assertIsNone(self.gateway.notional_eur('GBPUSD',.01,1.3,1000))
        self.assertIsNone(self.gateway.notional_eur('UNKNOWN',1,100,1000))
