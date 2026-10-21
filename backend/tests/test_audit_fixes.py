import asyncio
import json
import math
import sqlite3
import tempfile
import time
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import bots, history, learning, training
from app.database import Connection


class TemporaryDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path = Path(tmp) / 'audit.db'
        for module in (bots, history, learning, training):
            self.stack.enter_context(patch.object(module, 'DB_PATH', self.path))
        for cache in (learning._policies, learning._edges, learning._models, training.STATUS):
            self.stack.enter_context(patch.dict(cache, {}, clear=True))
        with bots._conn():
            pass

    def trade(self, exit_time, bot='scalper', pnl=-10, status='closed', snapshot=None):
        with bots._conn() as con:
            con.execute(
                '''INSERT INTO bot_trades
                   (bot, symbol, direction, qty, entry_price, entry_time, sl, tp,
                    reasons, exit_time, pnl, status, initial_risk_dist, snapshot)
                   VALUES (?, 'BTC', 'long', 1, 100, ?, 90, 120, '[]', ?, ?, ?, 10, ?)''',
                (bot, exit_time - 60, exit_time, pnl, status,
                 json.dumps(snapshot) if snapshot is not None else None),
            )


class TrainingAttemptTests(TemporaryDatabaseTests):
    def run_training(self, source):
        bot = bots.TradingBot(replace(bots.SWING, universe=('BTC',)))
        training.STATUS['swing'] = dict(results=[])
        with patch.object(training, 'fetch_history', source), \
             patch.object(training.time, 'time', return_value=100000), \
             patch.object(training, 'GRID_THRESHOLD', (.5,)), \
             patch.object(training, 'GRID_SL', (1,)), \
             patch.object(training, 'GRID_TP', (2,)), \
             patch.object(training, 'GRID_TRAIL', (1,)), \
             patch.object(learning, 'refresh'):
            asyncio.run(training._train_bot(bot, ['BTC']))
        return training.STATUS['swing']['results'][0]

    def save_previous(self):
        with training._conn() as con:
            con.execute('INSERT INTO bot_params VALUES (?,?,?,?,?,?)',
                        ('swing', 'BTC', '{}', json.dumps(dict(engineVersion=training.ENGINE_VERSION)), 1, 99000))

    def test_negative_evaluation_waits_full_cycle_and_revokes_previous(self):
        self.save_previous()
        rows = [dict(time=i*60, open=100, high=100, low=100, close=100, volume=10)
                for i in range(600)]
        result = self.run_training(AsyncMock(return_value=rows))
        self.assertFalse(result['ok'])
        self.assertEqual(result['engineVersion'], training.ENGINE_VERSION)
        self.assertFalse(training.load_results()[0]['applied'])
        with training._conn() as con:
            self.assertEqual(con.execute('SELECT ok FROM bot_training_attempts').fetchone()[0], 1)
        for elapsed in (1, 3600, 12*3600 - 1):
            self.assertEqual(training.due_symbols('swing', ('BTC',), 12, 100000+elapsed), [])
        self.assertEqual(training.due_symbols('swing', ('BTC',), 12, 100000+12*3600), ['BTC'])

    def test_network_failure_keeps_parameters_and_retries_after_one_hour(self):
        self.save_previous()
        result = self.run_training(AsyncMock(side_effect=RuntimeError('offline')))
        self.assertFalse(result['ok'])
        self.assertNotIn('engineVersion', result)
        self.assertIn(('swing', 'BTC'), training.load_applied_params())
        self.assertEqual(training.due_symbols('swing', ('BTC',), 12, 103599), [])
        self.assertEqual(training.due_symbols('swing', ('BTC',), 12, 103600), ['BTC'])

    def test_insufficient_history_uses_short_backoff(self):
        result = self.run_training(AsyncMock(return_value=[]))
        self.assertFalse(result['ok'])
        self.assertNotIn('engineVersion', result)
        self.assertEqual(training.due_symbols('swing', ('BTC',), 12, 103599), [])
        self.assertEqual(training.due_symbols('swing', ('BTC',), 12, 103600), ['BTC'])

    def test_newer_result_supersedes_an_older_source_error(self):
        self.save_previous()
        with training._conn() as con:
            con.execute('INSERT INTO bot_training_attempts VALUES (?,?,?,?)', ('swing', 'BTC', 98000, 0))
        self.assertEqual(training.due_symbols('swing', ('BTC',), 12, 100000), [])


