import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app import ai_swarm, decision_audit, subscription_bridge as bridge
from test_ai_swarm import economics, pulse, flow, risk, commander, batch_pulse


READY=dict(configured=True,grokReady=True,chatgptReady=True,requiresApiKeys=False)


class BridgeContractTests(unittest.TestCase):
    def test_health_never_starts_clients_reads_auth_or_claims_live_authentication(self):
        with patch.object(bridge,'status',side_effect=AssertionError('No auth probe')), \
             patch.object(bridge,'executable',return_value='installed'), \
             patch.object(bridge.subprocess,'Popen',side_effect=AssertionError('No child')):
            health=bridge.health_snapshot()
        self.assertTrue(health['readOnly'])
        self.assertEqual(health['startedClients'],0)
        self.assertTrue(all(p['installed'] and p['authenticated'] is None for p in health['providers']))

    def test_grok_non_object_envelope_is_an_invalid_response(self):
        with self.assertRaises(bridge.SubscriptionError) as error:
            bridge.parse_result('xai','[]')
        self.assertEqual(error.exception.state,'invalid')

    def test_paid_api_environment_is_not_inherited(self):
        with patch.dict(os.environ,{'XAI_API_KEY':'not-for-this-bridge','OPENAI_API_KEY':'not-for-this-bridge',
                                    'CODEX_API_KEY':'not-for-this-bridge','OPENAI_BASE_URL':'https://other.invalid'}):
            env=bridge.child_environment()
        self.assertNotIn('XAI_API_KEY',env)
        self.assertNotIn('OPENAI_API_KEY',env)
        self.assertNotIn('CODEX_API_KEY',env)
        self.assertNotIn('OPENAI_BASE_URL',env)

    def test_grok_exposes_only_public_structured_answer(self):
        output=json.dumps(dict(text='ignored',thought='private internal text',structuredOutput={'approved':False},usage={'input_tokens':10}))
        result,usage=bridge.parse_result('xai',output)
        self.assertEqual(json.loads(result),{'approved':False})
        self.assertNotIn('private',result)
        self.assertEqual(usage['input_tokens'],10)

    def test_codex_requires_confirmed_completion(self):
        item=dict(type='item.completed',item=dict(type='agent_message',text='{"approved":false}'))
        with self.assertRaises(bridge.SubscriptionError):
            bridge.parse_result('openai',json.dumps(item))
        result,_=bridge.parse_result('openai',json.dumps(item)+'\n'+json.dumps(dict(type='turn.completed',usage={})))
        self.assertEqual(json.loads(result),{'approved':False})

    def test_a_failed_codex_turn_is_not_accepted_as_an_approval(self):
        output=json.dumps(dict(type='turn.failed',error=dict(message='weekly limit exceeded')))
        with self.assertRaises(bridge.SubscriptionError) as error:
            bridge.parse_result('openai',output)
        self.assertEqual(error.exception.state,'quota')

    def test_commands_deny_actions_and_use_supported_clients(self):
        specs=ai_swarm.MultiAgentSwarm(ai_swarm.Settings(transport='subscription')).specs()
        with tempfile.TemporaryDirectory() as folder, patch.object(bridge,'status',return_value=READY), \
             patch.object(bridge,'executable',return_value='C:/installed/client.exe'):
            flow_args,_=bridge.command(specs[1],Path(folder),'untrusted market input',{})
            risk_args,stdin=bridge.command(specs[2],Path(folder),'risk data',{})
        self.assertIn('--deny',flow_args)
        self.assertIn('*',flow_args)
        self.assertIn('--disable-web-search',flow_args)
        self.assertIn('--ignore-user-config',risk_args)
        self.assertIn('read-only',risk_args)
        self.assertIn('features.shell_tool=false',risk_args)
        self.assertEqual(stdin,'risk data')

    def test_codex_with_api_key_login_is_not_a_subscription_session(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder)
            (home/'.codex').mkdir()
            (home/'.grok').mkdir()
            (home/'.codex'/'auth.json').write_text(json.dumps({'auth_mode':'apikey','OPENAI_API_KEY':'secret'}))
            (home/'.grok'/'auth.json').write_text('{}')
            with patch.object(bridge.Path,'home',return_value=home),patch.object(bridge,'executable',return_value='client'):
                self.assertFalse(bridge.status()['chatgptReady'])

    def test_grok_byok_configuration_is_refused_in_subscription_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder)
            (home/'.codex').mkdir()
            (home/'.grok').mkdir()
            (home/'.codex'/'auth.json').write_text(json.dumps({'auth_mode':'chatgpt'}))
            (home/'.grok'/'auth.json').write_text('{}')
            (home/'.grok'/'config.toml').write_text('[model.custom]\nenv_key="AN_API_KEY"\n')
            with patch.object(bridge.Path,'home',return_value=home),patch.object(bridge,'executable',return_value='client'):
                self.assertFalse(bridge.status()['grokReady'])

    def test_cancelled_evaluation_terminates_its_owned_process(self):
        cancel=threading.Event()
        cancel.set()
        process=Mock()
        spec=ai_swarm.MultiAgentSwarm(ai_swarm.Settings(transport='subscription')).specs()[1]
        with tempfile.TemporaryDirectory() as folder,patch.object(bridge,'WORK',Path(folder)), \
             patch.object(bridge,'command',return_value=(['client'],None)), \
             patch.object(bridge.subprocess,'Popen',return_value=process),patch.object(bridge,'_kill') as kill:
            with self.assertRaises(bridge.SubscriptionError):
                bridge._run(spec,'{}',{},10,cancel)
            kill.assert_called_once_with(process)


class SubscriptionSwarmTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.db=patch.object(ai_swarm,'DB_PATH',Path(self.folder.name)/'swarm.sqlite')
        self.db.start()
        self.readiness=patch.object(bridge,'status',return_value=READY)
        self.readiness.start()
        self.swarm=ai_swarm.MultiAgentSwarm(ai_swarm.Settings(transport='subscription',min_request_seconds=0,max_day=4))

    async def asyncTearDown(self):
        await self.swarm.close()
        self.db.stop()
        self.readiness.stop()
        self.folder.cleanup()

    async def test_context_agents_use_cli_and_flow_risk_stay_local_without_api_or_http_fallback(self):
        order=[]
        async def complete(spec,payload,schema,timeout):
            order.append(spec.agent_id)
            self.assertFalse(schema['additionalProperties'])
            await asyncio.sleep(.001)
            report={'grok-x-pulse':batch_pulse(),'grok-flow-scalper':flow(),'chatgpt-quant-risk':risk(),'grok-desk-commander':commander()}[spec.agent_id]
            return json.dumps(report),{'input_tokens':5000,'output_tokens':100}
        book=dict(available=True,imbalance=.7,spreadBps=1,ageSeconds=.1)
        self.swarm.market_pack=AsyncMock(return_value=({},book))
        with patch.object(bridge,'complete',side_effect=complete),patch.object(self.swarm,'_post',AsyncMock()) as http, \
             patch.object(ai_swarm,'microstructure',return_value=book), \
             patch.object(ai_swarm,'_portfolio_allowed',return_value=True),patch.object(ai_swarm,'_catalyst_allowed',return_value=True):
            decision=await self.swarm.evaluate('BTC','long',economics=economics())
        self.assertTrue(decision['quorum'])
        self.assertFalse(decision['authorized'])
        self.assertEqual(decision['billing'],'subscription')
        self.assertEqual(order[-1],'grok-desk-commander')
        self.assertEqual(order,['grok-x-pulse','grok-desk-commander'])
        http.assert_not_awaited()
        self.assertEqual(self.swarm.limits()['estimatedCostMonthUSD'],0)
        audit=decision_audit.health_snapshot()
        self.assertEqual((audit['candidates'],audit['pairedDecisionCandidates'],audit['outcomes']),(1,1,0))
        self.assertFalse(audit['incrementalAdvantageMeasured'])
        self.assertEqual(decision['auditState'],'decision_recorded_without_outcome')

    async def test_usage_limit_closes_entry_without_switching_to_paid_api(self):
        self.swarm.market_pack=AsyncMock(return_value=({},{}))
        with patch.object(bridge,'complete',side_effect=bridge.SubscriptionError('quota','Cupo de suscripción agotado')), \
             patch.object(self.swarm,'_post',AsyncMock()) as http:
            decision=await self.swarm.evaluate('SPY','long',economics=economics())
        self.assertFalse(decision['quorum'])
        self.assertFalse(decision['authorized'])
        http.assert_not_awaited()
        self.assertEqual(self.swarm.limits()['connectionStatus'],'quota')

    def test_subscription_cost_is_never_misreported_as_api_usage(self):
        for spec in self.swarm.specs():
            self.assertEqual(ai_swarm._usage_cost(spec,{'input_tokens':100000,'num_sources_used':100}),0)
        public=self.swarm.status()
        self.assertTrue(public['configured'])
        self.assertFalse(public['requiresApiKeys'])
        self.assertEqual(public['billing'],'subscription')

    def test_risk_cannot_borrow_performance_from_another_account(self):
        report=risk()
        report.update(expectancyNet=99,profitFactor=100,winRate=99,breakEvenWinRate=1)
        checked=ai_swarm.harden_risk(report,dict(planned={'viable':False}),'SPY')
        for key in ('expectancyNet','profitFactor','winRate','breakEvenWinRate'):
            self.assertIsNone(checked[key])
        self.assertFalse(checked['approved'])


class PriorityGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_at_handoff_passes_the_turn_to_the_next_waiter(self):
        gate=bridge.PriorityGate()
        await gate.acquire(1)
        entry=asyncio.create_task(gate.acquire(0))
        scan=asyncio.create_task(gate.acquire(1))
        await asyncio.sleep(0)
        gate.release()
        entry.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await entry
        await asyncio.wait_for(scan,1)
        gate.release()
        await asyncio.wait_for(gate.acquire(0),1)
        gate.release()

    async def test_cancelling_a_queued_waiter_preserves_the_current_owner(self):
        gate=bridge.PriorityGate()
        await gate.acquire(1)
        queued=asyncio.create_task(gate.acquire(0))
        await asyncio.sleep(0)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        self.assertTrue(gate._held)
        self.assertFalse(gate._waiters)
        gate.release()
        await asyncio.wait_for(gate.acquire(0),1)
        gate.release()

    async def test_cancelled_worker_keeps_the_gate_until_its_process_cleanup_finishes(self):
        started=threading.Event()
        cancel_seen=threading.Event()
        finish=threading.Event()
        exited=threading.Event()
        order=[]
        def run(spec,payload,schema,timeout,cancelled):
            order.append(payload)
            if payload=='first':
                started.set()
                cancelled.wait(5)
                cancel_seen.set()
                finish.wait(5)
                exited.set()
                raise bridge.SubscriptionError('unavailable','Cancelación comprobada')
            self.assertTrue(exited.is_set())
            return payload,{}
        with patch.object(bridge,'_run',side_effect=run):
            first=asyncio.create_task(bridge.complete(object(),'first',{},5))
            second=None
            try:
                self.assertTrue(await asyncio.to_thread(started.wait,5))
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                self.assertTrue(await asyncio.to_thread(cancel_seen.wait,5))
                second=asyncio.create_task(bridge.complete(object(),'second',{},5))
                await asyncio.sleep(0)
                self.assertEqual(len(bridge.priority_gate()._waiters),1)
                self.assertEqual(order,['first'])
                finish.set()
                self.assertEqual(await asyncio.wait_for(second,5),('second',{}))
                self.assertEqual(order,['first','second'])
            finally:
                finish.set()
                if second is not None:
                    await asyncio.gather(second,return_exceptions=True)

    async def test_complete_reads_the_task_priority_and_serves_entry_first(self):
        seen = []
        gate = bridge.priority_gate()
        real_acquire = gate.acquire

        async def spy(priority):
            seen.append(priority)
            await real_acquire(priority)

        gate.acquire = spy

        def run(spec, payload, schema, timeout, cancelled):
            return payload, {}

        with patch.object(bridge, '_run', side_effect=run):
            token = bridge.push_priority(0)
            try:
                await bridge.complete(object(), 'entry-context', {}, 1)
            finally:
                bridge.reset_priority(token)
            await bridge.complete(object(), 'explicit-scan', {}, 1, priority=1)
        self.assertEqual(seen, [0, 1])
        self.assertEqual(bridge.current_priority(), 1)

    async def test_a_queued_entry_runs_before_a_queued_scan(self):
        order = []
        hold_started = threading.Event()
        release = threading.Event()

        def run(spec, payload, schema, timeout, cancelled):
            order.append(payload)
            if payload == 'hold':
                hold_started.set()
                release.wait(5)
            return payload, {}

        async def call(priority, payload):
            token = bridge.push_priority(priority)
            try:
                return await bridge.complete(object(), payload, {}, 5)
            finally:
                bridge.reset_priority(token)

        with patch.object(bridge, '_run', side_effect=run):
            hold = asyncio.create_task(call(1, 'hold'))
            try:
                self.assertTrue(await asyncio.to_thread(hold_started.wait, 5))
                gate = bridge.priority_gate()
                scan = asyncio.create_task(call(1, 'scan'))
                for _ in range(50):
                    if gate._waiters:
                        break
                    await asyncio.sleep(0.01)
                entry = asyncio.create_task(call(0, 'entry'))
                for _ in range(50):
                    if len(gate._waiters) >= 2:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(sorted(item[0] for item in gate._waiters), [0, 1])
                release.set()
                await asyncio.wait_for(asyncio.gather(hold, scan, entry), 5)
            finally:
                release.set()
        self.assertEqual(order, ['hold', 'entry', 'scan'])
