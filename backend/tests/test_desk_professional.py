import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import desk, opportunities,decision_audit
from app.spot_orders import valid_quantity
from test_desk import data, add_markets


class EntryCommitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine=desk.Desk(db_path=Path(self.tmp.name)/'desk.sqlite')
        self.engine.state['funding']=dict(currency='EUR',amount=1000,netQuotePerEuro=1,
                                         at=time.time(),source='TEST ONLY')
        self.engine.task=asyncio.current_task()
        self.engine.master_regime=dict(stressed=False,choppiness=40,adx=25)
        self.snapshot=dict(data(),at=time.time(),scope='execution-books')
        self.analysis=dict(bar=int(time.time())//3600*3600-3600,close=100,ema20=100,
            ema50=99,atr=2,rsi=55,trend=True,pullback=True,entryAllowed=True,rows=[],strategy='breakout')
        self.candidate=dict(symbol='SOLUSDT',eligible=True,rsEligible=True,reason='test')
        self.enterContext(patch.object(desk.gemini_agent,'AGENT',None))
        self.enterContext(patch.object(self.engine,'wire',AsyncMock(return_value=dict(ok=True,detail='fixture'))))
        self.enterContext(patch.object(self.engine,'market_books',AsyncMock(side_effect=lambda *_: dict(self.snapshot))))
        self.enterContext(patch.object(desk.portfolio_risk,'assess',return_value=dict(allowed=True,token=None)))

    async def review(self, mutation):
        async def derivatives(*_):
            await mutation()
            return {}
        with patch.object(opportunities,'derivatives',side_effect=derivatives):
            await self.engine.review_candidate(self.candidate,self.snapshot,
                prefetched={'SOLUSDT':[]},prepared=self.analysis)

    def assert_no_entry(self):
        self.assertFalse(self.engine.state['positions'])
        self.assertEqual(self.engine.state['cash'],1000)
        with self.engine.connect() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM trades').fetchone()[0],0)
        self.assertEqual(self.candidate['status'],'blocked')

    async def test_pause_during_derivatives_wait_prevents_entry(self):
        await self.review(lambda: self.engine.control('pause'))
        self.assert_no_entry()

    async def test_pause_then_resume_does_not_revive_old_proposal(self):
        async def mutate():
            await self.engine.control('pause')
            await self.engine.control('start')
        await self.review(mutate)
        self.assertTrue(self.engine.state['enabled'])
        self.assert_no_entry()

    async def test_daily_stop_or_flatten_during_wait_prevents_entry(self):
        async def mutate():
            self.engine.state.update(dailyStopped=True,flatten=True)
        await self.review(mutate)
        self.assert_no_entry()

    async def test_cash_change_during_wait_rechecks_size(self):
        async def mutate(): self.engine.state['cash']=0
        await self.review(mutate)
        self.assertFalse(self.engine.state['positions'])
        self.assertEqual(self.candidate['status'],'blocked')

    async def test_stale_final_book_blocks_entry(self):
        async def mutate(): self.snapshot['at']=time.time()-5
        await self.review(mutate)
        self.assert_no_entry()

    async def test_no_conversion_blocks_before_derivatives_or_reservation(self):
        self.engine.state.pop('funding')
        with patch.object(opportunities,'derivatives',AsyncMock()) as derivatives:
            await self.engine.review_candidate(self.candidate,self.snapshot,
                prefetched={'SOLUSDT':[]},prepared=self.analysis)
        derivatives.assert_not_awaited()
        self.assert_no_entry()

    async def test_unchanged_valid_proposal_still_commits_and_reconciles(self):
        async def unchanged(): pass
        await self.review(unchanged)
        p=self.engine.state['positions'][0]
        self.assertAlmostEqual(self.engine.state['cash']+p['notional']+p['fee'],1000)
        with self.engine.connect() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM trades').fetchone()[0],1)

    async def test_quorum_revoked_during_wait_cannot_open(self):
        allowed=True
        def clearance(*_,**__):
            return dict(binding=True,allowed=allowed,venue='spot',profile='conservative',
                sizeMultiplier=1,maxRiskEUR=20,maxNotionalEUR=500,reason='Revoked' if not allowed else 'Approved')
        async def revoke():
            nonlocal allowed
            allowed=False
        with patch.object(desk.ai_swarm,'execution_clearance',side_effect=clearance) as read:
            await self.review(revoke)
        self.assertGreaterEqual(read.call_count,2)
        self.assert_no_entry()

    async def test_observed_close_has_one_outcome_and_duplicate_retry_is_idempotent(self):
        async def unchanged():pass
        await self.review(unchanged)
        self.engine.close_position(103,'test')
        path=decision_audit.storage_for(self.engine.db_path)
        self.assertEqual(decision_audit.health_snapshot(path)['outcomes'],1)
        self.engine.reconcile_attribution()
        self.assertEqual(decision_audit.health_snapshot(path)['outcomes'],1)
        with self.engine.connect() as con:
            closed=json.loads(con.execute('SELECT payload FROM trades').fetchone()[0])
        self.assertFalse(closed['attributionPending'])

    async def test_broken_first_outcome_does_not_starve_later_valid_closures(self):
        async def unchanged():pass
        await self.review(unchanged)
        with patch.object(decision_audit,'record_outcome',side_effect=sqlite3.OperationalError('fixture down')):
            self.engine.close_position(103,'test')
        with self.engine.connect() as con:
            first=json.loads(con.execute('SELECT payload FROM trades').fetchone()[0])
            first['candidateId']='missing-candidate'
            first['attributionRetryAfter']=0
            con.execute('UPDATE trades SET payload=?',(json.dumps(first),))
            second=dict(first,entryKey='second-observed-closure',candidateId=self.candidate['candidateId'])
            con.execute('INSERT INTO trades(entry_key,payload) VALUES (?,?)',(second['entryKey'],json.dumps(second)))
        self.engine.reconcile_attribution()
        self.engine.reconcile_attribution()
        self.assertEqual(decision_audit.health_snapshot(decision_audit.storage_for(self.engine.db_path))['outcomes'],1)

    async def test_structure_exit_uses_newer_risk_quote_after_candle_wait(self):
        async def unchanged():pass
        await self.review(unchanged)
        async def candles(*_):
            self.engine.last_risk_data=data()
            self.engine.last_risk_data['at']=time.time()
            self.engine.last_risk_data['books']['SOLUSDT'].update(bidPrice='120',askPrice='120.01')
            self.engine.state['positions'][0].update(lastBid=120,quoteAt=self.engine.last_risk_data['at'])
            return []
        with patch.object(self.engine,'candidate_candles',side_effect=candles):
            await self.engine.review_candidate(self.candidate,self.snapshot,prefetched={'SOLUSDT':[]},
                prepared=dict(self.analysis,trend=False,close=90))
        with self.engine.connect() as con:
            closed=json.loads(con.execute('SELECT payload FROM trades').fetchone()[0])
        self.assertEqual(closed['status'],'closed')
        self.assertAlmostEqual(closed['exitPrice'],120*(1-self.engine.slippage))


