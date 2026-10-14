import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import bots, market_data, training
from app.config import EXTRA_CRYPTO, SYMBOLS, SYMBOLS_BY_ID
from app.flow import chaikin_money_flow, flow_scores
from app.news import parse_feed
from app.opportunities import assess


class MarketTests(unittest.TestCase):
    def test_new_markets_are_unique_and_registered(self):
        self.assertEqual(len(SYMBOLS), len(SYMBOLS_BY_ID))
        for symbol in EXTRA_CRYPTO:
            self.assertIn(symbol, bots.FLOW.universe)
            self.assertIn(symbol, bots.SWING.universe)
            self.assertEqual(SYMBOLS_BY_ID[symbol].source, 'binance')

    def data(self):
        return dict(at=100, metadata={'BTCUSDT': dict(status='TRADING', isSpotTradingAllowed=True)},
                    tickers={'BTCUSDT': dict(quoteVolume='20000000')},
                    books={'BTCUSDT': dict(bidPrice='100', askPrice='100.01')})

    def test_quality_rejects_wide_spread_illiquidity_and_halts(self):
        d = self.data()
        self.assertTrue(assess(SYMBOLS_BY_ID['BTC'], d)['eligible'])
        d['books']['BTCUSDT']['askPrice'] = '102'
        self.assertFalse(assess(SYMBOLS_BY_ID['BTC'], d)['eligible'])
        d = self.data()
        d['tickers']['BTCUSDT']['quoteVolume'] = '1'
        self.assertFalse(assess(SYMBOLS_BY_ID['BTC'], d)['eligible'])
        d = self.data()
        d['metadata']['BTCUSDT']['status'] = 'BREAK'
        self.assertFalse(assess(SYMBOLS_BY_ID['BTC'], d)['eligible'])

    def test_cmf_bounds_and_missing_volume(self):
        rows = [dict(high=10, low=0, close=10, volume=2) for _ in range(25)]
        self.assertEqual(chaikin_money_flow(rows)[-1], 1)
        for r in rows:
            r['volume'] = 0
        self.assertIsNone(chaikin_money_flow(rows)[-1])

    def test_flow_can_generate_an_entry(self):
        rows = [dict(time=i*900, open=100+i, high=101+i, low=98+i, close=101+i, volume=100+i)
                for i in range(160)]
        higher = [dict(time=i*3600, close=100+4*i) for i in range(40)]
        scores = flow_scores(rows, higher)
        self.assertGreater(scores[-1], .55)
        self.assertTrue(all(-1 <= s <= 1 for s in scores))

    def test_official_news_dates_and_links(self):
        item = '<item><title>Policy</title><link>https://www.federalreserve.gov/a.htm</link><pubDate>Wed, 09 Sep 2026 12:00:00 GMT</pubDate></item>'
        import email.utils
        now = email.utils.parsedate_to_datetime('Wed, 09 Sep 2026 13:00:00 GMT').timestamp()
        xml = '<rss><channel>' + item + item + '</channel></rss>'
        self.assertEqual(len(parse_feed(xml, now)), 1)
        self.assertEqual(parse_feed(xml, now + 31*86400), [])
        self.assertEqual(parse_feed(xml, now - 7200), [])
        self.assertEqual(parse_feed(xml.replace('www.federalreserve.gov', 'example.com'), now), [])

    def test_concurrent_candle_requests_are_shared_and_copied(self):
        async def run():
            market_data._candle_cache.clear()
            market_data._candle_locks.clear()
            with patch.object(market_data, '_fetch_candles', AsyncMock(return_value=[dict(close=100)])) as fetch:
                first, second = await asyncio.gather(*(market_data.get_candles(SYMBOLS_BY_ID['BTC'], '1m') for _ in range(2)))
                fetch.assert_awaited_once()
                first[0]['close'] = 0
                self.assertEqual(second[0]['close'], 100)
            market_data._candle_cache.clear()
            market_data._candle_locks.clear()
        asyncio.run(run())


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(training, 'DB_PATH', Path(self.tmp.name)/'test.db')
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_new_symbol_is_due_even_if_other_symbol_just_trained(self):
        with training._conn() as con:
            con.execute('INSERT INTO bot_params VALUES (?, ?, ?, ?, ?, ?)',
                        ('flow', 'BTC', '{}', json.dumps(dict(engineVersion=training.ENGINE_VERSION)), 1, 10000))
        self.assertEqual(training.due_symbols('flow', ('BTC', 'ETH'), 6, 10001), ['ETH'])

    def test_failed_source_has_backoff(self):
        with training._conn() as con:
            con.execute('INSERT INTO bot_training_attempts VALUES (?, ?, ?, ?)', ('flow', 'BTC', 10000, 0))
        self.assertEqual(training.due_symbols('flow', ('BTC',), 6, 10001), [])
        self.assertEqual(training.due_symbols('flow', ('BTC',), 6, 13601), ['BTC'])

    def test_old_engine_is_due_immediately(self):
        with training._conn() as con:
            con.execute('INSERT INTO bot_params VALUES (?, ?, ?, ?, ?, ?)', ('flow', 'BTC', '{}', '{}', 1, 10000))
        self.assertEqual(training.due_symbols('flow', ('BTC',), 6, 10001), ['BTC'])


if __name__ == '__main__':
    unittest.main()
