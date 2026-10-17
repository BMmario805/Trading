import copy
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from app import open_positions


class OpenPositionsTests(unittest.TestCase):
    def setUp(self):
        self.now = 10_000
        self.data = dict(at=self.now, metadata={'ETHUSDT': {'quoteAsset': 'USDT'}}, books={
            'SOLUSDT': dict(bidPrice='102', askPrice='102.1'),
            'ETHUSDT': dict(bidPrice='99', askPrice='99.2'),
        })
        self.desk_position = dict(symbol='SOLUSDT', entryKey='SOLUSDT:9000', entryTime=9000,
                                  qty=2, entry=100, notional=200, fee=.2, lastBid=101,
                                  sl=95, tp=110, status='open')
        self.engine = NS(state={'position': self.desk_position}, last_data=self.data,
                         last_error=None, save=MagicMock(), public=MagicMock())
        self.lab_position = NS(id=7, symbol='ETH', direction='long', qty=.1, entry_price=100,
                               last_price=98, entry_time=9100, sl=95, tp=110)
        self.lab_position.unrealized=lambda price:(price-self.lab_position.entry_price)*self.lab_position.qty*(1 if self.lab_position.direction=='long' else -1)*getattr(self.lab_position,'quote_to_account',1)
        self.bot = NS(positions={'ETH': self.lab_position}, cfg=NS(name='Bot de prueba'),
                      status=MagicMock(), _save_state=MagicMock(), _open=MagicMock(), _close=MagicMock())
        self.mt5 = dict(configured=True, demo=True, connected=True, updatedAt=self.now,
                        currency='EUR', login=12345, server='private', password='secret',
                        positions=[dict(ticket=42, symbol='EURUSD', bot='scalper', direction='long',
                                        volume=.02, entry=1.09, currentPrice=1.10, priceAt=self.now, openedAt=9200,
                                        sl=1.08, tp=1.12, pnl=5.5)])
        for target, value in (
            ('app.open_positions.time.time', lambda: self.now),
            ('app.open_positions.desk.DESK', self.engine),
            ('app.open_positions.bots.BOTS', {'test': self.bot}),
            ('app.open_positions.mt5_demo.STATE', self.mt5),
            ('app.open_positions.opportunities._cache', {'quotes': (self.now, self.data)}),
            ('app.open_positions.hub._last', {}),
        ):
            mock = patch(target, value)
            mock.start()
            self.addCleanup(mock.stop)

    def test_combines_sources_with_stable_ids_and_keeps_money_separate(self):
        result = open_positions.snapshot()
        self.assertEqual(result['counts'], dict(desk=1, lab=1, mt5=1, total=3))
        self.assertEqual([p['id'] for p in result['positions']], ['mt5:42', 'lab:test:7', 'desk:SOLUSDT:9000'])
        desk = next(p for p in result['positions'] if p['source'] == 'desk')
        lab = next(p for p in result['positions'] if p['source'] == 'lab')
        mt5 = result['positions'][0]
        self.assertAlmostEqual(desk['unrealizedPnl'], 2 * 102 * .9998 * .999 - 200.2)
        self.assertEqual(desk['pnlBasis'], 'estimated-net')
        self.assertAlmostEqual(lab['unrealizedPnl'], -.1)
        self.assertEqual(lab['currency'], 'USDT')
        self.assertEqual(mt5['unrealizedPnl'], 5.5)
        self.assertEqual(mt5['currency'], 'EUR')
        self.assertEqual(mt5['quantityUnit'], 'lots')
        self.assertIsNone(mt5['notional'])
        self.assertTrue(all(p['priceFresh'] for p in result['positions']))
        self.assertNotIn('unrealizedPnl', result)  # Never sum EUR and USDT.

    def test_snapshot_has_no_network_trading_persistence_or_status_side_effects(self):
        before = copy.deepcopy(self.engine.state)
        mt5_before = copy.deepcopy(self.mt5)
        with patch('app.market_data.get_price', new_callable=AsyncMock) as price, \
             patch('app.opportunities.snapshot', new_callable=AsyncMock) as market, \
             patch('app.mt5_market.read', new_callable=AsyncMock) as terminal:
            result = open_positions.snapshot()
        price.assert_not_called()
        market.assert_not_called()
        terminal.assert_not_called()
        for method in (self.engine.save, self.engine.public, self.bot.status,
                       self.bot._save_state, self.bot._open, self.bot._close):
            method.assert_not_called()
        self.assertEqual(before, self.engine.state)
        self.assertEqual(mt5_before, self.mt5)
        serialized = json.dumps(result, allow_nan=False)
        for private in ('secret', 'password', 'login', 'server', 'private'):
            self.assertNotIn(private, serialized)
        result['positions'][0]['symbol'] = 'CHANGED'
        self.assertEqual(self.mt5['positions'][0]['symbol'], 'EURUSD')

    def test_stale_prices_are_explicit_and_unknown_mt5_fields_stay_null(self):
        self.data['at'] = 1
        self.mt5['connected'] = False
        for key in ('currentPrice', 'openedAt'):
            self.mt5['positions'][0].pop(key)
        result = open_positions.snapshot()
        self.assertTrue(all(not p['priceFresh'] for p in result['positions']))
        self.assertTrue(all(s['stale'] for s in result['sources']))
        lab = next(p for p in result['positions'] if p['source'] == 'lab')
        self.assertEqual(lab['currentPrice'], 98)
        self.assertIsNone(lab['priceUpdatedAt'])
        mt5 = next(p for p in result['positions'] if p['source'] == 'mt5')
        self.assertIsNone(mt5['currentPrice'])
        self.assertIsNone(mt5['openedAt'])
        self.assertIsNone(mt5['notional'])

    def test_mt5_requires_explicit_demo_verified_snapshot(self):
        for demo in (False, None, 1, 'demo'):
            with self.subTest(demo=demo):
                self.mt5['demo'] = demo
                result = open_positions.snapshot()
                self.assertEqual(result['counts']['mt5'], 0)
                self.assertFalse(result['sources'][2]['available'])

    def test_mt5_recent_snapshot_cannot_make_old_or_undated_tick_fresh(self):
        for price_at in (None, self.now - 180, self.now + 3600):
            with self.subTest(price_at=price_at):
                self.mt5['positions'][0]['priceAt'] = price_at
                result = open_positions.snapshot()
                self.assertFalse(result['positions'][0]['priceFresh'])
                self.assertEqual(result['positions'][0]['currentPrice'], 1.10)

    def test_forex_pnl_keeps_quote_currency_without_conversion(self):
        p = self.lab_position
        p.symbol, p.direction, p.qty, p.entry_price = 'USDJPY', 'short', 100, 150
        with patch.dict(open_positions.hub._last,
                        {'USDJPY': dict(price=149, time=self.now)}, clear=True):
            result = open_positions.snapshot()
        lab = next(p for p in result['positions'] if p['source'] == 'lab')
        self.assertEqual(lab['currency'], 'JPY')
        self.assertEqual(lab['unrealizedPnl'], 100)
        self.assertEqual(lab['notional'], 15000)
        self.assertTrue(lab['priceFresh'])

    def test_modern_lab_positions_publish_engine_net_pnl_and_cost_contract(self):
        from app.bots import Position
        from app.economics import cost_profile
        modern=Position(id=9,symbol='ETH',direction='long',qty=1,entry_price=100,
                        entry_time=self.now,sl=95,tp=110,atr_entry=2,best_price=100,
                        trail_atr=1,r_dist=5,last_price=99,quote_to_account=.9,
                        execution_costs=cost_profile('lab','cripto'),entry_fee=.1)
        self.bot.positions={'ETH':modern}
        result=open_positions.snapshot()
        row=next(p for p in result['positions'] if p['source']=='lab')
        self.assertEqual(row['pnlBasis'],'estimated-net')
        self.assertEqual(row['executionMode'],'simulated-margin')
        self.assertAlmostEqual(row['unrealizedPnl'],modern.unrealized(row['currentPrice']))
        self.assertLess(row['unrealizedPnl'],-.9)

    def test_empty_uninitialized_sources_and_nonfinite_numbers_are_json_safe(self):
        self.lab_position.last_price = float('nan')
        self.data['at'] = 1
        self.mt5['positions'][0]['pnl'] = float('inf')
        result = open_positions.snapshot()
        lab = next(p for p in result['positions'] if p['source'] == 'lab')
        self.assertIsNone(lab['currentPrice'])
        self.assertIsNone(lab['unrealizedPnl'])
        self.assertIsNone(result['positions'][0]['unrealizedPnl'])
        json.dumps(result, allow_nan=False)
        with patch.object(open_positions.desk, 'DESK', None), \
             patch.dict(open_positions.bots.BOTS, {}, clear=True), \
             patch.dict(open_positions.mt5_demo.STATE, {}, clear=True):
            result = open_positions.snapshot()
        self.assertEqual(result['counts']['total'], 0)
        self.assertTrue(all(not s['available'] for s in result['sources']))


if __name__ == '__main__':
    unittest.main()
