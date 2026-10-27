import asyncio
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import bots, learning, training
from app.config import SYMBOLS_BY_ID
from app.execution import closed_candles, fresh_signal, valid_tick
from app.strategy import score_series, resample, TREND_STEP


def candle(t, close=100, high=None, low=None, opening=None):
    return dict(time=t, open=opening or close, high=high or close,
                low=low or close, close=close, volume=10)


class ExecutionTests(unittest.TestCase):
    def test_closed_and_valid_candles(self):
        rows = [candle(60), candle(0), candle(60), candle(120), candle(0, math.nan)]
        self.assertEqual([c['time'] for c in closed_candles(rows, 60, 120)], [0, 60])

    def test_direction_reversal_is_fresh(self):
        self.assertTrue(fresh_signal(-.6, [.7, .8, .7], .5))
        self.assertFalse(fresh_signal(.6, [.7, .8, .7], .5))
        self.assertFalse(fresh_signal(.6, [], .5))

    def test_quotes_require_recent_source_timestamp(self):
        self.assertFalse(valid_tick(dict(price=100, time=1), 1000))
        self.assertFalse(valid_tick(dict(price=math.nan, time=1000), 1000))
        self.assertTrue(valid_tick(dict(price=100, time=990), 1000))

    def simulate(self, rows, scores):
        return training.simulate(rows, scores, [2] * len(rows), 0,
                                 .5, 1, 3, 1, 100, 1, 100, 100000, 0, len(rows))

    def test_terminal_open_loss_is_counted(self):
        m = self.simulate([candle(0), candle(60), candle(120, 99, 100, 99, 100)], [0, 1, 1])
        self.assertEqual(m['trades'], 1)
        self.assertEqual(m['pnl'], -50)

    def test_gap_stop_uses_open_not_stop(self):
        m = self.simulate([candle(0), candle(60), candle(120), candle(180, 90)], [0, 1, 1, 1])
        self.assertEqual(m['pnl'], -500)

    def test_no_fill_at_signal_close(self):
        m = self.simulate([candle(0), candle(60), candle(120, 102, 102, 101, 101)], [0, 1, 1])
        self.assertEqual(m['pnl'], 50)

    def test_ambiguous_bar_stop_first(self):
        m = self.simulate([candle(0), candle(60), candle(120, 100, 108, 97)], [0, 1, 1])
        self.assertEqual(m['pnl'], -100)

    def test_strategy_scores_do_not_change_with_future_bars(self):
        rows = [candle(i * 3600, 100 + i * .02 + math.sin(i / 5),
                       102 + i * .02 + math.sin(i / 5),
                       98 + i * .02 + math.sin(i / 5)) for i in range(340)]
        for cfg in bots.ALL_BOTS:
            with self.subTest(bot=cfg.id):
                full = score_series(cfg.id, rows, resample(rows, TREND_STEP[cfg.id]))
                prefix = score_series(cfg.id, rows[:300], resample(rows[:300], TREND_STEP[cfg.id]))
                self.assertAlmostEqual(full[299], prefix[-1])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(module, 'DB_PATH', Path(self.tmp.name) / 'test.db')
                        for module in (bots, training, learning)]
        for p in self.patches:
            p.start()
        self.bot = bots.TradingBot(bots.SCALPER)
        self.bot.load_state()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def open_position(self):
        signal = dict(reasons=[], entryQuote=dict(price=100,time=bots.time.time()), candles=[dict(time=0,open=100,high=101,low=99,close=100,volume=100)], confirmations=[])
        with patch.object(learning, 'size_multiplier', return_value=(1, None)), \
             patch.object(bots.systems, 'chart_overlays', return_value={}):
            self.bot._open(SYMBOLS_BY_ID['BTC'], 'long', 100, 2, .8, signal, 2, 4, .8)
        return self.bot.positions['BTC']

    def test_restart_keeps_initial_risk_and_trailing(self):
        pos = self.open_position()
        self.bot._update_stop(pos, 102)
        restored = bots.TradingBot(bots.SCALPER)
        restored.load_state()
        self.assertEqual(restored.positions['BTC'].r_dist, 4)
        self.assertEqual(restored.positions['BTC'].trail_atr, .8)

    def test_learning_uses_initial_risk(self):
        pos = self.open_position()
        self.bot._update_stop(pos, pos.entry_price + .01)
        with patch.object(learning, 'refresh'):
            self.bot._close(pos, pos.entry_price + 2, 'test')
        with bots._conn() as con:
            row = con.execute('SELECT initial_risk_dist, pnl, qty, economic_risk_dist FROM bot_trades').fetchone()
            balance = con.execute('SELECT balance FROM bot_state WHERE bot=?', ('scalper',)).fetchone()[0]
        self.assertEqual(row[0], 4)  # mover el stop no reescribe el riesgo original
        self.assertGreater(row[3], row[0])  # la nueva base incluye todas las fricciones
        self.assertLess(row[1] / (row[3] * row[2]), .5)
        self.assertEqual(balance, self.bot.balance)
        result = learning.analyze('scalper')
        self.assertIn('BTC', result['bySymbol'])
        self.assertEqual(result['bySymbol']['BTC']['expectancyR'], round(row[1]/(row[3]*row[2]), 3))

    def test_live_stop_uses_current_price(self):
        pos = self.open_position()
        with patch.object(bots.market_data, 'get_price', AsyncMock(return_value=dict(price=90, time=time.time()))), \
             patch.object(self.bot, '_close') as close:
            asyncio.run(self.bot._manage_positions())
        self.assertAlmostEqual(close.call_args.args[1], 90 * (1 - .0005 / 2) * (1 - .0002))

    def test_stale_quote_cannot_close(self):
        self.open_position()
        with patch.object(bots.market_data, 'get_price', AsyncMock(return_value=dict(price=90, time=1))), \
             patch.object(self.bot, '_close') as close:
            asyncio.run(self.bot._manage_positions())
        close.assert_not_called()

    def test_risk_includes_floating_losses(self):
        pos = self.open_position()
        pos.last_price = pos.entry_price - 100
        self.assertTrue(self.bot.entries_paused())

    def test_validated_threshold_is_not_discounted(self):
        self.bot.trained_params['XAUUSD'] = dict(entryThreshold=.5, slAtr=2, tpAtr=3, trailAtr=1)
        self.assertEqual(self.bot._exec_params(SYMBOLS_BY_ID['XAUUSD'])[0], .5)

    def test_legacy_validation_requires_retraining(self):
        with training._conn() as con:
            con.execute('INSERT INTO bot_params VALUES (?, ?, ?, ?, ?, ?)',
                        ('scalper', 'BTC', '{}', '{}', 1, 1))
        self.assertEqual(training.load_applied_params(), {})
        self.assertFalse(training.load_results()[0]['applied'])

    def test_failed_training_revokes_previous_parameters(self):
        with training._conn() as con:
            con.execute('INSERT INTO bot_params VALUES (?, ?, ?, ?, ?, ?)',
                        ('scalper', 'BTC', '{}', '{"engineVersion": 2}', 1, 1))
        rows = [candle(i * 60) for i in range(600)]
        with patch.object(training, 'fetch_history', AsyncMock(return_value=rows)), \
             patch.object(training, 'optimize', return_value=dict(ok=False, reason='Sin ventaja')):
            asyncio.run(training._train_symbol(self.bot, SYMBOLS_BY_ID['BTC']))
        self.assertEqual(training.load_applied_params(), {})

    def test_daily_close_is_counted_after_midnight(self):
        pos = self.open_position()
        self.bot.day_utc -= 1
        with patch.object(learning, 'refresh'):
            self.bot._close(pos, pos.entry_price - 2, 'test')
        self.assertLess(self.bot.daily_pnl(), -2 * pos.qty)
        with bots._conn() as con:
            recorded = con.execute('SELECT pnl FROM bot_trades WHERE id=?', (pos.id,)).fetchone()[0]
        self.assertAlmostEqual(self.bot.daily_pnl(), recorded)


if __name__ == '__main__':
    unittest.main()
