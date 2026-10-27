import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from app import decision_audit as audit, desk, experiments, research_plans
from app.economics import net_outcome
from app.book_stream import BookStream
from test_desk import data


class PairedCollectorTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.engine=desk.Desk(db_path=root/'desk.sqlite')
        self.path=audit.storage_for(self.engine.db_path)
        self.registry=experiments.registry_path(self.engine.db_path)
        self.stack.enter_context(patch.object(desk.gemini_agent,'AGENT',None))
        self.stack.enter_context(patch.object(desk.ai_swarm,'SWARM',None))
        self.order=desk.size_order(1000,1000,100,2,data()['metadata']['SOLUSDT'])
        self.economics=dict(planned=self.order['economics'])

    def capture(self,bar=100):
        return self.engine.audit_candidate(dict(symbol='SOLUSDT'),self.order,self.economics,1,bar)

    def test_candidate_and_all_four_decisions_are_one_atomic_receipt(self):
        cid=self.capture()
        receipt=audit.candidate_receipt(cid,self.path)
        self.assertEqual(set(receipt['decisions']),set(audit.ARMS))
        self.assertTrue(receipt['decisions']['rules_local']['allowed'])
        self.assertFalse(receipt['decisions']['rules_plus_ai']['allowed'])
        self.assertEqual(self.capture(),cid)
        self.assertEqual(audit.health_snapshot(self.path)['decisions'],4)
        with audit._connect(write=True,db_path=self.path) as con:
            con.execute("""CREATE TRIGGER fixture_failure BEFORE INSERT ON audit_decisions
                WHEN NEW.arm='rules_plus_ai' BEGIN SELECT RAISE(ABORT,'fixture'); END""")
        self.assertIsNone(self.capture(101))
        self.assertEqual(audit.health_snapshot(self.path)['candidates'],1)

    def test_future_inputs_and_rejected_arms_cannot_acquire_outcomes(self):
        with self.assertRaises(ValueError):
            audit.record_candidate('test','SOLUSDT','long',strategy_version='v',cost_version='net-costs-v1',
                risk_policy='r',economics={},inputs=dict(news=dict(available=True,availableAt=time.time()+100)),db_path=self.path)
        with self.assertRaises(ValueError):
            audit.record_candidate('test','SOLUSDT','long',strategy_version='v',cost_version='net-costs-v1',
                risk_policy='r',economics={},snapshot_at=time.time()+10,db_path=self.path)
        cid=self.capture()
        with self.assertRaises(ValueError):
            audit.record_outcome(cid,'rules_plus_ai',net_pnl=1,currency='USDT',initial_risk=1,
                fees=0,slippage=0,carry=0,outcome_type='counterfactual-observed',db_path=self.path)

    def test_cached_arms_close_net_with_partials_and_idempotent_pairs(self):
        now=time.time()
        pulse=dict(available=True,convictionMultiplier=1.0,fakeout=False)
        cached=dict(at=now,direction='long',quorum=True,sizeMultiplier=1.0,
                    commander=dict(available=True,action='enter',direction='long',sizeMultiplier=1.0))
        with patch.object(desk.ai_swarm,'fresh_decision',return_value=cached), \
             patch.object(desk.ai_swarm,'fresh_pulse',return_value=pulse):
            cid=self.capture()
        order=self.order
        p=dict(order,symbol='SOLUSDT',candidateId=cid,observedArm='rules_local',entryKey='fixture',
               entryTime=now,initialRisk=order['risk'],best=order['entry'],initialDist=4,
               atr=2,status='open',marketFilters=data()['metadata']['SOLUSDT']['filters'])
        self.engine.state['positions'].append(p);self.engine.focus_position()
        self.engine.state['cash']-=p['notional']+p['fee']
        with self.engine.connect() as con:
            con.execute('INSERT INTO trades(entry_key,payload) VALUES (?,?)',('fixture',json.dumps(p)))
        self.assertTrue(self.engine.take_partial(p,110,data()))
        self.engine.close_position(108,'fixture')
        with self.engine.connect() as con:
            closed=json.loads(con.execute('SELECT payload FROM trades').fetchone()[0])
        expected=net_outcome(order['entry'],closed['exitPrice'],closed['finalExitQty'],
                            entry_fee=p['fee'],exit_fee_rate=desk.FEE)+closed['realizedPnl']
        self.assertAlmostEqual(closed['pnl'],expected)
        self.assertEqual(audit.health_snapshot(self.path)['outcomes'],4)
        self.engine.reconcile_attribution()
        self.assertEqual(audit.health_snapshot(self.path)['outcomes'],4)
        plans=experiments.list_experiments(db_path=self.registry)
        a6=[research_plans.evaluate_plan(e['id'],db_path=self.registry) for e in plans if e['plan_id']=='A6']
        self.assertTrue(all(e['samples']==1 and e['incrementalNet']==0 for e in a6))
        a5=next(e for e in plans if e['plan_id']=='A5')
        self.assertEqual(research_plans.evaluate_plan(a5['id'],db_path=self.registry)['samples'],0)
        self.assertTrue(experiments.verify_ledger(self.registry))

    def test_news_must_be_published_and_analyzed_before_capture(self):
        now=time.time()
        snap=dict(data=dict(analyzedAt=now-1,items=[dict(relevant=True,is_noise=False,publishedAt=now+100)]))
        with patch.object(desk.gemini_agent,'snapshot',return_value=snap):
            cid=self.capture()
        self.assertFalse(audit.candidate_receipt(cid,self.path)['candidate']['inputs']['news']['available'])
        snap['data']['items'][0]['publishedAt']=now-2
        with patch.object(desk.gemini_agent,'snapshot',return_value=snap):
            cid=self.capture(101)
        receipt=audit.candidate_receipt(cid,self.path)['candidate']
        self.assertTrue(any(p['planId']=='A7' for p in receipt['pairedReceipts']))
        self.assertTrue(all(p['prediction']['availableAt']<=receipt['observedAt'] for p in receipt['pairedReceipts']))

    def test_a5_links_real_queue_fills_to_the_frozen_taker_alternative(self):
        now=time.time()
        clock=self.stack.enter_context(patch('time.time',return_value=now))
        stream=BookStream();stream.connected=True;stream.watch(['SOLUSDT'])
        stream.books['SOLUSDT']=dict(symbol='SOLUSDT',bidPrice='99.99',askPrice='100',
            bidQty='.01',askQty='10000',receivedAt=now,monotonicAt=time.monotonic())
        self.stack.enter_context(patch('app.desk_maker.BOOKS',stream))
        self.stack.enter_context(patch.object(desk,'BOOKS',stream))
        cid=self.capture()
        order=self.order
        p=dict(order,symbol='SOLUSDT',candidateId=cid,observedArm='rules_local',entryKey='maker-fixture',
               entryTime=now,initialRisk=order['risk'],best=order['entry'],initialDist=4,
               atr=2,status='open',marketFilters=data()['metadata']['SOLUSDT']['filters'])
        self.assertTrue(self.engine.maker.post(p,'buy',p['qty']))
        self.engine.maker.reconcile(data())
        self.assertFalse(self.engine.state['positions'])  # a quote cannot fill a maker
        clock.return_value=now+.1
        stream.ingest(dict(e='trade',s='SOLUSDT',t=1,T=(now+.1)*1000,p='99.99',q='100',m=True))
        self.engine.maker.reconcile(data())
        self.engine.close_position(105,'fixture','SOLUSDT')
        a5=next(e for e in experiments.list_experiments(db_path=self.registry) if e['plan_id']=='A5')
        result=research_plans.evaluate_plan(a5['id'],db_path=self.registry)
        self.assertEqual(result['samples'],1)
        self.assertGreater(result['incrementalNet'],0)
        pair=next(e for e in experiments.observations(a5['id'],time.time(),db_path=self.registry) if e['kind']=='paired-execution')
        self.assertFalse(pair['payload']['executionEquivalent'])

    def test_reduced_shadow_arm_reprices_net_cashflow_and_costs_at_frozen_size(self):
        with patch.object(desk.ai_swarm,'fresh_pulse',return_value=dict(available=True,convictionMultiplier=.5)):
            cid=self.capture()
        order=self.order
        exit_price=105*(1-desk.SLIPPAGE)
        closed=dict(order,candidateId=cid,observedArm='rules_local',entryKey='scaled',status='closed',
            attributionPending=True,exitTime=time.time(),exitFee=order['qty']*exit_price*desk.FEE,
            initialRisk=order['risk'],pnl=net_outcome(order['entry'],exit_price,order['qty'],
                entry_fee=order['fee'],exit_fee_rate=desk.FEE))
        with self.engine.connect() as con:
            con.execute('INSERT INTO trades(entry_key,payload) VALUES (?,?)',('scaled',json.dumps(closed)))
        self.engine.record_closed_outcome(closed)
        with audit._connect(db_path=self.path) as con:
            outcomes={arm:json.loads(payload) for arm,payload in con.execute('SELECT arm,payload FROM audit_outcomes')}
        self.assertEqual(set(outcomes),{'rules_local','rules_plus_pulse'})
        base=outcomes['rules_local'];overlay=outcomes['rules_plus_pulse']
        self.assertAlmostEqual(overlay['netPnl'],base['netPnl']/2)
        self.assertAlmostEqual(overlay['fees'],base['fees']/2)
        self.assertEqual(overlay['outcomeType'],'counterfactual-observed')
