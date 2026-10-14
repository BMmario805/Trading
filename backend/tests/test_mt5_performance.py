import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch, MagicMock

from app import mt5_performance as perf
from app.mt5_demo import DemoEngine


def deal(ticket, stamp, profit=0, **kwargs):
    values = dict(ticket=ticket, time=stamp, time_msc=stamp*1000, position_id=7, magic=42,
                  type=0, entry=0, volume=1, symbol='EURUSD', profit=profit, commission=0, swap=0, fee=0)
    values.update(kwargs)
    return NS(**values)


class PerformanceTests(unittest.TestCase):
    def test_server_clock_ahead_does_not_hide_deposit(self):
        engine = DemoEngine.__new__(DemoEngine)
        engine.last_performance = 0
        engine.owner = 'demo-clock'
        engine.api = MagicMock()
        engine.gateway = MagicMock()
        engine.gateway.positions.return_value = []
        engine.gateway.account.return_value = (self.account, None)
        deposit = deal(99, 110800, 100000, type=2, position_id=0)
        engine.api.history_deals_get.side_effect = lambda start, end: [deposit] if end >= deposit.time else []
        engine.sync_performance(100000)
        result = perf.performance(engine.owner, self.bots)
        self.assertEqual(result['account']['netFunding'], 100000)
        self.assertEqual(result['account']['reconciliationDifference'], 0)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(perf, 'DB_PATH', Path(self.tmp.name)/'mt5.sqlite')
        self.patch.start()
        self.bots = [dict(id='scalper', name='Scalper', magic=42)]
        self.account = NS(balance=100000, equity=100000, credit=0, currency='EUR')

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def result(self, **kwargs):
        return perf.performance('demo:1', self.bots, **kwargs)

    def test_initial_deposit_is_not_profit(self):
        perf.ingest('demo:1', self.account, [], [deal(1, 100, 100000, type=2, position_id=0)], 200, True)
        result = self.result()
        self.assertEqual(result['summary']['pnl'], 0)
        self.assertEqual(result['summary']['trades'], 0)
        self.assertEqual(result['account']['netFunding'], 100000)
        self.assertEqual(result['currency'], 'EUR')

    def test_costs_and_manual_exit_are_attributed_to_opening_bot(self):
        rows = [deal(1, 100, commission=-2), deal(2, 150, 20, magic=0, type=1, entry=1, commission=-2, swap=-1, fee=-.5)]
        perf.ingest('demo:1', self.account, [], rows, 200, True)
        result = self.result(per_bot=True)
        self.assertEqual(result['summary']['pnl'], 14.5)
        self.assertEqual(result['summary']['trades'], 1)
        self.assertEqual(result['summary']['winRate'], 100)
        self.assertEqual(result['cards'][0]['realizedPnl'], 14.5)
        self.assertEqual(result['series'][0]['summary']['pnl'], 14.5)

    def test_partial_closes_are_one_trade_and_import_is_idempotent(self):
        rows = [deal(1, 100), deal(2, 150, 10, entry=1, type=1, volume=.4),
                deal(3, 150, 15, entry=1, type=1, volume=.6, time_msc=150001)]
        perf.ingest('demo:1', self.account, [], rows, 200, True)
        perf.ingest('demo:1', self.account, [], rows, 210)
        result = self.result()
        self.assertEqual(result['summary']['pnl'], 25)
        self.assertEqual(result['summary']['trades'], 1)
        timestamps = [p['ts'] for p in result['points']]
        self.assertEqual(timestamps, sorted(set(timestamps)))

    def test_still_open_position_is_not_counted_as_closed(self):
        positions = [NS(ticket=8, identifier=7, magic=42, symbol='EURUSD', profit=9, swap=-1, volume=.6)]
        self.account.equity = 100008
        perf.ingest('demo:1', self.account, positions,
                    [deal(1, 100), deal(2, 150, 10, entry=1, type=1, volume=.4)], 200, True)
        result = self.result()
        self.assertEqual(result['summary']['trades'], 0)
        self.assertEqual(result['cards'][0]['unrealizedPnl'], 8)
        self.assertEqual(result['account']['totalPnl'], 18)
        self.assertEqual(sum(c['totalPnl'] for c in result['cards']), 18)

    def test_accounts_are_isolated(self):
        perf.ingest('demo:2', self.account, [], [deal(1, 100, 999)], 200, True)
        self.assertEqual(self.result()['summary']['pnl'], 0)
        self.assertEqual(self.result()['accountHistory'], [])

    def test_manual_trades_and_account_charges_reconcile(self):
        rows = [deal(1, 100, magic=0), deal(2, 150, 20, magic=0, type=1, entry=1),
                deal(3, 170, -3, type=4, position_id=0, magic=0, symbol='')]
        perf.ingest('demo:1', self.account, [], rows, 200, True)
        result = self.result()
        self.assertEqual(result['account']['realizedPnl'], 17)
        self.assertEqual(sum(c['realizedPnl'] for c in result['cards']), 17)
        self.assertEqual(result['cards'][0]['realizedPnl'], 0)

    def test_filters_and_position_attribution_include_original_entry_outside_range(self):
        perf.ingest('demo:1', self.account, [], [deal(1, 100, commission=-2),
                    deal(2, 150, 20, magic=0, type=1, entry=1)], 200, True)
        result = self.result(bot='scalper', symbol='EURUSD', since=120, until=160)
        self.assertEqual(result['summary']['pnl'], 20)
        self.assertEqual(result['summary']['trades'], 1)
        self.assertEqual(result['summary']['best'], 18)

    def test_failed_sync_preserves_previous_values(self):
        perf.ingest('demo:1', self.account, [], [], 200, True)
        perf.sync_error('demo:1', 'Desconectado')
        result = self.result()
        self.assertEqual(result['updatedAt'], 200)
        self.assertEqual(result['syncError'], 'Desconectado')
        self.assertEqual(result['account']['balance'], 100000)

    def test_corrected_deal_updates_instead_of_duplicating(self):
        perf.ingest('demo:1', self.account, [], [deal(1, 100, 10)], 200, True)
        perf.ingest('demo:1', self.account, [], [deal(1, 100, 5)], 210)
        self.assertEqual(self.result()['summary']['pnl'], 5)


if __name__ == '__main__':
    unittest.main()
