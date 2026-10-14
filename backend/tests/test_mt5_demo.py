import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

from app.mt5_gateway import DemoGateway, DemoOnlyError, lot_floor
from app import mt5_demo


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


if __name__ == '__main__':
    unittest.main()
