import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

from app import bots, learning, training, mt5_demo, mt5_performance as perf, reset_accounts, portfolio_risk, fx_rates
from app.config import SYMBOLS_BY_ID
from app.mt5_gateway import DemoGateway, DemoOnlyError
from tests.test_mt5_demo import setup_api
from tests.test_mt5_performance import deal


class ResetSessionTests(unittest.TestCase):
    def setUp(self):
        self.fx_patch=patch.object(fx_rates,'get',side_effect=lambda currency:dict(rate={'USDT':1,'USD':1,'JPY':.005,'EUR':1}[currency],at=bots.time.time(),source='synthetic conversion fixture'))
        self.fx_patch.start();self.addCleanup(self.fx_patch.stop)
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for module in (bots,learning,training,mt5_demo,perf):
            p=patch.object(module,'DB_PATH',Path(self.tmp.name)/'test.sqlite')
            p.start()
            self.addCleanup(p.stop)
        self.session=dict(initial=100.0,currency='EUR',startedAt=1000,
                          quoteToEuro={'USDT':.9,'USD':.8,'JPY':.005})

    def test_lab_reset_clears_all_bot_trades_and_persists_euro_funding(self):
        b=bots.TradingBot(bots.SCALPER)
        b.load_state()
        with patch.object(learning,'size_multiplier',return_value=(1,None)),patch.object(bots.systems,'chart_overlays',return_value={}),patch.object(portfolio_risk,'assess',return_value=dict(allowed=True,token=None,reason='Isolated conversion fixture')):
            b._open(SYMBOLS_BY_ID['BTC'],'long',100,2,.8,dict(reasons=[],entryQuote=dict(price=100,time=bots.time.time()),candles=[dict(time=0,open=100,high=101,low=99,close=100,volume=100)],confirmations=[]),2,4,.8)
        reset_accounts.reset_lab(self.session)
        for cfg in bots.ALL_BOTS:
            restored=bots.TradingBot(cfg)
            restored.load_state()
            self.assertEqual(restored.balance,100)
            self.assertEqual(restored.cfg.initial_balance,100)
            self.assertEqual(restored.positions,{})
            self.assertEqual(restored.status()['currency'],'EUR')
            self.assertEqual(restored.status()['stats']['closedTrades'],0)

    def test_euro_lab_sizing_pnl_and_restart_use_quote_conversion(self):
        reset_accounts.reset_lab(self.session)
        b=bots.TradingBot(bots.SCALPER)
        b.load_state()
        with patch.object(learning,'size_multiplier',return_value=(1,None)),patch.object(bots.systems,'chart_overlays',return_value={}),patch.object(portfolio_risk,'assess',return_value=dict(allowed=True,token=None,reason='Isolated conversion fixture')):
            b._open(SYMBOLS_BY_ID['USDJPY'],'long',150,1,.8,dict(reasons=[],entryQuote=dict(price=150,time=bots.time.time()),candles=[dict(time=0,open=150,high=151,low=149,close=150,volume=100)],confirmations=[]),2,4,.8)
        pos=b.positions['USDJPY']
        self.assertEqual(pos.quote_to_account,.005)
        self.assertLessEqual(pos.qty*pos.r_dist*.005,100*b.cfg.risk_pct)
        restored=bots.TradingBot(bots.SCALPER)
        restored.load_state()
        self.assertEqual(restored.positions['USDJPY'].quote_to_account,.005)
        mark=pos.entry_price+2
        fill=mark*(1-.0001/2)*(1-.0002)
        expected_mark=((fill-pos.entry_price)*pos.qty-pos.entry_fee-fill*pos.qty*.0002)*.005
        with patch.object(bots.time,'time',return_value=pos.entry_time):
            self.assertAlmostEqual(pos.unrealized(mark),expected_mark)
        expected=(2*pos.qty-pos.entry_fee-mark*pos.qty*.0002)*.005
        with patch.object(learning,'refresh'),patch.object(bots.time,'time',return_value=pos.entry_time):
            b._close(pos,pos.entry_price+2,'test')
        self.assertAlmostEqual(b.balance,100+expected)
        self.assertEqual(learning.analyze(b.cfg.id)['bySymbol']['USDJPY']['expectancyR'],round(expected/pos.initial_risk(),3))

    def test_demo_full_sync_cannot_reintroduce_old_trades_or_deposits(self):
        settings=dict(initial=100,currency='EUR',startedAt=150,cutoffMsc=150000)
        with perf.connection() as con:
            con.execute('INSERT INTO mt5_sessions VALUES (?,?)',('demo:1',json.dumps(settings)))
        account=NS(balance=100000,equity=100000,credit=0,currency='EUR')
        rows=[deal(1,100,profit=300),deal(2,160,profit=5,commission=-1),deal(3,170,profit=10000,type=2)]
        perf.ingest('demo:1',account,[],rows,200,True)
        result=perf.performance('demo:1',[])
        self.assertEqual(result['summary']['pnl'],4)
        self.assertEqual(result['account']['balance'],104)
        self.assertEqual(result['account']['reconciliationDifference'],0)
        self.assertEqual(perf.capital('demo:1',account,[])['equity'],104)
        perf.ingest('demo:1',account,[],rows,210,True)
        self.assertEqual(perf.performance('demo:1',[])['summary']['pnl'],4)

    def test_virtual_budget_caps_lots_even_when_broker_balance_is_large(self):
        api,cfg,_,_=setup_api()
        cfg['risk_pct']=.01
        gateway=DemoGateway(api,cfg)
        request,_=gateway.prepare_entry('XAUUSD',1,2,2,3,123,'test',1000,virtual_equity=1000)
        loss=abs(api.order_calc_profit(request['type'],'XAUUSD',request['volume'],request['price'],request['sl']))
        self.assertLessEqual(loss*1.1,10)
        with self.assertRaises(DemoOnlyError):
            gateway.prepare_entry('XAUUSD',1,2,2,3,123,'test',1000,virtual_equity=100)
        api.order_send.assert_not_called()

    def test_unconfirmed_demo_close_never_reports_a_clean_reset(self):
        engine=MagicMock()
        engine.api.TRADE_RETCODE_DONE=10009
        engine.api.TRADE_RETCODE_DONE_PARTIAL=10010
        engine.submit.return_value=NS(retcode=10010)
        position=NS(ticket=1,volume=.1,symbol='EURUSD')
        engine.gateway.positions.return_value=[position]
        engine.api.orders_get.return_value=()
        with self.assertRaises(DemoOnlyError):
            reset_accounts.close_demo(engine,[position],[])
