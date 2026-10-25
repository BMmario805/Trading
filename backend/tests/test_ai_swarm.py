import asyncio
import json
import os
import tempfile
import time
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


def batch_pulse(*symbols):
    item={k:v for k,v in pulse().items() if k!='available'}
    return dict(items=[dict(item,symbol=symbol,catalystType='institutional') for symbol in symbols or ('BTC',)])


def regime_allocation(**overrides):
    return dict(dict(marketRegime='trend_expansion',allowBreakouts=True,requireMakerEntry=False,
                     allowedFamilies=['trend','squeeze'],maxPortfolioRiskMultiplier=.7,
                     favoredSymbols=[],avoidedSymbols=[],rationale='Tendencia'),**overrides)


def forensic_audit(**overrides):
    return dict(dict(statisticalMaturity='validated',sampleSize=40,netExpectancyR=.1,primaryLeak='Costes',
                     recommendedMaxExtensionAtr=2.0,recommendedMinRsPercentile=85.0,
                     requireMakerOnly=False,vetoedSetups=[],rationale='Revisión'),**overrides)


def decide(**overrides):
    payload = dict(symbol='BTC', direction='long', pulse=pulse(), flow=flow(), risk=risk(), commander=commander(),
                   economics=economics(), catalyst_allowed=True, portfolio_allowed=True, armed=False, mode='shadow')
    payload.update(overrides)
    return ai_swarm.consensus(**payload)


