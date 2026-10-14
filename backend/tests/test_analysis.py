import asyncio
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import httpx

from app import sentiment
from app.config import SYMBOLS_BY_ID
from app.main import app


class AnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        sentiment._cache.clear()
        sentiment._compute_locks.clear()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(sentiment.history, 'record'))
        self.sources = {}
        for name in ('_stocktwits', '_reddit', '_fear_greed', 'ideas_source', 'minds_source', 'official_news'):
            self.sources[name] = self.stack.enter_context(patch.object(sentiment, name, AsyncMock(return_value=None)))
        self.sources['_stocktwits'].return_value = dict(
            source='StockTwits', detail='2 alcistas', bullish=1, sample=2,
            items=[dict(text='Example', badge='bullish')],
        )
        self.stack.enter_context(patch('app.main.market_data.get_candles', AsyncMock(return_value=[
            dict(time=i*3600, open=100+i, high=102+i, low=99+i, close=101+i, volume=100)
            for i in range(100)
        ])))

    async def asyncTearDown(self):
        sentiment._cache.clear()
        sentiment._compute_locks.clear()

    async def request(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.get('/api/analysis/XAUUSD?interval=1h')

    async def test_full_analysis_endpoint_returns_technical_and_sentiment(self):
        response = await self.request()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['technical']['signals'])
        self.assertEqual(body['sentiment']['sampleSize'], 2)
        self.assertTrue(body['sentiment']['sources'][0]['hasItems'])
        for source in self.sources.values():
            source.assert_awaited_once()

    async def test_one_failed_source_does_not_remove_analysis(self):
        self.sources['_reddit'].side_effect = RuntimeError('offline')
        response = await self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sentiment']['sampleSize'], 2)
        self.assertIn('RuntimeError', response.json()['sentiment']['errors'])

    async def test_aggregator_failure_preserves_technical_analysis(self):
        with patch('app.main.get_sentiment', AsyncMock(side_effect=RuntimeError('offline'))):
            response = await self.request()
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body['sentiment']['sampleSize'], 0)
        self.assertEqual(body['upProbability'], round((body['technical']['score'] + 1) * 50, 1))

    async def test_concurrent_sentiment_requests_share_sources(self):
        first, second = await asyncio.gather(*(sentiment.get_sentiment(SYMBOLS_BY_ID['XAUUSD']) for _ in range(2)))
        self.assertEqual(first, second)
        for source in self.sources.values():
            source.assert_awaited_once()

    async def test_stocktwits_parser_is_available(self):
        # Exercise the actual parser too, not just its mocked aggregator interface.
        self.stack.close()
        message = dict(id=1, body='Bullish', user=dict(username='trader'),
                       entities=dict(sentiment=dict(basic='Bullish')))
        with patch.object(sentiment, '_stocktwits_stream', AsyncMock(return_value=[message])):
            result = await sentiment._stocktwits(SYMBOLS_BY_ID['XAUUSD'])
        self.assertEqual(result['sample'], 1)  # same message in three streams


if __name__ == '__main__':
    unittest.main()
