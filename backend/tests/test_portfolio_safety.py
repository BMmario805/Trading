import unittest
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

from app import portfolio_risk as risk


class PortfolioSafetyTests(unittest.TestCase):
    def setUp(self):
        p = patch.dict(risk._reservations, {}, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def test_unknown_instrument_is_not_classified_as_crypto(self):
        self.assertEqual(risk.factor('XAUUSD.a'), 'unknown')
        self.assertEqual(risk.factor('UNCLASSIFIED'), 'unknown')
        self.assertEqual(risk.factor('BTC'), 'crypto')
        self.assertEqual(risk.factor('SOLUSDT'), 'crypto')

    def test_unregistered_live_portfolio_does_not_fail_open(self):
        with patch.object(risk, '_collect', return_value=([], [])):
            self.assertFalse(risk.assess('BTC', 'long', 1, 10, 'lab', 'one')['allowed'])
            self.assertTrue(risk.assess('BTC','long',1,10,'lab','one',isolated=True)['allowed'])

    def test_explicit_broker_mapping_shares_gold_budget_and_unknown_is_blocked(self):
        accounts=[dict(source='lab',bot='one',equityEUR=100,initialEUR=100)]
        with patch.object(risk,'_collect',return_value=(accounts,[])):
            self.assertFalse(risk.assess('XAUUSD.a','long',1,10,'mt5','swing')['allowed'])
            self.assertTrue(risk.assess('XAUUSD.a','long',1,10,'mt5','swing',True,canonical_symbol='XAUUSD')['allowed'])
            self.assertFalse(risk.assess('PAXG','long',1,10,'lab','one')['allowed'])

    def test_only_corroborated_metadata_expands_spot_catalogue(self):
        with patch.dict(risk._catalog,{},clear=False),patch.dict(risk._aliases,{},clear=False):
            self.assertEqual(risk.factor('NEWCOINUSDT'),'unknown')
            risk.register_spot_symbols({'NEWCOINUSDT':dict(baseAsset='NEWCOIN',quoteAsset='USDT',
                                                         status='TRADING',isSpotTradingAllowed=True)})
            self.assertEqual(risk.factor('NEWCOINUSDT'),'crypto')
            risk.register_spot_symbols({'BADUSDT':dict(baseAsset='BAD',quoteAsset='USD',
                                                     status='TRADING',isSpotTradingAllowed=True)})
            self.assertEqual(risk.factor('BADUSDT'),'unknown')

    def test_malformed_restore_does_not_create_zero_risk_reservation(self):
        for payload in ({},dict(source='mt5',bot=[],symbol='BTC',factor='crypto',direction='long',riskEUR=1,notionalEUR=1,at=1),
                        dict(source='mt5',bot='swing',symbol='BTC',factor='crypto',direction='long',riskEUR=float('nan'),notionalEUR=1,at=1)):
            self.assertFalse(risk.restore('bad',payload))
        self.assertIsNone(risk.reservation('bad'))

    def test_marked_notional_and_stale_conversion_are_visible(self):
        from app import bots,desk,mt5_demo
        p=dict(symbol='BTCUSDT',qty=2,notional=100,fee=.1,sl=40,lastBid=100,quoteAt=time.time(),costPolicy={})
        engine=NS(state=dict(funding=dict(amount=100,netQuotePerEuro=1),positions=[p]),
                  equity=lambda:200,risk_conversion=lambda:2,last_data=None)
        with patch.dict(bots.BOTS,{},clear=True),patch.dict(desk.DESKS,{'crypto':engine},clear=True), \
             patch.object(desk,'DESK',None),patch.dict(mt5_demo.STATE,{},clear=True):
            snapshot=risk.public_snapshot()
            self.assertEqual(snapshot['grossExposureEUR'],100)
            self.assertTrue(snapshot['conversionComplete'])
            engine.risk_conversion=lambda:None
            snapshot=risk.public_snapshot()
            self.assertFalse(snapshot['exposureComplete'])
            self.assertFalse(snapshot['conversionComplete'])
            self.assertEqual(len(snapshot['positions']),1)

    def test_lab_risk_uses_current_fx_and_keeps_unknown_rows_when_conversion_expires(self):
        from app import bots,desk,mt5_demo
        now=time.time()
        position=NS(symbol='BTC',direction='long',qty=2,last_price=100,sl=40,quote_to_account=2,
                    last_quote_at=now,risk_quote_to_account=1.5,risk_conversion_at=now,risk_conversion_source='test-tick',
                    unrealized=lambda price=None:-10 if price==40 else 0)
        bot=NS(session=dict(currency='EUR'),balance=100,cfg=NS(id='one',initial_balance=100),positions={'BTC':position})
        with patch.dict(bots.BOTS,{'one':bot},clear=True),patch.dict(desk.DESKS,{},clear=True), \
             patch.object(desk,'DESK',None),patch.dict(mt5_demo.STATE,{},clear=True):
            snapshot=risk.public_snapshot()
            self.assertEqual(snapshot['grossExposureEUR'],300)
            self.assertEqual(snapshot['openRiskEUR'],7.5)
            self.assertTrue(snapshot['conversionComplete'])
            position.risk_conversion_at=now-121
            snapshot=risk.public_snapshot()
            self.assertFalse(snapshot['conversionComplete'])
            self.assertFalse(snapshot['exposureComplete'])
            self.assertEqual(len(snapshot['positions']),1)


if __name__ == '__main__':
    unittest.main()
