import asyncio
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import desk, gemini_agent


def data():
    return dict(at=int(time.time()),metadata={'SOLUSDT':dict(baseAsset='SOL',quoteAsset='USDT',status='TRADING',isSpotTradingAllowed=True,
        filters=[dict(filterType='LOT_SIZE',stepSize='.01',minQty='.01'),dict(filterType='MIN_NOTIONAL',minNotional='5')])},
        books={'SOLUSDT':dict(bidPrice='99.99',askPrice='100',askQty='10000')},
        tickers={'SOLUSDT':dict(quoteVolume='50000000',priceChangePercent='2')})


class SizingTests(unittest.TestCase):
    def test_size_caps_exposure_cash_and_initial_risk(self):
        for a in (.05,2,20):
            for cash in (10,500,1000):
                order=desk.size_order(1000,cash,100,a,data()['metadata']['SOLUSDT'])
                if order:
                    self.assertLessEqual(order['notional'],1000/3)
                    self.assertLessEqual(order['notional']+order['fee'],cash)
                    self.assertLessEqual(order['risk'],10)

    def test_minimum_lot_is_never_forced(self):
        self.assertIsNone(desk.size_order(1,1,100,2,data()['metadata']['SOLUSDT']))

    def test_unknown_identity_is_filtered_and_pool_capped(self):
        d=data()
        for i in range(100):
            key=f'COIN{i}USDT'
            d['metadata'][key]=dict(baseAsset=f'COIN{i}',quoteAsset='USDT',status='TRADING',isSpotTradingAllowed=True)
            d['tickers'][key]=dict(quoteVolume='20000000',priceChangePercent='1')
            d['books'][key]=dict(bidPrice='100',askPrice='100.01')
        rows=desk.universe(d)
        self.assertEqual(len(rows),81)
        self.assertEqual(sum(c['eligible'] for c in rows),1)
        self.assertEqual(rows[0]['symbol'],'SOLUSDT')

    def test_tape_excludes_open_and_future_candles(self):
        now=100000
        rows=[dict(time=now-900*(80-i),open=100+i,high=102+i,low=99+i,close=101+i,volume=100) for i in range(80)]
        base=desk.tape(rows,now)
        extra=dict(time=now,open=1,high=99999,low=1,close=9999,volume=100)
        self.assertEqual(base,desk.tape(rows+[extra],now))


class DeskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=patch.object(desk,'DB_PATH',Path(self.tmp.name)/'desk.sqlite')
        self.db.start()
        self.engine=desk.Desk()
        self.engine.state['selected']='SOLUSDT'
        self.engine.state['selectionAt']=time.time()
        self.analysis=dict(bar=int(time.time())-900,close=100,ema20=100,ema50=99,atr=2,rsi=55,trend=True,pullback=True,rows=[])

    async def asyncTearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    async def entry(self,identity=True):
        with patch.object(self.engine,'wire',AsyncMock(return_value=dict(ok=identity,detail='Referencia probada',url='https://solana.com/'))),patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await self.engine.scan(data())

    async def test_wire_veto_cannot_be_overridden_by_price(self):
        await self.entry(False)
        self.assertIsNone(self.engine.state['position'])
        self.assertEqual(self.engine.state['cash'],desk.INITIAL)

    async def test_entry_key_prevents_duplicate_after_restart(self):
        await self.entry()
        first=self.engine.state['position']
        self.engine.close_position(101,'test')
        restarted=desk.Desk()
        with patch.object(restarted,'wire',AsyncMock(return_value=dict(ok=True,detail='ok'))),patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await restarted.scan(data())
        self.assertIsNone(restarted.state['position'])
        self.assertEqual(len(restarted.public()['trades']),1)
        self.assertIn('SOLUSDT',first['entryKey'])

    async def test_cash_pnl_and_fees_reconcile_after_close(self):
        await self.entry()
        order=dict(self.engine.state['position'])
        self.assertAlmostEqual(self.engine.state['cash'],1000-order['notional']-order['fee'])
        self.engine.close_position(103,'test')
        trade=self.engine.public()['trades'][0]
        self.assertAlmostEqual(self.engine.state['cash']-1000,trade['pnl'])
        self.assertGreater(trade['exitFee'],0)

    async def test_restart_keeps_stop_and_cash(self):
        await self.entry()
        self.engine.state['position']['sl']=101
        self.engine.save()
        restarted=desk.Desk()
        self.assertEqual(restarted.state['position']['sl'],101)
        self.assertEqual(restarted.state['cash'],self.engine.state['cash'])

    async def test_stale_quote_cannot_close_at_fabricated_price(self):
        await self.entry()
        d=data();d['at']-=1000
        d['books']['SOLUSDT']['bidPrice']='1'
        self.engine.manage(d)
        self.assertIsNotNone(self.engine.state['position'])

    async def test_pause_still_manages_stop(self):
        await self.entry()
        await self.engine.control('pause')
        d=data();d['books']['SOLUSDT']['bidPrice']='90'
        self.engine.manage(d)
        self.assertIsNone(self.engine.state['position'])
        self.assertFalse(self.engine.state['enabled'])

    async def test_loss_of_trend_closes_open_position(self):
        await self.entry()
        self.analysis.update(trend=False,close=98)
        await self.entry()
        self.assertIsNone(self.engine.state['position'])
        self.assertEqual(self.engine.public()['trades'][0]['exitReason'],'Tape detecta pérdida de tendencia')

    async def test_paused_desk_does_not_reselect_or_spend_tokens(self):
        self.engine.state.update(enabled=False,selectionAt=0)
        agent=AsyncMock()
        with patch.object(desk.gemini_agent,'AGENT',agent):
            await self.entry()
        agent.choose_market.assert_not_awaited()
        self.assertIsNone(self.engine.state['position'])

    async def test_position_prevents_rotation_even_after_six_hours(self):
        await self.entry()
        self.engine.state['selectionAt']=0
        agent=AsyncMock()
        with patch.object(desk.gemini_agent,'AGENT',agent):
            await self.entry()
        agent.choose_market.assert_not_awaited()
        self.assertIsNotNone(self.engine.state['position'])

    async def test_chasing_and_insufficient_liquidity_block_entry(self):
        self.analysis['ema20']=95
        await self.entry()
        self.assertIsNone(self.engine.state['position'])
        self.analysis['ema20']=100
        d=data();d['books']['SOLUSDT']['askQty']='0.001'
        with patch.object(self.engine,'wire',AsyncMock(return_value=dict(ok=True,detail='ok'))),patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await self.engine.scan(d)
        self.assertIsNone(self.engine.state['position'])

    async def test_daily_limit_blocks_restart_of_entries(self):
        self.engine.state['cash']=940
        self.engine.manage(data())
        await self.engine.control('start')
        self.assertFalse(self.engine.state['enabled'])

    async def test_wire_requires_exact_handle_on_official_site(self):
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,text='<a href="https://x.com/solana-scams">Fake</a>')))
        try:
            with patch.object(desk.market_data,'client',return_value=client):
                result=await self.engine.wire('SOL')
            self.assertFalse(result['ok'])
        finally: await client.aclose()


class SelectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_cannot_select_an_unlisted_symbol(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(gemini_agent,'DB_PATH',Path(tmp)/'gemini.sqlite'):
            agent=gemini_agent.Agent(gemini_agent.Settings(key='fake'))
            raw=dict(steps=[dict(type='model_output',content=[dict(type='text',text=json.dumps(dict(symbol='FAKEUSDT',confidence=.9,reason='Inventada')))])],usage={})
            agent.http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=raw)))
            try:
                result=await agent.choose_market([dict(symbol='SOLUSDT',eligible=True)])
                self.assertIsNone(result)
                self.assertEqual(agent.status()['connectionStatus'],'unavailable')
            finally: await agent.close()
