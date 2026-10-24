import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import ai_swarm, gemini_agent


def economics(sample=40, reward=1.8, expectancy=0.4, factor=1.6, win=58, break_even=42):
    return dict(sampleSize=sample, expectancyNet=expectancy, profitFactor=factor, winRate=win,
                breakEvenWinRate=break_even, planned=dict(viable=True, netRewardRisk=reward, netTarget=2, netRisk=1))


def pulse(bias=.8, multiplier=1.2, fakeout=False, trap='none'):
    return dict(available=True, symbol='BTC', bias=bias, convictionMultiplier=multiplier, fakeout=fakeout,
                trapDirection=trap, headlineRisk='none', catalysts=[], citations=[], reason='Textos alineados')


def flow(imbalance=.7, confidence=.9, entry=True, regime='scalp', symbol='BTC'):
    return dict(available=True, symbol=symbol, imbalance=imbalance, confidence=confidence, entryValid=entry,
                spreadAcceptable=True, regime=regime, reason='Libro utilizable')


def risk(asymmetry=.8, size=1, approved=True, veto=False, drawdown=False):
    return dict(available=True, approved=approved, veto=veto, asymmetryScore=asymmetry, sizeMultiplier=size,
                drawdownBlocked=drawdown, venue='spot', reason='Asimetría aceptable')


def commander(action='enter', profile='aggressive', direction='long', size=1):
    return dict(available=True, action=action, profile=profile, direction=direction, conviction=.7,
                sizeMultiplier=size, reason='Confirma', disagreements=[])


def decide(**overrides):
    payload = dict(symbol='BTC', direction='long', pulse=pulse(), flow=flow(), risk=risk(), commander=commander(),
                   economics=economics(), catalyst_allowed=True, portfolio_allowed=True, armed=False, mode='shadow')
    payload.update(overrides)
    return ai_swarm.consensus(**payload)


