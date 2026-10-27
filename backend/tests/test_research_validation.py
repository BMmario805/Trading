import asyncio
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import data_archive, experiments, research, training
from app.economics import cost_profile, execution_fill, net_outcome
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
    def test_run_research_archives_exact_short_and_empty_inputs_in_its_isolated_registry_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            default=Path(folder)/'MUST-NOT-USE-GLOBAL-ARCHIVE'
            for count in (600,7,0):
                with self.subTest(bars=count):
                    ledger=Path(folder)/str(count)/'ledger.sqlite'
                    data=[candle(i,high=100,low=100) for i in range(count)]
                    with patch.object(research,'_bounded_history',AsyncMock(return_value=data)), \
                         patch.object(research.market_data,'_binance_get',AsyncMock()) as network, \
                         patch.object(data_archive,'ROOT',default):
                        report=asyncio.run(research.run_research(['BTC'],['5m'],max_bars=600,db_path=ledger))
                    network.assert_not_awaited()
                    result=report['results'][0]
                    self.assertEqual(result['bars'],count)
                    self.assertEqual(result['ok'],count>=600)
                    exp=experiments.get_experiment(result['experimentId'],ledger)
                    events=experiments.observations(exp['id'],research.time.time(),db_path=ledger)
                    receipts=[e['payload'] for e in events if e['kind']=='dataset_snapshot']
                    self.assertEqual(len(receipts),1)
                    receipt=receipts[0]
                    self.assertEqual(receipt['rowOffset'],0)
                    self.assertEqual(receipt['totalBars'],count)
                    self.assertEqual(receipt['inputDataHash'],exp['manifest']['dataset']['dataHash'])
                    raw=(ledger.parent/'research-data'/receipt['artifact']).read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(),receipt['sha256'])
                    self.assertEqual(json.loads(raw)['candles'],data)
                    self.assertEqual(json.loads(raw)['source'],'Binance Spot USDT')
                    self.assertFalse(default.exists())

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
        with patch.object(research, 'simulate', side_effect=replay), \
             patch.object(research,'deflated_sharpe',return_value=dict(passed=True,probability=.99)):
            result = research.evaluate_dataset(data, 'BTC', '5m')
        holdout = tuple(result['protocol']['holdout'])
        final_calls = [call for call in calls if call == holdout]
        self.assertEqual(len(final_calls), 2)  # candidato congelado + estrés, no 12 candidatos
        self.assertTrue(result['selected'])
        self.assertFalse(result['promotionApplied'])
        self.assertFalse(result['candidateEligible'])  # solo diez cierres en holdout

    def test_failed_dsr_never_reads_or_claims_the_holdout_and_records_all_trials(self):
        calls=[]
        def replay(*args,**kwargs):
            start,end=args[12:14];calls.append((start,end))
            return dict(pnl=3,trades=10,maxDrawdown=1,tradePnls=[.3]*10,
                        periodPnls=[.3,-.1,.2]*20,expectancy=.3,wins=10,winRate=100,profitFactor=None)
        with tempfile.TemporaryDirectory() as folder:
            ledger=Path(folder)/'isolated.sqlite'
            for plan in ('earlier','current'):
                exp=experiments.register_experiment(plan,dict(parameters={'fixed':1},
                    costModel=cost_profile('binance','cripto'),trialCount=len(REGISTRY)),db_path=ledger)
            with patch.object(research,'simulate',side_effect=replay), \
                 patch.object(research,'deflated_sharpe',return_value=dict(passed=False,probability=.1)) as dsr:
                result=research.evaluate_dataset(rows(),'BTC','5m',ledger_context=dict(experimentId=exp['id'],dbPath=ledger))
            self.assertEqual(dsr.call_args.kwargs['total_trials'],2*len(REGISTRY))
            self.assertEqual(len(dsr.call_args.args[1]),len(REGISTRY))
            self.assertNotIn(tuple(result['protocol']['holdout']),calls)
            self.assertIsNone(result['holdout'])
            events=experiments.observations(exp['id'],research.time.time(),db_path=ledger)
            trials=next(e for e in events if e['kind']=='selection_trials')['payload']
            self.assertEqual(len(trials['sharpes']),len(REGISTRY))
            self.assertTrue(trials['winnerFound'])
            self.assertFalse(any(e['kind']=='holdout_claim' for e in events))

    def test_failed_search_is_still_persisted_and_exploration_does_not_create_a_ledger(self):
        data=[candle(i,high=100,low=100) for i in range(900)]
        with tempfile.TemporaryDirectory() as folder:
            default=Path(folder)/'default-MUST-NOT-EXIST.sqlite'
            ledger=Path(folder)/'explicit.sqlite'
            with patch.object(experiments,'DB_PATH',default):
                result=research.evaluate_dataset(data,'BTC','5m')
                self.assertFalse(default.exists())
                exp=experiments.register_experiment('failed-search',dict(parameters={'fixed':1},
                    costModel=cost_profile('binance','cripto'),trialCount=len(REGISTRY)),db_path=ledger)
                result=research.evaluate_dataset(data,'BTC','5m',ledger_context=dict(experimentId=exp['id'],dbPath=ledger))
                events=experiments.observations(exp['id'],research.time.time(),db_path=ledger)
                selection=next(e for e in events if e['kind']=='selection_trials')['payload']
                self.assertFalse(selection['winnerFound'])
                self.assertEqual(len(selection['sharpes']),len(REGISTRY))
                self.assertIsNone(result['holdout'])
                self.assertEqual(experiments.accumulated_trials(db_path=ledger),len(REGISTRY))
                self.assertFalse(default.exists())

    def test_registry_backed_research_has_one_final_claim_without_promoting_history(self):
        data=rows()
        def replay(*args,**kwargs):
            return dict(pnl=3,trades=30,maxDrawdown=1,tradePnls=[.3]*30,
                        periodPnls=[.3,-.1,.2]*20,expectancy=.3,wins=30,winRate=100,profitFactor=None)
        with tempfile.TemporaryDirectory() as folder:
            ledger=Path(folder)/'explicit.sqlite'
            exp=experiments.register_experiment('fixed-families',dict(parameters={'registry':'fixed'},
                costModel=cost_profile('binance','cripto'),trialCount=len(REGISTRY)),db_path=ledger)
            with patch.object(research,'simulate',side_effect=replay), \
                 patch.object(research,'deflated_sharpe',return_value=dict(passed=True,probability=.99)):
                first=research.evaluate_dataset(data,'BTC','5m',ledger_context=dict(experimentId=exp['id'],dbPath=ledger))
                second=research.evaluate_dataset(data,'BTC','5m',ledger_context=dict(experimentId=exp['id'],dbPath=ledger))
            self.assertIsNotNone(first['holdout'])
            self.assertFalse(first['promotion']['promotable'])
            self.assertFalse(first['promotion']['independentFuture'])
            self.assertIsNone(second['holdout'])
            self.assertTrue(second['holdoutClaim']['reused'])
            self.assertTrue(experiments.verify_ledger(ledger))

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
