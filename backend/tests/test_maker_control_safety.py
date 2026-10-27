import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import desk
from app.book_stream import BookStream
from test_desk import data


class MakerControlSafetyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.engine = desk.Desk(db_path=Path(folder.name) / 'desk.sqlite')
        self.now = 1000000.0
        self.enterContext(patch('time.time', side_effect=lambda: self.now))
        self.stream = BookStream()
        self.stream.connected = True
        self.stream.watch(['SOLUSDT', 'ETHUSDT'])
        self.enterContext(patch('app.desk_maker.BOOKS', self.stream))
        self.enterContext(patch.object(desk, 'BOOKS', self.stream))
        self.snapshot = data()
        for symbol in ('SOLUSDT', 'ETHUSDT'):
            self.stream.books[symbol] = dict(symbol=symbol, bidPrice='100', askPrice='100.01',
                bidQty='2', askQty='2', receivedAt=self.now, monotonicAt=time.monotonic())
        order = desk.size_order(1000, 1000, 100.01, 2, self.snapshot['metadata']['SOLUSDT'])
        self.template = dict(order, symbol='SOLUSDT', entryKey='SOLUSDT:999000',
            entryTime=self.now, quoteAt=self.now, atr=2, initialDist=order['entry'] - order['sl'],
            best=order['entry'], lastBid=100, status='open',
            marketFilters=self.snapshot['metadata']['SOLUSDT']['filters'], evidence={})

    def trade(self, identifier, quantity):
        self.now += .1
        self.assertTrue(self.stream.ingest(dict(e='trade', s='SOLUSDT', t=identifier,
            T=self.now * 1000, p='100', q=str(quantity), m=True)))

    def test_control_preserves_prior_fill_and_excludes_later_trades(self):
        self.engine.maker.post(self.template, 'buy', self.template['qty'])
        self.trade(1, 2.1)  # Two units ahead, then a genuine observed fill of .1.
        cutoff = self.now + .05
        self.trade(2, 1)  # This observation is later than the control timestamp.
        self.engine.maker.reconcile(self.snapshot, cutoff=cutoff)
        self.assertAlmostEqual(self.engine.state['positions'][0]['qty'], .1)
        self.assertAlmostEqual(self.engine.maker.orders[0]['accountedQty'], .1)
        self.engine.state['enabled'] = False
        self.engine.state['controlGeneration'] += 1
        self.assertEqual(self.engine.maker.cancel_entries(), 1)
        self.engine.maker.reconcile(self.snapshot)
        self.assertFalse(self.engine.maker.pending_entry())
        self.assertAlmostEqual(self.engine.state['cash'], 989.99)
        self.assertAlmostEqual(self.engine.state['positions'][0]['qty'], .1)
        self.assertEqual(self.engine.state['makerHistory'][-1]['status'], 'cancelled-control')
        self.assertAlmostEqual(self.engine.state['makerHistory'][-1]['filled'], .1)
        restored = desk.Desk(db_path=self.engine.db_path)
        self.assertFalse(restored.state['enabled'])
        self.assertFalse(restored.maker.pending_entry())
        self.assertAlmostEqual(restored.state['positions'][0]['qty'], .1)
        self.assertAlmostEqual(restored.state['cash'], 989.99)

    def test_cancel_entries_keeps_protective_sell_and_existing_position(self):
        self.engine.maker.post(self.template, 'buy', self.template['qty'])
        self.trade(1, 100)
        self.engine.maker.reconcile(self.snapshot)
        position = self.engine.state['positions'][0]
        self.assertTrue(self.engine.maker.post(position, 'sell', position['qty']))
        other = dict(copy.deepcopy(self.template), symbol='ETHUSDT', entryKey='ETHUSDT:999000')
        self.assertTrue(self.engine.maker.post(other, 'buy', other['qty']))
        before = copy.deepcopy(self.engine.state['positions'])
        cash = self.engine.state['cash']
        self.assertEqual(self.engine.maker.cancel_entries(), 1)
        self.assertEqual(self.engine.state['positions'], before)
        self.assertEqual(self.engine.state['cash'], cash)
        self.assertEqual([(row['symbol'], row['side']) for row in self.engine.maker.orders],
                         [('SOLUSDT', 'sell')])

    def test_cancellation_persistence_failure_preserves_pending_reservation(self):
        self.engine.maker.post(self.template, 'buy', self.template['qty'])
        before = copy.deepcopy(self.engine.state)
        with patch.object(self.engine, 'connect', side_effect=OSError('isolated persistence failure')):
            with self.assertRaises(OSError):
                self.engine.maker.cancel_entries()
        self.assertEqual(self.engine.state, before)
        self.assertTrue(self.engine.maker.pending_entry())
        self.assertGreater(self.engine.maker.orders[0]['reservedRisk'], 0)


if __name__ == '__main__':
    unittest.main()
