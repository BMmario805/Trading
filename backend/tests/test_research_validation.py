import asyncio
import json
import math
import unittest
from unittest.mock import AsyncMock, patch

from app import research, training
from app.economics import execution_fill, net_outcome
from app.research_strategies import REGISTRY, signal_series
from app.strategy import volatility_risk_series


def candle(i, price=100, step=300, high=None, low=None, close=None):
    return dict(time=i * step, open=price, high=price + .1 if high is None else high,
                low=price - .1 if low is None else low,
                close=price if close is None else close, volume=100)


def rows(n=1200):
    return [candle(i, 100 + .001 * i + 2 * math.sin(i / 15),
                   high=101 + .001 * i + 2 * math.sin(i / 15),
                   low=99 + .001 * i + 2 * math.sin(i / 15)) for i in range(n)]


class CausalStrategiesTests(unittest.TestCase):
    def test_every_registered_family_is_prefix_invariant(self):
        data = rows(700)
        for strategy in REGISTRY:
            with self.subTest(strategy=strategy.id):
                prefix = signal_series(strategy, data[:600])
                full = signal_series(strategy, data)
                self.assertEqual(prefix, full[:600])
                self.assertTrue(all(-1 <= score <= 1 for score in full))

    def test_volatility_target_reduces_shock_without_adding_leverage(self):
        data = [candle(i, 100 + .01 * math.sin(i)) for i in range(180)]
        data += [candle(i, 100 + 4 * math.sin(i)) for i in range(180, 205)]
        multipliers = volatility_risk_series(data)
        self.assertLess(multipliers[-1], .8)
        self.assertTrue(all(.25 <= m <= 1 for m in multipliers))
        self.assertEqual(multipliers[:190], volatility_risk_series(data[:190]))

    def test_flat_data_produces_no_false_signals_or_nonfinite_metrics(self):
        data = [candle(i, high=100, low=100) for i in range(900)]
        result = research.evaluate_dataset(data, 'BTC', '5m')
        self.assertTrue(result['ok'])
        self.assertIsNone(result['selected'])
        self.assertIsNone(result['holdout'])
        self.assertFalse(result['promotionApplied'])
        self.assertEqual(len(result['candidates']), len(REGISTRY))
        json.dumps(result, allow_nan=False)


class NetSimulationTests(unittest.TestCase):
    def simulate(self, data, model, **kwargs):
        return training.simulate(data, [0, 1, 1], [2] * 3, 0,
                                 .5, 1, 3, 1, 100, 1, 1, 100, 0, 3,
                                 cost_model=model, **kwargs)

    def test_fees_slippage_and_carry_reconcile_exactly(self):
        model = dict(feeRate=.001, slippageRate=.0002, spreadPct=.0005, carryDailyRate=.0003)
        data = [candle(0), candle(1), candle(2, high=101, low=100, close=101)]
        result = self.simulate(data, model)
        entry = execution_fill(100, 1, True, .0005, .0002)
        exit_price = execution_fill(101, 1, False, .0005, .0002)
        # Carry de la permanencia máxima se reserva en el tamaño. El cierre
        # terminal en la misma marca temporal no inventa días de financiación.
        unit = training.planned_trade(entry, entry - 2, entry + 6, 1, 1,
                                      .001, .0002, .0005, carry=entry * .0003 * 100 * 300 / 86400)
        qty = min(1 / unit['netRisk'], 100 / entry)
        expected = net_outcome(entry, exit_price, qty, 1, entry * qty * .001, .001)
        self.assertEqual(result['pnl'], round(expected, 2))
        self.assertGreater(result['fees'], 0)

    def test_unprofitable_net_target_cannot_open(self):
        data = [candle(i, high=100, low=100) for i in range(3)]
        result = training.simulate(data, [0, 1, 1], [.01] * 3, 0,
                                   .5, 1, 3, 1, 20, 1, 1, 100, 0, 3,
                                   cost_model=dict(feeRate=.001, slippageRate=.0002, spreadPct=0))
        self.assertEqual(result['trades'], 0)

    def test_spot_disallows_short_and_multiplier_can_disable_entry(self):
        data = [candle(i) for i in range(3)]
        result = training.simulate(data, [0, -1, -1], [2] * 3, 0,
                                   .5, 1, 3, 1, 20, 1, 1, 100, 0, 3, allow_short=False)
        self.assertEqual(result['trades'], 0)
        result = self.simulate(data, dict(feeRate=0, spreadPct=0), risk_multipliers=[1, 0, 0])
        self.assertEqual(result['trades'], 0)

    def test_invalid_cost_model_is_rejected(self):
        with self.assertRaises(ValueError):
            self.simulate([candle(i) for i in range(3)], dict(feeRate=float('nan')))

    def test_long_and_short_break_even_stop_cover_both_fees_exactly(self):
        for sign in (1, -1):
            with self.subTest(direction=sign):
                advance = 100 + sign * 3
                data = [candle(0), candle(1),
                        candle(2, high=max(100, advance), low=min(100, advance), close=advance),
                        candle(3, advance, high=103 if sign > 0 else 99.9,
                               low=100.1 if sign > 0 else 97, close=101 if sign > 0 else 99)]
                result = training.simulate(data, [0, sign, sign, sign], [2] * 4, 0,
                                           .5, 1, 3, 10, 100, 1, 1, 100, 0, 4,
                                           cost_model=dict(feeRate=.001, slippageRate=0, spreadPct=0))
                self.assertEqual(result['trades'], 1)
                self.assertAlmostEqual(result['tradePnls'][0], 0, places=10)


