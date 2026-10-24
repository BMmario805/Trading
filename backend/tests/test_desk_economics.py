import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

from app import desk,opportunities
from test_desk import data,add_markets


class DeskEconomicsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=patch.object(desk,'DB_PATH',Path(self.tmp.name)/'desk.sqlite')
        self.path.start()
        self.engine=desk.Desk()
        self.analysis=dict(bar=int(time.time())-900,close=100,ema20=100,ema50=99,
                           atr=2,rsi=55,trend=True,pullback=True,rows=[])
        self.patches=[patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),
                      patch.object(desk,'tape',return_value=self.analysis),patch.object(desk.gemini_agent,'AGENT',None)]
        for p in self.patches:p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches):p.stop()
        self.path.stop()
        self.tmp.cleanup()

    async def test_round_trip_friction_blocks_tiny_targets_and_keeps_reduced_allocation(self):
        self.assertIsNone(desk.size_order(1000,1000,100,.01,data()['metadata']['SOLUSDT']))
        order=desk.size_order(1000,1000,100,2,data()['metadata']['SOLUSDT'])
        self.assertLessEqual(order['notional'],1000*desk.MAX_NOTIONAL_FRACTION)
        self.assertGreaterEqual(order['economics']['netRewardRisk'],1)

    async def test_old_scan_cannot_rewind_execution_mark(self):
        await self.engine.scan(data())
        p=self.engine.state['positions'][0]
        p.update(quoteAt=time.time()+1,lastBid=101)
        old=data();old['books']['SOLUSDT']['bidPrice']='1'
        self.engine.manage(old)
        self.assertEqual(self.engine.state['positions'][0]['lastBid'],101)

    async def test_risk_monitor_closes_stop_while_candidate_download_is_waiting(self):
        await self.engine.scan(data())
        waiting=asyncio.Event();entered=asyncio.Event()
        async def hanging(*args):
            entered.set()
            await waiting.wait()
            return []
        with patch.object(desk.market_data,'get_candles',side_effect=hanging):
            scan=asyncio.create_task(self.engine.scan(add_markets(data(),['BTC'])))
            await asyncio.wait_for(entered.wait(),1)
            fresh=data();fresh['at']=time.time();fresh['books']['SOLUSDT']['bidPrice']='90'
            with patch.object(opportunities,'execution_snapshot',AsyncMock(return_value=fresh)):
                await asyncio.wait_for(self.engine.risk_cycle(),1)
            self.assertFalse(self.engine.state['positions'])
            self.assertFalse(scan.done())
            scan.cancel()
            with self.assertRaises(asyncio.CancelledError):await scan
        self.assertEqual(self.engine.public()['trades'][0]['exitReason'],'Stop')

    async def test_stablecoins_do_not_consume_signal_scan(self):
        markets=desk.universe(add_markets(data(),['RLUSD','USDE','USDS','PYUSD','BTC']))
        self.assertEqual({r['base'] for r in markets},{'SOL','BTC'})


class ExecutionBookTests(unittest.IsolatedAsyncioTestCase):
    async def test_execution_books_do_not_wait_for_global_snapshot_lock(self):
        opportunities._book_cache.clear()
        async with opportunities._lock:
            with patch.object(opportunities.market_data,'_binance_get',AsyncMock(return_value=[
                dict(symbol='BTCUSDT',bidPrice='99',askPrice='100',askQty='5')])):
                snapshot=await asyncio.wait_for(opportunities.execution_snapshot(['BTCUSDT'],{}),1)
        self.assertEqual(snapshot['books']['BTCUSDT']['bidPrice'],'99')

