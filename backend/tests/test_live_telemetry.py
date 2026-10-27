from desk_unit_support import fund_unit_desk
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import ai_swarm, desk, gpt_quant_agent as quant, learning
from app.hub import PriceHub


class Socket:
    def __init__(self, blocked=False):
        self.messages = []
        self.blocked = blocked
        self.closed = False

    async def send_json(self, message):
        if self.blocked:
            await asyncio.Event().wait()
        self.messages.append(message)

    async def close(self, code=1000):
        self.closed = True


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = PriceHub()
        self.hub.start()

    async def asyncTearDown(self):
        await self.hub.stop()

    async def flush(self):
        for _ in range(8):
            await asyncio.sleep(0)

    async def test_telemetry_needs_no_price_subscription_and_copies_payload(self):
        ws = Socket()
        await self.hub.connect(ws)
        payload = dict(type='agent_thought', detail='Revisión', verdict=dict(approved=False))
        self.hub.broadcast_event(payload)
        payload['verdict']['approved'] = True
        await self.flush()
        self.assertFalse(ws.messages[0]['verdict']['approved'])
        self.assertEqual(ws.messages[0]['sequence'], 1)
        self.assertFalse(self.hub._tasks)

    async def test_blocked_browser_does_not_delay_other_clients(self):
        slow, fast = Socket(True), Socket()
        await self.hub.connect(slow)
        await self.hub.connect(fast)
        for i in range(300):
            self.hub.broadcast_event(dict(type='role_update', id=i))
            await self.flush()
        self.assertNotIn(slow, self.hub._clients)
        self.assertIn(fast, self.hub._clients)
        self.assertEqual(len(fast.messages), 300)

    async def test_new_connection_recovers_latest_snapshots_in_sequence(self):
        self.hub.broadcast_event(dict(type='desk_snapshot', desk='crypto', data={'enabled':True}))
        self.hub.broadcast_event(dict(type='agent_thought', detail='Última lectura'))
        self.hub.broadcast_event(dict(type='desk_snapshot', desk='crypto', data={'enabled':False}))
        ws=Socket()
        await self.hub.connect(ws)
        await self.flush()
        self.assertEqual([m['sequence'] for m in ws.messages], [2,3])
        self.assertFalse(ws.messages[-1]['data']['enabled'])

    async def test_background_thread_can_publish(self):
        ws=Socket()
        await self.hub.connect(ws)
        await asyncio.to_thread(self.hub.broadcast_event, dict(type='position_update',data={'positions':[]}))
        await self.flush()
        self.assertEqual(ws.messages[0]['type'], 'position_update')

    async def test_legacy_price_subscription_still_receives_ticks(self):
        ws=Socket()
        with patch('app.hub.market_data.get_price', AsyncMock(return_value=dict(symbol='BTC',price=100,time=time.time()))):
            await self.hub.subscribe(ws,'BTC')
            await self.flush()
        self.assertEqual(ws.messages[0]['type'],'tick')
        self.assertEqual(ws.messages[0]['price'],100)