class TemporalValidationTests(unittest.TestCase):
    def test_protocol_has_separate_discovery_and_final_holdout(self):
        protocol = training.temporal_protocol(2000, max_hold_bars=48)
        self.assertEqual(protocol['selection'], (0, 900))
        self.assertEqual(protocol['embargo'], 48)
        self.assertGreaterEqual(protocol['holdout'][0], 1600 + 48)
        windows = [protocol['selection']] + protocol['folds'] + [protocol['holdout']]
        for prior, following in zip(windows, windows[1:]):
            self.assertGreaterEqual(following[0] - prior[1], 48)

    def test_research_selection_never_reads_holdout_for_other_candidates(self):
        data = rows()
        calls = []
        def replay(*args, **kwargs):
            start, end = args[12:14]
            calls.append((start, end))
            return dict(pnl=3, trades=10, maxDrawdown=1, tradePnls=[.3] * 10,
                        expectancy=.3, wins=10, winRate=100, profitFactor=None)
        with patch.object(research, 'simulate', side_effect=replay):
            result = research.evaluate_dataset(data, 'BTC', '5m')
        holdout = tuple(result['protocol']['holdout'])
        final_calls = [call for call in calls if call == holdout]
        self.assertEqual(len(final_calls), 2)  # candidato congelado + estrés, no 12 candidatos
        self.assertTrue(result['selected'])
        self.assertFalse(result['promotionApplied'])
        self.assertFalse(result['candidateEligible'])  # solo diez cierres en holdout

    def test_changed_final_prices_do_not_change_selection_or_development(self):
        data = rows()
        changed = [dict(row) for row in data]
        for row in changed[int(len(data) * .8):]:
            for key in ('open', 'high', 'low', 'close'):
                row[key] *= 1.8
        before = research.evaluate_dataset(data, 'BTC', '5m')
        after = research.evaluate_dataset(changed, 'BTC', '5m')
        self.assertEqual(before['selected'], after['selected'])
        self.assertEqual(before['candidates'], after['candidates'])
        self.assertNotEqual(before['datasetHash'], after['datasetHash'])

    def test_uncertainty_is_deterministic_and_labels_selection_limit(self):
        a = training.expectancy_uncertainty([-.5, .3, -.2, .8, .2] * 4)
        self.assertEqual(a, training.expectancy_uncertainty([-.5, .3, -.2, .8, .2] * 4))
        self.assertLess(a['lower95'], a['mean'])
        self.assertFalse(a['selectionAdjusted'])

    def test_bounded_fetch_has_request_cap_and_discards_open_bar(self):
        now = 2_000_000
        raw = [[(now - i * 300) * 1000, '100', '101', '99', '100', '10']
               for i in range(999, -1, -1)]
        with patch.object(research.market_data, '_binance_get', AsyncMock(return_value=raw)) as fetch:
            data = asyncio.run(research._bounded_history('BTCUSDT', '5m', 7, 1000, now))
        self.assertEqual(fetch.await_count, 1)
        self.assertTrue(all(row['time'] + 300 <= now for row in data))

    def test_forex_and_proxy_are_not_misrepresented_as_spot_backtests(self):
        with patch.object(research.market_data, '_binance_get', AsyncMock()) as fetch:
            report = asyncio.run(research.run_research(['XAUUSD'], ['1h'], days=7, max_bars=600))
        fetch.assert_not_awaited()
        self.assertFalse(report['results'][0]['ok'])


if __name__ == '__main__':
    unittest.main()
