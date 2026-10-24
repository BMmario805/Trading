import math
import unittest

from app.extreme_review import impulse_scores, replay


class ExtremeReplayTests(unittest.TestCase):
    def rows(self,n=10):
        return [dict(time=172800+i*3600,open=100.,close=100.,low=100.,high=100.,volume=100.) for i in range(n)]

    def run_case(self,rows,scores,**options):
        config=dict(interval='1h',slAtr=10.,tpAtr=10.,trailAtr=2.,maxHoldSeconds=86400,
            cooldownBars=1,threshold=.6,exitOnSignal=False,
            exitPolicy=dict(trailStartR=10.,tightenAtR=20.,tightenMultiple=1.),
            minAtrSpread=3.,overextAtr=3.,extensions=[0.]*len(rows))
        costs=options.get('costs') or dict(feeRate=0.,slippageRate=0.,spreadPct=0.,carryDailyRate=0.)
        return replay(rows,scores,[1.]*len(rows),start=1,config=config,costs=costs,
            risk_fraction=options.get('risk',.1),leverage=options.get('leverage',5.),
            daily_loss_fraction=options.get('daily',1.))

    def test_compounds_available_balance_instead_of_multiplying_old_pnl(self):
        rows=self.rows();scores=[0.,1.,0.,0.,1.,0.,0.,0.,0.,0.]
        rows[2]['high']=rows[5]['high']=110.
        result=self.run_case(rows,scores)
        self.assertEqual(result['trades'],2)
        self.assertAlmostEqual(result['finalEquity'],121.)

    def test_decision_executes_next_open_and_stop_precedes_target(self):
        rows=self.rows();scores=[0.,1.]+[0.]*8
        rows[1].update(high=200.,low=1.)
        rows[2].update(high=110.,low=90.)
        result=self.run_case(rows,scores)
        self.assertAlmostEqual(result['finalEquity'],90.)
        self.assertEqual(result['wins'],0)

    def test_gap_keeps_debt_and_does_not_restart_a_ruined_account(self):
        rows=self.rows();scores=[0.,1.,0.,1.,0.,1.,0.,1.,0.,1.]
        rows[3].update(open=50.,close=50.,low=49.,high=51.)
        result=self.run_case(rows,scores,risk=.9,leverage=50.)
        self.assertEqual(result['liquidations'],1)
        self.assertAlmostEqual(result['finalEquity'],-350.)
        self.assertTrue(result['capitalExhausted'])
        self.assertEqual(result['entries'],1)
        self.assertGreater(result['maxDrawdownPct'],100.)

    def test_daily_loss_closes_intrabar_and_blocks_remaining_day(self):
        rows=self.rows();scores=[0.,1.,0.,1.,0.,1.,0.,1.,0.,1.]
        rows[2].update(low=80.)
        result=self.run_case(rows,scores,risk=.2,daily=.1)
        self.assertAlmostEqual(result['finalEquity'],90.)
        self.assertEqual(result['dailyLossStops'],1)
        self.assertEqual(result['entries'],1)

    def test_leverage_caps_notional_instead_of_granting_free_purchasing_power(self):
        rows=self.rows();scores=[0.,1.]+[0.]*8
        rows[2]['high']=110.
        result=self.run_case(rows,scores,risk=.9,leverage=1.)
        self.assertAlmostEqual(result['finalEquity'],110.)
        self.assertLessEqual(result['maxUsedLeverage'],1.)

    def test_rejects_invalid_capital(self):
        with self.assertRaises(ValueError): self.run_case(self.rows(),[0.]*10,risk=math.nan)

    def test_daily_return_includes_overnight_gap_from_previous_close(self):
        rows=self.rows(73);scores=[0.]*73;scores[22]=1.
        rows[24].update(open=110.,high=110.,low=110.,close=110.)
        result=self.run_case(rows,scores)
        self.assertEqual(result['days'],2)
        self.assertAlmostEqual(result['daily'][0]['returnPct'],10.)

    def test_costs_reduce_results_and_are_reported(self):
        rows=self.rows();scores=[0.,1.]+[0.]*8
        rows[2]['high']=120.
        free=self.run_case(rows,scores)
        paid=self.run_case(rows,scores,costs=dict(feeRate=.001,slippageRate=.0002,spreadPct=.0005,carryDailyRate=.0003))
        self.assertLess(paid['finalEquity'],free['finalEquity'])
        self.assertGreater(paid['fees'],0.)
        self.assertGreater(paid['carry'],0.)

    def test_double_and_triple_days_are_counted_when_they_actually_occur(self):
        rows=self.rows(73);scores=[0.]*73
        scores[25]=scores[28]=1.
        rows[26]['high']=rows[29]['high']=110.
        result=self.run_case(rows,scores,risk=.9,leverage=50.)
        self.assertEqual(result['daysDouble'],1)
        self.assertEqual(result['daysTriple'],1)
        self.assertAlmostEqual(result['bestDayPct'],261.)

    def test_new_impulse_strategy_is_causal(self):
        rows=[]
        for i in range(150):
            price=100+i*.1+math.sin(i/4)
            rows.append(dict(time=i*300,open=price-.2,close=price,high=price+.3,low=price-.4,volume=100+i%7*20))
        full=impulse_scores(rows)
        for cut in (61,99,121): self.assertEqual(full[:cut],impulse_scores(rows[:cut]))


if __name__=='__main__': unittest.main()