class LearningWindowTests(TemporaryDatabaseTests):
    def test_statistics_survive_long_shutdown_and_expire_by_last_close(self):
        last_exit = 1000000
        for age in (0, 1, 7*86400, 7*86400 + 1):
            self.trade(last_exit - age)
        self.trade(last_exit + 30*86400, bot='swing')
        self.trade(last_exit + 30*86400, pnl=None)
        self.trade(last_exit + 30*86400, status='open')
        with patch.object(learning.time, 'time', return_value=last_exit + 60*86400):
            stats = learning.analyze('scalper')
        self.assertEqual(stats['trades'], 3)
        self.assertEqual(stats['bySymbol']['BTC']['pnl'], -30)
        self.assertEqual(learning.analyze('scalper', window_days=1)['trades'], 2)

    def test_empty_bot_has_empty_statistics(self):
        self.assertEqual(learning.analyze('scalper')['trades'], 0)

    def test_first_load_recomputes_stale_persisted_policy(self):
        for i in range(10):
            self.trade(1000000 + i)
        with learning._conn() as con:
            con.execute('INSERT INTO bot_learning VALUES (?,?,?)',
                        ('scalper', json.dumps(dict(stats=dict(trades=1), blocks=[], sizeMult={})), 0))
        policy = learning.load('scalper')
        self.assertEqual(policy['stats']['trades'], 10)
        self.assertEqual(policy['sizeMult']['BTC'], .5)
        self.assertTrue(any(b['symbol'] == 'BTC' for b in policy['blocks']))
        with patch.object(learning, 'refresh') as refresh:
            self.assertIs(learning.load('scalper'), policy)
            refresh.assert_not_called()


class MetaFeaturesTests(TemporaryDatabaseTests):
    def confirmations(self, vote):
        return [dict(system=system, vote=vote) for system in learning.NON_CAUSAL_CONFIRMATIONS]

    def test_non_causal_votes_cannot_change_features_or_mature_model_decisions(self):
        learning._models['scalper'] = {
            **learning._new_model(), 'n': 46,
            'w': {f'conf:{system}': 100 for system in learning.NON_CAUSAL_CONFIRMATIONS},
        }
        for direction in ('long', 'short'):
            base = learning.meta_features('scalper', 'crypto', direction, 12,
                                          [dict(system='MACD', vote=.7)])
            for vote in (-1, 0, 1):
                feats = learning.meta_features('scalper', 'crypto', direction, 12,
                                               [dict(system='MACD', vote=.7)] + self.confirmations(vote))
                self.assertEqual(feats, base)
                self.assertEqual(learning.meta_predict('scalper', feats), .5)
                self.assertEqual(learning.meta_gate('scalper', feats)[:2], (True, .5))
            self.assertAlmostEqual(base['conf:MACD'], .7 if direction == 'long' else -.7)

    def test_persisted_contaminated_model_is_rebuilt_from_filtered_snapshots(self):
        for i in range(32):
            self.trade(1000000 + i, pnl=10, snapshot={
                'engineVersion': training.ENGINE_VERSION,
                'confirmations': [dict(system='MACD', vote=1)] + self.confirmations(1),
            })
        contaminated = {**learning._new_model(), 'n': 46, 'lastTradeId': 999,
                        'w': {'bias': 50, 'conf:Opinión pública': 100}}
        contaminated.pop('featureVersion')
        with learning._conn() as con:
            con.execute('INSERT INTO bot_model VALUES (?,?,?)', ('scalper', json.dumps(contaminated), 0))
        self.assertEqual(learning.meta_learn_new_trades('scalper'), 32)
        model = learning._load_model('scalper')
        self.assertEqual(model['n'], 32)
        self.assertLess(abs(model['w']['bias']), 2)
        self.assertIn('conf:MACD', model['w'])
        for system in learning.NON_CAUSAL_CONFIRMATIONS:
            self.assertNotIn(f'conf:{system}', model['w'])
        learned = json.loads(json.dumps(model))
        learning._models.clear()
        self.assertEqual(learning._load_model('scalper'), learned)
        self.assertEqual(learning.meta_learn_new_trades('scalper'), 0)


