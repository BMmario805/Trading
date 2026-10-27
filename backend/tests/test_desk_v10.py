import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import desk, derivatives_flow, exit_experiments
from app.economics import net_outcome
from test_desk import data


class DeskV10Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.engine=desk.Desk(db_path=Path(self.tmp.name)/'desk.sqlite')
        self.snapshot=data()
        order=desk.size_order(1000,1000,100,2,self.snapshot['metadata']['SOLUSDT'])
        self.p=dict(order,symbol='SOLUSDT',entryKey='fixture',entryTime=time.time(),quoteAt=0,lastBid=100,
                    initialRisk=order['risk'],best=order['entry'],initialDist=4,atr=2,status='open',
                    marketFilters=self.snapshot['metadata']['SOLUSDT']['filters'])
        self.engine.state['positions']=[self.p];self.engine.focus_position()
        self.engine.state['cash']-=order['notional']+order['fee']

    def quote(self,bid):
        self.snapshot['at']=time.time()
        self.snapshot['books']['SOLUSDT'].update(bidPrice=str(bid),askPrice=str(bid+.01))
        return self.snapshot

    def test_partial_requires_1_8_net_r_sells_thirty_percent_and_sets_net_breakeven(self):
        p=self.p;original=p['qty']
        factor=(1-desk.FEE)*(1-desk.SLIPPAGE)
        threshold=(p['entry']*(1+desk.FEE)+1.8*p['initialRisk']/p['qty'])/factor
        self.engine.manage(self.quote(threshold-.00001))
        self.assertFalse(p.get('partialDone'))
        self.engine.manage(self.quote(threshold+.00001))
        self.assertTrue(p['partialDone'])
        step=.01
        self.assertLessEqual(abs(p['qty']-.7*original),step)
        self.assertAlmostEqual(net_outcome(p['entry'],p['sl']*(1-desk.SLIPPAGE),p['qty'],
            entry_fee=p['fee'],exit_fee_rate=desk.FEE),0,places=9)

    def test_dynamic_chandelier_uses_cached_regime_and_cvd_and_never_lowers_stop(self):
        self.engine.take_partial(self.p,110,self.quote(110))
        context=dict(bar=time.time()-14400,close=120,ema50=100,atr=2,bestHigh=120,adx=28)
        with patch.object(self.engine,'closed_exit_context',return_value=context), \
             patch.object(derivatives_flow,'peek',return_value=dict(regime=dict(cleanBreakout=True))) as read, \
             patch.object(desk.BOOKS,'taker_supports_bid',return_value=dict(cvdDelta=0)) as tape:
            self.engine.manage(self.quote(125))
            self.assertEqual(self.p['chandelierAtrMultiple'],3.0)
            self.assertAlmostEqual(self.p['sl'],114)
            tape.return_value=dict(cvdDelta=-.20001)
            self.engine.manage(self.quote(125))
            self.assertEqual(self.p['chandelierAtrMultiple'],1.8)
            self.assertAlmostEqual(self.p['sl'],116.4)
            tape.return_value=dict(cvdDelta=0)
            self.engine.manage(self.quote(125))
            self.assertAlmostEqual(self.p['sl'],116.4)
            self.assertEqual(read.call_count,3)

    def test_crowding_overrides_clean_breakout_and_legacy_geometry_is_frozen(self):
        legacy=desk.exit_policy(desk.LEGACY_POLICY_VERSION)
        self.assertEqual(legacy['partialAtR'],1.5)
        self.assertEqual(legacy['partialFraction'],1/3)
        self.assertEqual(legacy['residualStopR'],-.5)
        both=dict(crowdedLong=True,cleanBreakout=True)
        self.assertEqual(desk.chandelier_multiple(desk.exit_policy(desk.POLICY_VERSION),30,both),1.8)
        self.assertEqual(desk.chandelier_multiple(legacy,30,both,-1),2.8)
        self.assertEqual(desk.chandelier_multiple(desk.exit_policy(desk.POLICY_VERSION),27.999,dict(cleanBreakout=True)),2.8)
        policy=self.engine.public()['policy']
        self.assertEqual(policy['partialFraction'],.30)
        self.assertEqual(policy['partialAtR'],1.8)
        self.assertEqual(policy['residualStopR'],0)
        self.assertFalse(policy['automaticPromotion'])

    def test_short_squeeze_boundaries_and_direction(self):
        regime=derivatives_flow.derivatives_regime
        self.assertTrue(regime(-.00001,2,.01,'long')['shortSqueezeSetup'])
        self.assertEqual(regime(-.00001,2,.01,'long')['flowAdjustment'],.25)
        self.assertEqual(regime(-.00001,2,.01,'short')['flowAdjustment'],-.25)
        for funding,oi,price in [(0,2,1),(-.1,1.999,1),(-.1,2,0),(None,2,1),(-.1,float('nan'),1)]:
            self.assertFalse(regime(funding,oi,price,'long')['shortSqueezeSetup'])

    def test_replay_compares_versions_after_costs_without_automatic_promotion(self):
        bars=[dict(time=i*3600,open=100,high=101,low=99,close=100,volume=10) for i in range(4)]
        result=exit_experiments.compare_versions(bars,self.snapshot['metadata']['SOLUSDT'],analyses={})
        self.assertEqual(set(result['results']),{desk.LEGACY_POLICY_VERSION,desk.POLICY_VERSION})
        self.assertFalse(result['automaticPromotion'])
        self.assertEqual(result['trialCount'],4)
        self.assertTrue(all(v['normal']['fees']==0 for v in result['results'].values()))

    def test_versioned_replay_has_distinct_cashflows_and_ignores_intrabar_partial_peaks(self):
        prices=[(100,101,99,100),(100,107.2,99.8,107),
                (107,107.5,106.8,107.3),(107.3,109.2,107.1,109),
                (109,109.5,108.7,109.2),(100.1,100.4,99.8,100.2)]
        bars=[dict(time=i*3600,open=o,high=h,low=l,close=c,volume=10)
              for i,(o,h,l,c) in enumerate(prices)]
        analyses={1:dict(trend=True,pullback=True,rsi=55,ema20=100,atr=2,close=100,ema50=99)}
        result=exit_experiments.compare_versions(bars,self.snapshot['metadata']['SOLUSDT'],analyses=analyses)
        old=result['results'][desk.LEGACY_POLICY_VERSION]['normal']
        new=result['results'][desk.POLICY_VERSION]['normal']
        self.assertEqual((old['trades'],old['partials']),(1,1))
        self.assertEqual((new['trades'],new['partials']),(1,1))
        self.assertGreater(new['fees'],0)
        self.assertNotEqual(old['pnl'],new['pnl'])
        bars[1]['high']=9999
        changed=exit_experiments.compare_versions(bars,self.snapshot['metadata']['SOLUSDT'],analyses=analyses)
        self.assertEqual(new,changed['results'][desk.POLICY_VERSION]['normal'])
