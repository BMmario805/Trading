import json
import unittest
from unittest.mock import AsyncMock, patch

from app import book_stream, opportunities


def payload(**kwargs):
    return dict(s='BTCUSDT',u=42,b='100',a='100.1',B='10',A='12',**kwargs)


class BookTests(unittest.TestCase):
    def setUp(self):
        self.feed=book_stream.BookStream()
        self.feed.watch(['BTCUSDT'])
        self.feed.connected=True

    def test_fresh_book_contains_price_and_liquidity(self):
        self.assertTrue(self.feed.ingest(payload()))
        quote=self.feed.fresh(['BTCUSDT'])['BTCUSDT']
        self.assertEqual(float(quote['askQty']),12)
        self.assertEqual(float(quote['bidPrice']),100)

    def test_stale_duplicate_and_reordered_updates_do_not_refresh_quotes(self):
        with patch.object(book_stream.time,'monotonic',return_value=10):
            self.feed.ingest(payload())
        with patch.object(book_stream.time,'monotonic',return_value=13):
            for update in (42,41):
                row=payload();row['u']=update
                self.assertFalse(self.feed.ingest(row))
            self.assertEqual(self.feed.fresh(['BTCUSDT']),{})

    def test_invalid_and_crossed_quotes_never_enter_cache(self):
        for field,value in (('b','nan'),('a','99'),('A','0'),('B','inf'),('u',True)):
            row=payload();row[field]=value
            self.assertFalse(self.feed.ingest(row))
        self.assertFalse(self.feed.ingest(dict(s='FAKEUSDT',u=1,b='1',a='2',B='3',A='4')))
        self.assertEqual(self.feed.books,{})

    def test_disconnection_and_clock_reversal_never_reuse_book(self):
        with patch.object(book_stream.time,'monotonic',return_value=10):
            self.feed.ingest(payload())
        with patch.object(book_stream.time,'monotonic',return_value=9):
            self.assertEqual(self.feed.fresh(['BTCUSDT']),{})
        self.feed.connected=False
        self.assertEqual(self.feed.fresh(['BTCUSDT']),{})

    def test_unicode_symbols_and_bounded_subscriptions(self):
        self.feed.watch(['牛来USDT']+[f'C{i}USDT' for i in range(600)])
        self.assertLessEqual(len(self.feed.wanted),book_stream.MAX_SYMBOLS)
        self.feed.watch(['牛来USDT'])
        row=payload();row['s']='牛来USDT'
        self.assertTrue(self.feed.ingest({'stream':'牛来usdt@bookTicker','data':row}))


