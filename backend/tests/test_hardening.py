import asyncio
import json
import math
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import bots, learning, market_data, mt5_market, training
from app.config import SYMBOLS_BY_ID
from app.database import Connection
from app.strategy import factor_series, extension_series, extension_atr, resample


class EntryTests(unittest.TestCase):
    def test_all_bots_require_validation(self):
        self.assertTrue(all(c.require_validation for c in bots.ALL_BOTS))

    def test_rejected_and_unknown_cannot_enter_with_immature_model(self):
        for edge in ('rejected', 'unknown'):
            with self.subTest(edge=edge), patch.object(learning, 'edge_status', return_value=edge), patch.object(learning, '_active_blocks', return_value=[]):
                self.assertEqual(learning.entry_mode('scalper', 'BTC')[0], 'blocked')

    def test_live_performance_veto_blocks_validated_pair(self):
        blocks = [dict(symbol='BTC', direction='short', reason='Pérdidas')]
        with patch.object(learning, 'edge_status', return_value='validated'), patch.object(learning, '_active_blocks', return_value=blocks):
            self.assertEqual(learning.entry_mode('swing', 'BTC', 'short')[0], 'blocked')
            self.assertEqual(learning.entry_mode('swing', 'BTC', 'long')[0], 'full')

    def test_scan_does_not_evaluate_unvalidated_pair(self):
        bot = bots.TradingBot(bots.SCALPER)
        with patch.object(bot, '_evaluate', AsyncMock()) as evaluate:
            asyncio.run(bot._scan())
            evaluate.assert_not_awaited()

    def test_old_snapshots_do_not_train_new_model(self):
        row = ('BTC', 'long', 1000, json.dumps(dict(confirmations=[dict(system='MACD', vote=1)])))
        self.assertIsNone(learning._trade_features('swing', row))

    def test_gold_cannot_enter_using_proxy_after_broker_validation(self):
        bot = bots.TradingBot(replace(bots.SCALPER, universe=('XAUUSD',)))
        bot.trained_params['XAUUSD'] = dict(dataSource=dict(provider='mt5', instrument='GOLD', account='demo'))
        with patch.object(learning, 'entry_mode', return_value=('full', None)), patch.object(bot, '_evaluate', AsyncMock(return_value=dict(candles=[dict(close=100)]))), patch.object(bot, '_open') as open_position:
            asyncio.run(bot._scan())
            open_position.assert_not_called()
        self.assertIn('Fuente distinta', bot.diagnostics['XAUUSD']['reason'])


class SimulationTests(unittest.TestCase):
    def test_extension_blocks_only_chasing_direction(self):
        rows = [dict(time=i*60, open=100, high=100, low=100, close=100, volume=10) for i in range(3)]
        def run(score, extension):
            return training.simulate(rows, [0, score, score], [2]*3, 0, .5, 1, 3, 1,
                                     100, 1, 100, 100000, 0, 3, [None, extension, extension], 2)['trades']
        self.assertEqual(run(1, 3), 0)
        self.assertEqual(run(-1, -3), 0)
        self.assertEqual(run(-1, 3), 1)
        self.assertEqual(run(1, -3), 1)

    def test_extension_series_matches_live_guard(self):
        rows = [dict(time=i*60, open=100+i, high=102+i, low=99+i, close=101+i, volume=10) for i in range(80)]
        self.assertAlmostEqual(extension_series(rows)[-1], extension_atr(rows))

    def test_no_trigger_means_no_entry_and_future_does_not_rewrite_past(self):
        rows = [dict(time=i*60, open=100, high=101, low=99, close=100, volume=10) for i in range(250)]
        factors = factor_series(rows, resample(rows, 300), 300)
        self.assertTrue(all(f['score'] == 0 for f in factors))
        shock = dict(time=250*60, open=100, high=200, low=100, close=190, volume=1000)
        self.assertEqual(factor_series(rows+[shock], resample(rows+[shock], 300), 300)[249], factors[-1])


class DatabaseTests(unittest.TestCase):
    def test_wal_reader_can_read_during_writer_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'test.db'
            with sqlite3.connect(path, factory=Connection) as con:
                con.execute('CREATE TABLE entries (id INTEGER)')
            with sqlite3.connect(path, factory=Connection) as writer, sqlite3.connect(path, factory=Connection) as reader:
                self.assertEqual(writer.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
                self.assertEqual(writer.execute('PRAGMA busy_timeout').fetchone()[0], 5000)
                writer.execute('INSERT INTO entries VALUES (1)')
                self.assertEqual(reader.execute('SELECT COUNT(*) FROM entries').fetchone()[0], 0)

    def test_new_engine_is_due_despite_recent_old_training_attempt(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(training, 'DB_PATH', Path(tmp)/'test.db'):
            with training._conn() as con:
                con.execute('INSERT INTO bot_params VALUES (?,?,?,?,?,?)', ('swing', 'BTC', '{}', '{"engineVersion":2}', 1, 10000))
                con.execute('INSERT INTO bot_training_attempts VALUES (?,?,?,?)', ('swing', 'BTC', 10000, 1))
            self.assertEqual(training.due_symbols('swing', ('BTC',), 12, 10001), ['BTC'])


class BrokerDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_gold_history_prefers_broker_and_preserves_provenance(self):
        rows = [dict(time=0, close=100, dataSource=dict(provider='mt5', instrument='GOLD', account='demo'))]
        with patch.object(mt5_market, '_reader', AsyncMock(return_value=rows)), patch.object(training, '_binance_history', AsyncMock()) as proxy:
            self.assertEqual(await training.fetch_history('scalper', SYMBOLS_BY_ID['XAUUSD'], '1m'), rows)
            proxy.assert_not_awaited()

    async def test_unavailable_terminal_falls_back_to_unvalidated_proxy(self):
        with patch.object(mt5_market, '_reader', AsyncMock(side_effect=RuntimeError('offline'))), patch.object(training, '_binance_history', AsyncMock(return_value=[dict(close=100)])) as proxy:
            self.assertEqual(await training.fetch_history('scalper', SYMBOLS_BY_ID['XAUUSD'], '1m'), [dict(close=100)])
            proxy.assert_awaited_once()

    async def test_gold_live_quote_uses_same_broker_source(self):
        tick = dict(price=100, time=1000, dataSource=dict(provider='mt5'))
        with patch.object(mt5_market, '_reader', AsyncMock(return_value=tick)), patch.object(market_data, '_swissquote_mid', AsyncMock()) as fallback:
            self.assertEqual(await market_data.get_price(SYMBOLS_BY_ID['XAUUSD']), tick)
            fallback.assert_not_awaited()