class ProtocolTests(unittest.TestCase):
    def test_weights_and_prompts_are_explicit(self):
        self.assertAlmostEqual(ai_swarm.WEIGHT_PULSE + ai_swarm.WEIGHT_FLOW + ai_swarm.WEIGHT_RISK, 1)
        self.assertIn('0.35', ai_swarm.PROMPT_COMMANDER)
        self.assertIn('winRate', ai_swarm.PROMPT_RISK)
        self.assertIn('imbalance', ai_swarm.PROMPT_FLOW)
        self.assertIn('fakeout', ai_swarm.PROMPT_PULSE)
        for prompt in (ai_swarm.PROMPT_PULSE, ai_swarm.PROMPT_FLOW, ai_swarm.PROMPT_RISK, ai_swarm.PROMPT_COMMANDER):
            self.assertIn('JSON', prompt)

    def test_quorum_selects_aggressive_and_does_not_submit_in_shadow(self):
        decision = decide()
        self.assertGreaterEqual(decision['score'], ai_swarm.AGGRESSIVE_SCORE)
        self.assertEqual(decision['components']['pulse'], round(1 * .8 * 1.2, 4))
        self.assertTrue(decision['quorum'])
        self.assertEqual(decision['profile'], 'aggressive')
        self.assertEqual(decision['action'], 'enter')
        self.assertFalse(decision['authorized'])
        self.assertFalse(decision['submitted'])
        self.assertIsNone(decision['handoff']['adapter'])

    def test_risk_veto_beats_a_high_score(self):
        decision = decide(risk=risk(veto=True, approved=False))
        self.assertGreaterEqual(decision['score'], ai_swarm.ENTER_SCORE)
        self.assertFalse(decision['quorum'])
        self.assertEqual(decision['profile'], 'flat')
        self.assertIn('Auditor de riesgo en veto', decision['vetoes'])

    def test_fakeout_and_commander_refusal_and_low_score(self):
        self.assertFalse(decide(pulse=pulse(fakeout=True, trap='long'))['quorum'])
        self.assertTrue(decide(pulse=pulse(fakeout=True, trap='short'))['quorum'])
        self.assertFalse(decide(commander=commander(action='stand_aside', profile='flat', direction='flat'))['quorum'])
        weak = decide(pulse=pulse(bias=.1, multiplier=.4), flow=flow(imbalance=.1, confidence=.2),
                      commander=commander(profile='aggressive'))
        self.assertLess(weak['score'], ai_swarm.ENTER_SCORE)
        self.assertFalse(weak['quorum'])

    def test_size_only_shrinks_and_sample_blocks_aggressive(self):
        shrunk = decide(risk=risk(size=.4), commander=commander(size=1))
        self.assertEqual(shrunk['sizeMultiplier'], .4)
        self.assertEqual(shrunk['profile'], 'conservative')
        unvalidated = decide(economics=economics(sample=10))
        self.assertLessEqual(unvalidated['sizeMultiplier'], .5)
        self.assertEqual(unvalidated['profile'], 'conservative')

    def test_commander_cannot_upgrade_profile_or_move_spy_off_mt5(self):
        capped = decide(flow=flow(regime='intraday', imbalance=.55, confidence=.8), commander=commander(profile='aggressive'))
        self.assertEqual(capped['profile'], 'maker')
        spy = decide(symbol='SPY', flow=flow(symbol='SPY', regime='intraday', imbalance=.4, confidence=.8),
                     commander=commander(profile='aggressive', direction='long'))
        self.assertEqual(spy['profile'], 'mt5')
        self.assertEqual(spy['venue'], 'mt5')

    def test_economics_and_portfolio_gates(self):
        poor = decide(economics=economics(reward=1.0))
        self.assertFalse(poor['quorum'])
        self.assertIn('Beneficio/riesgo neto inferior a 1.2', poor['vetoes'])
        unrouted = decide(portfolio_allowed=False)
        self.assertTrue(unrouted['quorum'])
        self.assertFalse(unrouted['routable'])
        self.assertFalse(unrouted['authorized'])

    def test_armed_paper_queues_adapter_without_sending(self):
        decision = decide(armed=True, mode='paper')
        self.assertTrue(decision['authorized'])
        self.assertEqual(decision['handoff']['adapter'], 'spot_orders')
        self.assertFalse(decision['submitted'])
        self.assertTrue(decision['handoff']['queued'])
        spy = decide(symbol='SPY', armed=True, mode='paper', flow=flow(symbol='SPY', regime='intraday'),
                     commander=commander(profile='mt5'))
        self.assertEqual(spy['handoff']['adapter'], 'mt5_gateway')

    def test_stale_crypto_book_overrides_the_model(self):
        hardened = ai_swarm.harden_flow(flow(entry=True), dict(available=False, imbalance=None, spreadBps=None, ageSeconds=9), 'BTC')
        self.assertFalse(hardened['entryValid'])
        self.assertEqual(hardened['regime'], 'stand_aside')

    def test_extract_json_ignores_fences(self):
        self.assertEqual(ai_swarm.extract_json('```json\n{"bias": 1}\n```'), {'bias': 1})


class SwarmRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(ai_swarm, 'DB_PATH', Path(self.tmp.name) / 'swarm.sqlite')
        self.env = patch.object(ai_swarm, 'ENV_PATH', Path(self.tmp.name) / 'missing.env')
        self.db.start()
        self.env.start()
        self.previous = ai_swarm.SWARM
        self.settings = ai_swarm.Settings(xai_key='test-xai-secret', openai_key='test-openai-secret',
                                          min_request_seconds=0, max_day=4, max_hour=4, monthly_cap_usd=15)
        self.swarm = ai_swarm.MultiAgentSwarm(self.settings)

    async def asyncTearDown(self):
        await self.swarm.close()
        ai_swarm.SWARM = self.previous
        self.env.stop()
        self.db.stop()
        self.tmp.cleanup()

    async def test_subagents_run_together_and_commander_is_last(self):
        order = []

        async def complete(spec, payload):
            order.append(spec.agent_id)
            await asyncio.sleep(.02)
            body = {'grok-x-pulse': pulse(), 'grok-flow-scalper': flow(), 'chatgpt-quant-risk': risk(),
                    'grok-desk-commander': commander()}[spec.agent_id]
            return json.dumps(body), dict(input_tokens=12, output_tokens=8, num_sources_used=1 if spec.search else 0)

        book = dict(available=True, imbalance=.7, spreadBps=1.5, ageSeconds=.2, bid=100, ask=100.01)
        self.swarm.market_pack = AsyncMock(return_value=(dict(symbol='BTC', direction='long'), book))
        with patch.object(self.swarm, '_complete', side_effect=complete), patch.object(ai_swarm, 'microstructure', return_value=book), \
             patch.object(ai_swarm, '_portfolio_allowed', return_value=True), patch.object(ai_swarm, '_catalyst_allowed', return_value=True):
            decision = await self.swarm.evaluate('BTC', 'long', economics=economics())
        self.assertEqual(set(order[:3]), {'grok-x-pulse', 'grok-flow-scalper', 'chatgpt-quant-risk'})
        self.assertEqual(order[-1], 'grok-desk-commander')
        self.assertTrue(decision['quorum'])
        self.assertFalse(decision['submitted'])
        self.assertNotIn('test-xai-secret', json.dumps(decision))

    async def test_missing_keys_do_not_call_the_provider(self):
        silent = ai_swarm.MultiAgentSwarm(ai_swarm.Settings())
        with patch.object(silent, '_complete', AsyncMock()) as complete:
            decision = await silent.evaluate('LINK', 'short')
        complete.assert_not_awaited()
        self.assertFalse(decision['quorum'])
        self.assertIn('XAI_API_KEY', decision['reason'])

    async def test_daily_cap_blocks_the_second_cycle(self):
        capped = ai_swarm.MultiAgentSwarm(ai_swarm.Settings(xai_key='k', openai_key='k', min_request_seconds=0, max_day=1, max_hour=4))
        book = dict(available=True, imbalance=.2, spreadBps=1, ageSeconds=.1, bid=1, ask=1.001)

        async def complete(spec, payload):
            return json.dumps({'grok-x-pulse': pulse(), 'grok-flow-scalper': flow(), 'chatgpt-quant-risk': risk(veto=True, approved=False),
                               'grok-desk-commander': commander(action='stand_aside', profile='flat', direction='flat')}[spec.agent_id]), {}

        capped.market_pack = AsyncMock(return_value=({}, book))
        with patch.object(capped, '_complete', side_effect=complete) as calls, patch.object(ai_swarm, 'microstructure', return_value=book):
            await capped.evaluate('BTC', 'long', economics=economics())
            second = await capped.evaluate('BTC', 'long', economics=economics())
        self.assertEqual(calls.await_count, 4)
        self.assertIn('cupo', second['reason'])

    async def test_providers_routes_and_openai_fallback(self):
        seen = []

        async def fake_post(url, headers, body, timeout):
            seen.append((url, body.get('model'), 'Authorization' in headers, 'test-xai-secret' in json.dumps(body), body))
            if 'openai.com' in url and body.get('model') == 'o3-mini':
                return httpx.Response(404, json={'error': {'code': 'model_not_found'}})
            if url.endswith('/responses'):
                content = json.dumps(pulse())
                return httpx.Response(200, json={'output_text': content, 'usage': {'input_tokens': 4, 'output_tokens': 4, 'num_sources_used': 2}})
            model = body.get('model')
            content = json.dumps(flow() if model == 'grok-3-mini-fast' else risk() if model == 'gpt-4o' else commander())
            return httpx.Response(200, json={'choices': [{'message': {'content': content}}], 'usage': {'prompt_tokens': 4, 'completion_tokens': 4}})

        book = dict(available=True, imbalance=.7, spreadBps=1, ageSeconds=.1, bid=10, ask=10.01)
        self.swarm.market_pack = AsyncMock(return_value=({}, book))
        with patch.object(self.swarm, '_post', side_effect=fake_post), patch.object(ai_swarm, 'microstructure', return_value=book), \
             patch.object(ai_swarm, '_catalyst_allowed', return_value=True), patch.object(ai_swarm, '_portfolio_allowed', return_value=True):
            decision = await self.swarm.evaluate('btc', 'LONG', economics=economics())
        urls = [item[0] for item in seen]
        self.assertIn('https://api.x.ai/v1/responses', urls)
        self.assertIn('https://api.x.ai/v1/chat/completions', urls)
        self.assertIn('https://api.openai.com/v1/chat/completions', urls)
        self.assertIn('gpt-4o', [item[1] for item in seen])
        self.assertTrue(any(url.endswith('/responses') and 'x_search' in json.dumps(body) for url, body in ((item[0], item[4]) for item in seen)))
        self.assertTrue(all(item[2] and item[3] is False for item in seen))
        self.assertTrue(decision['quorum'])
        self.assertEqual(decision['symbol'], 'BTC')
        self.assertEqual(decision['direction'], 'long')

    def test_settings_hide_secrets_and_stay_disarmed(self):
        with patch.dict(os.environ, {'XAI_API_KEY': 'super-secret', 'OPENAI_API_KEY': 'other-secret',
                                     'SWARM_ENABLED': '0', 'SWARM_ARM': '0', 'SWARM_MODE': 'shadow',
                                     'XAI_FLOW_MODEL': 'grok-3-mini-fast', 'OPENAI_RISK_MODEL': 'o3-mini',
                                     'XAI_COMMANDER_MODEL': 'grok-3', 'XAI_PULSE_MODEL': 'grok-3'}, clear=False):
            settings = ai_swarm.load_settings()
        self.assertEqual(settings.xai_key, 'super-secret')
        self.assertNotIn('super-secret', json.dumps(settings.public()))
        self.assertFalse(settings.enabled)
        self.assertFalse(settings.armed)
        self.assertEqual(settings.mode, 'shadow')
        self.assertEqual(settings.flow_model, 'grok-3-mini-fast')
        self.assertEqual(settings.risk_model, 'o3-mini')

    def test_fakeout_reaches_the_existing_catalyst_gate(self):
        now = 1_800_000_000
        ai_swarm.SWARM = self.swarm
        self.swarm.latest['BTC'] = dict(at=now, pulse=dict(fakeout=True, trapDirection='long', convictionMultiplier=1.1,
                                                           citations=['https://example.com/post']))
        with patch.object(gemini_agent, 'AGENT', None):
            blocked = gemini_agent.catalyst_gate('BTC', 'long', now=now)
            opened = gemini_agent.catalyst_gate('BTC', 'short', now=now)
        self.assertFalse(blocked['allowed'])
        self.assertEqual(blocked['evidence'][-1]['scope'], 'swarm-fakeout')
        self.assertTrue(opened['allowed'])

    def test_clearance_is_not_binding_until_armed(self):
        ai_swarm.SWARM = self.swarm
        self.swarm.latest['LINK'] = decide(symbol='LINK')
        shadow = ai_swarm.execution_clearance('LINK', 'long')
        self.assertFalse(shadow['binding'])
        self.assertTrue(shadow['allowed'])
        self.swarm.settings = ai_swarm.Settings(xai_key='k', openai_key='k', mode='paper', armed=True)
        self.swarm.latest['LINK'] = decide(symbol='LINK', armed=True, mode='paper')
        with patch.object(ai_swarm, 'microstructure', return_value=dict(available=True, imbalance=.7, spreadBps=1, ageSeconds=.1)):
            armed = ai_swarm.execution_clearance('LINK', 'long')
        self.assertTrue(armed['binding'])
        self.assertTrue(armed['allowed'])
        self.assertEqual(armed['adapter'], 'spot_orders')


if __name__ == '__main__':
    unittest.main()
