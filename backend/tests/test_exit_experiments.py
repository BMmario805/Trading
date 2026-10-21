import unittest

from app import exit_experiments as experiment


INFO=dict(filters=[dict(filterType='LOT_SIZE',stepSize='.0001',minQty='.0001'),dict(filterType='MIN_NOTIONAL',minNotional='5')])


def rows():
    return [dict(time=i*3600,open=o,high=h,low=l,close=c,volume=10)
            for i,(o,h,l,c) in enumerate(((100,101,99,100),(100,104.1,99.8,104),
                                         (104,105,103,104.5),(100.2,100.4,99,100.2)))]


def analyses():
    return {1:dict(trend=True,pullback=True,rsi=55,ema20=100,atr=1,close=100,ema50=99)}


class ExitExperimentTests(unittest.TestCase):
    def test_partial_closes_are_one_trade_with_all_fees_and_reconciled_cash(self):
        result=experiment.replay(rows(),INFO,3600,experiment.POLICIES[2],analyses=analyses())
        self.assertEqual(result['partials'],1)
        self.assertEqual(result['trades'],1)
        self.assertGreater(result['fees'],0)
        self.assertGreater(result['pnl'],0)
        self.assertFalse(result['promotionApplied'])

    def test_intrabar_peak_cannot_execute_partial_before_confirmed_close(self):
        bars=rows()
        bars[1]['close']=100.5
        bars[2].update(open=100.5,close=100.5,low=100.3)
        result=experiment.replay(bars,INFO,3600,experiment.POLICIES[2],analyses=analyses())
        self.assertEqual(result['partials'],0)

    def test_ambiguous_bar_hits_stop_before_partial_or_target(self):
        bars=rows()
        bars[1]['low']=95
        result=experiment.replay(bars,INFO,3600,experiment.POLICIES[2],analyses=analyses())
        self.assertEqual(result['partials'],0)
        self.assertEqual(result['trades'],1)
        self.assertLess(result['pnl'],0)

    def test_partial_respects_minimum_on_both_resulting_lots(self):
        self.assertEqual(experiment.partial_quantity(.06,100,INFO),0)
        self.assertGreater(experiment.partial_quantity(.2,100,INFO),0)

    def test_no_position_or_fee_is_invented_without_a_signal(self):
        result=experiment.replay(rows(),INFO,3600,experiment.POLICIES[2],analyses={})
        self.assertEqual((result['trades'],result['pnl'],result['fees']),(0,0,0))
