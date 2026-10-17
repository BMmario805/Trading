import json
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app import bots, learning, training, portfolio_risk


class EconomicDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        for module in (bots, learning, training):
            self.stack.enter_context(patch.object(module, 'DB_PATH', Path(folder) / 'economics.db'))
        for cache in (learning._models, learning._policies, learning._edges):
            self.stack.enter_context(patch.dict(cache, {}, clear=True))
        with bots._conn():
            pass

    def trade(self, status='closed', pnl=-1, exit_time=1000, symbol='BTC', direction='long'):
        snapshot = json.dumps(dict(engineVersion=training.ENGINE_VERSION,
                                   confirmations=[dict(system='MACD', vote=.7)]))
        with bots._conn() as con:
            return con.execute('''INSERT INTO bot_trades
                (bot,symbol,direction,qty,entry_price,entry_time,sl,tp,reasons,pnl,status,exit_time,snapshot,initial_risk_dist)
                VALUES ('scalper',?,?,1,100,900,90,120,'[]',?,?,?,?,10)''',
                (symbol, direction, pnl if status == 'closed' else None, status,
                 exit_time if status == 'closed' else None, snapshot)).lastrowid

    def validation(self, symbol, trained_at):
        with training._conn() as con:
            con.execute('INSERT OR REPLACE INTO bot_params VALUES (?,?,?,?,?,?)',
                        ('scalper', symbol, '{}', json.dumps(dict(engineVersion=training.ENGINE_VERSION)), 1, trained_at))


class LearningReceiptTests(EconomicDatabaseTests):
    def test_later_opened_trade_can_close_first_without_losing_earlier_trade(self):
        first = self.trade(status='open')
        self.trade(exit_time=1100, pnl=5)
        self.assertEqual(learning.meta_learn_new_trades('scalper'), 1)
        with bots._conn() as con:
            con.execute("UPDATE bot_trades SET status='closed',pnl=-3,exit_time=1200 WHERE id=?", (first,))
        self.assertEqual(learning.meta_learn_new_trades('scalper'), 1)
        self.assertEqual(learning._load_model('scalper')['n'], 2)
        learning._models.clear()
        self.assertEqual(learning.meta_learn_new_trades('scalper'), 0)
        self.assertEqual(learning._load_model('scalper')['n'], 2)
        with learning._conn() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM bot_model_processed').fetchone()[0], 2)

    def test_legacy_cursor_model_rebuilds_weights_without_double_learning(self):
        self.trade(pnl=3)
        self.trade(pnl=-2)
        legacy = {**learning._new_model(), 'n': 900, 'lastTradeId': 999, 'w': {'bias': 80}}
        legacy.pop('processingVersion')
        with learning._conn() as con:
            con.execute('INSERT INTO bot_model VALUES (?,?,?)', ('scalper', json.dumps(legacy), 1))
        self.assertEqual(learning.meta_learn_new_trades('scalper'), 2)
        self.assertEqual(learning._load_model('scalper')['n'], 2)
        self.assertLess(abs(learning._load_model('scalper')['w']['bias']), .1)
        self.assertEqual(learning.meta_learn_new_trades('scalper'), 0)

    def test_failed_batch_rolls_back_weights_and_processed_ids_together(self):
        self.trade()
        self.trade()
        real = learning._meta_sgd_step
        calls = 0

        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('interrupted learning')
            real(*args)

        with patch.object(learning, '_meta_sgd_step', side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                learning.meta_learn_new_trades('scalper')
        with learning._conn() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM bot_model_processed').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM bot_model').fetchone()[0], 0)
        self.assertEqual(learning.meta_learn_new_trades('scalper'), 2)

    def test_zero_win_rate_blocks_direction_and_keeps_original_creation_time(self):
        for i in range(6):
            self.trade(exit_time=1000+i)
        with patch.object(learning.time, 'time', return_value=2000):
            first = learning.refresh('scalper')
        self.assertEqual(first['blocks'][0]['direction'], 'long')
        self.assertEqual(first['stats']['byDirection']['BTC:long']['winRate'], 0)
        learning._policies.clear()  # un reinicio también conserva el episodio
        with patch.object(learning.time, 'time', return_value=3000):
            again = learning.refresh('scalper')
        self.assertEqual(again['blocks'][0]['createdAt'], 2000)

    def test_revalidation_is_per_symbol_and_new_losses_can_block_again(self):
        for i in range(6):
            self.trade(exit_time=1000+i)
        with patch.object(learning.time, 'time', return_value=2000):
            learning.refresh('scalper')
        self.validation('ETH', 2500)
        with patch.object(learning.time, 'time', return_value=3000):
            learning.refresh('scalper')
        self.assertEqual(learning.entry_mode('scalper', 'BTC', 'long')[0], 'blocked')
        self.validation('BTC', 3500)
        with patch.object(learning.time, 'time', return_value=4000):
            learning.refresh('scalper')
        self.assertEqual(learning.entry_mode('scalper', 'BTC', 'long')[0], 'full')
        for i in range(6):
            self.trade(exit_time=4500+i)
        with patch.object(learning.time, 'time', return_value=5000):
            policy = learning.refresh('scalper')
        self.assertEqual(learning.entry_mode('scalper', 'BTC', 'long')[0], 'blocked')
        self.assertEqual(policy['blocks'][0]['createdAt'], 5000)

    def test_positive_returns_cannot_increase_uncalibrated_size(self):
        for i in range(12):
            self.trade(pnl=20, exit_time=1000+i)
        policy = learning.refresh('scalper')
        self.assertEqual(policy['sizeMult']['BTC'], 1)
        learning._policies['scalper']['sizeMult']['BTC'] = 1.5  # antigua política en memoria
        self.assertEqual(learning.size_multiplier('scalper', 'BTC'), (1.0, None))


