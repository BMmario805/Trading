import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

from app import desk,performance_review as review,market_data,research
from test_desk import data,add_markets


class MetricsTests(unittest.TestCase):
    def test_win_rate_does_not_hide_bad_payoff_or_mix_legacy_results(self):
        trades=[dict(pnl=1,basis='net',fees=.1) for _ in range(3)]+[dict(pnl=-4,basis='net',fees=.1) for _ in range(2)]+[dict(pnl=100,basis='legacy')]
        result=review.metrics(trades)
        self.assertEqual(result['winRate'],60)
        self.assertEqual(result['expectancy'],-1)
        self.assertEqual(result['profitFactor'],.375)
        self.assertEqual(result['legacyPnl'],100)
        self.assertAlmostEqual(result['beforeExplicitCosts'],-4.5)
        self.assertFalse(result['validated'])

    def test_empty_metrics_are_missing_not_zero_expectancy_or_infinite_ratio(self):
        result=review.metrics([])
        self.assertIsNone(result['expectancy'])
        self.assertIsNone(result['profitFactor'])
        self.assertIsNone(result['winRate'])


class DeskEfficiencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine=desk.Desk(db_path=Path(self.tmp.name)/'desk.sqlite')

    def candles(self,now,lag=0):
        seconds=desk.CANDLE_SECONDS
        last=int(now//seconds)*seconds-seconds-lag
        return [dict(time=last-(69-i)*seconds,open=99,high=101,low=98,close=100,volume=100) for i in range(70)]

    async def test_cache_refreshes_at_new_closed_bar(self):
        sym=self.engine.market_symbol('SOLUSDT',data())
        now=1800100
        with patch.object(desk.time,'time',return_value=now),patch.object(market_data,'get_candles',AsyncMock(return_value=self.candles(now))) as get:
            await self.engine.candidate_candles(sym)
            await self.engine.candidate_candles(sym)
        self.assertEqual(get.await_count,1)
        with patch.object(desk.time,'time',return_value=now+desk.CANDLE_SECONDS),patch.object(market_data,'get_candles',AsyncMock(return_value=self.candles(now+desk.CANDLE_SECONDS))) as get:
            rows=await self.engine.candidate_candles(sym)
        self.assertEqual(get.await_count,1)
        self.assertEqual(rows[-1]['time'],1800000)
        self.assertEqual(self.engine.efficiency['candleCacheHits'],1)

    async def test_delayed_feed_is_not_cached_until_next_bar(self):
        sym=self.engine.market_symbol('SOLUSDT',data())
        now=1800100
        with patch.object(desk.time,'time',return_value=now),patch.object(market_data,'get_candles',AsyncMock(return_value=self.candles(now,desk.CANDLE_SECONDS))) as get:
            await self.engine.candidate_candles(sym)
            await self.engine.candidate_candles(sym)
        self.assertEqual(get.await_count,2)
        self.assertFalse(self.engine.candle_cache)

    async def test_fresh_execution_books_are_shared_but_failure_falls_back_per_symbol(self):
        snapshot=add_markets(data(),['BTC'])
        self.engine.task=object()
        analysis=dict(bar=int(time.time())-900,close=100,ema20=100,ema50=99,atr=2,rsi=55,trend=False,pullback=False,rows=[])
        fresh=dict(snapshot,at=time.time(),scope='execution-books')
        with patch.object(self.engine,'candidate_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=analysis),patch.object(self.engine,'market_books',AsyncMock(return_value=fresh)) as get:
            await self.engine.scan(snapshot)
        self.assertEqual(get.await_count,1)
        self.assertEqual(set(get.call_args.args[0]),{'BTCUSDT','SOLUSDT'})
        self.engine.state['candidates']=[]
        with patch.object(self.engine,'candidate_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=analysis),patch.object(self.engine,'market_books',AsyncMock(side_effect=[ValueError('one missing book'),fresh,fresh])) as get:
            await self.engine.scan(snapshot)
        self.assertEqual(get.await_count,3)
        self.assertTrue(all(c['status']=='waiting' for c in self.engine.state['candidates']))

    async def test_trade_accounting_and_current_policy_are_kept_separate(self):
        analysis=dict(bar=int(time.time())-900,close=100,ema20=100,ema50=99,atr=2,rsi=55,trend=True,pullback=True,rows=[])
        with patch.object(market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=analysis):
            await self.engine.scan(data())
        self.engine.close_position(103,'test')
        trades=review.desk_trades(self.engine)
        self.assertAlmostEqual(trades[0]['pnl'],self.engine.state['cash']-self.engine.initial)
        self.assertGreater(trades[0]['fees'],0)
        self.assertGreater(trades[0]['slippage'],0)
        result=review.opportunity_diagnostics(self.engine)
        min_atr=result['minimumAtrPct']/100*100
        fill=100*(1+desk.SLIPPAGE)
        plan=desk.planned_trade(fill,fill-2*min_atr,fill+6*min_atr,1,fee_rate=desk.FEE,exit_slippage=desk.SLIPPAGE)
        self.assertAlmostEqual(plan['netRewardRisk'],desk.MIN_NET_REWARD_RISK)


class ResearchHorizonTests(unittest.IsolatedAsyncioTestCase):
    async def test_four_hour_research_uses_direct_binance_history(self):
        now=1800000
        rows=[[int((now-(610-i)*14400)*1000),'100','101','99','100','100'] for i in range(610)]
        with patch.object(market_data,'_binance_get',AsyncMock(return_value=rows)) as get:
            result=await research._bounded_history('BTCUSDT','4h',365,9000,now)
        self.assertTrue(result)
        self.assertEqual(get.call_args.args[1]['interval'],'4h')
