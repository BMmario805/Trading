import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

from app.mt5_gateway import DemoGateway, DemoOnlyError, lot_floor
from app import bots, learning, mt5_demo, mt5_performance, training
from app.execution import closed_candles


def setup_api():
    api = MagicMock()
    for name, value in dict(ACCOUNT_TRADE_MODE_DEMO=0, ACCOUNT_MARGIN_MODE_RETAIL_HEDGING=2,
                            SYMBOL_TRADE_MODE_FULL=4, ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1,
                            TRADE_ACTION_DEAL=1, ORDER_TIME_GTC=0, ORDER_FILLING_IOC=1,
                            ORDER_FILLING_FOK=0, ORDER_FILLING_RETURN=2, SYMBOL_TRADE_EXECUTION_MARKET=2,
                            TRADE_RETCODE_DONE=10009, TRADE_RETCODE_DONE_PARTIAL=10010,
                            TRADE_RETCODE_TIMEOUT=10012, TRADE_RETCODE_CONNECTION=10031,
                            TRADE_RETCODE_PLACED=10008).items():
        setattr(api, name, value)
    account = NS(login=123, server='Test-Demo', trade_mode=0, margin_mode=2, trade_allowed=True,
                 trade_expert=True, equity=100000, margin_free=100000)
    terminal = NS(connected=True, trade_allowed=True, tradeapi_disabled=False)
    api.account_info.return_value = account
    api.terminal_info.return_value = terminal
    api.positions_get.return_value = ()
    api.symbol_select.return_value = True
    api.symbol_info.return_value = NS(trade_mode=4, trade_stops_level=10, point=.01, trade_tick_size=.01,
                                     digits=2, volume_step=.01, volume_min=.01, volume_max=100,
                                     filling_mode=3, trade_exemode=2)
    api.symbol_info_tick.return_value = NS(time=1000, bid=100, ask=100.02)
    api.order_calc_profit.side_effect = lambda kind, symbol, volume, price, stop: (stop-price)*volume*100*(1 if kind==0 else -1)
    api.order_calc_margin.return_value = 100
    api.order_check.return_value = NS(retcode=0)
    config = dict(login=123, server='Test-Demo', max_positions=3, risk_pct=.001, max_open_risk_pct=.01)
    return api, config, account, terminal


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.api, self.config, self.account, self.terminal = setup_api()
        self.gateway = DemoGateway(self.api, self.config)

    def test_real_account_cannot_send(self):
        self.account.trade_mode = 2
        with self.assertRaises(DemoOnlyError): self.gateway.send({})
        self.api.order_send.assert_not_called()

    def test_account_switch_cannot_send(self):
        self.account.login = 456
        with self.assertRaises(DemoOnlyError): self.gateway.send({})
        self.api.order_send.assert_not_called()

    def test_algo_disabled_cannot_send(self):
        self.terminal.trade_allowed = False
        with self.assertRaises(DemoOnlyError): self.gateway.send({})
        self.api.order_send.assert_not_called()

    def test_tick_accepts_server_clock_skew(self):
        self.api.symbol_info_tick.return_value.time = 1000 + 3 * 3600
        self.assertEqual(self.gateway.tick('XAUUSD', 1000, 3 * 3600).bid, 100)

    def test_position_query_failure_blocks_entries(self):
        self.api.positions_get.return_value = None
        with self.assertRaises(DemoOnlyError): self.entry()

    def entry(self):
        return self.gateway.prepare_entry('XAUUSD', 1, 2, 2, 3, 1234, 'test', 1000)

    def test_sizing_uses_broker_profit_and_never_sends_during_check(self):
        request, risk = self.entry()
        loss = abs(self.api.order_calc_profit(request['type'], 'XAUUSD', request['volume'], request['price'], request['sl']))
        self.assertLessEqual(loss*1.1, 100)
        self.assertGreater(request['tp'], request['price'])
        self.assertLess(request['sl'], request['price'])
        self.assertGreater(risk, 0)
        self.api.order_send.assert_not_called()

    def test_min_lot_never_forced_above_risk_budget(self):
        self.account.equity = 1
        with self.assertRaises(DemoOnlyError): self.entry()

    def test_existing_manual_symbol_blocks_new_position(self):
        self.api.positions_get.return_value = (NS(symbol='XAUUSD'),)
        with self.assertRaises(DemoOnlyError): self.entry()

    def test_stale_tick_blocks_entry(self):
        self.api.symbol_info_tick.return_value.time = 1
        with self.assertRaises(DemoOnlyError): self.entry()

    def test_switch_after_order_check_is_still_blocked(self):
        request, _ = self.entry()
        self.account.trade_mode = 2
        with self.assertRaises(DemoOnlyError): self.gateway.send(request)
        self.api.order_send.assert_not_called()

    def test_manual_position_without_stop_blocks_risk(self):
        self.api.positions_get.return_value = (NS(symbol='EURUSD', sl=0),)
        with self.assertRaises(DemoOnlyError): self.entry()

    def test_exhausted_portfolio_risk_blocks_new_position(self):
        self.api.positions_get.return_value = (NS(symbol='EURUSD', sl=1, type=0, volume=100, price_open=10),)
        with self.assertRaises(DemoOnlyError): self.entry()

    def test_check_rejection_does_not_send(self):
        self.api.order_check.return_value = NS(retcode=10030)
        with self.assertRaises(DemoOnlyError): self.entry()
        self.api.order_send.assert_not_called()

    def test_lot_rounds_down(self):
        self.assertEqual(lot_floor(.129, .01), .12)

    def test_probation_uses_one_quarter_of_the_risk_budget(self):
        full, _ = self.entry()
        probation, _ = self.gateway.prepare_entry('XAUUSD', 1, 2, 2, 3, 1234, 'test', 1000, risk_mult=.25)
        loss = abs(self.api.order_calc_profit(probation['type'], 'XAUUSD', probation['volume'], probation['price'], probation['sl']))
        self.assertLessEqual(loss*1.1, self.account.equity*self.config['risk_pct']*.25)
        self.assertLessEqual(probation['volume'], full['volume']*.25)
        self.api.order_send.assert_not_called()

    def test_probation_cannot_force_minimum_lot_above_its_reduced_budget(self):
        self.account.equity = 5000
        self.entry()
        with self.assertRaises(DemoOnlyError):
            self.gateway.prepare_entry('XAUUSD', 1, 2, 2, 3, 1234, 'test', 1000, risk_mult=.25)

    def test_invalid_risk_multiplier_cannot_increase_exposure(self):
        for mult in (0, -1, 1.01, float('nan'), float('inf')):
            with self.subTest(mult=mult), self.assertRaises(DemoOnlyError):
                self.gateway.prepare_entry('XAUUSD', 1, 2, 2, 3, 1234, 'test', 1000, risk_mult=mult)


class ProbationEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_patch = patch.object(mt5_demo, 'DB_PATH', Path(self.tmp.name)/'test.sqlite')
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        perf_patch=patch.object(mt5_performance,'DB_PATH',Path(self.tmp.name)/'test.sqlite')
        perf_patch.start()
        self.addCleanup(perf_patch.stop)
        self.state_patch = patch.dict(mt5_demo.STATE, {}, clear=True)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.api, self.config, self.account, _ = setup_api()
        self.account.balance = self.account.equity
        self.account.currency = 'USD'
        self.config.update(terminal='test', enabled=True, max_daily_loss_pct=.02, symbols={'XAUUSD': 'XAUUSD'})
        self.bot = replace(bots.SCALPER, universe=('XAUUSD',))
        self.engine = mt5_demo.DemoEngine(self.config, self.api)
        self.addCleanup(self.engine.worker.shutdown)
        self.rows = [dict(time=i*60, open=100, high=101, low=99, close=100, volume=10) for i in range(61)]

    def cycle(self, bot=None, score=.8):
        with patch.object(mt5_demo, 'ALL_BOTS', (bot or self.bot,)), \
             patch.object(mt5_demo.time, 'time', return_value=3660), \
             patch.object(self.engine, 'update_clock_skew'), \
             patch.object(self.engine, 'manage'), patch.object(self.engine, 'sync_performance'), \
             patch.object(self.engine, 'training'), \
             patch.object(self.engine, 'bars', return_value=self.rows), \
             patch.object(mt5_demo, 'score_series', return_value=[0]*60+[score]), \
             patch.object(mt5_demo, 'extension_atr', return_value=0), \
             patch.object(mt5_demo, 'atr_series', return_value=[2]*61), \
             patch.object(self.engine.gateway, 'tick', return_value=NS(bid=100, ask=100.02)), \
             patch.object(self.engine.gateway, 'prepare_entry', return_value=({}, 2)) as prepare, \
             patch.object(self.engine, 'submit', return_value=NS(retcode=self.api.TRADE_RETCODE_DONE)) as submit:
            self.engine.cycle()
        return prepare, submit

    def test_open_position_snapshot_has_prices_and_utc_timestamps(self):
        self.engine.clock_skew = 3 * 3600
        self.api.POSITION_TYPE_BUY = 0
        self.api.symbol_info_tick.return_value.time = 3660 + 3 * 3600
        self.api.positions_get.return_value = (NS(
            ticket=42, symbol='XAUUSD', magic=mt5_demo.MAGICS['scalper'], comment='cxp-scalper-old',
            type=0, volume=.01, price_open=100, price_current=101.25,
            time=3500 + 3 * 3600, sl=90, tp=120, profit=1.25),)
        self.cycle()
        row = mt5_demo.STATE['positions'][0]
        self.assertEqual(row['currentPrice'], 101.25)
        self.assertEqual(row['openedAt'], 3500)
        self.assertEqual(row['priceAt'], 3660)
        self.assertEqual(row['quantityUnit'], 'lots')

    def test_unvalidated_demo_pair_uses_reduced_risk(self):
        prepare, submit = self.cycle()
        prepare.assert_called_once()
        self.assertEqual(prepare.call_args.kwargs['risk_mult'], .35)
        self.assertTrue(prepare.call_args.args[6].startswith('cxp-'))
        submit.assert_called_once()
        self.assertIn('35%', self.engine.diagnostics['scalper/XAUUSD'])
        self.assertEqual(mt5_demo.STATE['probationRiskPct'], .035)

    def test_validated_demo_pair_uses_normal_budget_and_threshold(self):
        mt5_demo.save(self.engine.owner+'params:scalper/XAUUSD', dict(
            applied=True, engineVersion=training.ENGINE_VERSION, params=dict(entryThreshold=.5)))
        prepare, _ = self.cycle(score=.51)
        prepare.assert_called_once()
        self.assertEqual(prepare.call_args.kwargs['risk_mult'], 1)

    def test_probation_accepts_original_threshold_without_surcharge(self):
        prepare, submit = self.cycle(score=self.bot.entry_threshold*1.02)
        prepare.assert_called_once()
        submit.assert_called_once()

    def test_probation_still_requires_threshold_crossing(self):
        prepare, submit = self.cycle(score=self.bot.entry_threshold*.99)
        prepare.assert_not_called()
        submit.assert_not_called()
        self.assertIn('Sin cruce', self.engine.diagnostics['scalper/XAUUSD'])

    def test_strict_opt_in_still_blocks_unvalidated_demo_pair(self):
        prepare, submit = self.cycle(bot=replace(self.bot, require_validation=True))
        prepare.assert_not_called()
        submit.assert_not_called()
        self.assertIn('Esperando validación', self.engine.diagnostics['scalper/XAUUSD'])

    def test_probation_cap_survives_missing_metadata_after_restart(self):
        self.config['max_positions'] = 6
        self.api.positions_get.return_value = tuple(
            NS(ticket=i, symbol=f'TEST{i}', magic=mt5_demo.MAGICS['scalper'], comment='cxp-scalper-old',
               type=0, volume=.01, price_open=100, sl=90, tp=120, profit=0)
            for i in range(5))
        prepare, submit = self.cycle()
        prepare.assert_not_called()
        submit.assert_not_called()
        self.assertIn('Límite de posiciones a prueba', self.engine.diagnostics['scalper/XAUUSD'])

    def test_shared_limit_updates_all_markets_instead_of_leaving_stale_diagnostics(self):
        self.bot = replace(self.bot, universe=('XAUUSD', 'EURUSD'))
        self.config['symbols']['EURUSD'] = 'EURUSD'
        self.api.positions_get.return_value = tuple(
            NS(ticket=i, symbol=f'TEST{i}', magic=mt5_demo.MAGICS['scalper'], comment='cxp-scalper-old',
               type=0, volume=.01, price_open=100, sl=90, tp=120, profit=0)
            for i in range(3))
        self.engine.diagnostics['scalper/EURUSD'] = 'Sin cruce reciente de señal'
        prepare, submit = self.cycle()
        prepare.assert_not_called()
        submit.assert_not_called()
        for symbol in ('XAUUSD', 'EURUSD'):
            self.assertEqual(self.engine.diagnostics['scalper/'+symbol], 'Límite compartido de posiciones alcanzado')


