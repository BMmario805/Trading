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
