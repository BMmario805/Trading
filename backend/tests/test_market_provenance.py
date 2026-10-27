import time
import unittest
from unittest.mock import AsyncMock,patch

from app import market_data,derivatives_flow,fx_rates
from app.book_stream import BookStream
from app.config import SYMBOLS_BY_ID
from app.execution import valid_tick


class SourceTimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_downloading_old_ticker_does_not_refresh_its_source_time(self):
        now=time.time()
        payload=dict(lastPrice='100',priceChangePercent='1',closeTime=(now-1000)*1000)
        with patch.object(market_data,'_binance_get',AsyncMock(return_value=payload)):
            tick=await market_data.get_price(SYMBOLS_BY_ID['BTC'])
        self.assertAlmostEqual(tick['time'],now-1000)
        self.assertFalse(valid_tick(tick,now))


class DerivativeFreshnessTests(unittest.TestCase):
    def test_closed_hourly_change_ignores_open_bar(self):
        now=10*3600+1800
        def row(start,price):
            return [start*1000,'1','1','1',str(price),'1',(start+3600)*1000-1]
        rows=[row(8*3600,100),row(9*3600,110),row(10*3600,9999)]
        self.assertAlmostEqual(derivatives_flow._price_change(rows,now),10)

    def test_recent_download_with_old_sources_has_no_regime_input(self):
        now=time.time()
        old=(now-86400)*1000
        snap=derivatives_flow._assemble('BTCUSDT',dict(time=old,lastFundingRate='.001',markPrice='100',indexPrice='99'),
            [dict(timestamp=old-3600000,sumOpenInterest='100'),dict(timestamp=old,sumOpenInterest='110')],
            [dict(timestamp=old,longShortRatio='2')],[],now)
        self.assertFalse(snap['available'])
        self.assertTrue(all(v is False for v in snap['freshness'].values()))

    def test_trade_gap_only_invalidates_its_own_symbol(self):
        stream=BookStream();stream.connected=True
        stream.watch_trades(['BTCUSDT','ETHUSDT'])
        now=time.time()
        def event(symbol,index):
            return dict(e='trade',s=symbol,t=index,T=now*1000,p='100',q='1',m=True)
        stream.ingest(event('BTCUSDT',1));stream.ingest(event('ETHUSDT',1))
        btc=stream.generation_for('BTCUSDT');eth=stream.generation_for('ETHUSDT')
        stream.ingest(event('BTCUSDT',3))
        self.assertIsNone(stream.observed_trades('BTCUSDT',now-1,1,btc))
        self.assertIsNotNone(stream.observed_trades('ETHUSDT',now-1,0,eth))


class RiskFxTests(unittest.TestCase):
    def test_session_accounting_is_not_used_for_current_risk_fx(self):
        with patch.object(fx_rates,'_rates',{'USDT':dict(rate=.9,at=1000,source='explicit fixture')}):
            self.assertEqual(fx_rates.get('USDT',1001)['rate'],.9)
            self.assertIsNone(fx_rates.get('USDT',1121))