class QuantTests(unittest.TestCase):
    def economic(self, **overrides):
        payload = dict(planned=dict(viable=True,netRewardRisk=2), sampleSize=10)
        payload.update(overrides)
        return payload

    def test_small_sample_cannot_claim_a_kelly_edge_or_increase_risk(self):
        result=quant.local_verdict(self.economic(winRate=99,payoffRatio=100),.01)
        self.assertTrue(result['approved'])
        self.assertEqual(result['kelly_score'],0)
        self.assertLessEqual(result['adjusted_risk_fraction'],.005)

    def test_net_negative_and_portfolio_and_drawdown_override_approval(self):
        cases=[self.economic(sampleSize=40,expectancyNet=-1,profitFactor=2,winRate=70,breakEvenWinRate=40),
               self.economic(dailyStopped=True),self.economic(planned=dict(viable=False,netRewardRisk=10))]
        for economics in cases:
            self.assertFalse(quant.local_verdict(economics,.01)['approved'])
        self.assertFalse(quant.local_verdict(self.economic(),.01,portfolio={'allowed':False})['approved'])
        self.assertFalse(quant.local_verdict(self.economic(),.01,learning_allowed=False)['approved'])

    def test_kelly_uses_observed_net_payoff_and_quarter_kelly_cap(self):
        result=quant.local_verdict(self.economic(sampleSize=50,winRate=55,profitFactor=1.8,
            expectancyNet=1,breakEvenWinRate=40,payoffRatio=1.5),.1)
        self.assertAlmostEqual(result['kelly_score'],.25)
        self.assertAlmostEqual(result['adjusted_risk_fraction'],.0625)

    def test_shared_sample_ignores_open_partial_and_non_finite_rows(self):
        sample=learning.closed_net_sample([{'status':'closed','pnl':2},{'status':'closed','pnl':-1},
            {'status':'open','pnl':100},{'status':'closed','pnl':float('nan')},{'status':'partial','pnl':5}])
        self.assertEqual(sample['sampleSize'],2)
        self.assertEqual(sample['payoffRatio'],2)
        self.assertEqual(sample['expectancyNet'],.5)

    def test_structured_output_requires_every_field_and_forbids_extras(self):
        contract=quant.structured_format(ai_swarm.RiskReport)
        self.assertEqual(contract['type'],'json_schema')
        schema=contract['json_schema']['schema']
        self.assertTrue(contract['json_schema']['strict'])
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['required']),set(schema['properties']))

    def test_role_update_is_emitted_only_after_database_commit(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(desk.hub,'broadcast_event') as send:
            engine=desk.Desk(db_path=Path(folder)/'desk.sqlite')
            engine.role('Size','blocked','Costes excesivos',provider='GPT-Quant-Risk')
            event=send.call_args.args[0]
            with engine.connect() as con:
                self.assertEqual(con.execute('SELECT detail FROM events WHERE id=?',(event['id'],)).fetchone()[0],event['detail'])
            self.assertEqual(event['type'],'role_update')
            self.assertEqual(event['desk'],'crypto')
            send.reset_mock()
            engine.role('Size','blocked','Costes excesivos',provider='GPT-Quant-Risk')
            send.assert_not_called()


class ClearanceTests(unittest.TestCase):
    def test_old_quorum_and_stale_book_are_rejected_at_execution(self):
        swarm=ai_swarm.MultiAgentSwarm(ai_swarm.Settings(mode='paper',armed=True))
        swarm.latest['BTC']=dict(at=time.time()-121,direction='long',authorized=True,flow={})
        with patch.object(ai_swarm,'SWARM',swarm):
            self.assertFalse(ai_swarm.execution_clearance('BTCUSDT','long')['allowed'])
            swarm.latest['BTC']['at']=time.time()
            with patch.object(ai_swarm,'microstructure',return_value=dict(available=False)):
                self.assertFalse(ai_swarm.execution_clearance('BTC','long')['allowed'])


class DeskAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.engine=desk.Desk(db_path=Path(self.folder.name)/'desk.sqlite')
        fund_unit_desk(self,self.engine)
        self.analysis=dict(bar=int(time.time())-900,close=100,ema20=100,ema50=99,
                           atr=2,rsi=55,trend=True,pullback=True,rows=[])

    async def asyncTearDown(self):
        self.folder.cleanup()

    async def enter(self, clearance):
        from test_desk import data
        with patch.object(desk.gemini_agent,'AGENT',None), \
             patch.object(self.engine,'wire',AsyncMock(return_value=dict(ok=True,detail='Fuente probada'))), \
             patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])), \
             patch.object(desk,'tape',return_value=self.analysis), \
             patch.object(ai_swarm,'execution_clearance',return_value=clearance), \
             patch.object(ai_swarm,'request_entry_audit'), \
             patch.object(desk.portfolio_risk,'assess',return_value=dict(allowed=True,token=None)):
            await self.engine.scan(data())

    async def test_binding_veto_prevents_entry_before_reserving_money(self):
        await self.enter(dict(binding=True,allowed=False,reason='Sin quorum'))
        self.assertEqual(self.engine.state['positions'],[])
        self.assertEqual(self.engine.state['cash'],desk.INITIAL)
        self.assertEqual(self.engine.public()['trades'],[])

    async def test_approved_entry_respects_both_audited_eur_ceilings(self):
        await self.enter(dict(binding=True,allowed=True,venue='spot',profile='conservative',
                              sizeMultiplier=.5,maxRiskEUR=.8,maxNotionalEUR=30))
        position=self.engine.state['position']
        self.assertIsNotNone(position)
        self.assertLessEqual(position['risk'],.8*.5)
        self.assertLessEqual(position['notional'],30*.5)

    async def test_missing_audited_eur_budget_does_not_admit_an_order(self):
        await self.enter(dict(binding=True,allowed=True,venue='spot',profile='conservative',
                              sizeMultiplier=.5,maxRiskEUR=None,maxNotionalEUR=30))
        self.assertEqual(self.engine.state['positions'],[])


if __name__=='__main__':
    unittest.main()
