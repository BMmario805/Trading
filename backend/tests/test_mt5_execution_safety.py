"""Execution regressions use inert API doubles and temporary ledgers only."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from app import mt5_demo, mt5_performance, portfolio_risk, broker_replay, bots
from app.mt5_gateway import DemoGateway, DemoOnlyError
from tests.test_mt5_demo import setup_api


class ExecutionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for module in (mt5_demo, mt5_performance):
            p = patch.object(module, 'DB_PATH', Path(self.tmp.name) / 'ledger.sqlite')
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(portfolio_risk._reservations, {}, clear=True)
        p.start()
        self.addCleanup(p.stop)
        p = patch.dict(mt5_demo.STATE, {}, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.api, self.cfg, self.account, _ = setup_api()
        self.cfg['symbols'] = {'XAUUSD': 'XAUUSD.a'}
        self.engine = mt5_demo.DemoEngine(self.cfg, self.api)
        self.addCleanup(self.engine.worker.shutdown)

    def response(self, code, **fields):
        return NS(retcode=code, order=0, deal=0, comment='fake', **fields)

    def test_definitively_rejected_close_can_retry_but_uncertain_close_cannot(self):
        request = dict(position=42, volume=.01, symbol='XAUUSD.a')
        self.api.order_send.side_effect = [self.response(10004), None]
        self.engine.submit('close:42:0.01', 'XAUUSD.a', request)
        self.engine.submit('close:42:0.01', 'XAUUSD.a', request)
        self.engine.submit('close:42:0.01', 'XAUUSD.a', request)
        self.assertEqual(self.api.order_send.call_count, 2)
        self.assertTrue(self.engine.uncertain('XAUUSD.a'))

    def test_reservation_payload_survives_restart_before_any_new_assessment(self):
        accounts = [dict(source='mt5', bot='shared', equityEUR=1000, initialEUR=1000)]
        with patch.object(portfolio_risk, '_collect', return_value=(accounts, [])):
            proposal = portfolio_risk.assess('XAUUSD', 'long', 10, 100, 'mt5', 'swing', True)
            request = dict(symbol='XAUUSD.a', magic=123, comment='cx-swing-1',
                           type=0, volume=.01, price=100, sl=90)
            self.api.order_send.return_value = None
            self.engine.submit('entry:swing:XAUUSD.a:1', 'XAUUSD.a', request, proposal['token'])
            with mt5_demo.connection() as con:
                detail = json.loads(con.execute('SELECT detail FROM intents').fetchone()[0])
            self.assertEqual(detail['portfolioReservation']['riskEUR'], 10)
            portfolio_risk._reservations.clear()
            restarted = mt5_demo.DemoEngine(self.cfg, self.api)
            self.addCleanup(restarted.worker.shutdown)
            restarted.restore_portfolio_reservations()
            self.assertEqual(portfolio_risk.public_snapshot()['openRiskEUR'], 10)
            self.assertFalse(portfolio_risk.assess('PAXG', 'long', 6, 10, 'lab', 'one')['allowed'])

    def test_existing_positions_consume_remaining_contract_commission(self):
        self.account.equity = self.account.margin_free = 1000
        self.api.symbol_info.return_value.volume_min = .001
        self.api.symbol_info.return_value.volume_step = .001
        self.cfg.update(risk_pct=.01, max_open_risk_pct=.02, max_positions=5,
                        replay_costs={s: dict(roundTripCommissionPerLot=400) for s in 'ABC'})
        positions = []
        self.api.positions_get.side_effect = lambda: tuple(positions)
        self.api.order_calc_margin.return_value = 1
        gateway = DemoGateway(self.api, self.cfg)
        total = 0
        from app.mt5_gateway import DemoOnlyError
        for symbol in 'ABC':
            try:
                request, _ = gateway.prepare_entry(symbol, 1, 2, 2, 3, 123, 'fake', 1000)
            except DemoOnlyError:
                break
            loss = abs(self.api.order_calc_profit(0, symbol, request['volume'], request['price'], request['sl']))
            total += loss * 1.1 + request['volume'] * 400
            positions.append(NS(ticket=len(positions)+1, symbol=symbol, sl=request['sl'],
                                type=0, volume=request['volume'], price_open=request['price']))
        self.assertLessEqual(total, 20 + 1e-8)

    def test_accepted_close_and_rejected_entry_are_never_replayed(self):
        for key,request,code in (
            ('close:42:0.01',dict(position=42,volume=.01),self.api.TRADE_RETCODE_DONE),
            ('entry:42',dict(volume=.01),10004),
        ):
            self.api.order_send.return_value=self.response(code)
            self.engine.submit(key,'XAUUSD.a',request)
            self.engine.submit(key,'XAUUSD.a',request)
        self.assertEqual(self.api.order_send.call_count,2)

    def test_transport_failure_keeps_durable_intent_and_reserve(self):
        portfolio_risk.restore('risk',dict(source='mt5',bot='swing',symbol='GOLD',factor='gold',
                                         direction='long',riskEUR=10,notionalEUR=100,at=1000))
        self.api.order_send.side_effect=TimeoutError('inert transport double')
        self.assertIsNone(self.engine.submit('entry:42','XAUUSD.a',dict(volume=.01),'risk'))
        self.engine.submit('entry:42','XAUUSD.a',dict(volume=.01),'risk')
        self.api.order_send.assert_called_once()
        self.assertEqual(portfolio_risk.reservation('risk')['riskEUR'],10)
        with mt5_demo.connection() as con:
            status,raw=con.execute('SELECT status,detail FROM intents').fetchone()
        self.assertEqual(status,'uncertain')
        self.assertEqual(json.loads(raw)['transportError'],'TimeoutError')

    def test_missing_reserve_or_failed_durable_write_never_sends(self):
        with self.assertRaises(DemoOnlyError):
            self.engine.submit('entry:42','XAUUSD.a',{},'missing-token')
        accounts=[dict(source='mt5',bot='shared',equityEUR=1000,initialEUR=1000)]
        with patch.object(portfolio_risk,'_collect',return_value=(accounts,[])):
            proposal=portfolio_risk.assess('XAUUSD','long',10,100,'mt5','swing',True)
        token=proposal['token']
        with patch.object(mt5_demo,'connection',side_effect=OSError('inert disk failure')):
            with self.assertRaises(OSError): self.engine.submit('entry:42','XAUUSD.a',{},token)
        self.assertIsNone(portfolio_risk.reservation(token))
        self.api.order_send.assert_not_called()

    def test_post_send_persistence_failure_keeps_pending_intent_and_reserve(self):
        payload=dict(source='mt5',bot='swing',symbol='GOLD',factor='gold',direction='long',
                     riskEUR=10,notionalEUR=100,at=1000)
        portfolio_risk.restore('post-send',payload)
        connection=mt5_demo.connection
        count=0
        def fail_after_send():
            nonlocal count
            count+=1
            if count>1: raise OSError('inert post-send disk failure')
            return connection()
        self.api.order_send.return_value=self.response(self.api.TRADE_RETCODE_DONE)
        with patch.object(mt5_demo,'connection',side_effect=fail_after_send):
            with self.assertRaises(OSError):
                self.engine.submit('entry:42','XAUUSD.a',dict(volume=.01),'post-send')
        self.api.order_send.assert_called_once()
        self.assertEqual(portfolio_risk.reservation('post-send')['riskEUR'],10)
        with patch.object(mt5_demo,'connection',side_effect=OSError('inert retry while disk offline')):
            with self.assertRaises(OSError):
                self.engine.submit('entry:42','XAUUSD.a',dict(volume=.01),'post-send')
        self.assertEqual(portfolio_risk.reservation('post-send')['riskEUR'],10)
        with connection() as con:
            self.assertEqual(con.execute('SELECT status FROM intents').fetchone()[0],'pending')
        portfolio_risk._reservations.clear()
        self.assertTrue(self.engine.restore_portfolio_reservations())
        self.assertEqual(portfolio_risk.reservation('post-send')['riskEUR'],10)

    def test_execution_metrics_use_actual_partial_fill_not_requested_volume(self):
        self.api.order_send.return_value=self.response(self.api.TRADE_RETCODE_DONE_PARTIAL,price=101,volume=.004)
        self.engine.submit('entry:42','XAUUSD.a',dict(type=0,price=100,volume=.01))
        metrics=self.engine.execution_telemetry()['recent'][0]
        self.assertGreaterEqual(metrics['latencySeconds'],0)
        self.assertAlmostEqual(metrics['execution']['fillRatio'],.4)
        self.assertEqual(metrics['execution']['adversePriceDifference'],1)
        self.assertTrue(metrics['execution']['executionVerified'])

    def test_legacy_unknown_reserve_blocks_entries_and_preserves_management(self):
        self.account.currency='EUR'
        self.account.balance=100000
        self.cfg.update(terminal='fake',enabled=True,max_daily_loss_pct=.02)
        detail=dict(portfolioToken='legacy',request={},submittedAt=1000)
        with mt5_demo.connection() as con:
            con.execute('INSERT INTO intents VALUES (?,?,?,?)',(self.engine.owner+'entry:old','XAUUSD.a','uncertain',json.dumps(detail)))
        with patch.object(self.engine,'update_clock_skew'),patch.object(self.engine,'sync_performance'), \
             patch.object(self.engine,'training'),patch.object(self.engine,'manage') as manage, \
             patch.object(self.engine.gateway,'prepare_entry') as prepare:
            self.engine.cycle()
        manage.assert_called_once()
        prepare.assert_not_called()
        self.assertFalse(mt5_demo.STATE['portfolioRecoveryReady'])
        self.assertFalse(mt5_demo.STATE['tradingAllowed'])

    def test_legacy_exact_request_can_recover_contract_and_canonical_symbol(self):
        self.account.currency='EUR'
        request=dict(type=0,volume=.01,price=100,sl=96,magic=mt5_demo.MAGICS['swing'])
        detail=dict(portfolioToken='legacy',request=request,submittedAt=1000)
        with mt5_demo.connection() as con:
            con.execute('INSERT INTO intents VALUES (?,?,?,?)',(self.engine.owner+'entry:old','XAUUSD.a','uncertain',json.dumps(detail)))
        with patch.object(self.engine.gateway,'notional_eur',return_value=100):
            self.assertTrue(self.engine.restore_portfolio_reservations())
        restored=portfolio_risk.reservation('legacy')
        self.assertEqual(restored['symbol'],'GOLD')
        self.assertEqual(restored['factor'],'gold')
        self.assertEqual(restored['riskEUR'],4.4)

    def test_paid_entry_commission_is_allocated_to_remaining_lots_after_partial_close(self):
        self.cfg['replay_costs']={'XAUUSD.a':dict(roundTripCommissionPerLot=100)}
        with mt5_performance.connection() as con:
            con.execute('INSERT INTO mt5_deals(owner,ticket,position_id,kind,entry,volume,commission) '
                        'VALUES (?,?,?,?,?,?,?)',(self.engine.owner,1,42,0,0,1,-50))
        self.engine.refresh_paid_commissions()
        position=NS(ticket=42,identifier=42,type=0,symbol='XAUUSD.a',volume=.5,price_open=100,sl=96)
        risk=self.engine.gateway.position_risk(position)
        self.assertEqual(risk['commissionPaid'],25)
        self.assertEqual(risk['commissionPending'],25)
        self.assertEqual(risk['risk'],245)

    def test_stop_ack_requires_broker_reread_confirmation(self):
        self.api.POSITION_TYPE_BUY=0
        self.api.TRADE_ACTION_SLTP=6
        self.api.symbol_info.return_value.trade_freeze_level=0
        position=NS(ticket=42,symbol='XAUUSD.a',magic=mt5_demo.MAGICS['scalper'],type=0,
                    volume=.01,price_open=100,sl=96,tp=130,time=990)
        mt5_demo.save(self.engine.owner+'position:42',dict(risk=4,best=110,atr=2,trail=1,maxHoldSeconds=10000))
        self.api.symbol_info_tick.return_value=NS(time=1000,bid=110,ask=110.02)
        self.api.order_send.return_value=self.response(self.api.TRADE_RETCODE_DONE)
        self.api.positions_get.return_value=[position]
        self.engine.manage([position],1000)
        ack=mt5_demo.read(self.engine.owner+'position:42')['lastStopUpdate']
        self.assertTrue(ack['acknowledged'])
        self.assertFalse(ack['confirmed'])
        position.sl=108.5
        self.engine.manage([position],1000)
        self.assertEqual(self.api.order_send.call_count,1)
        self.assertTrue(self.engine.stop_diagnostics['42']['confirmed'])

    def test_rejected_stop_is_visible_and_can_be_retried(self):
        self.api.POSITION_TYPE_BUY=0
        self.api.TRADE_ACTION_SLTP=6
        self.api.symbol_info.return_value.trade_freeze_level=0
        position=NS(ticket=42,symbol='XAUUSD.a',magic=mt5_demo.MAGICS['scalper'],type=0,
                    volume=.01,price_open=100,sl=96,tp=130,time=990)
        mt5_demo.save(self.engine.owner+'position:42',dict(risk=4,best=110,atr=2,trail=1,maxHoldSeconds=10000))
        self.api.symbol_info_tick.return_value=NS(time=1000,bid=110,ask=110.02)
        self.api.order_send.return_value=self.response(10016)
        self.engine.manage([position],1000)
        self.engine.manage([position],1000)
        self.assertEqual(self.api.order_send.call_count,2)
        self.assertFalse(self.engine.stop_diagnostics['42']['acknowledged'])
        self.assertFalse(self.engine.stop_diagnostics['42']['confirmed'])

    def test_offline_terminal_does_not_erase_durable_unresolved_exposure(self):
        payload=dict(source='mt5',bot='swing',symbol='GOLD',factor='gold',direction='long',
                     riskEUR=10,notionalEUR=100,at=1000)
        with mt5_demo.connection() as con:
            con.execute('INSERT INTO intents VALUES (?,?,?,?)',(self.engine.owner+'entry:old','XAUUSD.a',
                        'uncertain',json.dumps(dict(portfolioToken='durable',portfolioReservation=payload))))
        self.cfg['terminal']='fake'
        self.api.initialize.return_value=False
        with self.assertRaises(DemoOnlyError): self.engine.cycle()
        self.assertEqual(portfolio_risk.reservation('durable')['riskEUR'],10)
        self.api.order_send.assert_not_called()

    def test_position_exposure_uses_fresh_mark_and_stale_tick_is_unknown(self):
        self.account.currency='EUR'
        self.account.balance=100000
        self.cfg.update(terminal='fake',enabled=False,max_daily_loss_pct=.02)
        self.api.POSITION_TYPE_BUY=0
        self.api.positions_get.return_value=[NS(ticket=42,symbol='XAUUSD.a',magic=mt5_demo.MAGICS['swing'],
            type=0,volume=.01,price_open=100,price_current=201,sl=96,tp=300,profit=101,time=900)]
        self.api.symbol_info_tick.return_value=NS(time=1000,bid=200,ask=202)
        with patch.object(mt5_demo.time,'time',return_value=1000), \
             patch.object(self.engine,'update_clock_skew'),patch.object(self.engine,'sync_performance'), \
             patch.object(self.engine,'training'),patch.object(self.engine,'manage'), \
             patch.object(self.engine.gateway,'notional_eur',return_value=201) as nominal:
            self.engine.cycle()
            self.assertEqual(nominal.call_args.args[2],201)
            self.assertEqual(mt5_demo.STATE['positions'][0]['canonicalSymbol'],'XAUUSD')
            self.assertEqual(mt5_demo.STATE['positions'][0]['notionalEUR'],201)
            nominal.reset_mock()
            self.api.symbol_info_tick.return_value.time=1
            self.engine.cycle()
            nominal.assert_not_called()
            self.assertIsNone(mt5_demo.STATE['positions'][0]['notionalEUR'])
            self.assertFalse(mt5_demo.STATE['positions'][0]['exposureKnown'])

    def test_gateway_and_replay_share_known_commission_risk_and_lot_size(self):
        self.account.equity=self.account.margin_free=1000
        self.cfg.update(risk_pct=.01,max_open_risk_pct=.02,replay_costs={'TEST':dict(roundTripCommissionPerLot=400)})
        c=broker_replay.BrokerContract('TEST','EUR',.01,.01,2,10,0,.01,.01,100,100,1,.02,
            commission_roundtrip_per_lot=400,carry_per_lot_day=0,historical_verified=True)
        request,_=self.engine.gateway.prepare_entry('TEST',1,2,2,3,1,'fake',1000)
        plan=broker_replay.entry_plan(c,1,100.02,2,2,3,1000,.01,.02)
        self.assertTrue(plan['executable'])
        self.assertEqual(plan['lots'],request['volume'])
        self.assertEqual(plan['minimumLotRisk'],8.4)

    def test_profitable_ohlc_history_is_never_promoted_without_equivalence_and_future_evidence(self):
        c=broker_replay.BrokerContract('TEST','EUR',.01,.01,2,0,0,.01,.01,10,100,1,.02,
            commission_roundtrip_per_lot=.2,carry_per_lot_day=0,historical_verified=True)
        rows=[dict(time=i*60,open=100,high=101,low=99,close=100,volume=1) for i in range(500)]
        with patch.object(broker_replay,'simulate',side_effect=lambda *a,**k:dict(trades=10,pnl=10,blocked={},executions=[])):
            result=broker_replay.optimize(replace(bots.SCALPER,max_hold_seconds=120),rows,[0]*500,[2]*500,c,initial=1000,
                risk_pct=.01,max_open_risk_pct=.02,max_daily_loss_pct=.02,engine_version=999)
        self.assertTrue(result['historicalPassed'])
        self.assertFalse(result['applied'])
        self.assertFalse(result['executionEquivalent'])
        self.assertFalse(result['promotion']['promotable'])

    def test_mt5_holdout_reuse_is_recorded_across_replay_experiments(self):
        from app import experiments
        c=broker_replay.BrokerContract('TEST','EUR',.01,.01,2,0,0,.01,.01,10,100,1,.02,
            commission_roundtrip_per_lot=.2,carry_per_lot_day=0,historical_verified=True)
        rows=[dict(time=i*60,open=100,high=101,low=99,close=100,volume=1) for i in range(500)]
        path=Path(self.tmp.name)/'research.sqlite'
        source=dict(provider='mt5',account='test-only')
        results=[]
        for i in range(2):
            registered=experiments.register_experiment('mt5-test-'+str(i),dict(parameters=dict(variant=i),
                costModel=dict(version='test'),trialCount=12),db_path=path)
            result=broker_replay.optimize(replace(bots.SCALPER,max_hold_seconds=120),rows,[0]*500,[2]*500,c,initial=1000,
                risk_pct=.01,max_open_risk_pct=.02,max_daily_loss_pct=.02,engine_version=999,
                ledger_context=dict(experimentId=registered['id'],symbol='TEST',source=source,dbPath=path))
            results.append(result)
        self.assertFalse(results[0]['holdoutClaim']['reused'])
        self.assertTrue(results[1]['holdoutClaim']['reused'])
        self.assertFalse(any(r['applied'] for r in results))


if __name__ == '__main__':
    unittest.main()
