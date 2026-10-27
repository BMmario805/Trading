import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from app import aggressive,bots,desk,learning
from test_desk import data
from test_learning_economics import EconomicDatabaseTests


class DeskCapacityTests(unittest.TestCase):
    def test_all_three_desks_publish_active_capacity_without_borrowing(self):
        with tempfile.TemporaryDirectory() as folder:
            for market in ('crypto','forex','stocks'):
                engine=desk.Desk(market,Path(folder)/(market+'.sqlite'))
                public=engine.public()
                self.assertEqual(public['maxPositions'],6)
                self.assertEqual(public['scanIntervalSeconds'],15)
                self.assertEqual(public['policy']['riskProfile'],aggressive.CAPACITY_VERSION)
                self.assertEqual(public['policy']['riskPct'],1.5)
                self.assertEqual(public['policy']['totalRiskPct'],6)
                self.assertEqual(public['policy']['maxNotionalPct'],35)
                self.assertEqual(public['mode'],'paper')

    def test_increased_risk_enlarges_orders_but_cash_and_remaining_risk_still_bind(self):
        info=data()['metadata']['SOLUSDT']
        previous=desk.size_order(1000,1000,100,2,info,risk_fraction=.0075,notional_fraction=.25)
        current=desk.size_order(1000,1000,100,2,info)
        self.assertGreater(current['qty'],previous['qty']*1.9)
        self.assertLessEqual(current['risk'],15)
        for cash,remaining in ((20,15),(1000,.4)):
            capped=desk.size_order(1000,cash,100,2,info,risk_budget=remaining)
            self.assertIsNotNone(capped)
            self.assertLessEqual(capped['notional']+capped['fee'],cash)
            self.assertLessEqual(capped['risk'],remaining)


class LabCapacityTests(EconomicDatabaseTests):
    def open(self,bot):
        with bots._conn() as con:
            bar=3600*con.execute('SELECT COUNT(*) FROM bot_trades').fetchone()[0]
        with patch.object(bots.systems,'chart_overlays',return_value={}):
            bot._open(bots.SYMBOLS_BY_ID['BTC'],'long',100,2,.8,
                dict(reasons=[],entryQuote=dict(price=100,time=bots.time.time()),candles=[dict(time=bar,open=100,high=101,low=99,close=100,volume=100)],confirmations=[]),2,7,2.6,probation_why='Sin validar')
        return bot.positions['BTC']

    def test_paper_doubles_its_effective_budget_and_snapshots_the_profile(self):
        with patch.object(learning,'LAB_PROBATION_SIZE',.25):
            previous=self.open(bots.TradingBot(bots.SWING))
        bot=bots.TradingBot(bots.SWING);current=self.open(bot)
        self.assertAlmostEqual(current.qty,previous.qty*2)
        with bots._conn() as con:
            snapshot=json.loads(con.execute('SELECT snapshot FROM bot_trades WHERE id=?',(current.id,)).fetchone()[0])
        self.assertEqual(snapshot['riskProfile'],aggressive.CAPACITY_VERSION)
        self.assertEqual(bot.status()['config']['probationSizePct'],50)
        self.assertEqual(bot.status()['config']['maxProbationPositions'],4)
        self.assertEqual((learning.PROBATION_SIZE,learning.MAX_PROBATION_POSITIONS),(.25,2))

    def test_two_existing_probation_positions_allow_evaluating_a_third(self):
        bot=bots.TradingBot(replace(bots.SCALPER,universe=('BTC',)))
        bot.probation_open.update(('ETH','SOL'))
        with patch.object(learning,'entry_mode',return_value=('probation','Sin validar')),patch.object(bot,'_evaluate',AsyncMock(return_value=None)) as evaluate:
            asyncio.run(bot._scan())
        evaluate.assert_awaited_once()


if __name__=='__main__':unittest.main()
