from desk_unit_support import fund_unit_desk
import asyncio
from contextlib import closing
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import desk, gemini_agent


def data():
    return dict(at=int(time.time()),metadata={'SOLUSDT':dict(baseAsset='SOL',quoteAsset='USDT',status='TRADING',isSpotTradingAllowed=True,
        filters=[dict(filterType='LOT_SIZE',stepSize='.01',minQty='.01'),dict(filterType='MIN_NOTIONAL',minNotional='5')])},
        books={'SOLUSDT':dict(bidPrice='99.99',askPrice='100',askQty='10000')},
        tickers={'SOLUSDT':dict(quoteVolume='50000000',priceChangePercent='2')})


def add_markets(snapshot,bases):
    for base in bases:
        symbol=base+'USDT'
        snapshot['metadata'][symbol]=dict(snapshot['metadata']['SOLUSDT'],baseAsset=base,symbol=symbol)
        snapshot['books'][symbol]=dict(snapshot['books']['SOLUSDT'])
        snapshot['tickers'][symbol]=dict(quoteVolume='20000000',priceChangePercent='1')
    return snapshot


def ranked_fixture(candidates, *_args):
    """El selector se prueba aislado de RS; los cálculos reales tienen pruebas propias."""
    for candidate in candidates:
        candidate.update(rsScore=1.0,rsPercentile=100.0,rsEligible=True)
    return candidates


