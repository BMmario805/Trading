import unittest
from unittest.mock import patch
from app import portfolio_risk as risk


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.accounts=[dict(source='lab',bot='one',equityEUR=100,initialEUR=100)]
        self.rows=[]
        self.patch=patch.object(risk,'_collect',side_effect=lambda:(self.accounts,list(self.rows)))
        self.patch.start();self.addCleanup(self.patch.stop)
        self.addCleanup(risk._reservations.clear)
        risk._reservations.clear()

    def test_gold_proxies_share_factor_and_pending_orders_consume_budget(self):
        admitted=risk.assess('PAXG','long',1,40,'lab','one',True)
        self.assertTrue(admitted['allowed'])
        self.assertEqual(risk.factor('XAUUSD'),'gold')
        self.assertFalse(risk.assess('XAUUSD','long',1,40,'lab','two')['allowed'])
        risk.release(admitted['token'])
        self.assertTrue(risk.assess('XAUUSD','long',1,40,'lab','two')['allowed'])

    def test_accepted_reservation_is_replaced_by_actual_position_without_double_count(self):
        admitted=risk.assess('BTCUSDT','long',1,20,'desk','desk',True)
        self.rows.append(dict(source='desk',bot='desk',symbol='BTC',factor='crypto',direction='long',riskEUR=1,notionalEUR=20))
        state=risk.public_snapshot()
        self.assertEqual(state['openRiskEUR'],1)
        self.assertEqual(state['pendingReservations'],0)

    def test_loss_of_capital_reduces_remaining_budget(self):
        self.accounts[0]['equityEUR']=20
        self.assertFalse(risk.assess('BTC','long',1,1,'lab','one')['allowed'])