class ConversionAndFilterTests(unittest.TestCase):
    def test_existing_book_registration_does_not_add_or_reset_money(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=desk.Desk(db_path=Path(folder)/'desk.sqlite')
            engine.state['cash']=752
            snapshot=add_markets(data(),['EUR'])
            snapshot['books']['EURUSDT'].update(bidPrice='1.1',askPrice='1.101')
            engine.refresh_conversion(snapshot)
            self.assertEqual(engine.state['cash'],752)
            self.assertTrue(engine.state['funding']['registeredExistingBook'])
            self.assertLess(engine.risk_conversion(),1.1)
            self.assertIsNone(engine.risk_conversion(now=time.time()+121))

    def test_stale_funding_is_not_a_current_conversion(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=desk.Desk(db_path=Path(folder)/'desk.sqlite')
            engine.state['funding']=dict(netQuotePerEuro=1.1,at=time.time()-1000)
            self.assertIsNone(engine.risk_conversion())

    def test_max_notional_applies_to_both_maker_and_taker(self):
        info=data()['metadata']['SOLUSDT']
        info['filters'].append(dict(filterType='NOTIONAL',minNotional='1',maxNotional='5',applyMaxToMarket=True))
        self.assertIsNone(desk.size_order(1000,1000,100,2,info))
        self.assertFalse(desk.valid_lot(1,100,info['filters']))

    def test_market_flags_and_market_lot_size_are_respected(self):
        filters=[dict(filterType='LOT_SIZE',stepSize='.01',minQty='.01'),
            dict(filterType='NOTIONAL',maxNotional='5',applyMaxToMarket=False),
            dict(filterType='MARKET_LOT_SIZE',stepSize='.1',minQty='.1',maxQty='2')]
        self.assertTrue(valid_quantity(1,100,filters,order_type='MARKET'))
        self.assertFalse(valid_quantity(1,100,filters,order_type='LIMIT'))
        self.assertFalse(valid_quantity(.11,100,filters,order_type='MARKET'))
        self.assertFalse(valid_quantity(3,100,filters,order_type='MARKET'))

    def test_nonfinite_and_malformed_filters_fail_closed(self):
        for value in ('NaN','Infinity','bad'):
            self.assertFalse(valid_quantity(1,100,[dict(filterType='LOT_SIZE',stepSize=value)]))