class IntentTests(unittest.TestCase):
    def test_uncertain_send_is_never_repeated_after_restart(self):
        api, cfg, _, _ = setup_api()
        api.order_send.return_value = None
        with tempfile.TemporaryDirectory() as tmp, patch.object(mt5_demo, 'DB_PATH', Path(tmp)/'test.sqlite'):
            engine = mt5_demo.DemoEngine(cfg, api)
            try:
                engine.submit('entry:1', 'XAUUSD', {})
                engine.submit('entry:1', 'XAUUSD', {})
                restarted = mt5_demo.DemoEngine(cfg, api)
                try:
                    restarted.submit('entry:1', 'XAUUSD', {})
                    self.assertTrue(restarted.uncertain('XAUUSD'))
                finally: restarted.worker.shutdown()
                api.order_send.assert_called_once()
            finally: engine.worker.shutdown()


class BrokerClockTests(unittest.TestCase):
    def setUp(self):
        self.api, self.config, _, _ = setup_api()
        self.config['symbols'] = {'XAUUSD': 'XAUUSD'}
        self.engine = mt5_demo.DemoEngine(self.config, self.api)
        self.addCleanup(self.engine.worker.shutdown)

    def test_broker_candles_remain_fresh_after_utc_normalization(self):
        now = 4200
        for offset in (0, 7200, 10800):
            with self.subTest(offset=offset):
                self.engine.clock_skew = offset
                self.api.copy_rates_from_pos.return_value = [
                    dict(time=t+offset, open=100, high=101, low=99, close=100,
                         real_volume=0, tick_volume=10)
                    for t in (now-180, now-120, now-60, now)
                ]
                with patch.object(mt5_demo.time, 'time', return_value=now):
                    candles = self.engine.market_read('candles', '1m', 4)
                self.assertEqual([c['time'] for c in candles], [now-180, now-120, now-60])
                self.assertEqual(closed_candles(candles, 60, now), candles)
                self.assertEqual(candles[-1]['dataSource']['provider'], 'mt5')

    def test_most_recent_tick_prevents_other_symbols_appearing_in_the_future(self):
        now, offset = 1000000, 10800
        self.config['symbols']['EURUSD'] = 'EURUSD'
        ticks = {'XAUUSD': NS(time=now+offset-20, bid=100, ask=100.02),
                 'EURUSD': NS(time=now+offset, bid=1, ask=1.0001)}
        self.api.symbol_info_tick.side_effect = ticks.get
        with patch.object(mt5_demo.time, 'time', return_value=now):
            self.engine.update_clock_skew()
        self.assertEqual(self.engine.clock_skew, offset)
        self.assertEqual(self.engine.gateway.clock_skew, offset)
        for symbol in ticks:
            self.api.symbol_select.assert_any_call(symbol, True)
            self.assertIs(self.engine.gateway.tick(symbol, now), ticks[symbol])

    def test_stale_ticks_stay_stale_even_when_another_symbol_is_fresh(self):
        now, offset = 1000000, 7200
        self.config['symbols']['EURUSD'] = 'EURUSD'
        self.api.symbol_info_tick.side_effect = {
            'XAUUSD': NS(time=now+offset-600, bid=100, ask=100.02),
            'EURUSD': NS(time=now+offset, bid=1, ask=1.0001),
        }.get
        with patch.object(mt5_demo.time, 'time', return_value=now):
            self.engine.update_clock_skew()
        self.assertEqual(self.engine.clock_skew, offset)
        with self.assertRaises(DemoOnlyError):
            self.engine.gateway.tick('XAUUSD', now)

    def test_invalid_or_unselectable_ticks_do_not_replace_existing_skew(self):
        self.engine.clock_skew = self.engine.gateway.clock_skew = 7200
        self.config['symbols'].update(EURUSD='EURUSD', GBPUSD='GBPUSD')
        self.api.symbol_select.side_effect = lambda symbol, selected: symbol != 'XAUUSD'
        self.api.symbol_info_tick.side_effect = {'EURUSD': None, 'GBPUSD': NS(time=float('nan'))}.get
        with patch.object(mt5_demo.time, 'time', return_value=1000000):
            self.engine.update_clock_skew()
        self.assertEqual(self.engine.clock_skew, 7200)
        self.assertEqual(self.engine.gateway.clock_skew, 7200)
        self.assertNotIn('XAUUSD', [call.args[0] for call in self.api.symbol_info_tick.call_args_list])


if __name__ == '__main__':
    unittest.main()