class SizingTests(unittest.TestCase):
    def test_rs_top_fifteen_percent_uses_closed_aligned_24h_and_72h_bars(self):
        now=1000*3600
        def bars(rate):
            return [dict(time=now-(73-i)*3600,open=100*(1+rate)**i,close=100*(1+rate)**i,
                         high=101*(1+rate)**i,low=99*(1+rate)**i,volume=100) for i in range(73)]
        btc=bars(.0001)
        candidates=[dict(symbol=f'C{i}',eligible=True) for i in range(20)]
        histories={c['symbol']:bars(.0001*(i+1)) for i,c in enumerate(candidates)}
        desk.rank_relative_strength(candidates,histories,btc,now)
        self.assertEqual([c['symbol'] for c in candidates if c['rsEligible']],['C17','C18','C19'])
        self.assertAlmostEqual(candidates[0]['rsScore'],0)
        last=candidates[-1]
        av=desk.atr(histories['C19']);price=histories['C19'][-1]['close']
        expected=((price/histories['C19'][-25]['close']-btc[-1]['close']/btc[-25]['close'])*100)/(av/price*100)
        self.assertAlmostEqual(last['rsScore24h'],expected)
        old=last['rsScore']
        histories['C19'].append(dict(time=now,open=1,close=100000,high=100000,low=1,volume=100))
        desk.rank_relative_strength(candidates,histories,btc,now)
        self.assertEqual(last['rsScore'],old)
        histories['C19']=[dict(r,time=r['time']-3600) for r in histories['C19'][:-1]]
        desk.rank_relative_strength(candidates,histories,btc,now)
        self.assertFalse(last['rsEligible'])

    def test_flat_rs_and_missing_72h_history_cannot_promote_laggards(self):
        now=1000*3600
        rows=[dict(time=now-(73-i)*3600,open=100,close=100,high=101,low=99,volume=100) for i in range(73)]
        candidates=[dict(symbol='A',eligible=True),dict(symbol='B',eligible=True)]
        desk.rank_relative_strength(candidates,{'A':rows,'B':rows},rows,now)
        self.assertTrue(all(not c['rsEligible'] for c in candidates))
        desk.rank_relative_strength(candidates,{'A':rows[-24:]},rows,now)
        self.assertIsNone(candidates[0]['rsScore'])

    def test_size_caps_exposure_cash_and_initial_risk(self):
        for a in (.05,2,20):
            for cash in (10,500,1000):
                order=desk.size_order(1000,cash,100,a,data()['metadata']['SOLUSDT'])
                if order:
                    self.assertLessEqual(order['notional'],1000*desk.MAX_NOTIONAL_FRACTION)
                    self.assertLessEqual(order['notional']+order['fee'],cash)
                    self.assertLessEqual(order['risk'],1000*desk.RISK_FRACTION)

    def test_minimum_lot_is_never_forced(self):
        self.assertIsNone(desk.size_order(1,1,100,2,data()['metadata']['SOLUSDT']))

    def test_all_liquid_spot_markets_are_available_without_a_social_whitelist_or_81_cap(self):
        d=add_markets(data(),['BTC','PAXG','JUP']+[f'COIN{i}' for i in range(100)])
        rows=desk.universe(d)
        self.assertEqual(len(rows),104)
        self.assertTrue(all(c['eligible'] for c in rows))
        self.assertTrue({'BTCUSDT','PAXGUSDT','JUPUSDT','COIN0USDT'}.issubset({c['symbol'] for c in rows}))
        self.assertEqual(rows[0]['symbol'],'SOLUSDT')

    def test_malformed_quotes_timestamps_and_change_cannot_be_eligible(self):
        for field,value in [('bidPrice','NaN'),('askPrice','0')]:
            d=data();d['books']['SOLUSDT'][field]=value
            self.assertFalse(desk.universe(d)[0]['eligible'])
        for at in (time.time()-121,time.time()+1000,float('nan'),'bad'):
            d=data();d['at']=at
            self.assertFalse(desk.universe(d)[0]['eligible'])
        d=data();d['tickers']['SOLUSDT']['priceChangePercent']='NaN'
        self.assertFalse(desk.universe(d)[0]['eligible'])

    def test_stables_leveraged_tokens_and_nonspot_markets_stay_out(self):
        d=add_markets(data(),['USDC','BTCUP','BTC'])
        d['metadata']['BTCUSDT']['isSpotTradingAllowed']=False
        self.assertEqual([c['symbol'] for c in desk.universe(d)],['SOLUSDT'])

    def test_tape_excludes_open_and_future_candles(self):
        now=100000
        rows=[dict(time=now-desk.CANDLE_SECONDS*(80-i),open=100+i,high=102+i,low=99+i,close=101+i,volume=100) for i in range(80)]
        base=desk.tape(rows,now)
        extra=dict(time=now,open=1,high=99999,low=1,close=9999,volume=100)
        self.assertEqual(base,desk.tape(rows+[extra],now))

    def test_tape_rejects_a_bounce_below_the_slow_average(self):
        now=100000
        rows=[dict(time=now-desk.CANDLE_SECONDS*(80-i),open=99,high=101,low=98,close=99,volume=100) for i in range(80)]
        rows[-1].update(close=100)
        with patch.object(desk,'ema',side_effect=[[100]*80,[100.5]*80]), \
             patch.object(desk,'atr',return_value=2), patch.object(desk,'rsi',return_value=[78]*80):
            analysis=desk.baseline_tape(rows,now)
        self.assertLess(analysis['ema20'],analysis['ema50'])
        self.assertFalse(analysis['trend'])
        self.assertFalse(analysis['entryAllowed'])


class ChandelierAndRegimeTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.engine=desk.Desk(db_path=Path(folder.name)/'desk.sqlite')
        fund_unit_desk(self,self.engine)
        self.now=400*14400
        patcher=patch.object(desk.time,'time',return_value=self.now)
        patcher.start();self.addCleanup(patcher.stop)
        order=desk.size_order(1000,1000,100,2,data()['metadata']['SOLUSDT'])
        self.position=dict(order,symbol='SOLUSDT',entryKey='test',entryTime=self.now-49*3600,
            lastBid=100,best=100,initialDist=4,atr=2,quoteAt=0,status='open',marketFilters=data()['metadata']['SOLUSDT']['filters'])
        self.position['exitPolicy']['partialEnabled']=False
        self.engine.state['positions']=[self.position]
        self.engine.state['cash']-=order['notional']+order['fee']
        self.engine.focus_position()
        self.rows=[dict(time=self.now-(70-i)*14400,open=100+i*.2,close=100+i*.2,
            high=101+i*.2,low=99+i*.2,volume=100) for i in range(70)]
        self.engine.candle_cache[('SOLUSDT','4h')]=(self.now,self.rows)

    def quote(self,bid):
        snapshot=data();snapshot['at']=self.now
        snapshot['books']['SOLUSDT'].update(bidPrice=str(bid),askPrice=str(bid+.01))
        return snapshot

    def test_profitable_trend_extends_48h_and_chandelier_ignores_open_4h_bar(self):
        self.rows.append(dict(time=self.now,open=100,close=10000,high=10000,low=100,volume=100))
        context=self.engine.closed_exit_context(self.position)
        self.engine.manage(self.quote(115))
        self.assertEqual(len(self.engine.state['positions']),1)
        self.assertTrue(self.position['holdExtended'])
        self.assertTrue(self.position['trailActivated'])
        self.assertAlmostEqual(self.position['sl'],context['bestHigh']-2.8*context['atr'])
        stop=self.position['sl']
        self.position['best']=99999
        self.engine.manage(self.quote(116))
        self.assertEqual(self.position['sl'],stop)
        self.engine.manage(self.quote(stop-.1))
        self.assertFalse(self.engine.state['positions'])

    def test_48h_still_closes_without_profit_or_closed_trend_evidence(self):
        self.engine.candle_cache.clear()
        self.engine.manage(self.quote(115))
        self.assertFalse(self.engine.state['positions'])

    def test_btc_choppy_regime_blocks_breakout_including_btc_and_requires_sweep(self):
        self.engine.master_regime=dict(choppiness=59,adx=17,stressed=False)
        for symbol in ('BTCUSDT','SOLUSDT'):
            self.assertFalse(self.engine.master_gate(symbol,dict(strategy='breakout'),[])['allowed'])
            self.assertTrue(self.engine.master_gate(symbol,dict(strategy='trend-pullback'),[])['allowed'])
            self.assertFalse(self.engine.master_gate(symbol,dict(strategy='recovery'),[])['allowed'])
            self.assertTrue(self.engine.master_gate(symbol,dict(strategy='recovery',liquiditySweep=True),[])['allowed'])

    def test_hourly_pullback_and_recovery_post_at_best_bid_for_180_seconds(self):
        from app import desk_maker
        book=dict(bidPrice='100',askPrice='100.01',bidQty='10',askQty='10',receivedAt=self.now)
        self.engine.state['positions']=[]
        self.position['evidence']={}
        for strategy in ('trend-pullback','recovery'):
            self.position['strategy']=strategy
            with patch.object(desk_maker.BOOKS,'fresh',return_value={'SOLUSDT':book}), \
                 patch.object(desk_maker.BOOKS,'watch_trades'):
                self.assertTrue(self.engine.maker.post(self.position,'buy',self.position['qty']))
            order=self.engine.maker.orders[0]['order']
            self.assertEqual(order['price'],100)
            self.assertEqual(order['expires_at']-order['placed_at'],180)
            self.engine.maker.cancel('SOLUSDT')


class DeskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        rank_patch=patch.object(desk,'rank_relative_strength',side_effect=ranked_fixture)
        rank_patch.start()
        self.addCleanup(rank_patch.stop)
        self.tmp=tempfile.TemporaryDirectory()
        self.db=patch.object(desk,'DB_PATH',Path(self.tmp.name)/'desk.sqlite')
        self.db.start()
        self.engine=desk.Desk()
        fund_unit_desk(self,self.engine)
        self.engine.state['selected']='SOLUSDT'
        self.engine.state['selectionAt']=time.time()
        self.analysis=dict(bar=int(time.time())-900,close=100,ema20=100,ema50=99,atr=2,rsi=55,trend=True,pullback=True,rows=[])

    async def asyncTearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    async def entry(self,identity=True):
        with patch.object(self.engine,'wire',AsyncMock(return_value=dict(ok=identity,detail='Referencia probada',url='https://solana.com/'))),patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await self.engine.scan(data())

    async def test_regime_maker_requirement_blocks_taker_fallback_after_expiry(self):
        self.engine.task=object()
        analysis=dict(self.analysis,strategy='trend-pullback',entryAllowed=True)
        self.engine.state['makerFallback']={f'SOLUSDT:{analysis["bar"]}':time.time()-1}
        snapshot=dict(data(),scope='execution-books')
        allocation=dict(allowed=True,maxExposureMultiplier=.5,requireMakerEntry=True,allowBreakouts=False)
        with patch.object(desk.ai_swarm,'allocation_gate',return_value=allocation), \
             patch.object(self.engine,'wire',AsyncMock(return_value=dict(ok=True,detail='Referencia probada'))), \
             patch.object(self.engine,'market_books',AsyncMock(return_value=snapshot)), \
             patch.object(self.engine,'candidate_candles',AsyncMock(return_value=[])), \
             patch.object(desk,'tape',return_value=analysis),patch.object(self.engine.maker,'post',return_value=False) as post:
            await self.engine.scan(snapshot)
        post.assert_called_once()
        self.assertFalse(self.engine.state['positions'])
        self.assertEqual(self.engine.state['candidates'][0]['status'],'blocked')
        self.assertIn('maker',self.engine.state['candidates'][0]['reason'])

    def funding_data(self):
        snapshot=add_markets(data(),['EUR'])
        snapshot['books']['EURUSDT'].update(bidPrice='1.12',askPrice='1.121')
        return snapshot

    async def test_reset_archives_previous_book_and_persists_100_euro_session(self):
        await self.entry()
        self.assertIsNotNone(self.engine.state['position'])
        with patch.object(desk.opportunities,'snapshot',AsyncMock(return_value=self.funding_data())):
            result=await self.engine.reset()
        initial=100*1.12*(1-desk.SLIPPAGE)*(1-desk.FEE)
        self.assertAlmostEqual(result['initial'],initial)
        self.assertEqual(result['funding']['amount'],100)
        self.assertEqual(result['cash'],round(initial,2))
        self.assertTrue(result['enabled'])
        self.assertIsNone(result['position'])
        self.assertEqual(result['stats']['closed'],0)
        self.assertEqual(result['trades'],[])
        self.assertEqual(len(result['curve']),1)
        archive=desk.DB_PATH.with_name(self.engine.state['previousSession'])
        import sqlite3
        with closing(sqlite3.connect(archive)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM trades').fetchone()[0],1)
            saved=json.loads(con.execute("SELECT payload FROM state WHERE key='desk'").fetchone()[0])
            self.assertIsNotNone(saved['position'])
        restored=desk.Desk()
        self.assertAlmostEqual(restored.initial,initial)
        self.assertAlmostEqual(restored.hold_equity(),initial)
        order=desk.size_order(initial,initial,100,2,data()['metadata']['SOLUSDT'])
        self.assertIsNotNone(order)
        self.assertLessEqual(order['risk'],initial*desk.RISK_FRACTION)
        self.assertLessEqual(order['notional'],initial*desk.MAX_NOTIONAL_FRACTION)
        with patch.object(restored,'wire',AsyncMock(return_value=dict(ok=True,detail='Validado'))),patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await restored.scan(data())
        self.assertAlmostEqual(restored.state['benchmark']['qty'],initial/(100*(1+desk.SLIPPAGE)*(1+desk.FEE)))

    async def test_reset_without_fresh_conversion_preserves_existing_session(self):
        original=json.dumps(self.engine.state)
        for snapshot in (data(),dict(self.funding_data(),at=time.time()-121)):
            with patch.object(desk.opportunities,'snapshot',AsyncMock(return_value=snapshot)):
                with self.assertRaises(ValueError):
                    await self.engine.reset()
            self.assertEqual(json.dumps(self.engine.state),original)
        self.assertFalse(list(desk.DB_PATH.parent.glob('desk-session-*.sqlite')))

    async def test_wire_veto_cannot_be_overridden_by_price(self):
        await self.entry(False)
        self.assertIsNone(self.engine.state['position'])
        self.assertEqual(self.engine.state['cash'],desk.INITIAL)

    async def test_entry_key_prevents_duplicate_after_restart(self):
        await self.entry()
        first=self.engine.state['position']
        self.engine.close_position(101,'test')
        restarted=desk.Desk()
        with patch.object(restarted,'wire',AsyncMock(return_value=dict(ok=True,detail='ok'))),patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await restarted.scan(data())
        self.assertIsNone(restarted.state['position'])
        self.assertEqual(len(restarted.public()['trades']),1)
        self.assertIn('SOLUSDT',first['entryKey'])

    async def test_cash_pnl_and_fees_reconcile_after_close(self):
        await self.entry()
        order=dict(self.engine.state['position'])
        self.assertAlmostEqual(self.engine.state['cash'],1000-order['notional']-order['fee'])
        self.engine.close_position(103,'test')
        trade=self.engine.public()['trades'][0]
        self.assertAlmostEqual(self.engine.state['cash']-1000,trade['pnl'])
        self.assertGreater(trade['exitFee'],0)

    async def test_restart_keeps_stop_and_cash(self):
        await self.entry()
        self.engine.state['position']['sl']=101
        self.engine.save()
        restarted=desk.Desk()
        self.assertEqual(restarted.state['position']['sl'],101)
        self.assertEqual(restarted.state['cash'],self.engine.state['cash'])

    async def test_stale_quote_cannot_close_at_fabricated_price(self):
        await self.entry()
        d=data();d['at']-=1000
        d['books']['SOLUSDT']['bidPrice']='1'
        self.engine.manage(d)
        self.assertIsNotNone(self.engine.state['position'])

    async def test_pause_still_manages_stop(self):
        await self.entry()
        await self.engine.control('pause')
        d=data();d['books']['SOLUSDT']['bidPrice']='90'
        self.engine.manage(d)
        self.assertIsNone(self.engine.state['position'])
        self.assertFalse(self.engine.state['enabled'])

    async def test_loss_of_structure_closes_open_position(self):
        await self.entry()
        self.analysis.update(trend=False,close=94)
        await self.entry()
        self.assertIsNone(self.engine.state['position'])
        self.assertEqual(self.engine.public()['trades'][0]['exitReason'],'Tape detecta pérdida de estructura')

    async def test_small_trend_dip_keeps_existing_structure_exit_rule(self):
        await self.entry()
        self.analysis.update(trend=False,close=98)
        await self.entry()
        self.assertIsNotNone(self.engine.state['position'])

    async def test_low_rsi_without_confirmed_recovery_cannot_enter(self):
        self.analysis['rsi']=18
        await self.entry()
        self.assertIsNone(self.engine.state['position'])

    async def test_moderate_impulse_can_enter_without_a_pullback(self):
        self.analysis.update(rsi=68,pullback=False,momentum=True,ema20=98)
        await self.entry()
        self.assertIsNotNone(self.engine.state['position'])
        self.assertEqual(self.engine.state['roles']['Price']['status'],'ready')

    async def test_extreme_rsi_still_blocks_entry(self):
        for strength in (10,90):
            with self.subTest(rsi=strength):
                self.analysis['rsi']=strength
                await self.entry()
                self.assertIsNone(self.engine.state['position'])

    async def test_missing_pullback_and_impulse_still_blocks_entry(self):
        self.analysis.update(pullback=False,momentum=False)
        await self.entry()
        self.assertIsNone(self.engine.state['position'])

    async def test_quote_too_far_from_signal_still_blocks_entry(self):
        self.analysis['close']=95
        await self.entry()
        self.assertIsNone(self.engine.state['position'])

    async def test_paused_desk_does_not_reselect_or_spend_tokens(self):
        self.engine.state.update(enabled=False,selectionAt=0)
        agent=AsyncMock()
        with patch.object(desk.gemini_agent,'AGENT',agent):
            await self.entry()
        agent.choose_market.assert_not_awaited()
        self.assertIsNone(self.engine.state['position'])

    async def test_position_prevents_rotation_even_after_six_hours(self):
        await self.entry()
        self.engine.state['selectionAt']=0
        agent=AsyncMock()
        with patch.object(desk.gemini_agent,'AGENT',agent):
            await self.entry()
        agent.choose_market.assert_not_awaited()
        self.assertIsNotNone(self.engine.state['position'])

    async def test_position_analysis_error_preserves_risk_and_continues_searching_free_slots(self):
        await self.entry()
        position=dict(self.engine.state['position'])
        selection=dict(self.engine.state['selection'])
        agent=AsyncMock()
        with patch.object(desk.gemini_agent,'AGENT',agent),patch.object(desk.market_data,'get_candles',AsyncMock(side_effect=ValueError('temporary'))):
            await self.engine.scan(add_markets(data(),['BTC']))
        agent.choose_market.assert_not_awaited()
        self.assertEqual(self.engine.state['position'],position)
        self.assertEqual(self.engine.state['selected'],'BTCUSDT')
        self.assertEqual(position['evidence']['selection'],selection)
        self.assertEqual(self.engine.state['candidates'][0]['status'],'error')

    async def test_cash_limits_positions_and_restart_does_not_duplicate_them(self):
        snapshot=add_markets(data(),['BTC','ETH','XRP','PAXG','ADA','LINK'])
        with patch.object(self.engine,'wire',AsyncMock(return_value=dict(ok=True,detail='ok'))),patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await self.engine.scan(snapshot)
            count=len(self.engine.state['positions'])
            self.assertEqual(count,3)  # El efectivo disponible limita este caso antes del cupo de seis.
            self.assertEqual(len({p['symbol'] for p in self.engine.state['positions']}),count)
            self.assertGreaterEqual(self.engine.state['cash'],0)
            risk=sum(p['risk'] for p in self.engine.state['positions'])
            self.assertLessEqual(risk,self.engine.equity()*desk.TOTAL_RISK_FRACTION)
            self.engine.save()
            restored=desk.Desk()
            await restored.scan(snapshot)
            self.assertEqual(len(restored.state['positions']),count)
            self.assertEqual(restored.state['cash'],self.engine.state['cash'])
            self.assertEqual(len(restored.public()['trades']),count)
            before=restored.state['cash']
            expected=sum(p['qty']*99.99*(1-desk.SLIPPAGE)*(1-desk.FEE) for p in restored.state['positions'])
            await restored.control('close')
            restored.manage(snapshot)
            self.assertEqual(restored.state['positions'],[])
            self.assertAlmostEqual(restored.state['cash'],before+expected)

    async def test_open_market_does_not_prevent_other_assets_from_entering(self):
        await self.entry()
        first=dict(self.engine.state['position'])
        with patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await self.engine.scan(add_markets(data(),['BTC','ETH']))
        self.assertEqual(len(self.engine.state['positions']),3)
        self.assertEqual(self.engine.state['positions'][0],first)

    async def test_daily_stop_closes_all_positions_and_never_reopens(self):
        snapshot=add_markets(data(),['BTC','ETH'])
        with patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await self.engine.scan(snapshot)
        self.engine.state['dayEquity']=1200
        self.engine.manage(snapshot)
        self.assertEqual(self.engine.state['positions'],[])
        self.assertFalse(self.engine.state['enabled'])
        await self.engine.control('start')
        self.assertFalse(self.engine.state['enabled'])

    async def test_position_rotates_after_45_minutes_using_a_fresh_quote(self):
        await self.entry()
        self.engine.state['positions'][0]['entryTime']=time.time()-desk.MAX_HOLD_S-1
        self.engine.manage(data())
        self.assertEqual(self.engine.state['positions'],[])
        self.assertEqual(self.engine.public()['trades'][0]['exitReason'],'Tiempo máximo')

    async def test_chasing_and_insufficient_liquidity_block_entry(self):
        self.analysis['ema20']=95
        await self.entry()
        self.assertIsNone(self.engine.state['position'])
        self.analysis['ema20']=100
        d=data();d['books']['SOLUSDT']['askQty']='0.001'
        with patch.object(self.engine,'wire',AsyncMock(return_value=dict(ok=True,detail='ok'))),patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await self.engine.scan(d)
        self.assertIsNone(self.engine.state['position'])

    async def test_daily_limit_blocks_restart_of_entries(self):
        self.engine.state['cash']=940
        self.engine.manage(data())
        await self.engine.control('start')
        self.assertFalse(self.engine.state['enabled'])

    async def test_wire_checks_exact_spot_market_and_fresh_book_without_claiming_social_identity(self):
        d=add_markets(data(),['BTC','PAXG','UNKNOWN'])
        for symbol in ('BTCUSDT','PAXGUSDT','UNKNOWNUSDT'):
            result=await self.engine.wire(symbol,d)
            self.assertTrue(result['ok'])
            self.assertFalse(result['socialChecked'])
            self.assertIn('no verificadas',result['socialDetail'])
        d['metadata']['BTCUSDT']['symbol']='OTHERUSDT'
        self.assertFalse((await self.engine.wire('BTCUSDT',d))['ok'])
        d['metadata']['BTCUSDT']['symbol']='BTCUSDT'
        d['at']-=121
        self.assertFalse((await self.engine.wire('BTCUSDT',d))['ok'])
        self.assertFalse((await self.engine.wire('FAKEUSDT',d))['ok'])

    async def test_unknown_but_listed_liquid_market_can_enter_using_real_wire_checks(self):
        d=add_markets(data(),['UNKNOWN'])
        d['tickers']['SOLUSDT']['quoteVolume']='1'
        with patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await self.engine.scan(d)
        self.assertEqual(self.engine.state['position']['symbol'],'UNKNOWNUSDT')
        self.assertFalse(self.engine.state['position']['evidence']['wire']['socialChecked'])

    async def test_public_counts_distinguish_filtered_from_valid_unselected_markets(self):
        d=add_markets(data(),['BTC','PAXG','UNKNOWN'])
        d['tickers']['BTCUSDT']['quoteVolume']='1'
        self.engine.state['candidates']=desk.universe(d)
        public=self.engine.public()
        self.assertEqual(public['examined'],4)
        self.assertEqual(public['eligible'],3)
        self.assertEqual(public['filtered'],1)
        self.assertEqual(public['notSelected'],2)
        self.assertEqual(public['discarded'],public['filtered'])
        self.assertEqual(public['examined'],public['eligible']+public['filtered'])
        self.assertEqual(sum(c['count'] for c in public['filterReasons']),public['filtered'])

    async def test_scan_continues_to_second_market_when_first_has_no_signal(self):
        d=add_markets(data(),['BTC'])
        waiting=dict(self.analysis,trend=False)
        with patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',side_effect=[waiting,self.analysis]):
            await self.engine.scan(d)
        self.assertEqual(self.engine.state['position']['symbol'],'BTCUSDT')
        self.assertEqual(self.engine.state['scanReviewed'],2)
        states={c['symbol']:c['status'] for c in self.engine.state['candidates']}
        self.assertEqual(states,{'SOLUSDT':'waiting','BTCUSDT':'executed'})

    async def test_fallback_trade_evidence_belongs_to_actual_symbol_not_previous_gemini_pick(self):
        agent=AsyncMock()
        agent.choose_market.return_value=dict(symbol='SOLUSDT',reason='Prioridad del modelo',confidence=.7)
        with patch.object(desk.gemini_agent,'AGENT',agent),patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',side_effect=[dict(self.analysis,trend=False),self.analysis]):
            await self.engine.scan(add_markets(data(),['BTC']))
        evidence=self.engine.state['position']['evidence']['selection']
        self.assertEqual(evidence['symbol'],'BTCUSDT')
        self.assertEqual(evidence['source'],'rules')
        self.assertNotIn('confidence',evidence)

    async def test_one_market_data_failure_does_not_abort_other_candidates(self):
        d=add_markets(data(),['BTC'])
        with patch.object(desk.market_data,'get_candles',AsyncMock(side_effect=[ValueError('bad data'),[]])),patch.object(desk,'tape',return_value=self.analysis):
            await self.engine.scan(d)
        self.assertEqual(self.engine.state['position']['symbol'],'BTCUSDT')
        self.assertEqual(self.engine.state['candidates'][0]['status'],'error')

    async def test_each_scan_covers_the_entire_eligible_universe(self):
        d=add_markets(data(),[f'COIN{i:02}' for i in range(25)])
        reviewed=[]
        async def candles(symbol,*args):
            reviewed.append(symbol.source_symbol)
            return []
        with patch.object(desk.market_data,'get_candles',side_effect=candles),patch.object(desk,'tape',return_value=dict(self.analysis,trend=False)),patch.object(desk.gemini_agent,'AGENT',None):
            for _ in range(3):
                before=len(reviewed)
                await self.engine.scan(d)
                self.assertEqual(len(reviewed)-before,26)
                self.assertEqual(self.engine.state['scanReviewed'],26)
        self.assertEqual(len(set(reviewed)),26)
        self.assertIsNone(self.engine.state['position'])

    async def test_gemini_only_receives_bounded_ready_setups_and_no_fake_fallback_confidence(self):
        d=add_markets(data(),[f'COIN{i}' for i in range(120)])
        d['tickers']['COIN0USDT']['quoteVolume']='1'
        agent=AsyncMock()
        agent.choose_market.return_value=None
        with patch.object(desk.gemini_agent,'AGENT',agent),patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await self.engine.scan(d)
            await self.engine.scan(d)
        agent.choose_market.assert_awaited_once()
        shortlist=agent.choose_market.call_args.args[0]
        self.assertLessEqual(len(shortlist),50)
        self.assertLess(len(json.dumps(shortlist).encode()),12000)
        self.assertTrue(all(c['eligible'] for c in shortlist))
        self.assertTrue(all(c['netRewardRisk']>=desk.MIN_NET_REWARD_RISK and 'atrPct' in c and 'extensionAtr' in c for c in shortlist))
        self.assertFalse(any(k in c for c in shortlist for k in ('cash','balance','positions','qty','notional')))
        self.assertNotIn('confidence',self.engine.state['selection'])
        self.assertEqual(self.engine.state['roles']['Selector']['provider'],'Reglas deterministas')

    async def test_no_technical_setups_never_spend_selector_calls(self):
        self.engine.state['selectionAt']=0
        agent=AsyncMock()
        with patch.object(desk.gemini_agent,'AGENT',agent),patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=dict(self.analysis,trend=False)):
            await self.engine.scan(data())
        agent.choose_market.assert_not_awaited()
        self.assertEqual(self.engine.state['selectionAt'],0)
        self.assertEqual(self.engine.state['positions'],[])

    async def test_pause_during_model_request_blocks_previously_valid_setup(self):
        agent=AsyncMock()
        async def pause(_):
            self.engine.state['enabled']=False
            return dict(symbol='SOLUSDT',reason='Prioridad técnica',confidence=.9)
        agent.choose_market.side_effect=pause
        with patch.object(desk.gemini_agent,'AGENT',agent),patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await self.engine.scan(data())
        agent.choose_market.assert_awaited_once()
        self.assertEqual(self.engine.state['positions'],[])

    async def test_stale_or_malformed_market_never_opens_a_trade(self):
        for field,value in [('bidPrice','nan'),('askPrice','-1'),('askQty','nan')]:
            d=data();d['books']['SOLUSDT'][field]=value
            with patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
                await self.engine.scan(d)
            self.assertIsNone(self.engine.state['position'])
        d=data();d['at']-=121
        with patch.object(desk.market_data,'get_candles',AsyncMock(return_value=[])),patch.object(desk,'tape',return_value=self.analysis):
            await self.engine.scan(d)
        self.assertIsNone(self.engine.state['position'])


class SelectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_cannot_select_an_unlisted_symbol(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(gemini_agent,'DB_PATH',Path(tmp)/'gemini.sqlite'):
            agent=gemini_agent.Agent(gemini_agent.Settings(key='fake'))
            raw=dict(steps=[dict(type='model_output',content=[dict(type='text',text=json.dumps(dict(symbol='FAKEUSDT',confidence=.9,reason='Inventada')))])],usage={})
            agent.http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=raw)))
            try:
                result=await agent.choose_market([dict(symbol='SOLUSDT',eligible=True)])
                self.assertIsNone(result)
                self.assertEqual(agent.status()['connectionStatus'],'unavailable')
            finally: await agent.close()