class HistoryConnectionTests(TemporaryDatabaseTests):
    def test_history_uses_wal_and_closes_its_connection(self):
        with history._conn() as con:
            self.assertIsInstance(con, Connection)
            self.assertEqual(con.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(con.execute('PRAGMA busy_timeout').fetchone()[0], 5000)
        with self.assertRaises(sqlite3.ProgrammingError):
            con.execute('SELECT 1')

    def test_history_remains_readable_during_another_writer_transaction(self):
        history.record('BTC', 60, 10)
        with bots._conn() as writer:
            writer.execute("INSERT INTO bot_state VALUES ('scalper', 10000, 1)")
            self.assertEqual(len(history.get_history('BTC')), 1)

    def test_history_rolls_back_failed_transaction(self):
        with self.assertRaises(RuntimeError):
            with history._conn() as con:
                con.execute("INSERT INTO sentiment_history VALUES ('BTC', 0, 60, 10)")
                raise RuntimeError('abort')
        with history._conn() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM sentiment_history').fetchone()[0], 0)


class ProbationSizingTests(TemporaryDatabaseTests):
    def open_probation(self, bot, learn_mult=1, p_win=None):
        with patch.object(learning, 'size_multiplier', return_value=(learn_mult, None)), \
             patch.object(bots.systems, 'chart_overlays', return_value={}):
            bot._open(bots.SYMBOLS_BY_ID['BTC'], 'long', 100, 2, .8,
                      dict(reasons=[], candles=[], confirmations=[]), 2, 4, 1,
                      p_win=p_win, probation_why='Sin validar')
        return bot.positions['BTC']

    def test_probation_risk_cannot_be_amplified_above_configured_fraction(self):
        bot = bots.TradingBot(bots.SCALPER)
        pos = self.open_probation(bot, learn_mult=1.5, p_win=.99)
        risk_with_spread = pos.initial_risk()
        self.assertAlmostEqual(risk_with_spread, bot.balance * bot.cfg.risk_pct * .15)

    def test_loss_streak_can_reduce_probation_risk_further(self):
        bot = bots.TradingBot(bots.SCALPER)
        bot.consec_losses = 2
        pos = self.open_probation(bot)
        risk_with_spread = pos.initial_risk()
        self.assertAlmostEqual(risk_with_spread, bot.balance * bot.cfg.risk_pct * .075)

    def test_restart_preserves_probation_slots(self):
        bot = bots.TradingBot(bots.SCALPER)
        self.open_probation(bot)
        restored = bots.TradingBot(bots.SCALPER)
        restored.load_state()
        self.assertIn('BTC', restored.probation_open)

    def test_one_probation_position_stops_further_exploration(self):
        bot = bots.TradingBot(replace(bots.SCALPER, universe=('BTC',)))
        bot.probation_open.add('ETH')
        with patch.object(learning, 'entry_mode', return_value=('probation', 'Sin validar')), \
             patch.object(bot, '_evaluate', AsyncMock()) as evaluate:
            asyncio.run(bot._scan())
            evaluate.assert_not_awaited()
        self.assertIn('Límite de posiciones a prueba', bot.diagnostics['BTC']['reason'])

    def test_unknown_pair_does_not_apply_a_second_exploration_discount(self):
        learning._policies['scalper'] = dict(sizeMult={})
        self.assertEqual(learning.size_multiplier('scalper', 'BTC'), (1.0, None))


class MetaEntryDiagnosticsTests(TemporaryDatabaseTests):
    def scan(self, probability):
        bot = bots.TradingBot(replace(bots.SCALPER, universe=('EURUSD',)))
        signal = dict(score=.8, stale=False, atr=2, price=100, prev=0,
                      recent_prev=[0]*3, extension=0, confirmations=[])
        learning._models['scalper'] = {
            **learning._new_model(), 'n': 46,
            'w': {'bias': math.log(probability/(1-probability))},
        }
        with patch.object(learning, 'entry_mode', return_value=('probation', 'Sin validar')), \
             patch.object(bot, '_evaluate', AsyncMock(return_value=signal)), \
             patch.object(bots.market_data, 'get_price', AsyncMock(return_value=dict(price=100, time=time.time()))), \
             patch.object(bot, '_open') as open_position:
            asyncio.run(bot._scan())
        return bot, open_position

    def test_mature_model_can_admit_probation_above_thirty_percent(self):
        for probability in (.31, .42):
            with self.subTest(probability=probability):
                _, open_position = self.scan(probability)
                open_position.assert_called_once()

    def test_probation_rejection_reports_probability_and_threshold(self):
        bot, open_position = self.scan(.27)
        open_position.assert_not_called()
        self.assertIn('27.0%', bot.diagnostics['EURUSD']['reason'])
        self.assertIn('30%', bot.diagnostics['EURUSD']['reason'])

    def test_general_meta_rejection_reports_probability_and_threshold(self):
        bot, open_position = self.scan(.20)
        open_position.assert_not_called()
        self.assertIn('20%', bot.diagnostics['EURUSD']['reason'])
        self.assertIn('25%', bot.diagnostics['EURUSD']['reason'])


class ManualParameterTests(TemporaryDatabaseTests):
    def save(self, metrics, applied=0):
        params = dict(entryThreshold=.35, slAtr=2, tpAtr=3, trailAtr=1)
        with training._conn() as con:
            con.execute('INSERT OR REPLACE INTO bot_params VALUES (?,?,?,?,?,?)',
                        ('swing', 'BTC', json.dumps(params), json.dumps(metrics), applied, 10000))

    def test_manual_parameters_are_loaded_only_for_probation(self):
        self.save(dict(engineVersion=training.ENGINE_VERSION, manualAdjustment=dict(previousThreshold=.75)))
        bot = bots.TradingBot(bots.SWING)
        bot.reload_params()
        self.assertEqual(bot._exec_params(bots.SYMBOLS_BY_ID['BTC']), (.35, 2, 3, 1))
        self.assertNotIn('BTC', bot.trained_params)
        self.assertFalse(training.load_results()[0]['applied'])
        learning._edges['swing'] = learning._load_edges('swing')
        with patch.object(learning, '_active_blocks', return_value=[]):
            self.assertEqual(learning.entry_mode('swing', 'BTC')[0], 'probation')

    def test_rejected_or_old_engine_parameters_are_not_manual_overrides(self):
        for metrics in (dict(engineVersion=training.ENGINE_VERSION),
                        dict(engineVersion=training.ENGINE_VERSION-1, manualAdjustment=True)):
            with self.subTest(metrics=metrics):
                self.save(metrics)
                self.assertEqual(training.load_manual_params(), {})

    def test_revalidation_replaces_manual_parameters(self):
        self.save(dict(engineVersion=training.ENGINE_VERSION, manualAdjustment=True))
        bot = bots.TradingBot(bots.SWING)
        bot.reload_params()
        self.assertIn('BTC', bot.manual_params)
        self.save(dict(engineVersion=training.ENGINE_VERSION), applied=1)
        bot.reload_params()
        self.assertNotIn('BTC', bot.manual_params)
        self.assertIn('BTC', bot.trained_params)