class LabCostTests(EconomicDatabaseTests):
    def open(self, direction='long', atr=2, p_win=None):
        bot = bots.TradingBot(replace(bots.SCALPER, initial_balance=100))
        bot.load_state()
        with patch.object(learning, 'size_multiplier', return_value=(1, None)), \
             patch.object(bots.systems, 'chart_overlays', return_value={}), \
             patch.object(bots.time, 'time', return_value=100000):
            bot._open(bots.SYMBOLS_BY_ID['BTC'], direction, 100, atr, .8,
                      dict(reasons=[], candles=[], confirmations=[]), 2, 4, 1, p_win=p_win)
        return bot

    def test_tiny_target_is_rejected_after_round_trip_costs(self):
        bot = self.open(atr=.001)
        self.assertFalse(bot.positions)
        self.assertIn('Objetivo neto', bot.diagnostics['BTC']['reason'])

    def test_short_is_explicit_margin_scenario_with_fees_and_borrow_cost(self):
        bot = self.open('short')
        pos = bot.positions['BTC']
        self.assertEqual(pos.to_dict()['executionMode'], 'simulated-margin')
        self.assertFalse(pos.execution_costs['verified'])
        self.assertAlmostEqual(pos.entry_price, 100 * (1-.0005/2) * (1-.0002))
        with patch.object(bots.time, 'time', return_value=pos.entry_time+86400):
            expected = ((pos.entry_price-90)*pos.qty-pos.entry_fee
                        -90*pos.qty*.001-pos.entry_price*pos.qty*.0003)
            with patch.object(learning, 'refresh'):
                bot._close(pos, 90, 'test-executed-fill')
        self.assertAlmostEqual(bot.balance, 100+expected)
        with bots._conn() as con:
            fee, carry, pnl = con.execute('SELECT exit_fee,carry_cost,pnl FROM bot_trades').fetchone()
        self.assertAlmostEqual(fee, 90*pos.qty*.001)
        self.assertAlmostEqual(carry, pos.entry_price*pos.qty*.0003)
        self.assertAlmostEqual(pnl, expected)

    def test_restart_preserves_cost_contract_and_economic_risk(self):
        bot = self.open()
        original = bot.positions['BTC']
        restored = bots.TradingBot(bots.SCALPER)
        restored.load_state()
        pos = restored.positions['BTC']
        self.assertEqual(pos.execution_costs, original.execution_costs)
        self.assertEqual(pos.entry_fee, original.entry_fee)
        self.assertEqual(pos.economic_risk_dist, original.economic_risk_dist)
        self.assertAlmostEqual(pos.initial_risk(), 1)

    def test_legacy_positions_keep_old_friction_contract(self):
        tid = self.trade(status='open')
        bot = bots.TradingBot(bots.SCALPER)
        bot.load_state()
        pos = bot.positions['BTC']
        self.assertIsNone(pos.execution_costs)
        self.assertIsNone(pos.break_even_price())
        self.assertEqual(pos.unrealized(110), 10)
        self.assertEqual(pos.exit_fill(110), 110*(1-.0005/2))
        before = bot.balance
        with patch.object(learning, 'refresh'):
            bot._close(pos, 110, 'test')
        self.assertEqual(bot.balance, before+10)
        with bots._conn() as con:
            self.assertEqual(con.execute('SELECT pnl,exit_fee,carry_cost FROM bot_trades WHERE id=?', (tid,)).fetchone(),
                             (10, 0, 0))

    def test_high_meta_probability_never_increases_risk_and_close_is_idempotent(self):
        bot = self.open(p_win=.99)
        pos = bot.positions['BTC']
        self.assertAlmostEqual(pos.initial_risk(), bot.cfg.risk_pct*100)
        with patch.object(learning, 'refresh'), patch.object(bots.time, 'time', return_value=pos.entry_time):
            bot._close(pos, 101, 'first')
            balance = bot.balance
            bot._close(pos, 101, 'duplicate')
        self.assertEqual(bot.balance, balance)

    def test_break_even_covers_commissions_spread_slippage_and_financing(self):
        for direction in ('long', 'short'):
            with self.subTest(direction=direction):
                # Cada subescenario usa su propio contrato para no mezclar tablas.
                bot = self.open(direction)
                pos = bot.positions['BTC']
                with patch.object(bots.time, 'time', return_value=pos.entry_time+86400):
                    level = pos.break_even_price()
                    self.assertAlmostEqual(pos.unrealized(level), 0, places=9)
                    if direction == 'long':
                        self.assertGreater(level, pos.entry_price)
                    else:
                        self.assertLess(level, pos.entry_price)
                with bots._conn() as con:
                    con.execute('DELETE FROM bot_trades')

    def test_global_risk_denial_prevents_persisting_a_euro_entry(self):
        bot = bots.TradingBot(replace(bots.SCALPER, initial_balance=100))
        bot.load_state()
        bot.session = dict(currency='EUR', quoteToEuro={'USDT': .9})
        with patch.object(learning, 'size_multiplier', return_value=(1, None)), \
             patch.object(bots.systems, 'chart_overlays', return_value={}), \
             patch.object(portfolio_risk, 'assess', return_value=dict(allowed=False, reason='factor exposure')) as assess:
            bot._open(bots.SYMBOLS_BY_ID['BTC'], 'long', 100, 2, .8,
                      dict(reasons=[], candles=[], confirmations=[]), 2, 4, 1)
        self.assertFalse(bot.positions)
        self.assertEqual(bot.diagnostics['BTC']['reason'], 'factor exposure')
        self.assertAlmostEqual(assess.call_args.args[2], 1)
        self.assertTrue(assess.call_args.kwargs['reserve'])
        with bots._conn() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM bot_trades').fetchone()[0], 0)

    def test_global_reservation_released_when_persistence_fails(self):
        bot = bots.TradingBot(replace(bots.SCALPER, initial_balance=100))
        bot.load_state()
        bot.session = dict(currency='EUR', quoteToEuro={'USDT': .9})
        with patch.object(learning, 'size_multiplier', return_value=(1, None)), \
             patch.object(bots.systems, 'chart_overlays', return_value={}), \
             patch.object(portfolio_risk, 'assess', return_value=dict(allowed=True, token='reservation')), \
             patch.object(portfolio_risk, 'release') as release, \
             patch.object(bots, '_conn', side_effect=RuntimeError('disk unavailable')):
            with self.assertRaises(RuntimeError):
                bot._open(bots.SYMBOLS_BY_ID['BTC'], 'long', 100, 2, .8,
                          dict(reasons=[], candles=[], confirmations=[]), 2, 4, 1)
        release.assert_called_once_with('reservation')
        self.assertFalse(bot.positions)


if __name__ == '__main__':
    unittest.main()
