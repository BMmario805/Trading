import unittest
from app.economics import execution_fill,net_outcome,planned_trade


class EconomicsTests(unittest.TestCase):
    def test_round_trip_loses_costs_with_no_price_move_for_both_directions(self):
        for side in (1,-1):
            entry=execution_fill(100,side,True,.0005,.0002)
            exit_price=execution_fill(100,side,False,.0005,.0002)
            self.assertLess(net_outcome(entry,exit_price,1,side,entry*.001,.001),0)

    def test_small_targets_can_be_losing_despite_positive_price_change(self):
        plan=planned_trade(100,99.9,100.01,1)
        self.assertFalse(plan['viable'])
        self.assertLess(plan['netTarget'],0)

    def test_conversion_happens_once_for_all_costs(self):
        self.assertAlmostEqual(net_outcome(100,110,2,1,.2,.001,.5,.4),9.59)

    def test_malformed_prices_or_costs_cannot_create_profit(self):
        for price in (float('nan'),float('inf'),0,-1):
            with self.assertRaises(ValueError): execution_fill(price,1,True)
