from desk_unit_support import fund_unit_desk
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock,patch
from zoneinfo import ZoneInfo

import httpx

from app import desk, cash_markets, market_data, portfolio_risk, open_positions, opportunities
from app.main import app
from test_desk import data,add_markets


class SessionsTests(unittest.TestCase):
    def stamp(self,value):
        return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('America/New_York')).timestamp()

    def test_weekend_and_dst_sessions(self):
        for market,value,expected in [('forex','2026-10-18T16:59',False),('forex','2026-10-18T17:00',True),
            ('forex','2026-10-16T17:00',False),('stocks','2026-10-18T12:00',False),
            ('stocks','2026-10-19T09:29',False),('stocks','2026-10-19T09:30',True),
            ('stocks','2026-11-02T15:59',True),('stocks','2026-11-02T16:00',False)]:
            self.assertEqual(cash_markets.market_session(market,self.stamp(value))['open'],expected)

    def test_known_new_symbols_keep_the_correct_risk_factor(self):
        self.assertEqual(portfolio_risk.factor('AUDUSD'),'fx')
        self.assertEqual(portfolio_risk.factor('AMD'),'us-equities')


class DeskIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engines={key:desk.Desk(key,Path(self.tmp.name)/(key+'.sqlite')) for key in ('crypto','forex','stocks')}
        for engine in self.engines.values(): fund_unit_desk(self,engine)

    async def test_pause_cash_and_history_are_independent_and_survive_restart(self):
        await self.engines['forex'].control('pause')
        self.engines['stocks'].state['cash']=91
        self.engines['stocks'].save()
        for key,engine in self.engines.items():
            restored=desk.Desk(key,engine.db_path)
            self.assertEqual(restored.state['cash'],91 if key=='stocks' else 100 if key=='forex' else desk.INITIAL)
            self.assertEqual(restored.state['enabled'],key!='forex')
            self.assertEqual(len(restored.public()['roles']),7)
            self.assertEqual(restored.public()['currency'],'USDT' if key=='crypto' else 'USD')

    async def test_api_scopes_controls_and_keeps_legacy_crypto_endpoint(self):
        with patch.object(desk,'DESKS',self.engines),patch.object(desk,'DESK',self.engines['crypto']):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
                self.assertEqual(len((await client.get('/api/desks')).json()),3)
                self.assertEqual((await client.get('/api/desk')).json()['id'],'crypto')
                await client.post('/api/desks/forex/pause')
                self.assertTrue((await client.get('/api/desks/crypto')).json()['enabled'])
                self.assertFalse((await client.get('/api/desks/forex')).json()['enabled'])
                self.assertEqual((await client.post('/api/desks/stocks/reset')).status_code,400)
                self.assertEqual((await client.get('/api/desks/unknown')).status_code,404)

    async def test_symbol_cooldown_persists_after_a_loss(self):
        engine=self.engines['crypto']
        analysis=dict(bar=int(time.time())-900,close=100,ema20=100,ema50=99,atr=2,rsi=55,
                      trend=True,pullback=True,rows=[])
        with patch.object(market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=analysis):
            await engine.scan(data())
            engine.close_position(98,'Stop')
            restored=desk.Desk('crypto',engine.db_path)
            analysis['bar']+=900
            await restored.scan(data())
        self.assertEqual(restored.state['positions'],[])
        self.assertGreater(restored.state['cooldowns']['SOLUSDT'],time.time()+3500)

    async def test_cash_price_timestamp_is_not_refreshed_by_a_new_snapshot(self):
        engine=self.engines['stocks']
        snapshot=dict(at=time.time(),books={'AAPL':dict(bidPrice=100,askPrice=101,referenceAt=time.time()-121)})
        self.assertIsNone(engine.quote('AAPL',snapshot))
        self.assertEqual(open_positions._book(snapshot,'AAPL','long',time.time()),(None,None))

    async def test_failed_market_backs_off_without_blocking_the_rest(self):
        engine=self.engines['crypto']
        snapshot=add_markets(data(),['BTC'])
        analysis=dict(bar=int(time.time())-900,close=100,ema20=100,ema50=99,atr=2,rsi=55,
                      trend=False,pullback=False,rows=[])
        with patch.object(market_data,'get_candles',AsyncMock(side_effect=[ValueError('unavailable'),[]])),patch.object(desk,'tape',return_value=analysis):
            await engine.scan(snapshot)
        failed=next(c for c in engine.state['candidates'] if c['symbol']=='SOLUSDT')
        self.assertGreater(failed['retryAfter'],time.time())
        with patch.object(market_data,'get_candles',AsyncMock(return_value=[])) as candles,patch.object(desk,'tape',return_value=analysis):
            await engine.scan(snapshot)
        self.assertEqual(candles.await_count,1)
        self.assertEqual(candles.call_args.args[0].source_symbol,'BTCUSDT')


class CashFeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_unicode_binance_symbol_is_sent_without_json_ascii_escapes(self):
        opportunities._book_cache.clear()
        book=dict(symbol='牛来USDT',bidPrice='1',askPrice='1.001',askQty='100')
        with patch.object(market_data,'_binance_get',AsyncMock(return_value=[book])) as get:
            result=await opportunities.execution_snapshot(['牛来USDT'],{})
        self.assertIn('牛来',get.call_args.args[1]['symbols'])
        self.assertNotIn('\\u',get.call_args.args[1]['symbols'])
        self.assertIn('牛来USDT',result['books'])
        opportunities._book_cache.clear()

    async def test_closed_market_does_not_create_prices_or_download_data(self):
        feed=cash_markets.CashMarketFeed('stocks')
        with patch.object(feed,'session',return_value=dict(open=False,label='Cerrado')),patch.object(feed,'read',AsyncMock()) as read:
            snapshot=await feed.snapshot()
        read.assert_not_awaited()
        self.assertFalse(snapshot['books'])
        self.assertTrue(all(not c['eligible'] for c in feed.universe(snapshot)))

    async def test_holiday_session_stale_quotes_and_wrong_currency_fail_closed(self):
        for stamp,verified,currency in [(time.time(),False,'USD'),(time.time()-121,True,'USD'),(time.time(),True,'EUR')]:
            feed=cash_markets.CashMarketFeed('stocks')
            quote=dict(at=stamp,sessionVerified=verified,currency=currency,price=100,volume=1e8,change=0)
            with patch.object(feed,'session',return_value=dict(open=True,label='Regular')),patch.object(feed,'read',AsyncMock(return_value=quote)):
                snapshot=await feed.snapshot(['AAPL'])
            self.assertFalse(feed.quality('AAPL',snapshot)['eligible'])

    async def test_valid_reference_discloses_synthetic_spread_and_is_usd(self):
        feed=cash_markets.CashMarketFeed('stocks')
        quote=dict(at=time.time(),sessionVerified=True,currency='USD',price=100,volume=1e8,change=0)
        with patch.object(feed,'session',return_value=dict(open=True,label='Regular')),patch.object(feed,'read',AsyncMock(return_value=quote)):
            snapshot=await feed.snapshot(['AAPL'])
        self.assertTrue(feed.quality('AAPL',snapshot)['eligible'])
        self.assertTrue(feed.wire('AAPL',snapshot)['syntheticSpread'])
        self.assertEqual(snapshot['metadata']['AAPL']['quoteAsset'],'USD')


class ConfirmationTests(unittest.TestCase):
    def test_tokenized_stocks_do_not_enter_the_crypto_desk(self):
        rows=desk.universe(add_markets(data(),['SNDKB','SOXLB','KORUB','SNXXB','INTWB','TQQQB','BNB','JUP']))
        eligible={r['symbol'] for r in rows if r['eligible']}
        self.assertNotIn('SOXLBUSDT',eligible)
        self.assertNotIn('SNDKBUSDT',eligible)
        for base in ('KORUB','SNXXB','INTWB','TQQQB'):
            self.assertNotIn(base+'USDT',eligible)
        self.assertIn('BNBUSDT',eligible)
        self.assertIn('JUPUSDT',eligible)
    def rows(self,down=False):
        now=1800000
        closes=[100+i*.05*(-1 if down else 1) for i in range(90)]
        return now,[dict(time=now-(90-i)*desk.CANDLE_SECONDS,open=p+.02,close=p,high=p+.2,low=p-.2,volume=100) for i,p in enumerate(closes)]

    def test_declining_discount_is_not_an_entry(self):
        now,rows=self.rows(True)
        result=desk.tape(rows,now)
        self.assertFalse(result['entryAllowed'])
        self.assertIsNone(result['strategy'])

    def test_pullback_requires_a_bullish_confirmation(self):
        now,rows=self.rows()
        rows[-2].update(low=103,close=104.2)
        rows[-1].update(open=104.15,close=104.4,high=104.7,low=104.0)
        with patch.object(desk,'ema',side_effect=[[104]*90,[103]*90]):
            result=desk.baseline_tape(rows,now)
        self.assertEqual(result['strategy'],'trend-pullback')
        self.assertTrue(result['entryAllowed'])

    def test_risk_and_reward_are_net_of_fees(self):
        order=desk.size_order(100,100,100,2,data()['metadata']['SOLUSDT'])
        self.assertLessEqual(order['risk'],100*desk.RISK_FRACTION)
        self.assertGreaterEqual(order['economics']['netRewardRisk'],1.2)
        self.assertIsNone(desk.size_order(100,100,100,.05,data()['metadata']['SOLUSDT']))


class ReplayTests(unittest.TestCase):
    def test_ambiguous_entry_bar_uses_stop_and_future_candles_do_not_change_the_period(self):
        from app.desk_replay import replay
        rows=[dict(time=i*900,open=100,close=100,high=101,low=99,volume=100) for i in range(250)]
        rows.append(dict(time=250*900,open=100,close=100,high=110,low=90,volume=100))
        analysis=dict(trend=True,entryAllowed=True,pullback=True,rsi=55,ema20=100,atr=2,close=100,strategy='trend-pullback')
        with patch.object(desk,'tape',return_value=analysis):
            result=replay(rows,data()['metadata']['SOLUSDT'],end=251*900)
            future=replay(rows+[dict(time=251*900,open=1,close=1,high=1,low=1,volume=100)],data()['metadata']['SOLUSDT'],end=251*900)
        self.assertEqual(result,future)
        self.assertEqual(result['closed'],1)
        self.assertLess(result['netRealized'],0)