class ProtocolTests(unittest.TestCase):
    def test_local_flow_boundaries_and_alignment(self):
        book=dict(available=True,imbalance=.7,ageSeconds=.1,spreadBps=1)
        good=ai_swarm.local_flow_report('BTC',book,.8,.2)
        contrary=ai_swarm.local_flow_report('BTC',book,-.8,-.2)
        self.assertEqual(good['regime'],'scalp')
        self.assertGreater(good['confidence'],contrary['confidence'])
        boundary=ai_swarm.local_flow_report('BTC',dict(book,ageSeconds=2,spreadBps=8),.8)
        self.assertTrue(boundary['entryValid'])
        self.assertEqual(boundary['regime'],'intraday')
        for changes in (dict(available=False),dict(available=1),dict(ageSeconds=2.001),
                        dict(ageSeconds=-.1),dict(spreadBps=8.001),dict(imbalance=float('nan')),
                        dict(spreadBps=None),dict(ageSeconds=float('inf'))):
            with self.subTest(changes=changes):
                report=ai_swarm.local_flow_report('BTC',dict(book,**changes),.8)
                self.assertFalse(report['entryValid'])
                self.assertEqual(report['regime'],'stand_aside')
                self.assertEqual(report['confidence'],0)
        self.assertFalse(ai_swarm.local_flow_report('SPY',book,.8)['entryValid'])
        self.assertEqual(ai_swarm.local_flow_report('SPY',dict(book,explicitFlow=True),.8)['regime'],'intraday')

    def test_local_risk_is_capped_and_portfolio_veto_is_absolute(self):
        from app import gpt_quant_agent
        base=dict(economics(),portfolio=dict(allowed=True),requestedRiskFraction=.01,payoffRatio=2)
        report=ai_swarm.local_risk_report('BTC',base)
        self.assertTrue(report['approved'])
        self.assertGreater(report['kelly_score'],0)
        self.assertLessEqual(report['sizeMultiplier'],1)
        for changes in (dict(portfolio=dict(allowed=False)),dict(learningAllowed=False),
                        dict(dailyStopped=True),dict(payoffRatio=.1),dict(expectancyNet=0),
                        dict(profitFactor=1.14),dict(winRate=41)):
            with self.subTest(changes=changes):
                veto=ai_swarm.local_risk_report('BTC',dict(base,**changes))
                self.assertFalse(veto['approved'])
                self.assertEqual(veto['sizeMultiplier'],0)
        small=ai_swarm.local_risk_report('BTC',dict(base,sampleSize=5))
        self.assertLessEqual(small['sizeMultiplier'],.5)
        # audit_report también revalida cartera aunque el modelo apruebe.
        self.assertFalse(gpt_quant_agent.audit_report(risk(),dict(base,portfolio=dict(allowed=False)),'BTC')['approved'])

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

    async def test_subscription_only_calls_context_models_and_rechecks_final_book(self):
        from dataclasses import replace
        swarm=ai_swarm.MultiAgentSwarm(replace(self.settings,transport='subscription'))
        book=dict(available=True,imbalance=.7,spreadBps=1,ageSeconds=.1)
        books=[book]
        called=[]
        async def complete(spec,payload):
            called.append(spec.agent_id)
            if spec.agent_id=='grok-desk-commander':
                # El libro final cambió tras la propuesta del commander.
                books[0]=dict(book,available=False)
                return json.dumps(commander()),{}
            return json.dumps(batch_pulse()),{}
        swarm.market_pack=AsyncMock(return_value=({'cmf20':.8},book))
        with patch.object(ai_swarm.subscription_bridge,'status',return_value=dict(configured=True,grokReady=True,chatgptReady=True)), \
             patch.object(swarm,'_complete',side_effect=complete), \
             patch.object(ai_swarm,'microstructure',side_effect=lambda _:books[0]), \
             patch.object(ai_swarm,'_portfolio_allowed',return_value=True), \
             patch.object(ai_swarm,'_catalyst_allowed',return_value=True):
            report=await swarm.evaluate('BTC','long',economics=economics())
        self.assertEqual(called,['grok-x-pulse','grok-desk-commander'])
        self.assertFalse(report['quorum'])
        with ai_swarm.connection() as con:
            rows=con.execute("SELECT status,estimated_cost FROM attempts WHERE agent IN ('grok-flow-scalper','chatgpt-quant-risk')").fetchall()
        self.assertEqual(rows,[('success',0.0),('success',0.0)])

    async def test_strategic_reviews_persist_cache_and_refresh_at_time_or_ten_closures(self):
        from dataclasses import replace
        swarm=ai_swarm.MultiAgentSwarm(replace(self.settings,transport='subscription',min_request_seconds=0,max_day=16,max_hour=12))
        allocation=regime_allocation()
        post=forensic_audit()
        clock=[1800000000.0]
        async def complete(spec,payload):
            return json.dumps(allocation if spec.report is ai_swarm.RegimeAllocationReport else post),{}
        with patch.object(ai_swarm.subscription_bridge,'status',return_value=dict(configured=True,grokReady=True,chatgptReady=True)), \
             patch.object(ai_swarm.time,'time',side_effect=lambda:clock[0]), \
             patch.object(swarm,'_complete',side_effect=complete) as calls:
            self.assertEqual(await swarm.evaluate_regime_allocation({'btc':{}}),allocation)
            await swarm.evaluate_regime_allocation({'changed':True})
            self.assertEqual(calls.await_count,1)
            restarted=ai_swarm.MultiAgentSwarm(swarm.settings)
            self.assertEqual(await restarted.evaluate_regime_allocation({'btc':{}}),allocation)
            clock[0]+=4*3600
            await swarm.evaluate_regime_allocation({'btc':{}})
            self.assertEqual(calls.await_count,2)
            await swarm.audit_post_mortem(dict(trades=[{}],closedCount=40))
            await swarm.audit_post_mortem(dict(trades=[{}],closedCount=49))
            self.assertEqual(calls.await_count,3)
            await swarm.audit_post_mortem(dict(trades=[{}],closedCount=50))
            self.assertEqual(calls.await_count,4)
            clock[0]+=12*3600
            await swarm.audit_post_mortem(dict(trades=[{}],closedCount=50))
            self.assertEqual(calls.await_count,5)
        with ai_swarm.connection() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM strategic_reviews').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT SUM(estimated_cost) FROM attempts').fetchone()[0],0)

    async def test_invalid_strategic_report_does_not_enter_cache_or_fallback(self):
        from dataclasses import replace
        swarm=ai_swarm.MultiAgentSwarm(replace(self.settings,transport='subscription',min_request_seconds=0))
        with patch.object(ai_swarm.subscription_bridge,'status',return_value=dict(configured=True,grokReady=True,chatgptReady=True)), \
             patch.object(swarm,'_complete',AsyncMock(return_value=(json.dumps(regime_allocation(maxPortfolioRiskMultiplier=1.1)),{}))), \
             patch.object(swarm,'_post',AsyncMock()) as http:
            self.assertIsNone(await swarm.evaluate_regime_allocation({'btc':{}}))
        http.assert_not_awaited()
        with ai_swarm.connection() as con:
            self.assertIsNone(con.execute('SELECT payload FROM strategic_reviews').fetchone()[0])

    def test_allocation_is_binding_only_when_armed_and_stale_reports_fail_closed(self):
        from dataclasses import replace
        now=1800000000
        payload=json.dumps(regime_allocation(allowedFamilies=['trend'],maxPortfolioRiskMultiplier=.6))
        with ai_swarm.connection() as con:
            con.execute('INSERT INTO strategic_reviews(kind,ts,payload) VALUES (?,?,?)',('regime',now,payload))
        swarm=ai_swarm.MultiAgentSwarm(replace(self.settings,transport='subscription',mode='paper',armed=True))
        with patch.object(ai_swarm,'SWARM',swarm),patch.object(ai_swarm.time,'time',return_value=now):
            self.assertTrue(ai_swarm.allocation_gate('trend')['allowed'])
            self.assertEqual(ai_swarm.allocation_gate('trend')['maxExposureMultiplier'],.6)
            self.assertFalse(ai_swarm.allocation_gate('meanrev')['allowed'])
            with patch.object(ai_swarm.time,'time',return_value=now+4*3600):
                self.assertFalse(ai_swarm.allocation_gate('trend')['allowed'])
            swarm.settings=replace(swarm.settings,armed=False,mode='shadow')
            self.assertEqual(ai_swarm.allocation_gate('meanrev'),dict(allowed=True,maxExposureMultiplier=1.0))

    def test_post_mortem_merges_last_forty_net_closures_and_preserves_entry_evidence(self):
        from app import desk,bots
        desk_path=Path(self.tmp.name)/'desk.sqlite'
        bot_path=Path(self.tmp.name)/'bots.sqlite'
        with desk.connect(desk_path) as con:
            for i in range(25):
                payload=dict(status='closed',costPolicy=dict(version='net-costs-v1'),entryKey=str(i),
                    symbol='BTCUSDT',pnl=i-10,exitTime=i,entryContext=dict(extension_atr=i),confirmations=[dict(system='Régimen',vote=1)])
                con.execute('INSERT INTO trades(entry_key,payload) VALUES (?,?)',(str(i),json.dumps(payload)))
            con.execute('INSERT INTO trades(entry_key,payload) VALUES (?,?)',('legacy',json.dumps(dict(status='closed',exitTime=10000,pnl=1000))))
        with patch.object(bots,'DB_PATH',bot_path),bots._conn() as con:
            for i in range(25,50):
                snap=json.dumps(dict(economics=dict(version='net-costs-v1'),entryContext=dict(extension_atr=i),confirmations=[]))
                con.execute("INSERT INTO bot_trades(bot,symbol,direction,qty,entry_price,entry_time,sl,tp,reasons,pnl,status,exit_time,snapshot) VALUES ('trend','BTC','long',1,100,1,90,120,'[]',?,'closed',?,?)",(i-10,i,snap))
        with patch.object(desk,'DB_PATH',desk_path),patch.object(bots,'DB_PATH',bot_path):
            context=ai_swarm.post_mortem_context()
        self.assertEqual(context['closedCount'],50)
        self.assertEqual(len(context['trades']),40)
        self.assertEqual([t['exitTime'] for t in context['trades']],list(range(49,9,-1)))
        self.assertEqual(context['trades'][0]['entryContext'],dict(extension_atr=49))
        self.assertEqual(context['trades'][0]['bot'],'trend')
        self.assertTrue(context['trades'][-1]['confirmations'])

    def test_regime_context_contains_closed_btc_4h_daily_and_universe_breadth(self):
        from types import SimpleNamespace
        from app import desk
        now=800*86400
        def bars(seconds):
            return [dict(time=now-(250-i)*seconds,open=100+i*.2,close=100+i*.2,
                         high=101+i*.2,low=99+i*.2,volume=100) for i in range(250)]
        cache={('BTCUSDT','4h'):(now,bars(14400)),('BTCUSDT','1d'):(now,bars(86400)),
               ('SOLUSDT','1h'):(now,bars(3600))}
        engine=SimpleNamespace(candle_cache=cache,state=dict(candidates=[dict(symbol='SOLUSDT',eligible=True)]))
        with patch.object(desk,'DESK',engine),patch.object(ai_swarm.time,'time',return_value=now),patch.object(gemini_agent,'AGENT',None), \
             patch.object(ai_swarm,'post_mortem_context',return_value=dict(trades=[])):
            context=ai_swarm.regime_context()
            self.assertEqual(set(context['btc']),{'4h','1d'})
            self.assertEqual(context['breadthAboveEma50Pct'],{'3600':100.0,'14400':100.0})
            for _,rows in cache.values():
                rows.append(dict(time=now,open=10000,high=10000,low=10000,close=10000,volume=1))
            self.assertEqual(ai_swarm.regime_context(),context)
            cache.pop(('BTCUSDT','1d'))
            self.assertIsNone(ai_swarm.regime_context())

    async def test_sqlite_lease_prevents_duplicate_cli_reviews_across_swarm_instances(self):
        from dataclasses import replace
        swarm=ai_swarm.MultiAgentSwarm(replace(self.settings,transport='subscription',min_request_seconds=0))
        started=asyncio.Event();release=asyncio.Event()
        report=regime_allocation(maxPortfolioRiskMultiplier=.5)
        async def complete(*args):
            started.set();await release.wait();return json.dumps(report),{}
        with patch.object(ai_swarm.subscription_bridge,'status',return_value=dict(configured=True,grokReady=True,chatgptReady=True)),patch.object(swarm,'_complete',side_effect=complete) as calls:
            first=asyncio.create_task(swarm.evaluate_regime_allocation({'btc':{}}))
            await started.wait()
            other=ai_swarm.MultiAgentSwarm(swarm.settings)
            self.assertIsNone(await other.evaluate_regime_allocation({'btc':{}}))
            release.set()
            self.assertEqual(await first,report)
        self.assertEqual(calls.await_count,1)

    async def test_cli_uses_exact_new_prompts_schemas_and_refreshes_legacy_caches(self):
        from dataclasses import replace
        from app import desk,derivatives_flow
        now=1800000000
        swarm=ai_swarm.MultiAgentSwarm(replace(self.settings,transport='subscription',min_request_seconds=0,max_day=16,max_hour=12))
        with ai_swarm.connection() as con:
            con.execute('INSERT INTO pulse_cache VALUES (?,?,?,?)',('BTC',now,'none',json.dumps(pulse())))
            con.execute('INSERT INTO strategic_reviews(kind,ts,payload) VALUES (?,?,?)',
                ('regime',now,json.dumps(dict(regime='trend',authorizedFamilies=['trend'],maxExposureMultiplier=1,reason='Anterior'))))
            con.execute('INSERT INTO strategic_reviews(kind,ts,payload) VALUES (?,?,?)',
                ('post_mortem',now,json.dumps(dict(suggestedVetoes=[],reason='Anterior'))))
        seen=[]
        async def complete(spec,payload,schema,timeout):
            seen.append((spec.prompt,spec.report,json.loads(payload),schema))
            if spec.report is ai_swarm.BatchPulseReport:
                self.assertIn('relativeVolume',json.loads(payload)['candidates'][0])
                self.assertEqual(json.loads(payload)['candidates'][0]['fundingRate'],.0001)
                return json.dumps(batch_pulse(*json.loads(payload)['symbols'])),{}
            if spec.report is ai_swarm.RegimeAllocationReport:
                return json.dumps(regime_allocation()),{}
            return json.dumps(forensic_audit(statisticalMaturity='insufficient_sample',sampleSize=4)),{}
        with patch.object(ai_swarm.subscription_bridge,'status',return_value=dict(configured=True,grokReady=True,chatgptReady=True)), \
             patch.object(ai_swarm.subscription_bridge,'complete',side_effect=complete),patch.object(ai_swarm.time,'time',return_value=now), \
             patch.object(ai_swarm,'select_pulse_batch',return_value=['BTC']),patch.object(desk,'DESK',None), \
             patch.object(derivatives_flow,'peek',return_value=dict(fundingRate=.0001,oiChange1hPct=2)), \
             patch.object(swarm,'_post',AsyncMock()) as http:
            parsed=await swarm.refresh_pulse_batch()
            self.assertIn('BTC',parsed)
            self.assertIsNone(await swarm.refresh_pulse_batch())
            regime=await swarm.evaluate_regime_allocation(dict(btc={'4h':dict(choppiness=58,adx=17)}))
            self.assertEqual(regime['marketRegime'],'choppy_range')
            self.assertFalse(regime['allowBreakouts'])
            audit=await swarm.audit_post_mortem(dict(trades=[{}],sampleSize=4,closedCount=4,netExpectancyR=-.3))
            self.assertEqual(audit['statisticalMaturity'],'insufficient_sample')
            self.assertEqual(ai_swarm.forensic_review()['sizeCap'],.5)
        self.assertEqual([item[0] for item in seen],[ai_swarm.PROMPT_PULSE_BATCH,ai_swarm.PROMPT_REGIME_STRATEGIST,ai_swarm.PROMPT_FORENSIC_AUDITOR])
        for prompt,model,payload,schema in seen:
            self.assertFalse(schema['additionalProperties'])
            self.assertEqual(set(schema['required']),set(schema['properties']))
        http.assert_not_awaited()
        with ai_swarm.connection() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM attempts WHERE status='success' AND estimated_cost=0").fetchone()[0],3)

    def test_allocation_blocks_breakouts_avoided_symbols_and_keeps_maker_only_families(self):
        from dataclasses import replace
        now=1800000000
        report=regime_allocation(marketRegime='choppy_range',allowedFamilies=['trend','desk-maker'],avoidedSymbols=['ETH'],requireMakerEntry=False)
        with ai_swarm.connection() as con:
            con.execute('INSERT INTO strategic_reviews(kind,ts,payload) VALUES (?,?,?)',('regime',now,json.dumps(report)))
        swarm=ai_swarm.MultiAgentSwarm(replace(self.settings,transport='subscription',mode='paper',armed=True))
        with patch.object(ai_swarm,'SWARM',swarm),patch.object(ai_swarm.time,'time',return_value=now):
            self.assertFalse(ai_swarm.allocation_gate('trend',symbol='BTC',setup='breakout')['allowed'])
            self.assertFalse(ai_swarm.allocation_gate('trend',symbol='ETHUSDT',setup='trend-pullback')['allowed'])
            maker=ai_swarm.allocation_gate('meanrev',symbol='BTC',setup='recovery')
            self.assertTrue(maker['allowed'])
            self.assertTrue(maker['requireMakerEntry'])

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


