import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import gemini_agent as gemini
from app.config import SYMBOLS_BY_ID
from app.main import app


def sources(now=1000000):
    return [dict(source='Fuente', items=[dict(text="Don't buy; bull trap", time=now-10, url='https://example.org/post')])]


def response_for(items):
    return json.dumps(dict(summary='Sesgo bajista en el texto.', items=[dict(
        id=item['id'], direction=-1, confidence=.9, relevant=True, is_noise=False,
        catalyst='none', explanation='La negación desaconseja la compra.') for item in items]))


class ObserverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(gemini, 'DB_PATH', Path(self.tmp.name)/'test.sqlite')
        self.db.start()
        self.now = 1000000
        self.clock_patch = patch.object(gemini.time, 'time', return_value=self.now)
        self.clock = self.clock_patch.start()
        self.agent = gemini.Agent(gemini.Settings(key='test-secret', min_request_seconds=60, max_day=2))
        async def answer(symbol, items):
            await asyncio.sleep(0)
            return response_for(items),dict(total_input_tokens=100,total_output_tokens=200),'gemini-3.5-flash-lite'
        self.request = AsyncMock(side_effect=answer)
        self.patch = patch.object(self.agent, 'request', self.request)
        self.patch.start()

    async def asyncTearDown(self):
        await self.agent.close()
        self.patch.stop()
        self.clock_patch.stop()
        self.db.stop()
        self.tmp.cleanup()

    async def test_concurrent_requests_share_one_call_and_keep_dates(self):
        result, duplicate = await asyncio.gather(self.agent.observe('XAUUSD', sources()), self.agent.observe('XAUUSD', sources()))
        self.request.assert_awaited_once()
        self.assertEqual(result,duplicate)
        self.assertEqual(result['items'][0]['publishedAt'],self.now-10)
        self.assertEqual(result['items'][0]['receivedAt'],self.now)
        self.assertEqual(result['label'],'bajista')
        self.assertEqual(result['mode'],'observation')

    async def test_restart_reuses_cache_and_persistent_quota(self):
        await self.agent.observe('XAUUSD', sources())
        restarted=gemini.Agent(self.agent.settings)
        with patch.object(restarted,'request',AsyncMock()) as request:
            await restarted.observe('XAUUSD', sources())
            request.assert_not_awaited()
        self.assertEqual(restarted.limits()['requests24h'],1)
        self.assertEqual(restarted.snapshot('XAUUSD')['data']['sampleSize'],1)

    async def test_global_spacing_and_daily_limit_across_symbols(self):
        await self.agent.observe('XAUUSD', sources())
        self.assertIsNone(await self.agent.observe('BTC', sources()))
        self.clock.return_value += 61
        await self.agent.observe('BTC', sources())
        self.clock.return_value += 61
        self.assertIsNone(await self.agent.observe('ETH', sources()))
        self.assertEqual(self.request.await_count,2)

    async def test_two_agent_instances_cannot_bypass_global_reservation(self):
        other=gemini.Agent(self.agent.settings)
        with patch.object(other,'request',AsyncMock()) as request:
            first,second=await asyncio.gather(self.agent.observe('XAUUSD',sources()),other.observe('BTC',sources()))
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            request.assert_not_awaited()

    async def test_monthly_estimate_stops_calls_after_budget(self):
        self.agent.settings=replace(self.agent.settings,monthly_cap_usd=.02)
        self.request.side_effect=gemini.ProviderError('unavailable','Timeout')
        await self.agent.observe('XAUUSD',sources())
        self.clock.return_value += 3601
        self.assertIsNone(await self.agent.observe('BTC',sources(self.clock.return_value)))
        self.request.assert_awaited_once()
        self.assertFalse(self.agent.limits()['budgetAvailable'])

    async def test_gemini_observation_does_not_change_combined_analysis(self):
        from app.main import analysis
        technical=dict(score=.4,rsi=50,signals=[])
        public=dict(bullishPct=50,sampleSize=0)
        with patch('app.main.market_data.get_candles',AsyncMock(return_value=[])),patch('app.main.get_sentiment',AsyncMock(return_value=public)),patch('app.main.technical_analysis',return_value=technical),patch.object(gemini,'snapshot',return_value=dict(data=dict(score=-1))):
            body=await analysis('XAUUSD')
        self.assertEqual(body['upProbability'],70)

    async def test_no_valid_timestamp_no_call(self):
        bad=[dict(source='untrusted',items=[dict(text='Buy',time=None),dict(text='Future',time=self.now+100)])]
        self.assertIsNone(await self.agent.observe('XAUUSD',bad))
        self.request.assert_not_awaited()

    async def test_unknown_symbol_no_call(self):
        self.assertIsNone(await self.agent.observe('MSFT', sources()))
        self.request.assert_not_awaited()

    async def test_missing_ids_and_invalid_confidence_cannot_publish(self):
        for payload in [dict(summary='bad',items=[]),dict(summary='bad',items=[dict(id='invented',direction=1,confidence=2,relevant=True,is_noise=False,catalyst='none',explanation='wrong')])]:
            self.request.side_effect=None
            self.request.return_value=(json.dumps(payload),dict(total_input_tokens=5),'model')
            await self.agent.observe('XAUUSD', sources(self.clock.return_value))
            self.assertIsNone(self.agent.snapshot('XAUUSD')['data'])
            self.clock.return_value += 3601

    async def test_quota_failure_keeps_previous_result_and_backs_off(self):
        old=await self.agent.observe('XAUUSD',sources())
        self.clock.return_value += 61
        self.request.side_effect=gemini.ProviderError('quota','Gemini HTTP 429')
        changed=sources(self.clock.return_value)
        await self.agent.observe('XAUUSD',changed)
        self.assertEqual(self.agent.snapshot('XAUUSD')['data'],old)
        self.assertGreater(self.agent.limits()['nextRequestAt'],self.clock.return_value)
        self.assertEqual(self.agent.status()['connectionStatus'],'quota')

    async def test_status_and_http_errors_never_expose_key(self):
        self.patch.stop()
        self.agent.http=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(403,json={'error':{'message':'test-secret'}})))
        await self.agent.observe('XAUUSD',sources())
        self.assertNotIn('test-secret',json.dumps(self.agent.status()))
        self.assertNotIn('test-secret',json.dumps(self.agent.snapshot('XAUUSD')))
        self.assertEqual(self.agent.status()['connectionStatus'],'auth')

    async def test_noise_is_not_a_directional_sample(self):
        self.request.side_effect=None
        item=gemini.collect_items(sources(),self.now)[0]
        payload=json.loads(response_for([item]))
        payload['items'][0]['is_noise']=True
        self.request.return_value=(json.dumps(payload),{},'model')
        result=await self.agent.observe('XAUUSD',sources())
        self.assertEqual(result['sampleSize'],0)
        self.assertEqual(result['score'],0)

    async def test_read_endpoints_never_trigger_provider_calls(self):
        with patch.object(gemini,'AGENT',self.agent):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                self.assertEqual((await client.get('/api/agents/gemini')).status_code,200)
                self.assertEqual((await client.get('/api/agents/gemini/XAUUSD')).status_code,200)
                self.assertEqual((await client.get('/api/agents/gemini/UNKNOWN')).status_code,404)
        self.request.assert_not_awaited()


class CollectionTests(unittest.TestCase):
    def test_duplicate_posts_and_batch_size_are_bounded(self):
        rows=sources()[0]
        self.assertEqual(len(gemini.collect_items([rows,rows],1000000)),1)
        rows['items']=[dict(text='x'*10000,time=1000000-i,url=f'https://example.org/{i}') for i in range(30)]
        items=gemini.collect_items([rows],1000000)
        self.assertEqual(len(items),gemini.MAX_ITEMS)
        self.assertTrue(all(len(item['text'])<=700 for item in items))
