import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from app import broker_replay as replay, bots, mt5_demo, mt5_performance as perf
from app.mt5_gateway import DemoGateway, DemoOnlyError
from tests.test_mt5_demo import setup_api
from tests.test_mt5_performance import deal


class SyncIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p=patch.object(perf,'DB_PATH',Path(self.tmp.name)/'test.sqlite')
        p.start()
        self.addCleanup(p.stop)
        self.account=NS(balance=100000,equity=100000,credit=0,currency='EUR')
        self.settings=dict(initial=100,currency='EUR',startedAt=1000,cutoffMsc=11800000)
        with perf.connection() as con:
            con.execute('INSERT INTO mt5_sessions VALUES (?,?)',('owner',json.dumps(self.settings)))

    def test_raw_cutoff_and_utc_presentation_are_independent(self):
        rows=[deal(1,11799,profit=7),deal(2,11810,profit=3,commission=-1)]
        perf.ingest('owner',self.account,[],rows,1010,True,clock_offset=10800)
        result=perf.performance('owner',[])
        self.assertEqual(result['summary']['pnl'],2)
        self.assertEqual(result['points'][1]['ts'],1010)
        self.assertEqual(perf.capital('owner',self.account,[])['equity'],102)
        with perf.connection() as con:
            row=con.execute('SELECT ts,raw_ts,offset_secs FROM mt5_deals WHERE owner=?',('owner',)).fetchone()
        self.assertEqual(row,(1010000,11810000,10800))
        # A later DST offset must not shift already normalized executions.
        perf.ingest('owner',self.account,[],rows,1020,True,clock_offset=7200)
        with perf.connection() as con:
            self.assertEqual(con.execute('SELECT ts FROM mt5_deals WHERE owner=?',('owner',)).fetchone()[0],1010000)

    def test_boot_sync_does_not_lock_an_unconfirmed_zero_offset(self):
        rows=[deal(2,11810,3,commission=-1)]
        perf.ingest('owner',self.account,[],rows,1010,True,clock_offset=None)
        with perf.connection() as con:
            self.assertEqual(con.execute('SELECT offset_assigned FROM mt5_deals').fetchone()[0],0)
        perf.ingest('owner',self.account,[],rows,1020,True,clock_offset=10800)
        with perf.connection() as con:
            self.assertEqual(con.execute('SELECT ts,raw_ts,offset_assigned FROM mt5_deals').fetchone(),(1010000,11810000,1))

    def test_sync_requires_full_balance_reconciliation_and_freshness(self):
        funding=deal(1,100,100000,type=2,position_id=0)
        perf.ingest('owner',self.account,[],[funding],1010,True)
        self.assertTrue(perf.capital('owner',self.account,[],now=1011)['entryReady'])
        self.assertFalse(perf.capital('owner',self.account,[],now=1041)['entryReady'])
        self.account.balance+=5
        self.assertFalse(perf.capital('owner',self.account,[],now=1011)['entryReady'])
        perf.ingest('owner',self.account,[],[funding],1015)
        self.assertFalse(perf.capital('owner',self.account,[],now=1016)['entryReady'])
        perf.ingest('owner',self.account,[],[funding,deal(2,11820,5)],1020)
        self.assertTrue(perf.capital('owner',self.account,[],now=1021)['entryReady'])
        perf.sync_error('owner','history timeout')
        self.assertFalse(perf.capital('owner',self.account,[],now=1022)['entryReady'])

    def test_position_change_invalidates_spendable_virtual_snapshot(self):
        funding=deal(1,100,100000,type=2,position_id=0)
        perf.ingest('owner',self.account,[],[funding],1010,True)
        position=NS(ticket=1,volume=.01,profit=0,swap=0)
        result=perf.capital('owner',self.account,[position],now=1011)
        self.assertFalse(result['entryReady'])
        self.assertEqual(result['equity'],100)

    def test_existing_schema_migrates_without_changing_raw_reset_cutoff(self):
        old=Path(self.tmp.name)/'old.sqlite'
        with closing(sqlite3.connect(old)) as con, con:
            con.execute('CREATE TABLE mt5_deals (owner TEXT,ticket INTEGER,ts INTEGER,position_id INTEGER,magic INTEGER,kind INTEGER,entry INTEGER,volume REAL,symbol TEXT,profit REAL,commission REAL,swap REAL,fee REAL,PRIMARY KEY(owner,ticket))')
            con.execute('INSERT INTO mt5_deals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',('owner',2,11810000,7,42,0,0,1,'EURUSD',3,-1,0,0))
        with patch.object(perf,'DB_PATH',old):
            with perf.connection() as con:
                self.assertEqual(con.execute('SELECT raw_ts FROM mt5_deals').fetchone()[0],11810000)
            perf.ingest('owner',self.account,[],[deal(2,11810,3,commission=-1)],1010,True,clock_offset=10800)
            with perf.connection() as con:
                self.assertEqual(con.execute('SELECT ts,raw_ts FROM mt5_deals').fetchone(),(1010000,11810000))


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p=patch.object(mt5_demo,'DB_PATH',Path(self.tmp.name)/'test.sqlite')
        p.start()
        self.addCleanup(p.stop)
        p=patch.object(perf,'DB_PATH',Path(self.tmp.name)/'test.sqlite')
        p.start()
        self.addCleanup(p.stop)
        self.api,self.cfg,_,_=setup_api()
        self.api.order_send.return_value=None
        self.api.history_deals_get.return_value=()
        self.engine=mt5_demo.DemoEngine(self.cfg,self.api)
        self.addCleanup(self.engine.worker.shutdown)
        self.request=dict(symbol='XAUUSD',magic=123,comment='cxp-scalper-1000',volume=.01,type=0)
        self.reservations=patch.dict(mt5_demo.portfolio_risk._reservations,{},clear=True)
        self.reservations.start()
        self.addCleanup(self.reservations.stop)
        mt5_demo.portfolio_risk.restore('reserved-test',dict(source='mt5',bot='scalper',symbol='GOLD',
            factor='gold',direction='long',riskEUR=1,notionalEUR=10,at=1000))
        with patch.object(mt5_demo.time,'time',return_value=1000):
            self.engine.submit('entry:scalper:XAUUSD:1000','XAUUSD',self.request,portfolio_token='reserved-test')

    def test_matching_broker_position_resolves_without_sending_again(self):
        p=NS(ticket=4,symbol='XAUUSD',magic=123,comment='cxp-scalper-1000')
        self.engine.reconcile_intents([p],1010)
        self.assertFalse(self.engine.uncertain('XAUUSD'))
        self.engine.submit('entry:scalper:XAUUSD:1000','XAUUSD',self.request)
        self.api.order_send.assert_called_once()

    def test_empty_history_and_missing_position_never_prove_rejection(self):
        self.engine.reconcile_intents([],1010)
        self.assertTrue(self.engine.uncertain('XAUUSD'))
        self.api.order_send.assert_called_once()

    def test_wrong_identity_and_old_deal_do_not_clear_block(self):
        self.api.history_deals_get.return_value=[deal(5,1005,symbol='EURUSD',magic=123,comment='cxp-scalper-1000')]
        self.engine.reconcile_intents([],1010)
        self.assertTrue(self.engine.uncertain('XAUUSD'))

    def test_deal_with_exact_request_identity_is_positive_evidence(self):
        self.api.history_deals_get.return_value=[deal(5,1005,symbol='XAUUSD',magic=123,comment='cxp-scalper-1000')]
        self.engine.reconcile_intents([],1010)
        self.assertFalse(self.engine.uncertain('XAUUSD'))

    def test_definitive_order_cancellation_resolves_only_known_order_ticket(self):
        with mt5_demo.connection() as con:
            raw=json.loads(con.execute('SELECT detail FROM intents').fetchone()[0])
            raw['order']=77
            con.execute('UPDATE intents SET detail=?',(json.dumps(raw),))
        self.api.ORDER_STATE_CANCELED=2
        self.api.history_orders_get.return_value=[NS(ticket=77,state=2)]
        self.engine.reconcile_intents([],1010)
        self.assertFalse(self.engine.uncertain('XAUUSD'))

    def test_fast_roundtrip_releases_reservation_from_reconciled_closed_ledger(self):
        rows=[deal(10,100,100000,type=2,position_id=0),
              deal(11,1001,symbol='XAUUSD',magic=123,comment='cxp-scalper-1000'),
              deal(12,1002,2,type=1,entry=1,symbol='XAUUSD',magic=123,comment='cx-time-exit')]
        account=NS(balance=100002,equity=100002,credit=0,currency='EUR')
        perf.ingest(self.engine.owner,account,[],rows,1010,True)
        self.api.history_deals_get.return_value=rows[1:]
        self.engine.reconcile_intents([],1010)
        with patch.object(mt5_demo.portfolio_risk,'release') as release:
            self.engine.reconcile_portfolio_reservations([],1010)
        release.assert_called_once_with('reserved-test')

    def test_missing_or_partial_exit_is_not_proof_to_release_reservation(self):
        rows=[deal(10,100,100000,type=2,position_id=0),
              deal(11,1001,symbol='XAUUSD',magic=123,comment='cxp-scalper-1000'),
              deal(12,1002,2,type=1,entry=1,volume=.4,symbol='XAUUSD',magic=123,comment='cx-time-exit')]
        account=NS(balance=100002,equity=100002,credit=0,currency='EUR')
        perf.ingest(self.engine.owner,account,[],rows,1010,True)
        self.api.history_deals_get.return_value=rows[1:]
        self.engine.reconcile_intents([],1010)
        with patch.object(mt5_demo.portfolio_risk,'release') as release:
            self.engine.reconcile_portfolio_reservations([],1010)
        release.assert_not_called()

    def test_accepted_position_releases_only_after_publishing_position(self):
        p=NS(ticket=4,symbol='XAUUSD',magic=123,comment='cxp-scalper-1000')
        self.engine.reconcile_intents([p],1010)
        with patch.object(mt5_demo.portfolio_risk,'release') as release:
            self.engine.reconcile_portfolio_reservations([p],1010)
        release.assert_called_once_with('reserved-test')