def pulse_row(symbol, headlineRisk='none'):
    return dict(symbol=symbol, bias=0.4, convictionMultiplier=1.0, fakeout=False, trapDirection='none',
                headlineRisk=headlineRisk,catalystType='none',catalysts=['liquidez'],citations=[],reason='Lectura de prueba')


class DerivativesAndBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(ai_swarm, 'DB_PATH', Path(self.tmp.name) / 'swarm.sqlite')
        self.db.start()
        from app import derivatives_flow
        self.derivatives = derivatives_flow
        derivatives_flow._CACHE.clear()
        derivatives_flow._INFLIGHT.clear()

    async def asyncTearDown(self):
        self.derivatives._CACHE.clear()
        self.derivatives._INFLIGHT.clear()
        self.db.stop()
        self.tmp.cleanup()

    def test_regime_boundaries_use_strict_funding_and_open_interest(self):
        regime = self.derivatives.derivatives_regime
        self.assertFalse(regime(0.0005, 3.1, 0.0, 'long')['crowdedLong'])
        self.assertFalse(regime(0.0006, 3.0, 0.0, 'long')['crowdedLong'])
        crowded = regime(0.0006, 3.1, 0.0, 'long')
        self.assertTrue(crowded['crowdedLong'])
        self.assertFalse(crowded['cleanBreakout'])
        self.assertEqual(crowded['flowAdjustment'], -0.25)
        self.assertEqual(regime(0.0006, 3.1, 0.0, 'short')['flowAdjustment'], 0.25)
        clean = regime(0.0002, 1.6, 0.1, 'long')
        self.assertTrue(clean['cleanBreakout'])
        self.assertFalse(clean['crowdedLong'])
        self.assertEqual(clean['flowAdjustment'], 0.25)
        self.assertFalse(regime(0.00021, 1.6, 0.1, 'long')['cleanBreakout'])
        self.assertFalse(regime(0.001, 4.0, 0.1, 'long')['crowdedLong'])
        missing = regime(None, None, None, 'long')
        self.assertFalse(missing['crowdedLong'])
        self.assertFalse(missing['cleanBreakout'])
        self.assertEqual(missing['flowAdjustment'], 0.0)

    async def test_fapi_outage_returns_a_neutral_regime_without_raising(self):
        class Down:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, *args, **kwargs):
                raise httpx.ConnectTimeout('fapi.binance.com')

        with patch.object(self.derivatives.httpx, 'AsyncClient', Down):
            snap = await self.derivatives.read_symbol('BTC', 'long')
        self.assertFalse(snap['available'])
        self.assertEqual(snap['source'], 'https://fapi.binance.com')
        self.assertFalse(snap['regime']['crowdedLong'])
        self.assertFalse(snap['regime']['cleanBreakout'])
        self.assertEqual(snap['regime']['flowAdjustment'], 0.0)
        self.assertEqual(self.derivatives._CACHE, {})
        with patch.object(self.derivatives.httpx, 'AsyncClient', side_effect=AssertionError('SPY no sale a la red')):
            spy = await self.derivatives.read_symbol('SPY', 'long')
        self.assertFalse(spy['available'])

    def test_batch_pulse_parses_five_symbols_and_rejects_a_bad_lot(self):
        symbols = ['BTC', 'SPY', 'ETH', 'SOL', 'LINK']
        parsed = ai_swarm.parse_batch_pulse({'items': [pulse_row(symbol) for symbol in symbols]}, symbols)
        self.assertEqual(list(parsed), symbols)
        self.assertTrue(all(row['available'] is True for row in parsed.values()))
        self.assertEqual(ai_swarm.PROMPT_VERSION, 2)
        self.assertIn('DATO NO FIABLE', ai_swarm.PROMPT_PULSE_BATCH)
        specs = ai_swarm.MultiAgentSwarm(ai_swarm.Settings(transport='subscription')).specs()
        self.assertEqual([spec.agent_id for spec in specs],
                         ['grok-x-pulse', 'grok-flow-scalper', 'chatgpt-quant-risk', 'grok-desk-commander'])
        self.assertIs(specs[0].report, ai_swarm.BatchPulseReport)
        with self.assertRaises(ValueError):
            ai_swarm.parse_batch_pulse({'items': [pulse_row(symbol) for symbol in symbols + ['ADA']]}, symbols + ['ADA'])
        with self.assertRaises(ValueError):
            ai_swarm.parse_batch_pulse({'items': [pulse_row('DOGE')]}, ['BTC'])
        with self.assertRaises(ValueError):
            ai_swarm.parse_batch_pulse({'items': [pulse_row('BTC'), pulse_row('BTC')]}, ['BTC'])
        with self.assertRaises(ValueError):
            ai_swarm.parse_batch_pulse(pulse_row('BTC'), ['BTC'])
        self.assertTrue(ai_swarm._clean_report('pulse', pulse_row('BTC'), 'BTC')['available'])

    def test_event_headline_is_stored_but_not_reused_and_cache_expires_at_forty_five_minutes(self):
        event = ai_swarm.parse_batch_pulse({'items':[pulse_row('BTC','event')]},['BTC'])['BTC']
        ai_swarm.store_pulse_batch({'BTC': event}, now=1_000.0)
        self.assertIsNone(ai_swarm.fresh_cached_pulse('BTC', now=1_010.0))
        fresh = ai_swarm.parse_batch_pulse({'items':[pulse_row('ETH')]},['ETH'])['ETH']
        ai_swarm.store_pulse_batch({'ETH': fresh}, now=2_000.0)
        self.assertEqual(ai_swarm.fresh_cached_pulse('ETH', now=2_000.0 + 2699)['symbol'], 'ETH')
        self.assertIsNone(ai_swarm.fresh_cached_pulse('ETH', now=2_000.0 + 2700))
        with ai_swarm.connection() as con:
            self.assertEqual(con.execute('SELECT headline_risk FROM pulse_cache WHERE symbol=?', ('BTC',)).fetchone()[0], 'event')
            symbols = [row[0] for row in con.execute('SELECT symbol FROM decisions ORDER BY id').fetchall()]
        self.assertEqual(symbols, ['BATCH', 'BATCH'])

    async def test_cached_pulse_skips_the_search_call_and_a_batch_cycle_does_not_block_entry(self):
        from dataclasses import replace
        report = ai_swarm.parse_batch_pulse(batch_pulse(),['BTC'])['BTC']
        ai_swarm.store_pulse_batch({'BTC': report}, now=time.time())
        with ai_swarm.connection() as con:
            con.execute("INSERT INTO cycles(ts,symbol,status,estimated_cost) VALUES (?,?,?,0)",
                        (time.time(), 'BATCH', 'success'))
        swarm = ai_swarm.MultiAgentSwarm(ai_swarm.Settings(
            xai_key='test-xai-secret', openai_key='test-openai-secret',
            transport='subscription', min_request_seconds=120, max_day=1))
        called = []

        async def complete(spec, payload):
            called.append(spec.agent_id)
            return json.dumps(commander()), {}

        book = dict(available=True, imbalance=.7, spreadBps=1, ageSeconds=.1)
        swarm.market_pack = AsyncMock(return_value=({'cmf20': .2, 'cvd_divergence': 0.0}, book))
        ready = dict(configured=True, grokReady=True, chatgptReady=True)
        with patch.object(ai_swarm.subscription_bridge, 'status', return_value=ready), \
             patch.object(swarm, '_complete', side_effect=complete), \
             patch.object(ai_swarm, 'microstructure', return_value=book), \
             patch.object(ai_swarm, '_portfolio_allowed', return_value=True), \
             patch.object(ai_swarm, '_catalyst_allowed', return_value=True):
            decision = await swarm.evaluate('BTC', 'long', economics=economics())
        self.assertEqual(called, ['grok-desk-commander'])
        self.assertTrue(decision['quorum'])
        with ai_swarm.connection() as con:
            message = con.execute("SELECT message FROM attempts WHERE agent='grok-x-pulse'").fetchone()[0]
        self.assertEqual(message, 'Pulso en caché de 45 minutos')