class TakerFlowTests(unittest.TestCase):
    def setUp(self):
        self.feed = book_stream.BookStream()
        self.now = 1_700_000_000.0

    def _load(self, rows):
        self.feed.trades['BTCUSDT'] = rows

    def test_buyer_maker_false_is_taker_buy_and_velocity_is_the_rate_ratio(self):
        rows = [dict(at=self.now - 5, price=100.0, quantity=2.0, isBuyerMaker=False) for _ in range(6)]
        rows += [dict(at=self.now - 5, price=50.0, quantity=1.0, isBuyerMaker=True) for _ in range(4)]
        rows.append(dict(at=self.now - 301, price=1000.0, quantity=1000.0, isBuyerMaker=True))
        self._load(rows)
        flow = self.feed.taker_flow('BTCUSDT', window_seconds=300, now=self.now)
        self.assertEqual(flow['buyVolume'], 1200.0)
        self.assertEqual(flow['sellVolume'], 200.0)
        self.assertAlmostEqual(flow['cvdDelta'], (1200.0 - 200.0) / 1400.0)
        self.assertAlmostEqual(flow['tradeVelocity'], 10.0)
        support = self.feed.taker_supports_bid('BTCUSDT', now=self.now)
        self.assertTrue(support['allowed'])

    def test_fewer_than_ten_trades_leave_cvd_unset_and_do_not_block_the_bid(self):
        self._load([dict(at=self.now - 1, price=10.0, quantity=1.0, isBuyerMaker=False) for _ in range(9)])
        flow = self.feed.taker_flow('BTCUSDT', now=self.now)
        self.assertIsNone(flow['cvdDelta'])
        self.assertEqual(flow['buyVolume'], 90.0)
        self.assertEqual(flow['sellVolume'], 0.0)
        self.assertAlmostEqual(flow['tradeVelocity'], (9 / 30.0) / (9 / 300.0))
        self.assertTrue(self.feed.taker_supports_bid('BTCUSDT', now=self.now)['allowed'])

    def test_heavy_taker_selling_fails_the_bid_floor(self):
        rows = [dict(at=self.now - 2, price=100.0, quantity=1.0, isBuyerMaker=True) for _ in range(8)]
        rows += [dict(at=self.now - 2, price=100.0, quantity=1.0, isBuyerMaker=False) for _ in range(2)]
        self._load(rows)
        flow = self.feed.taker_flow('BTCUSDT', now=self.now)
        self.assertAlmostEqual(flow['cvdDelta'], -0.6)
        self.assertFalse(self.feed.taker_supports_bid('BTCUSDT', now=self.now)['allowed'])
        balanced = [dict(at=self.now - 2, price=100.0, quantity=1.0, isBuyerMaker=True) for _ in range(5)]
        balanced += [dict(at=self.now - 2, price=100.0, quantity=0.85, isBuyerMaker=False) for _ in range(5)]
        self._load(balanced)
        # (4.25 - 5) / 9.25 = -0.081, por encima del suelo -0.15.
        self.assertGreaterEqual(self.feed.taker_flow('BTCUSDT', now=self.now)['cvdDelta'], -0.15)
        self.assertTrue(self.feed.taker_supports_bid('BTCUSDT', now=self.now)['allowed'])


class ExecutionBooksTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.feed=book_stream.BookStream()
        self.feed.watch(['BTCUSDT'])
        self.feed.connected=True
        self.feed.ingest(payload())
        self.patches=[patch.object(opportunities,'BOOKS',self.feed),patch.object(opportunities,'_book_cache',{})]
        for item in self.patches:item.start();self.addCleanup(item.stop)

    async def test_fresh_stream_removes_rest_request(self):
        with patch.object(opportunities.market_data,'_binance_get',AsyncMock()) as get:
            result=await opportunities.execution_snapshot(['BTCUSDT'],{})
        get.assert_not_awaited()
        self.assertEqual(result['provider'],'websocket')
        self.assertEqual(self.feed.hits,1)

    async def test_partial_stream_only_fetches_missing_books(self):
        rest=[dict(symbol='ETHUSDT',bidPrice='100',askPrice='101',bidQty='1',askQty='1')]
        with patch.object(opportunities.market_data,'_binance_get',AsyncMock(return_value=rest)) as get:
            result=await opportunities.execution_snapshot(['BTCUSDT','ETHUSDT'],{})
        self.assertEqual(result['provider'],'mixed')
        self.assertEqual(json.loads(get.call_args.args[1]['symbols']),['ETHUSDT'])

    async def test_stream_expiring_during_rest_is_not_rejuvenated(self):
        calls=[]
        async def rest(_,params):
            symbols=json.loads(params['symbols'])
            calls.append(symbols)
            self.feed.connected=False
            return [dict(symbol=s,bidPrice='200',askPrice='201',bidQty='1',askQty='1') for s in symbols]
        with patch.object(opportunities.market_data,'_binance_get',side_effect=rest):
            result=await opportunities.execution_snapshot(['BTCUSDT','ETHUSDT'],{})
        self.assertEqual(calls,[['ETHUSDT'],['BTCUSDT','ETHUSDT']])
        self.assertEqual(result['provider'],'rest')
        self.assertEqual(float(result['books']['BTCUSDT']['bidPrice']),200)

    async def test_unavailable_stream_keeps_rest_and_missing_books_fail_closed(self):
        self.feed.connected=False
        with patch.object(opportunities.market_data,'_binance_get',AsyncMock(return_value=[])):
            with self.assertRaises(ValueError):
                await opportunities.execution_snapshot(['BTCUSDT'],{})
