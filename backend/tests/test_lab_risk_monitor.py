import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

from app import bots,training,learning
from app.config import SYMBOLS_BY_ID


class IndependentRiskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patches=[patch.object(module,'DB_PATH',Path(self.tmp.name)/'lab.db') for module in (bots,training,learning)]
        for p in self.patches: p.start()
        self.bot=bots.TradingBot(bots.SCALPER)
        self.bot.load_state()
        with patch.object(learning,'size_multiplier',return_value=(1,None)),patch.object(bots.systems,'chart_overlays',return_value={}):
            self.bot._open(SYMBOLS_BY_ID['BTC'],'long',100,2,.8,dict(reasons=[],entryQuote=dict(price=100,time=bots.time.time()),candles=[dict(time=0,open=100,high=101,low=99,close=100,volume=100)],confirmations=[]),2,4,.8)

    async def asyncTearDown(self):
        await self.bot.stop_task()
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    async def test_stop_closes_while_signal_download_is_hanging(self):
        entered=asyncio.Event()
        block=asyncio.Event()
        closed=asyncio.Event()
        async def scan():
            entered.set()
            await block.wait()
        original=self.bot._close
        def close(*args):
            original(*args)
            closed.set()
        with patch.object(self.bot,'_scan',side_effect=scan),patch.object(self.bot,'_close',side_effect=close),patch.object(bots.market_data,'get_price',AsyncMock(return_value=dict(price=90,time=time.time()))):
            self.bot.start_task()
            await asyncio.wait_for(entered.wait(),1)
            await asyncio.wait_for(closed.wait(),1)
            self.assertFalse(self.bot._task.done())
            self.assertNotIn('BTC',self.bot.positions)
            await self.bot.stop_task()
        self.assertIsNone(self.bot._risk_task)

    async def test_paused_entries_still_have_an_independent_risk_task(self):
        self.bot.running=False
        called=asyncio.Event()
        async def manage(): called.set()
        with patch.object(self.bot,'_manage_positions',side_effect=manage),patch.object(self.bot,'_scan',AsyncMock()) as scan:
            self.bot.start_task()
            await asyncio.wait_for(called.wait(),1)
            self.assertIsNotNone(self.bot._risk_task)
            scan.assert_not_awaited()
            await self.bot.stop_task()