class RuntimePromptContractTests(unittest.TestCase):
    def test_strict_batch_schema_requires_nested_fields_and_preserves_enums(self):
        from app import gpt_quant_agent
        schema=gpt_quant_agent.structured_format(ai_swarm.BatchPulseReport)['json_schema']['schema']
        item=schema['$defs']['BatchPulseItem']
        self.assertEqual(set(schema['properties']),{'items'})
        self.assertFalse(item['additionalProperties'])
        self.assertEqual(set(item['required']),set(item['properties']))
        self.assertEqual(item['properties']['trapDirection']['enum'],['long','short','both','none'])
        self.assertNotIn('default',item['properties']['citations'])
        self.assertEqual(set(ai_swarm.RegimeAllocationReport.model_fields),
            {'marketRegime','allowBreakouts','requireMakerEntry','allowedFamilies','maxPortfolioRiskMultiplier','favoredSymbols','avoidedSymbols','rationale'})
        self.assertEqual(set(ai_swarm.ForensicAuditReport.model_fields),
            {'statisticalMaturity','sampleSize','netExpectancyR','primaryLeak','recommendedMaxExtensionAtr','recommendedMinRsPercentile','requireMakerOnly','vetoedSetups','rationale'})

    def test_batch_rejects_extras_coercions_partial_results_and_inconsistent_traps(self):
        for overrides in (dict(extra=True),dict(fakeout='false'),dict(bias='0.5'),dict(bias=float('nan')),
                          dict(convictionMultiplier=1.26),dict(trapDirection='up'),
                          dict(catalystType='rumor'),dict(headlineRisk='critical'),
                          dict(fakeout=True,trapDirection='none'),dict(fakeout=False,trapDirection='both'),
                          dict(citations=['http://example.com']),dict(citations=['https://']),
                          dict(citations=['https://user:password@example.com']),
                          dict(citations=['https://example.com']*5)):
            with self.subTest(overrides=overrides),self.assertRaises(ValueError):
                ai_swarm.parse_batch_pulse(dict(items=[dict(pulse_row('BTC'),**overrides)]),['BTC'])
        for data in (dict(items=[]),dict(items=[pulse_row('BTC')]),
                     dict(reports=[pulse_row('BTC')]),dict(items=[pulse_row('BTC'),pulse_row('BTC')])):
            with self.subTest(data=data),self.assertRaises(ValueError):
                ai_swarm.parse_batch_pulse(data,['BTC','ETH'])

    def test_bilateral_trap_reaches_local_hardening_consensus_and_contextual_veto(self):
        trap=dict(pulse_row('BTC'),fakeout=True,trapDirection='both')
        parsed=ai_swarm.parse_batch_pulse(dict(items=[trap]),['BTC'])['BTC']
        self.assertEqual(ai_swarm.harden_pulse(parsed,'BTC')['trapDirection'],'both')
        for direction in ('long','short'):
            decision=decide(direction=direction,pulse=parsed)
            self.assertFalse(decision['quorum'])
            self.assertTrue(any('fakeout' in veto.lower() for veto in decision['vetoes']))
            with patch.object(ai_swarm,'fresh_pulse',return_value=parsed),patch.object(gemini_agent,'AGENT',None):
                self.assertFalse(gemini_agent.catalyst_gate('BTC',direction)['allowed'])

    def test_regime_hardening_enforces_chop_boundary_and_risk_off_ceiling(self):
        report=ai_swarm.RegimeAllocationReport.model_validate(regime_allocation())
        choppy=ai_swarm.harden_strategic_report(report,dict(btc={'4h':dict(choppiness=58,adx=17.9)}))
        self.assertEqual(choppy.marketRegime,'choppy_range')
        self.assertFalse(choppy.allowBreakouts)
        self.assertTrue(choppy.requireMakerEntry)
        cascade=ai_swarm.harden_strategic_report(report,dict(breadth4hPct=24,leader=dict(timeframes={'4h':dict(return24hPct=-3,volatilityExpanding=True)})))
        self.assertEqual(cascade.marketRegime,'risk_off_cascade')
        self.assertEqual(cascade.maxPortfolioRiskMultiplier,.5)
        self.assertFalse(cascade.allowBreakouts)
        for overrides in (dict(marketRegime='bull'),dict(allowedFamilies=['scalper']),
                          dict(maxPortfolioRiskMultiplier=1.01),dict(maxPortfolioRiskMultiplier=float('nan')),
                          dict(allowBreakouts='true'),dict(orderPrice=100)):
            with self.subTest(overrides=overrides),self.assertRaises(ValueError):
                ai_swarm.RegimeAllocationReport.model_validate(regime_allocation(**overrides))

    def test_forensic_restrictions_require_eight_losing_observations_per_segment(self):
        report=ai_swarm.ForensicAuditReport.model_validate(forensic_audit(sampleSize=30,
            recommendedMaxExtensionAtr=1.5,recommendedMinRsPercentile=90.0,requireMakerOnly=True,
            vetoedSetups=['bad','small','good']))
        bad=[dict(rMultiple=-.3,entryContext=dict(extension_atr=3,rsPercentile=80,spread_to_atr=.5)) for _ in range(8)]
        good=[dict(rMultiple=.2,entryContext=dict(extension_atr=1,rsPercentile=99,spread_to_atr=.01)) for _ in range(22)]
        context=dict(sampleSize=30,netExpectancyR=.05,trades=bad+good,
                     segments=dict(bad=dict(sampleSize=8,expectancyR=-.3),small=dict(sampleSize=7,expectancyR=-1),good=dict(sampleSize=22,expectancyR=.2)))
        valid=ai_swarm.harden_strategic_report(report,context)
        self.assertEqual(valid.vetoedSetups,['bad'])
        self.assertEqual(valid.recommendedMaxExtensionAtr,1.5)
        self.assertEqual(valid.recommendedMinRsPercentile,90)
        self.assertTrue(valid.requireMakerOnly)
        self.assertEqual(valid.netExpectancyR,.05)
        context['trades']=bad[:7]+good
        context['segments']['bad']['sampleSize']=7
        small=ai_swarm.harden_strategic_report(report,context)
        self.assertEqual(small.vetoedSetups,[])
        self.assertEqual(small.recommendedMaxExtensionAtr,2.25)
        self.assertEqual(small.recommendedMinRsPercentile,85)
        self.assertFalse(small.requireMakerOnly)
        with self.assertRaises(ValueError):
            ai_swarm.harden_strategic_report(report,dict(sampleSize=29))

    def test_forensic_maturity_and_net_r_use_local_initial_risk(self):
        with self.assertRaises(ValueError):
            ai_swarm.ForensicAuditReport.model_validate(forensic_audit(sampleSize=29))
        for overrides in (dict(recommendedMaxExtensionAtr=3.1),dict(recommendedMinRsPercentile=96),
                          dict(netExpectancyR=float('inf')),dict(sampleSize=True),dict(sizeCap=2)):
            with self.subTest(overrides=overrides),self.assertRaises(ValueError):
                ai_swarm.ForensicAuditReport.model_validate(forensic_audit(**overrides))
        values=ai_swarm.forensic_trade_fields(-10,20,3600,dict(funding=.001,oi_delta=3,rsScore=1))
        self.assertEqual(values['rMultiple'],-.5)
        self.assertEqual(values['hourUTC'],1)
        self.assertEqual(values['entryContext']['funding_rate'],.001)
        self.assertEqual(values['entryContext']['oi_change_pct'],3)
        self.assertIsNone(ai_swarm.forensic_trade_fields(-10,None,3600,{})['rMultiple'])


if __name__ == '__main__':
    unittest.main()