class ContractReplayTests(unittest.TestCase):
    def setUp(self):
        self.contract=replay.BrokerContract('TEST','EUR',.01,.01,2,0,0,.01,.01,10,100,10,.02,
            commission_roundtrip_per_lot=.2,carry_per_lot_day=0,slippage=0,historical_verified=True)
        self.params=dict(entryThreshold=.5,slAtr=2,tpAtr=3,trailAtr=1)

    def test_lots_and_money_match_gateway_with_same_contract(self):
        api,cfg,account,_=setup_api()
        api.symbol_info.return_value.trade_stops_level=0
        cfg['risk_pct']=.01
        plan=replay.entry_plan(self.contract,1,100.02,2,2,3,1000,.01,.01)
        request,_=DemoGateway(api,cfg).prepare_entry('XAUUSD',1,2,2,3,1,'test',1000,virtual_equity=1000)
        self.assertEqual(plan['lots'],request['volume'])
        self.assertEqual(plan['stop'],request['sl'])
        self.assertEqual(plan['target'],request['tp'])
        self.assertLessEqual(plan['lots']*4*100*1.1,10)

    def test_minimum_lot_and_margin_cannot_be_bypassed(self):
        plan=replay.entry_plan(self.contract,1,100,2,2,3,100,.001,.02)
        self.assertFalse(plan['executable'])
        self.assertEqual(plan['reason'],'minimum-lot')
        self.assertGreater(plan['requiredCapital'],100)
        rich_margin=replace(self.contract,margin_per_lot=1000000)
        plan=replay.entry_plan(rich_margin,1,100,2,2,3,1000,.01,.02)
        self.assertFalse(plan['executable'])
        self.assertEqual(plan['reason'],'margin')

    def test_expensive_target_is_not_tradeable(self):
        expensive=replace(self.contract,commission_roundtrip_per_lot=10000)
        self.assertEqual(replay.entry_plan(expensive,1,100,2,2,3,100000,.01,.02)['reason'],'net-target')

    def candles(self):
        return [dict(time=i*60,open=100,high=100.1,low=99.9,close=100,volume=100) for i in range(9)]

    def run_replay(self,rows,scores,**kwargs):
        return replay.simulate(rows,scores,[2]*len(rows),self.contract,self.params,initial=10000,risk_pct=.01,
            max_open_risk_pct=.02,max_hold_seconds=120,**kwargs)

    def test_opposite_signal_is_not_a_live_mt5_exit_and_time_exit_matches(self):
        result=self.run_replay(self.candles(),[0,.8,-.9,-.9,0,0,0,0,0])
        self.assertEqual(result['trades'],1)
        self.assertEqual(result['executions'][0]['reason'],'time')
        self.assertEqual(result['executions'][0]['openedAt'],120)
        self.assertEqual(result['executions'][0]['closedAt'],240)
        self.assertLess(result['pnl'],0) # flat bid path still pays spread and commission

    def test_both_barriers_in_one_bar_always_choose_stop_first(self):
        rows=self.candles()
        rows[2].update(high=120,low=80)
        result=self.run_replay(rows,[0,.8,0,0,0,0,0,0,0])
        self.assertEqual(result['executions'][0]['reason'],'stop')
        self.assertLess(result['pnl'],0)

    def test_time_exit_never_uses_future_high_of_expired_bar(self):
        rows=self.candles()
        rows[4]['high']=120
        result=self.run_replay(rows,[0,.8,0,0,0,0,0,0,0])
        self.assertEqual(result['executions'][0]['reason'],'time')
        self.assertLess(result['pnl'],0)

    def test_short_pnl_closes_at_ask_and_costs_are_monetary(self):
        result=self.run_replay(self.candles(),[0,-.8,0,0,0,0,0,0,0])
        trade=result['executions'][0]
        self.assertAlmostEqual(trade['exit'],100.02)
        expected=(100-100.02)*trade['lots']*100-.2*trade['lots']
        self.assertAlmostEqual(result['pnl'],expected,places=5)

    def test_unknown_historical_costs_never_produce_applied_validation(self):
        rows=[dict(time=i*60,open=100,high=100.1,low=99.9,close=100,volume=100) for i in range(500)]
        unknown=replace(self.contract,commission_roundtrip_per_lot=None,historical_verified=False)
        result=replay.optimize(bots.SCALPER,rows,[0]*500,[2]*500,unknown,initial=100,risk_pct=.001,
            max_open_risk_pct=.02,max_daily_loss_pct=.02,engine_version=999)
        self.assertFalse(result['applied'])
        self.assertFalse(result['costsVerified'])
        self.assertEqual(result['brokerReplayVersion'],replay.VERSION)
        self.assertIn('informativo',result['reason'])

    def test_gateway_minimum_lot_error_exposes_required_account_currency_capital(self):
        api,cfg,account,_=setup_api()
        account.equity=100
        account.currency='EUR'
        with self.assertRaises(DemoOnlyError) as caught:
            DemoGateway(api,cfg).prepare_entry('TEST',1,2,2,3,1,'test',1000,virtual_equity=100)
        details=caught.exception.details
        self.assertEqual(details['currency'],'EUR')
        self.assertGreater(details['minimumLotRisk'],details['riskBudget'])
        self.assertGreater(details['requiredCapital'],100)


if __name__=='__main__':
    unittest.main()
