"""Mesa experimental de siete roles. Libro spot SIMULADO sin pasarela de órdenes."""

import asyncio
from collections import Counter
from contextlib import closing
import json
import inspect
import logging
import math
import sqlite3
import time
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from . import gemini_agent, market_data, opportunities, portfolio_risk, aggressive
from .config import Symbol
from .database import Connection
from .execution import closed_candles
from .indicators import atr, ema, rsi, adx_series, choppiness_series
from .flow import chaikin_money_flow
from .strategy import modulate_flow, resample
from .economics import planned_trade,net_outcome,ECONOMICS_VERSION
from .book_stream import BOOKS
from .spot_orders import partial_quantity, valid_quantity
from .desk_maker import DeskMaker, valid_lot
from .hub import hub
from . import telemetry, ai_swarm, gpt_quant_agent,decision_audit

DB_PATH = Path(__file__).resolve().parents[2]/'.runtime'/'desk.sqlite'
INITIAL = 1000.0
FEE = .001
SLIPPAGE = .0002
ROLES = ('Selector','Tape','Price','Wire','Size','Exec','Risk')
log=logging.getLogger('desk')
# Señales experimentales confirmadas; sin validación económica para promoción.
ENTRY_RSI_MIN = 35
ENTRY_RSI_MAX = 78
ENTRY_EXTENSION_MIN = -1.0
ENTRY_EXTENSION_MAX = 2.25
STABLES = {'USDT','USDC','FDUSD','TUSD','DAI','USDP','USD1','USDD','EUR','AEUR','EURI',
           'RLUSD','USDE','USDS','PYUSD','USDG','USDD','USDF','USDX','USDSB','EURCV'}
# Nombres completos de productos apalancados legacy, no un filtro por sufijo:
# JUP (Jupiter), por ejemplo, es un activo spot ordinario.
LEVERAGED_BASES = {
    asset+suffix for asset in ('BTC','ETH','BNB','XRP','ADA','DOT','LINK','LTC','BCH',
                              'EOS','TRX','XLM','UNI','SUSHI','YFI','1INCH','AAVE','SXP','FIL','XTZ')
    for suffix in ('UP','DOWN')
} | {asset+suffix for asset in ('BTC','ETH','EOS','XRP','BNB','TRX') for suffix in ('BULL','BEAR')}
# Catálogo bStocks confirmado en anuncios de 28-08 y 21-10-2026, incluidos ETF 2x/3x.
# Identidades exactas: no excluir todo símbolo acabado en B (p. ej. BNB).
# https://www.binance.com/en/support/announcement/detail/4fea39f8b6e54e1b88e388fc90ce323e
TOKENIZED_EQUITIES={ticker+'B' for ticker in (
    'AAOI','AAPL','ALAB','AMAT','AMD','AMZN','ARM','ASML','ASTS','AVGO','AXTI',
    'BABA','BE','BMNR','CBRS','COHR','COIN','CRCL','CRDO','CRWV','DELL','DJT',
    'DRAM','EWY','FLNC','GLW','GME','GOOGL','GS','HOOD','IBM','INTC','INTW',
    'IREN','KORU','LITE','META','MRVL','MSFT','MSTR','MU','MUU','MVLL','NBIS',
    'NFLX','NOK','NVDA','ORCL','PLTR','PYPL','QCOM','QNT','QQQ','RKLB','SKHY',
    'SMCI','SMH','SNDK','SNXX','SOXL','SOXS','SPCX','SPY','TQQQ','TSLA','TSM',
    'USAR','WDC','BOT','BRKB','KSTR','TMF','VST')}
SCAN_BATCH_SIZE = 40  # Tamaño de cada grupo de descargas; el barrido cubre todos.
MAX_OPEN_POSITIONS = aggressive.DESK_CAPACITY['maxPositions']
SCAN_INTERVAL_S = aggressive.DESK_CAPACITY['scanSeconds']
CANDLE_INTERVAL = '1h'
CANDLE_SECONDS = 3600
MAX_HOLD_S = 48*3600
MAX_NOTIONAL_FRACTION = aggressive.DESK_CAPACITY['maxNotionalFraction']
MIN_NET_REWARD_RISK = 1.5
LEGACY_POLICY_VERSION = 'desk-rs-chandelier-1h-v9'
POLICY_VERSION = 'desk-rs-chandelier-1h-v10'
CASH_POLICY_VERSION = 'desk-cash-guarded-v3'
MIN_QUOTE_VOLUME = 2_000_000
RISK_FRACTION = aggressive.DESK_CAPACITY['riskFraction']
TOTAL_RISK_FRACTION = aggressive.DESK_CAPACITY['totalRiskFraction']
SELECTION_INTERVAL = CANDLE_SECONDS


def fresh_snapshot(data):
    try:
        age=time.time()-float(data['at'])
        return math.isfinite(age) and -5<=age<=120
    except (KeyError,ValueError,TypeError):
        return False


def connect(path=None):
    path=Path(path or DB_PATH)
    path.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(path,factory=Connection)
    con.executescript('''
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,ts REAL,role TEXT,status TEXT,detail TEXT);
        CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY,entry_key TEXT UNIQUE,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS equity(ts INTEGER PRIMARY KEY,equity REAL,benchmark REAL);
    ''')
    return con


def universe(data, candle_rows=None, btc_rows=None):
    candidates=[]
    for symbol,meta in data['metadata'].items():
        base=meta.get('baseAsset','')
        if not isinstance(base,str) or not base or symbol!=base+'USDT':
            continue
        if meta.get('quoteAsset')!='USDT' or base in STABLES or base in LEVERAGED_BASES:
            continue
        if meta.get('status')!='TRADING' or not meta.get('isSpotTradingAllowed'):
            continue
        sym=Symbol(base,base,'cripto','binance',symbol)
        quality=opportunities.assess(sym,data,min_quote_volume=MIN_QUOTE_VOLUME)
        ticker=data['tickers'].get(symbol,{})
        eligible=quality['eligible'] and fresh_snapshot(data)
        reason=quality['reason'] if fresh_snapshot(data) else 'Cotización antigua o fecha inválida'
        if base in TOKENIZED_EQUITIES:
            eligible=False
            reason='Acción o ETF tokenizado; excluido de la mesa cripto'
        try:
            change=float(ticker['priceChangePercent'])
            if not math.isfinite(change): raise ValueError('Variación inválida')
        except (KeyError,ValueError,TypeError):
            change=0.0
            eligible=False
            reason='Sin variación de mercado válida'
        candidates.append(dict(symbol=symbol,base=base,eligible=eligible,
                               change24h=round(change,2),volumeUSD=round(quality.get('quoteVolume24h',0)),
                               spreadBp=round(quality.get('spreadPct',100)*100,2),
                               reason=reason,qualityReason=reason,status='pending' if eligible else 'filtered'))
    candidates=sorted(candidates,key=lambda c:(-c['volumeUSD'],c['symbol']))
    if candle_rows is not None:
        rank_relative_strength(candidates,candle_rows,btc_rows or [],time.time())
    return candidates


def rank_relative_strength(candidates, candle_rows, btc_rows, now):
    """RS 24h/72h en retornos porcentuales / ATR porcentual, con cierres alineados."""
    btc={r['time']:r for r in closed_candles(btc_rows,3600,now)}
    scored=[]
    for candidate in candidates:
        candidate.update(rsScore=None,rsPercentile=None,rsEligible=False)
        if not candidate['eligible']: continue
        bars=closed_candles(candle_rows.get(candidate['symbol']) or [],3600,now)
        if len(bars)<73 or now-bars[-1]['time']>3*3600: continue
        stamp=bars[-1]['time']
        if not btc or stamp!=max(btc): continue
        own={r['time']:r for r in bars}
        if any(t not in own or t not in btc for t in (stamp,stamp-86400,stamp-72*3600)): continue
        price=bars[-1]['close']
        av=atr(bars)
        if not av or not math.isfinite(av) or av<=0 or price<=0: continue
        scores=[]
        for hours in (24,72):
            past=stamp-hours*3600
            if min(own[past]['close'],btc[past]['close'],btc[stamp]['close'])<=0: break
            excess=(price/own[past]['close']-btc[stamp]['close']/btc[past]['close'])*100
            scores.append(excess/(av/price*100))
        if len(scores)!=2 or not all(math.isfinite(s) for s in scores): continue
        candidate.update(rsScore24h=scores[0],rsScore72h=scores[1],rsScore=sum(scores)/2)
        scored.append(candidate)
    for candidate in scored:
        # Percentil con empates conservadores; un universo plano no se promociona entero.
        rank=sum(other['rsScore']<candidate['rsScore'] for other in scored)
        percentile=100*rank/(len(scored)-1) if len(scored)>1 else 100.0
        candidate.update(rsPercentile=percentile,rsEligible=percentile>=85)
    return candidates


def baseline_tape(rows, now, candle_seconds=CANDLE_SECONDS):
    rows=closed_candles(rows,candle_seconds,now)
    if len(rows)<60 or now-rows[-1]['time']>3*candle_seconds:
        return None
    closes=[c['close'] for c in rows]
    e20,e50=ema(closes,20),ema(closes,50)
    a=atr(rows)
    strength=rsi(closes)[-1]
    if not a or strength is None: return None
    if not math.isfinite(a) or a<=0: return None
    bullish=closes[-1]>closes[-2] and closes[-1]>rows[-1]['open']
    trend=e20[-1]>=e50[-1] and e50[-1]>=e50[-4] and closes[-1]>=e50[-1]
    pullback=(trend and min(c['low'] for c in rows[-5:])<=e20[-1]+.25*a
              and bullish and closes[-1]>=e20[-1])
    volume=sum(c['volume'] for c in rows[-21:-1])/20
    relative_volume=rows[-1]['volume']/volume if volume>0 else 0
    momentum=(trend and bullish and closes[-1]>max(c['high'] for c in rows[-21:-1])
              and relative_volume>=1.2)
    recovery=(bullish and closes[-2]<e20[-2] and closes[-1]>=e20[-1]
              and e20[-1]>e20[-3] and closes[-1]>=e50[-1]-.25*a and strength>=40)
    setup='breakout' if momentum else 'trend-pullback' if pullback else 'recovery' if recovery else None
    return dict(bar=rows[-1]['time'],close=closes[-1],ema20=e20[-1],ema50=e50[-1],
                atr=a,rsi=strength,trend=trend or recovery,pullback=pullback,momentum=momentum,
                entryAllowed=setup is not None,strategy=setup,relativeVolume=relative_volume,rows=rows[-90:])


def tape(rows,now,candle_seconds=CANDLE_SECONDS):
    return aggressive.desk_signal(rows,now,candle_seconds)


def exit_policy(version):
    if version not in (LEGACY_POLICY_VERSION,POLICY_VERSION):
        raise ValueError('Unknown desk exit policy')
    policy=dict(partialAtR=1.5,partialFraction=1/3,trailAtr=2.8,trailAfterR=2.0,residualStopR=-.5,
                chandelier4h=True,extendProfitableTrend=True)
    if version==POLICY_VERSION:
        policy.update(partialAtR=1.8,partialFraction=.30,residualStopR=0.0,
                      netResidualStop=True,dynamicChandelier=True,trailAfterR=1.8)
    return policy


def chandelier_multiple(policy, adx=None, regime=None, cvd=None):
    regime=regime if isinstance(regime,dict) else {}
    if policy.get('dynamicChandelier'):
        if regime.get('crowdedLong') is True or (type(cvd) in (int,float) and math.isfinite(cvd) and cvd<-.20):
            return 1.8
        if (type(adx) in (int,float) and math.isfinite(adx) and adx>=28
                and regime.get('cleanBreakout') is True):
            return 3.0
    return policy.get('trailAtr',2.8)


def size_order(equity, cash, ask, a, info, risk_budget=None, *, fee=FEE,slippage=SLIPPAGE,
               partial=True,risk_fraction=RISK_FRACTION,notional_fraction=MAX_NOTIONAL_FRACTION,
               stop_atr=2.0,target_atr=6.0,min_net_rr=MIN_NET_REWARD_RISK,policy_version=POLICY_VERSION):
    if not all(math.isfinite(v) and v>0 for v in (equity,cash,ask,a)): return None
    fill=ask*(1+slippage)
    stop=fill-stop_atr*a
    if stop<=0: return None
    risk_per_unit=fill*(1+fee)-stop*(1-slippage)*(1-fee)
    allowed_risk=min(equity*risk_fraction,risk_budget) if risk_budget is not None else equity*risk_fraction
    budget=min(equity*notional_fraction,cash/(1+fee),max(0,allowed_risk)/risk_per_unit*fill)
    lot=next((f for f in info.get('filters',[]) if f['filterType']=='LOT_SIZE'),{})
    step=Decimal(str(lot.get('stepSize','.00000001')))
    if step<=0: raise ValueError('Paso inválido')
    qty=float((Decimal(str(budget/fill))/step).to_integral_value(rounding=ROUND_DOWN)*step)
    filters=info.get('filters',[])
    if not valid_quantity(qty,fill,filters,order_type='MARKET'): return None
    target=fill+target_atr*a
    plan=planned_trade(fill,stop,target,qty,fee_rate=fee,exit_slippage=slippage)
    if not plan['viable'] or plan['netRewardRisk']<min_net_rr:
        return None
    return dict(qty=qty,entry=fill,notional=qty*fill,fee=qty*fill*fee,
                sl=stop,tp=target,risk=qty*risk_per_unit,economics=plan,
                costPolicy=dict(version=ECONOMICS_VERSION,feeRate=fee,slippageRate=slippage),policyVersion=policy_version,
                maxHoldSeconds=MAX_HOLD_S if partial else 6*3600,
                candleInterval=CANDLE_INTERVAL if partial else '15m',
                riskProfile=aggressive.CAPACITY_VERSION,
                exitPolicy=exit_policy(policy_version) if partial else
                    dict(partialEnabled=False,fixedTarget=True,trailStartR=2.0,trailAtr=2.5),
                targetIsReference=partial)


def price_allowed(analysis, ask):
    extension=(ask-analysis['ema20'])/analysis['atr']
    setup=analysis.get('entryAllowed',analysis['pullback'] or analysis.get('momentum',False))
    return (analysis['trend'] and setup and ENTRY_RSI_MIN<=analysis['rsi']<=ENTRY_RSI_MAX
            and ENTRY_EXTENSION_MIN<=extension<=ENTRY_EXTENSION_MAX
            and abs(ask-analysis['close'])<=2.0*analysis['atr'])


class Desk:
    def __init__(self, market='crypto', db_path=None, *, policy_version=POLICY_VERSION):
        if market=='crypto': exit_policy(policy_version)
        self.desk_id=market
        self.db_path=Path(db_path or DB_PATH)
        self.currency='USDT' if market=='crypto' else 'USD'
        self.feed=None
        if market!='crypto':
            from .cash_markets import CashMarketFeed
            self.feed=CashMarketFeed(market)
        self.fee=FEE if self.feed is None else self.feed.fee
        self.slippage=SLIPPAGE if self.feed is None else self.feed.slippage
        self.candle_interval=CANDLE_INTERVAL if self.feed is None else '15m'
        self.candle_seconds=CANDLE_SECONDS if self.feed is None else 900
        self.policy_version=policy_version if self.feed is None else CASH_POLICY_VERSION
        self.min_net_rr=2.0 if market=='stocks' else MIN_NET_REWARD_RISK
        with self.connect() as con:
            row=con.execute("SELECT payload FROM state WHERE key='desk'").fetchone()
        self.state=json.loads(row[0]) if row else dict(initial=INITIAL,cash=INITIAL,enabled=True,position=None,
            selected=None,selectionAt=0,selection=None,candidates=[],benchmark=None,startedAt=time.time(),
            day=int(time.time()//86400),dayEquity=INITIAL,dailyStopped=False,roles={},lastScan=0)
        self.state.setdefault('positions',[self.state['position']] if self.state.get('position') else [])
        self.state.setdefault('cooldowns',{})
        self.state.setdefault('controlGeneration',0)
        if self.feed is not None and row is None:
            self.state.update(initial=100.0,cash=100.0,dayEquity=100.0)
        self.task=None
        self.risk_task=None
        self.lock=asyncio.Lock()
        self.scan_lock=asyncio.Lock()
        self.last_data=None
        self.last_risk_data=None
        self.risk_error=None
        self.cycle_duration=0.0
        self.progress=dict(startedAt=time.time(),cycleStartedAt=None,cycleCompletedAt=None,
                           riskStartedAt=None,riskCompletedAt=None)
        self.chart=[]
        self.last_curve=0
        self.last_error=None
        self.candle_cache={}
        self.master_regime=None
        self.btc_rows=[]
        self.maker=DeskMaker(self)
        self.efficiency=dict(candleDownloads=0,candleCacheHits=0,bookBatches=0)
        epochs=self.state.setdefault('policyEpochs',{})
        epochs.setdefault(self.policy_version,dict(at=time.time(),equity=self.equity()))
        self.save()

    def connect(self):
        return connect(self.db_path)

    async def market_snapshot(self):
        return await self.feed.snapshot() if self.feed else await opportunities.snapshot()

    async def market_books(self,symbols,reference):
        return await self.feed.snapshot(symbols) if self.feed else await opportunities.execution_snapshot(symbols,reference)

    def market_universe(self,data):
        return self.feed.universe(data) if self.feed else universe(data)

    def market_symbol(self,symbol,data):
        if self.feed: return self.feed.symbols[symbol]
        base=data['metadata'][symbol]['baseAsset']
        return Symbol(base,base,'cripto','binance',symbol)

    def market_quality(self,symbol,data):
        if self.feed: return self.feed.quality(symbol,data)
        return opportunities.assess(self.market_symbol(symbol,data),data,min_quote_volume=MIN_QUOTE_VOLUME)

    async def candidate_candles(self,sym,interval=None):
        """Una descarga por vela cerrada; un feed retrasado se vuelve a consultar.

        Solo se reutiliza histórico, nunca cotizaciones de ejecución ni controles
        de cartera. Al cambiar de vela no se sirve el análisis anterior.
        """
        now=time.time()
        interval=interval or self.candle_interval
        seconds={'15m':900,'1h':3600,'4h':14400,'1d':86400}[interval]
        expected=int(now//seconds)*seconds-seconds
        key=(sym.source_symbol,interval)
        cached=self.candle_cache.get(key)
        if cached and cached[0]==expected:
            self.efficiency['candleCacheHits']+=1
            return cached[1]
        rows=await market_data.get_candles(sym,interval,250)
        self.efficiency['candleDownloads']+=1
        completed=closed_candles(rows,seconds,time.time())
        if completed and completed[-1]['time']==expected:
            self.candle_cache[key]=(expected,rows)
            if len(self.candle_cache)>512:
                self.candle_cache.pop(next(iter(self.candle_cache)))
        return rows

    @property
    def initial(self):
        return self.state.get('initial',INITIAL)

    def risk_conversion(self, now=None):
        """Current conversion is distinct from the historical funding rate."""
        now=time.time() if now is None else now
        conversion=self.state.get('riskConversion') or self.state.get('funding') or {}
        try:
            rate=float(conversion.get('quotePerEuro',conversion.get('netQuotePerEuro',0)))
            age=now-float(conversion.get('at',0))
            return rate if math.isfinite(rate) and rate>0 and -5<=age<=120 else None
        except (TypeError,ValueError,OverflowError): return None

    def refresh_conversion(self,data):
        if self.feed is None:
            quote=self.quote('EURUSDT',data)
            if not quote: return
            # EUR sold to obtain quote currency to cover the loss, including costs.
            rate=quote[0]*(1-self.slippage)*(1-self.fee)
            at=data['books']['EURUSDT'].get('receivedAt',data['at'])
            source='Binance EURUSDT bid; paper conversion including costs'
        else:
            rate=self.feed.quote_per_euro
            at=getattr(self.feed,'quote_per_euro_at',None)
            if not rate or at is None: return
            source='Yahoo EURUSD reference; paper conversion'
        if not math.isfinite(rate) or rate<=0 or not -5<=time.time()-at<=120: return
        self.state['riskConversion']=dict(quotePerEuro=rate,at=at,source=source,currency=self.currency)
        # Register the existing virtual allocation; never reset cash or add funds.
        if not self.state.get('funding'):
            self.state['funding']=dict(currency='EUR',amount=self.initial/rate,
                netQuotePerEuro=rate,at=at,source=source,virtual=True,registeredExistingBook=True)

    async def reset(self):
        """Nueva sesión paper financiada con 100 EUR; conserva el libro anterior."""
        if self.feed:
            raise ValueError('Las mesas USD conservan su cuenta independiente; no hay conversión EUR/USD verificada')
        async with self.lock:
            data=await opportunities.snapshot()
            quote=self.quote('EURUSDT',data)
            meta=data.get('metadata',{}).get('EURUSDT',{})
            if not quote or meta.get('status')!='TRADING' or not meta.get('isSpotTradingAllowed'):
                raise ValueError('No hay cambio EUR/USDT fresco; se conserva la sesión actual')
            # Se simula vender EUR al bid, incluyendo el coste y deslizamiento habituales.
            rate=quote[0]*(1-SLIPPAGE)*(1-FEE)
            initial=100.0*rate
            now=time.time()
            archive=self.db_path.with_name(f'desk-session-{time.time_ns()}.sqlite')
            with self.connect() as con:
                with closing(sqlite3.connect(archive)) as backup:
                    con.backup(backup)
                state=dict(initial=initial,cash=initial,enabled=True,position=None,positions=[],
                    selected=None,selectionAt=0,selection=None,candidates=[],benchmark=None,
                    startedAt=now,day=int(now//86400),dayEquity=initial,dailyStopped=False,
                    roles={},lastScan=0,cooldowns={},policyEpochs={self.policy_version:dict(at=now,equity=initial)},funding=dict(currency='EUR',amount=100.0,
                    netQuotePerEuro=rate,bid=quote[0],at=data['at'],source='Binance EURUSDT',
                    fee=FEE,slippage=SLIPPAGE),previousSession=archive.name)
                con.execute('DELETE FROM events')
                con.execute('DELETE FROM trades')
                con.execute('DELETE FROM equity')
                con.execute("INSERT OR REPLACE INTO state VALUES ('desk',?)",(json.dumps(state),))
                con.execute('INSERT INTO equity VALUES (?,?,?)',(int(now),initial,initial))
            state['controlGeneration']=self.state.get('controlGeneration',0)+1
            self.state=state
            self.last_data=data
            self.chart=[]
            self.last_curve=now
            self.last_error=None
            self.role('Risk','ready',f'Nueva sesión desde 100 EUR; tamaño proporcional al capital, riesgo objetivo {RISK_FRACTION:.2%}')
            self.save()
        return self.public()

    def save(self):
        self.focus_position()
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO state VALUES ('desk',?)",(json.dumps(self.state),))
        telemetry.changed(self.desk_id)

    def focus_position(self):
        positions=self.state['positions']
        self.state['position']=next((p for p in positions if p['symbol']==self.state['selected']),
                                    positions[-1] if positions else None)

    def role(self,name,status,detail,provider=None):
        value=dict(status=status,detail=detail,at=time.time(),provider=provider or ('Gemini' if name=='Selector' else 'Reglas deterministas'))
        previous=self.state['roles'].get(name,{})
        self.state['roles'][name]=value
        if previous.get('status')!=status or previous.get('detail')!=detail or previous.get('provider')!=value['provider']:
            with self.connect() as con:
                event_id=con.execute('INSERT INTO events(ts,role,status,detail) VALUES (?,?,?,?)',(value['at'],name,status,detail)).lastrowid
            hub.broadcast_event(dict(type='role_update',desk=self.desk_id,role=name,id=event_id,**value))

    def quote(self,symbol,data):
        try:
            book=data['books'][symbol]
            bid,ask=float(book['bidPrice']),float(book['askPrice'])
            if self.feed and not -5<=time.time()-float(book.get('referenceAt',0))<=120:
                return None
            if not fresh_snapshot(data) or not all(math.isfinite(v) and v>0 for v in (bid,ask)) or ask<bid:
                return None
            return bid,ask
        except (KeyError,ValueError,TypeError): return None

    def equity(self):
        return self.state['cash']+sum(p['qty']*p['lastBid']*(1-self.costs(p)[1])*(1-self.costs(p)[0]) for p in self.state['positions'])

    @staticmethod
    def costs(position):
        policy=position.get('costPolicy') or {}
        return policy.get('feeRate',FEE),policy.get('slippageRate',SLIPPAGE)

    def hold_equity(self):
        b=self.state['benchmark']
        return b['qty']*b['lastBid']*(1-self.slippage)*(1-self.fee) if b else self.initial

    async def wire(self,symbol,data):
        """Corrobora el instrumento y su fuente; no certifica redes sociales."""
        if self.feed: return self.feed.wire(symbol,data)
        meta=data.get('metadata',{}).get(symbol,{})
        base=meta.get('baseAsset','')
        listed=(bool(base) and symbol==base+'USDT' and meta.get('symbol',symbol)==symbol
                and meta.get('quoteAsset')=='USDT' and meta.get('status')=='TRADING'
                and bool(meta.get('isSpotTradingAllowed')))
        ok=listed and self.quote(symbol,data) is not None
        return dict(ok=ok,symbol=symbol,source='Binance Spot',checkedAt=time.time(),
                    url='https://www.binance.com/en/trade/'+base+'_USDT' if listed else None,
                    socialChecked=False,socialDetail='Web y redes sociales del proyecto no verificadas',
                    detail=(f'{symbol}: listado spot y cotización bid/ask fresca corroborados; redes sociales no verificadas'
                            if ok else 'Instrumento spot o cotización fresca no corroborados; entrada bloqueada'))

    def close_position(self,bid,reason,symbol=None):
        p=next((p for p in self.state['positions'] if p['symbol']==symbol),None) if symbol else self.state['position']
        if not p: return
        previous=json.loads(json.dumps(self.state))
        self.maker.cancel(p['symbol'])
        fee,slip=self.costs(p)
        exit_price=bid*(1-slip)
        proceeds=p['qty']*exit_price*(1-fee)
        pnl=net_outcome(p['entry'],exit_price,p['qty'],entry_fee=p['fee'],exit_fee_rate=fee)+p.get('realizedPnl',0)
        partials=p.get('partialExits',[])
        original_qty=p.get('originalQty',p['qty'])
        average_exit=(p['qty']*exit_price+sum(e['qty']*e['price'] for e in partials))/original_qty
        closed=dict(p,status='closed',exitPrice=exit_price,exitTime=time.time(),pnl=pnl,
                    qty=original_qty,notional=p.get('originalNotional',p['notional']),
                    fee=p.get('originalFee',p['fee']),averageExitPrice=average_exit,
                    finalExitQty=p['qty'],exitFee=p['qty']*exit_price*fee+sum(e['fee'] for e in partials),exitReason=reason)
        if closed.get('candidateId'):
            closed['attributionPending']=True
            closed['exitSlippageEstimate']=p['qty']*max(0,bid-exit_price)
        self.state['cash']+=proceeds
        self.state['positions'].remove(p)
        seconds={'15m':900,'1h':3600,'4h':14400}.get(p.get('candleInterval'),900)
        self.state['cooldowns'][p['symbol']]=time.time()+seconds*(4 if pnl<=0 else 1)
        self.focus_position()
        try:
            with self.connect() as con:
                con.execute('UPDATE trades SET payload=? WHERE entry_key=?',(json.dumps(closed),p['entryKey']))
                con.execute("INSERT OR REPLACE INTO state VALUES ('desk',?)",(json.dumps(self.state),))
        except BaseException:
            self.state=previous
            self.focus_position()
            raise
        self.role('Risk','closed',f'{reason}; resultado neto {pnl:+.2f} {self.currency}')
        self.record_closed_outcome(closed)
        telemetry.changed(self.desk_id)

    def record_closed_outcome(self,closed):
        if not closed.get('candidateId') or not closed.get('attributionPending'): return
        try:
            path=decision_audit.storage_for(self.db_path)
            receipt=decision_audit.candidate_receipt(closed['candidateId'],path)
            outcomes={}
            for arm,decision in receipt['decisions'].items():
                # A veto has no fill. Shadow approvals reprice the observed
                # execution path with their frozen size (linear cost model).
                if not decision['allowed'] or decision['sizeMultiplier']<=0:
                    continue
                execution=receipt['candidate'].get('inputs',{}).get('execution',{})
                reference_qty=execution.get('referenceQuantity',closed['qty'])
                scale=reference_qty*decision['sizeMultiplier']/closed['qty']
                if arm==closed.get('observedArm','rules_local'): scale=1.0
                values=dict(net_pnl=closed['pnl']*scale,currency=self.currency,
                    initial_risk=closed.get('initialRisk',closed['risk'])*scale,
                    fees=(closed.get('fee',0)+closed.get('exitFee',0))*scale,
                    slippage=(closed.get('entrySlippage',0)+closed.get('exitSlippageEstimate',0)
                        +sum(e['qty']*max(0,e['bid']-e['price']) for e in closed.get('partialExits',[])))*scale,
                    carry=0.0,outcome_type='observed-paper' if arm==closed.get('observedArm','rules_local') else 'counterfactual-observed',
                    observed_at=closed['exitTime'],execution_id=closed['entryKey'])
                decision_audit.record_outcome(closed['candidateId'],arm,db_path=path,**values)
                outcomes[arm]=dict(netPnl=values['net_pnl'],initialRisk=values['initial_risk'],
                    currency=self.currency,observedAt=closed['exitTime'],executionId=closed['entryKey'])
            from . import research_plans,experiments
            twin=closed.get('pairedTaker')
            partials=closed.get('partialExits',[])
            if twin and all(e.get('execution')!='maker-observed' or e.get('takerBid') is not None for e in partials):
                fee,slip=self.costs(closed)
                # The immediate taker alternative uses its full frozen quantity
                # and the common exit schedule, even if maker only partly filled.
                ratio=twin['qty']/closed['qty']
                gross_exit=closed['finalExitQty']*closed['exitPrice']+sum(e['qty']*e['price'] for e in partials)
                exit_fees=closed['exitFee']
                for e in partials:
                    if e.get('execution')=='maker-observed':
                        delta=e['qty']*(e['takerBid']*(1-slip)-e['price'])
                        gross_exit+=delta
                        exit_fees+=delta*fee
                outcomes['taker']=dict(netPnl=(gross_exit-exit_fees)*ratio-twin['entry']*twin['qty']-twin['fee'],
                    initialRisk=twin['risk'],currency=self.currency,observedAt=closed['exitTime'],executionId=closed['entryKey']+':taker')
                outcomes['maker']=dict(netPnl=closed['pnl'],initialRisk=closed.get('initialRisk',closed['risk']),
                    currency=self.currency,observedAt=closed['exitTime'],executionId=closed['entryKey']+':maker')
            research_plans.collect_paired_outcomes(receipt['candidate'].get('pairedReceipts',[]),outcomes,
                db_path=experiments.registry_path(self.db_path))
            closed['attributionPending']=False
            closed.pop('attributionRetryAfter',None)
            closed.pop('attributionError',None)
            with self.connect() as con:
                con.execute('UPDATE trades SET payload=? WHERE entry_key=?',
                            (json.dumps(closed),closed['entryKey']))
        except (OSError,sqlite3.Error,ValueError,TypeError,KeyError):
            # The trading book is authoritative. Keep the persisted outbox for retry.
            self.state['attributionError']='Pending observed outcome; protection remains active'
            closed['attributionAttempts']=closed.get('attributionAttempts',0)+1
            closed['attributionRetryAfter']=time.time()+min(3600,30*2**min(6,closed['attributionAttempts']))
            closed['attributionError']='Outcome or its decision receipt is unavailable'
            try:
                with self.connect() as con:
                    con.execute('UPDATE trades SET payload=? WHERE entry_key=?',
                                (json.dumps(closed),closed['entryKey']))
            except (OSError,sqlite3.Error): pass

    def reconcile_attribution(self):
        with self.connect() as con:
            row=con.execute("SELECT payload FROM trades WHERE json_extract(payload,'$.attributionPending')=1 AND COALESCE(json_extract(payload,'$.attributionRetryAfter'),0)<=? ORDER BY id LIMIT 1",(time.time(),)).fetchone()
        if row:self.record_closed_outcome(json.loads(row[0]))

    def take_partial(self,p,bid,data,*,qty_override=None,price_override=None,persist=True):
        """Una realización por entrada; estado y flujos se confirman juntos."""
        info=data.get('metadata',{}).get(p['symbol']) or dict(filters=p.get('marketFilters',[]))
        if not info.get('filters'):
            return False  # Esperar metadatos; nunca inventar mínimos de mercado.
        fee,slip=self.costs(p)
        price=price_override if price_override is not None else bid*(1-slip)
        qty=qty_override if qty_override is not None else partial_quantity(p['qty'],price,info,fraction=(p.get('exitPolicy') or {}).get('partialFraction',.5))
        if qty_override is not None and (not math.isfinite(qty) or qty>p['qty'] or qty<=0): return False
        if not qty:
            p['partialSkippedByFilters']=True
            return False
        previous=json.loads(json.dumps(self.state))
        fraction=qty/p['qty']
        cost=(p['notional']+p['fee'])*fraction
        exit_fee=qty*price*fee
        proceeds=qty*price-exit_fee
        p.setdefault('originalQty',p['qty'])
        p.setdefault('originalNotional',p['notional'])
        p.setdefault('originalFee',p['fee'])
        p['qty']=float(Decimal(str(p['qty']))-Decimal(str(qty)))
        p['notional']*=1-fraction
        p['fee']*=1-fraction
        p['realizedPnl']=p.get('realizedPnl',0)+proceeds-cost
        p.setdefault('partialExits',[]).append(dict(qty=qty,price=price,bid=bid,fee=exit_fee,
            proceeds=proceeds,pnl=proceeds-cost,at=time.time(),quoteAt=data['at'],
            execution='maker-observed' if price_override is not None else 'taker-model',
            slippageRate=0.0 if price_override is not None else slip))
        p['partialDone']=True
        self.state['cash']+=proceeds
        policy=p.get('exitPolicy') or {}
        equilibrium=p['entry']*(1+fee)/((1-slip)*(1-fee))
        residual=(p['entry']+policy['residualStopR']*p['risk']/p.get('originalQty',p['qty'])
                  if 'residualStopR' in policy else equilibrium)
        if policy.get('netResidualStop'):
            residual=((p['notional']+p['fee'])/p['qty']+policy.get('residualStopR',0)*p.get('initialRisk',p['risk'])/p['originalQty'])/((1-slip)*(1-fee))
        if residual<bid or policy.get('netResidualStop'): p['sl']=max(p['sl'],residual)
        if not persist:
            return True
        try:
            with self.connect() as con:
                con.execute('UPDATE trades SET payload=? WHERE entry_key=?',(json.dumps(p),p['entryKey']))
                con.execute("INSERT OR REPLACE INTO state VALUES ('desk',?)",(json.dumps(self.state),))
        except BaseException:
            self.state=previous
            self.focus_position()
            raise
        self.role('Exec','partial',f'Salida parcial SIMULADA de {qty:g} {p["symbol"]}; costes incluidos')
        telemetry.changed(self.desk_id)
        return True

    def closed_exit_context(self, position):
        now=time.time()
        cached=self.candle_cache.get((position['symbol'],'4h'))
        rows=closed_candles(cached[1],14400,now) if cached else []
        if not rows:
            cached=self.candle_cache.get((position['symbol'],'1h'))
            hourly=closed_candles(cached[1],3600,now) if cached else []
            counts=Counter(c['time']//14400*14400 for c in hourly)
            rows=closed_candles([c for c in resample(hourly,14400) if counts[c['time']]==4],14400,now)
        if len(rows)<50 or now-rows[-1]['time']>3*14400: return None
        av=atr(rows)
        if not av or not math.isfinite(av) or av<=0: return None
        highs=[r['high'] for r in rows if r['time']>=position['entryTime']]
        return dict(bar=rows[-1]['time'],close=rows[-1]['close'],ema50=ema([r['close'] for r in rows],50)[-1],
                    atr=av,bestHigh=max(highs) if highs else None,adx=adx_series(rows)[-1])

    def manage(self,data):
        self.maker.reconcile(data)
        if not self.state['positions']:
            self.state.pop('flatten',None)
            if not self.state['dailyStopped']:
                self.role('Risk','ready',f'Riesgo objetivo {RISK_FRACTION:.2%}; riesgo conjunto {TOTAL_RISK_FRACTION:.0%}; exposición máxima {MAX_NOTIONAL_FRACTION:.0%} al entrar')
        for p in list(self.state['positions']):
            # Un escaneo lento no debe sustituir el último libro de vigilancia.
            if data.get('at',0)<p.get('quoteAt',0): continue
            q=self.quote(p['symbol'],data)
            if not q:
                self.role('Risk','blocked','Sin precio fresco: no se simula un cierre a un precio inventado')
                continue
            bid=q[0]
            p['lastBid']=bid
            p['quoteAt']=data['at']
            base=data.get('metadata',{}).get(p['symbol'],{}).get('baseAsset') or p['symbol'].removesuffix('USDT')
            if self.feed is None and base in TOKENIZED_EQUITIES:
                self.close_position(bid,'Instrumento fuera del universo cripto',p['symbol'])
                continue
            hold=p.get('maxHoldSeconds',45*60)
            policy=p.get('exitPolicy') or {}
            context=self.closed_exit_context(p) if policy.get('chandelier4h') or policy.get('extendProfitableTrend') else None
            fee,slip=self.costs(p)
            floating=p['qty']*bid*(1-slip)*(1-fee)-p['notional']-p['fee']
            extend=bool(policy.get('extendProfitableTrend') and context and floating>p['risk']
                        and context['close']>context['ema50'])
            p['holdExtended']=extend
            fixed_target=policy.get('fixedTarget',not policy) or p.get('partialSkippedByFilters',False)
            if self.state.get('flatten') or bid<=p['sl'] or (fixed_target and bid>=p['tp']) or (hold is not None and time.time()-p['entryTime']>=hold and not extend):
                reason=('Cierre solicitado' if self.state.get('flatten') else 'Stop' if bid<=p['sl']
                        else 'Objetivo' if fixed_target and bid>=p['tp'] else 'Tiempo máximo')
                self.close_position(bid,reason,p['symbol'])
            else:
                p['best']=max(p['best'],bid)
                fee,slip=self.costs(p)
                if policy.get('partialEnabled',True) and policy.get('partialAtR') and not p.get('partialDone') and not p.get('partialSkippedByFilters') and not any(o['symbol']==p['symbol'] and o['side']=='buy' for o in self.maker.orders):
                    original_qty=p.get('originalQty',p['qty'])
                    net_per_unit=bid*(1-slip)*(1-fee)-p['entry']*(1+fee)
                    if net_per_unit>=policy['partialAtR']*p['risk']/original_qty:
                        qty=partial_quantity(p['qty'],q[1],dict(filters=p.get('marketFilters',[])),fraction=policy.get('partialFraction',.5))
                        posted=(self.feed is None and self.task is not None and not p.get('makerPartialAttempted')
                                and qty and self.maker.post(p,'sell',qty))
                        if not posted and not any(o['symbol']==p['symbol'] for o in self.maker.orders):
                            self.take_partial(p,bid,data)
                if p.get('makerPartialFallbackQty',0)>0:
                    qty=p['makerPartialFallbackQty']
                    if valid_lot(qty,bid*(1-slip),p.get('marketFilters',[])):
                        p['makerPartialFallbackQty']=0
                        self.take_partial(p,bid,data,qty_override=qty)
                    else:
                        p['makerPartialFallbackQty']=0
                        p['makerPartialRemainderBelowFilters']=True
                trailing=p.get('partialDone') if policy.get('partialAtR') and not p.get('partialSkippedByFilters') else bid>=p['entry']+policy.get('trailStartR',1)*p['initialDist']
                if trailing and policy.get('trailAfterR') and not policy.get('chandelier4h'):
                    net_per_unit=bid*(1-slip)*(1-fee)-p['entry']*(1+fee)
                    if net_per_unit>=policy['trailAfterR']*p['risk']/p.get('originalQty',p['qty']):
                        p['trailActivated']=True
                    trailing=p.get('trailActivated',False)
                if policy.get('chandelier4h') and context:
                    original_qty=p.get('originalQty',p['qty'])
                    closed_net_per_unit=context['close']*(1-slip)*(1-fee)-p['entry']*(1+fee)
                    if policy.get('dynamicChandelier'):
                        if p.get('partialDone'): p['trailActivated']=True
                        elif policy.get('partialEnabled') is False and closed_net_per_unit>=2.0*p['risk']/original_qty:
                            p['trailActivated']=True
                    elif closed_net_per_unit>=2.0*p['risk']/original_qty: p['trailActivated']=True
                    if context['bestHigh'] is not None:
                        p['bestHigh4h']=max(p.get('bestHigh4h',p['entry']),context['bestHigh'])
                    if p.get('trailActivated') and p.get('bestHigh4h') is not None:
                        from .derivatives_flow import peek
                        flow=peek(p['symbol'],'long') if policy.get('dynamicChandelier') else {}
                        tape_flow=BOOKS.taker_supports_bid(p['symbol']) if policy.get('dynamicChandelier') else {}
                        multiple=chandelier_multiple(policy,context.get('adx'),flow.get('regime'),tape_flow.get('cvdDelta'))
                        p['chandelierAtrMultiple']=multiple
                        p['sl']=max(p['sl'],p['bestHigh4h']-multiple*context['atr'])
                        p['chandelierBar']=context['bar']
                        if bid<=p['sl']:
                            self.close_position(bid,'Chandelier 4h',p['symbol'])
                            continue
                if trailing and not policy.get('chandelier4h'):
                    multiple=policy.get('trailAtr',1.5) if p.get('partialDone') or policy.get('fixedTarget') else 1.5
                    stop=max(p['sl'],p['entry']*(1+fee)/((1-slip)*(1-fee)),p['best']-multiple*p['atr'])
                    if stop<bid: p['sl']=stop
                self.role('Risk','managing',f'Stop activo; riesgo inicial {p["risk"]:.2f} {self.currency}')
        if not self.state['positions']: self.state.pop('flatten',None)
        b=self.state['benchmark']
        if b:
            q=self.quote(b['symbol'],data)
            if q: b['lastBid']=q[0]
        equity=self.equity()
        day=int(time.time()//86400)
        if day!=self.state['day']:
            self.state.update(day=day,dayEquity=equity,dailyStopped=False)
        if equity<self.state['dayEquity']*.95:
            self.state['dailyStopped']=True
            self.state['enabled']=False
            self.state['flatten']=True
            for p in list(self.state['positions']):
                q=self.quote(p['symbol'],data)
                if q: self.close_position(q[0],'Límite diario de pérdida',p['symbol'])
            self.role('Risk','blocked','Pérdida diaria del 5%: entradas detenidas')

    async def scan(self,data):
        now=time.time()
        candidates=self.market_universe(data)
        if self.feed is None:
            BOOKS.watch([c['symbol'] for c in candidates if c['eligible']]+[p['symbol'] for p in self.state['positions']])
            # El refresco de futuros corre mientras bajan las velas. Un fallo no entra en run_risk.
            if isinstance(self.task, asyncio.Task):
                from .derivatives_flow import schedule
                leaders=sorted((c for c in candidates if c.get('eligible')), key=lambda c: c.get('volumeUSD') or 0, reverse=True)[:5]
                for leader in leaders:
                    schedule(leader['symbol'])
        previous={c['symbol']:c for c in self.state['candidates']}
        for candidate in candidates:
            old=previous.get(candidate['symbol'],{})
            if candidate['eligible'] and old.get('eligible') and old.get('checkedAt') and old.get('policyVersion')==self.policy_version:
                candidate.update({k:old[k] for k in ('status','reason','checkedAt','signal','policyVersion','retryAfter','errorCount','entryChecks','economics') if k in old})
        self.state['candidates']=candidates
        self.state['scanReviewed']=0
        consumed=0
        for position in list(self.state['positions']):
            self.state['selected']=position['symbol']
            self.state['selection']=position.get('evidence',{}).get('selection',self.state.get('selection'))
            self.role('Selector','managing',f'Gestión de la posición existente en {position["symbol"]}',provider='Reglas deterministas')
            candidate=next((c for c in candidates if c['symbol']==position['symbol']),None)
            if candidate is None:
                self.role('Exec','blocked','El mercado de la posición no está disponible; se mantiene su control de riesgo')
                continue
            try:
                await asyncio.wait_for(self.review_candidate(candidate,data),timeout=8)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception('Error analizando posición %s en %s',position['symbol'],self.desk_id)
                self.evaluated(candidate,'error',f'Análisis de la posición temporalmente no disponible ({type(exc).__name__}); se conservan stop y objetivo')
                self.role('Exec','waiting',candidate['reason'])
            self.state['scanReviewed']+=1
        if len(self.state['positions'])>=MAX_OPEN_POSITIONS:
            self.role('Exec','managing',f'Cupo completo: se gestionan las {MAX_OPEN_POSITIONS} posiciones')
            return
        if not self.state['enabled'] or self.state['dailyStopped']:
            return
        if self.maker.pending_entry():
            self.role('Exec','pending','Entrada limitada pendiente; capital y riesgo reservados hasta resolverla')
            return
        opened={p['symbol'] for p in self.state['positions']}
        eligible=[c for c in candidates if c['eligible'] and c['symbol'] not in opened and c.get('retryAfter',0)<=now]
        if not eligible:
            if not self.state['positions']: self.state['selected']=None
            self.state['selectionPending']=False
            self.role('Selector','waiting','Ningún mercado pasa los controles de cotización, spread y volumen',provider='Reglas deterministas')
            return
        cursor=int(self.state.get('scanCursor',0))%len(eligible)
        rotated=eligible[cursor:]+eligible[:cursor]
        batch=rotated  # Todas las señales del universo antes de asignar capital.
        agent=gemini_agent.AGENT if self.feed is None else None
        choice=None
        # Descargas acotadas en paralelo; las decisiones y el libro se actualizan en serie.
        prefetched={}
        if self.task is not None or agent:
            semaphore=asyncio.Semaphore(4)
            async def download(candidate):
                async with semaphore:
                    try:
                        rows=await asyncio.wait_for(self.candidate_candles(self.market_symbol(candidate['symbol'],data)),timeout=5)
                        return candidate['symbol'],rows
                    except Exception:
                        return candidate['symbol'],None
            download_pool=[c for c in candidates if c['eligible']]
            for offset in range(0,len(download_pool),SCAN_BATCH_SIZE):
                self.progress['scanProgressAt']=time.time()
                prefetched.update(await asyncio.gather(*(download(c) for c in download_pool[offset:offset+SCAN_BATCH_SIZE])))
                self.progress['scanProgressAt']=time.time()
            if self.feed is None and self.task is not None:
                # Un libro reciente compartido por el lote; el monitor de riesgo
                # sigue teniendo su tarea independiente y su tiempo máximo.
                try:
                    data=await self.market_books([c['symbol'] for c in batch],data)
                    self.efficiency['bookBatches']+=1
                except Exception:
                    log.warning('Libro del lote no disponible en %s; se consultan mercados individualmente',self.desk_id)
        prepared={}
        if agent or self.task is not None:
            if self.feed is None:
                await self.refresh_master_regime(prefetched)
                rank_relative_strength(candidates,prefetched,self.btc_rows,time.time())
                for candidate in candidates:
                    if candidate['eligible'] and candidate['symbol'] not in opened and not candidate.get('rsEligible'):
                        self.evaluated(candidate,'filtered','Fuera del Top 15% de fuerza relativa o sin cierres 24h/72h alineados')
                batch=[c for c in batch if c.get('rsEligible')]
            shortlist=[]
            for candidate in batch:
                try:
                    context,analysis=await self.selector_setup(candidate,data,prefetched)
                    prepared[candidate['symbol']]=analysis
                    if context:
                        shortlist.append(context)
                        self.evaluated(candidate,'waiting','Señal y tamaño aptos; pendiente de selección de esta vela')
                    else:
                        self.evaluated(candidate,'waiting','Sin señal, tamaño, liquidez o contexto aptos para selección')
                except asyncio.CancelledError:
                    raise
                except Exception:
                    continue
            # Se comparan métricas públicas agregadas, sin saldos ni posiciones.
            shortlist.sort(key=lambda c:(c.get('maxPositiveCorrelation') is None,
                c.get('maxPositiveCorrelation') or 0,-c['netRewardRisk'],-c['relativeVolume'],c['symbol']))
            all_ready=len(shortlist)
            # Cota de entrada independiente del tamaño del universo.
            shortlist=shortlist[:24]
            while shortlist and len(json.dumps(shortlist,ensure_ascii=False).encode())>11500:
                shortlist.pop()
            self.state['selectorCoverage']=dict(eligible=len(eligible),evaluated=len(batch),ready=all_ready,submitted=len(shortlist))
            bar=int(now//self.candle_seconds)*self.candle_seconds
            cached_choice=self.state.get('selectorDecision') if agent else None
            if cached_choice and cached_choice.get('bar')==bar:
                choice=cached_choice['choice']
            elif agent and shortlist and (not self.state.get('selection') or now-self.state.get('selectionAt',0)>=SELECTION_INTERVAL):
                self.state['selectionPending']=True
                self.role('Selector','waiting',f'Gemini compara {len(shortlist)} señales con tamaño y costes viables')
                try:
                    choice=await asyncio.wait_for(agent.choose_market(shortlist),timeout=8)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    choice=None
                finally:
                    self.state['selectionPending']=False
                self.state['selectionAt']=now
                if isinstance(choice,dict) and (choice.get('symbol') is None or choice.get('symbol') in {c['symbol'] for c in shortlist}):
                    self.state['selectorDecision']=dict(bar=bar,choice=choice)
            ranked=(choice.get('ranked_symbols') or ([choice['symbol']] if choice.get('symbol') else [])) if isinstance(choice,dict) else []
            if isinstance(choice,dict) and not ranked:
                batch=[]
                self.role('Selector','waiting','Gemini elige efectivo para esta vela; no se abren posiciones')
            elif isinstance(choice,dict) and ranked and all(s in {c['symbol'] for c in shortlist} for s in ranked):
                choice={**choice,'selectorVersion':'full-universe-v2','role':'exclusive-setup'}
                if cached_choice and cached_choice.get('executed'):
                    batch=[]
                else:
                    batch=sorted([c for c in batch if c['symbol'] in ranked],key=lambda c:ranked.index(c['symbol']))
            elif cached_choice and cached_choice.get('bar')==bar:
                batch=[]  # La elección de esta vela ya se ejecutó o dejó de ser apta.
            else:
                choice=None
                if shortlist:
                    # Sin respuesta utilizable se conserva una sola prioridad
                    # local entre todos los setups que superaron los controles.
                    batch=[c for c in batch if c['symbol']==shortlist[0]['symbol']]
        self.state['selectionPending']=False
        for candidate in batch:
            self.state['scanReviewed']+=1
            consumed+=1
            # Persistir el avance incluso si una entrada o un error termina esta vuelta.
            self.state['scanCursor']=(cursor+consumed)%len(eligible)
            if self.state['selected']!=candidate['symbol']:
                self.chart=[]
                self.state.pop('tape',None)
                self.state.pop('wire',None)
            self.state['selected']=candidate['symbol']
            selected_by_ai=choice is not None and candidate['symbol'] in ranked
            selection=dict(choice,symbol=candidate['symbol'],source='gemini') if selected_by_ai else dict(
                symbol=candidate['symbol'],source='rules',reason='Revisión rotativa por liquidez; sin estimación de confianza')
            self.state['selection']=selection
            self.role('Selector','selected',f'{candidate["symbol"]}: {selection["reason"]}',
                      provider='Gemini' if selected_by_ai else 'Reglas deterministas')
            try:
                await asyncio.wait_for(self.review_candidate(candidate,data,prefetched,prepared.get(candidate['symbol'])),timeout=8)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception('Error analizando candidato %s en %s',candidate['symbol'],self.desk_id)
                self.evaluated(candidate,'error',f'Análisis temporalmente no disponible ({type(exc).__name__}); se continúa con otros mercados')
                self.role('Exec','waiting',candidate['reason'])
            if candidate.get('status') in ('executed','pending') and (choice is not None or prepared):
                if candidate.get('status')=='executed' and choice is not None and self.state.get('selectorDecision'):
                    self.state['selectorDecision']['executed']=True
                break  # La lista es de suplentes; solo una entrada por decisión.
            if len(self.state['positions'])>=MAX_OPEN_POSITIONS:
                break

    def evaluated(self,candidate,status,reason):
        candidate.update(status=status,reason=reason,checkedAt=time.time(),policyVersion=self.policy_version)
        if status=='error':
            candidate['errorCount']=candidate.get('errorCount',0)+1
            candidate['retryAfter']=time.time()+min(900,60*2**min(candidate['errorCount'],4))
        else:
            candidate.pop('retryAfter',None)
            candidate.pop('errorCount',None)

    def audit_candidate(self,candidate,order,economics,rate,bar):
        """Prospective receipt; inability to record blocks entries, never exits."""
        path=decision_audit.storage_for(self.db_path)
        try:
            key=f'{candidate["symbol"]}:{bar}:{self.state["controlGeneration"]}'
            old=decision_audit.find_candidate('desk:'+self.desk_id,key,self.policy_version,path)
            if old:
                candidate['candidateId']=economics['candidateId']=old
                return old
            now=time.time()
            base=candidate.get('base') or candidate['symbol'].removesuffix('USDT')
            cached=ai_swarm.fresh_decision(base,now) or {}
            pulse=ai_swarm.fresh_pulse(base,now) or {}
            commander=cached.get('commander') or {}
            from .derivatives_flow import peek
            derivatives=peek(candidate['symbol'])
            news=candidate.get('catalystGate') or gemini_agent.catalyst_gate(base,'long',now=now)
            news_items=news.get('evidence') or []
            agent=gemini_agent.AGENT
            snap=gemini_agent.snapshot(base) if agent is None or not inspect.iscoroutinefunction(agent.snapshot) else {}
            news_data=snap.get('data') or {} if isinstance(snap,dict) else {}
            analyzed=news_data.get('analyzedAt')
            if type(analyzed) in (int,float) and math.isfinite(analyzed) and 0<=now-analyzed<7200:
                news_items=news_items+[e for e in news_data.get('items',[]) if isinstance(e,dict)
                                       and e.get('relevant') is True and e.get('is_noise') is False]
            news_items=[e for e in news_items if type(e.get('publishedAt')) in (int,float)
                        and math.isfinite(e['publishedAt']) and e['publishedAt']<=now]
            dated=[e['publishedAt'] for e in news_items]
            inputs=dict(pulse=dict(available=bool(pulse.get('available')),availableAt=now,data=pulse),
                commander=dict(available=bool(commander.get('available')),availableAt=now,data=commander),
                derivatives=dict(available=bool(derivatives.get('available')),availableAt=now,data=derivatives),
                news=dict(available=bool(dated),availableAt=now,publishedAt=max(dated) if dated else None,
                          data=dict(gate=news,items=news_items)))
            pulse_veto=pulse.get('fakeout') is True and pulse.get('trapDirection') in ('long','both','none',None)
            pulse_ok=pulse.get('available') is True and not pulse_veto and news['allowed']
            commander_ok=commander.get('available') is True and commander.get('action')=='enter' and commander.get('direction')=='long'
            ai_ok=cached.get('quorum') is True and cached.get('direction')=='long' and pulse_ok and commander_ok
            decisions={arm:dict(allowed=bool(allowed),size_multiplier=1.0 if allowed else 0.0,
                               blockers=[] if allowed else ['Cached overlay unavailable or vetoed'],execution_mode='shadow',
                               prompt_version=ai_swarm.PROMPT_VERSION)
                       for arm,allowed in zip(decision_audit.ARMS,(True,pulse_ok,commander_ok,ai_ok))}
            for arm,mult in (('rules_plus_pulse',pulse.get('convictionMultiplier',1.0)),
                             ('rules_plus_commander',commander.get('sizeMultiplier',1.0)),
                             ('rules_plus_ai',cached.get('sizeMultiplier',0.0))):
                size=min(1.0,max(0.0,float(mult))) if type(mult) in (int,float) and math.isfinite(mult) else 0.0
                if decisions[arm]['allowed']:
                    decisions[arm].update(allowed=size>0,size_multiplier=size)
            inputs['execution']=dict(available=True,availableAt=now,referenceQuantity=order['qty'],
                basis='fixed-observed-path-repricing; quantities and costs scale linearly; execution equivalence unverified')
            from . import research_plans,experiments
            costs=dict(order['costPolicy'],spreadPct=0.0,carryDailyRate=0.0)
            paired=research_plans.capture_paired_predictions(candidate['symbol'],self.policy_version,
                costs,order['risk'],self.currency,inputs,db_path=experiments.registry_path(self.db_path))
            candidate_id=decision_audit.record_candidate('desk:'+self.desk_id,candidate['symbol'],'long',
                strategy_version=self.policy_version,cost_version=ECONOMICS_VERSION,
                risk_policy=aggressive.CAPACITY_VERSION,economics=dict(economics,planned=order['economics']),
                cost_model=order['costPolicy'],risk_eur=order['risk']/rate,notional_eur=order['notional']/rate,
                candidate_key=key,db_path=path,
                decisions=decisions,inputs=inputs,paired_receipts=paired)
            if candidate_id is None: raise ValueError('Daily attribution budget exhausted')
            candidate['candidateId']=candidate_id
            economics['candidateId']=candidate_id
            return candidate_id
        except (OSError,ValueError,sqlite3.Error) as exc:
            self.state['attributionError']=type(exc).__name__
            self.evaluated(candidate,'blocked','Registro de la propuesta no disponible; entrada bloqueada')
            return None

    async def selector_setup(self,candidate,data,prefetched):
        """Solo métricas públicas de setups viables; no entrega saldo ni posiciones al modelo."""
        symbol=candidate['symbol']
        if self.feed is None and candidate.get('rsEligible') is False: return None,None
        q=self.quote(symbol,data)
        if not q or self.state['cooldowns'].get(symbol,0)>time.time(): return None,None
        rows=prefetched.get(symbol)
        if rows is None: return None,None
        analysis=tape(rows,time.time(),self.candle_seconds)
        if analysis is None: return None,None
        open_risk=sum(max(0,p['notional']+p['fee']-p['qty']*p['sl']*(1-self.costs(p)[1])*(1-self.costs(p)[0])) for p in self.state['positions'])
        order=size_order(self.equity(),self.state['cash'],q[1],analysis['atr'],data['metadata'][symbol],
                         max(0,self.equity()*TOTAL_RISK_FRACTION-open_risk),fee=self.fee,slippage=self.slippage,partial=self.feed is None,min_net_rr=self.min_net_rr,policy_version=self.policy_version)
        fill=q[1]*(1+self.slippage)
        sl_mult,tp_mult=2,6
        geometry=planned_trade(fill,fill-sl_mult*analysis['atr'],fill+tp_mult*analysis['atr'],1,
                               fee_rate=self.fee,exit_slippage=self.slippage) if fill>sl_mult*analysis['atr'] else None
        candidate['economics']=geometry
        candidate['signal']=dict(strategy=analysis.get('strategy'),rsi=round(analysis['rsi'],2),
                                 atrPct=round(analysis['atr']/q[1]*100,3),trend=analysis['trend'],relativeVolume=analysis.get('relativeVolume'))
        price_ok=price_allowed(analysis,q[1])
        master=self.master_gate(symbol,analysis,rows)
        candidate['masterGate']=master
        price_ok=price_ok and master['allowed']
        candidate['entryChecks']=dict(signal=price_ok,costs=bool(geometry and geometry['viable'] and geometry['netRewardRisk']>=self.min_net_rr),
                                     size=order is not None,enabled=self.state['enabled'] and not self.state['dailyStopped'])
        if not price_ok or not (await self.wire(symbol,data))['ok']:
            return None,analysis
        if order is None: return None,analysis
        available=float(data['books'][symbol].get('askQty',0))
        if not math.isfinite(available) or order['qty']>available*.1: return None,analysis
        rate=self.risk_conversion()
        if rate and await asyncio.to_thread(self.audit_candidate,candidate,order,gpt_quant_agent.proposal_context(self,order),rate,analysis['bar']) is None:
            return None,analysis
        gate=gemini_agent.catalyst_gate(candidate.get('base') or data['metadata'][symbol].get('baseAsset'),'long')
        candidate['catalystGate']=gate
        if not gate['allowed']: return None,analysis
        with self.connect() as con:
            if con.execute('SELECT 1 FROM trades WHERE entry_key=?',(f'{symbol}:{analysis["bar"]}',)).fetchone():
                return None,analysis
        context={k:candidate[k] for k in ('symbol','eligible','change24h','volumeUSD','spreadBp')}
        context.update({k:candidate.get(k) for k in ('rsScore','rsScore24h','rsScore72h','rsPercentile')})
        context.update(strategy=analysis.get('strategy') or 'confirmed-setup',candleInterval=self.candle_interval,
                       signalBar=analysis['bar'],rsi=round(analysis['rsi'],2),
                       atrPct=round(analysis['atr']/q[1]*100,4),extensionAtr=round((q[1]-analysis['ema20'])/analysis['atr'],3),
                       relativeVolume=round(analysis.get('relativeVolume',0),3),netRewardRisk=round(order['economics']['netRewardRisk'],3),
                       maxPositiveCorrelation=self.setup_correlation(rows))
        if self.feed is None:
            money=chaikin_money_flow(analysis['rows'])
            hourly=closed_candles(rows,3600,time.time())
            counts=Counter(c['time']//14400*14400 for c in hourly)
            higher=[c for c in resample(hourly,14400) if counts[c['time']]==4]
            context.update(htfTrend4h=self.htf_metrics(higher),
                           cmf20=money[-1] if money else None,rsVsBtc24h=self.relative_btc(rows),
                           btcRegime=self.master_regime)
        return context,analysis

    @staticmethod
    def htf_metrics(rows):
        bars=closed_candles(rows,14400,time.time())
        if len(bars)<54 or time.time()-bars[-1]['time']>3*14400: return None
        values=ema([c['close'] for c in bars],50)
        return dict(bar=bars[-1]['time'],slopePct=round((values[-1]/values[-4]-1)*100,4),
                    distancePct=round((bars[-1]['close']/values[-1]-1)*100,4))

    async def refresh_master_regime(self,prefetched):
        """BTC cerrado 4h y retorno 24h alineado, sin usar la vela abierta."""
        self.master_regime=None
        try:
            sym=Symbol('BTC','Bitcoin','cripto','binance','BTCUSDT')
            self.btc_rows=prefetched.get('BTCUSDT') or await self.candidate_candles(sym,'1h')
            rows=await self.candidate_candles(sym,'4h')
            await self.candidate_candles(sym,'1d')
            metrics=self.htf_metrics(rows)
            bars=closed_candles(self.btc_rows or [],3600,time.time())
            if not metrics or len(bars)<25: return
            daily=bars[-1]['close']/bars[-25]['close']-1 if bars[-1]['time']-bars[-25]['time']==86400 else None
            higher=closed_candles(rows,14400,time.time())
            adx=adx_series(higher)[-1]
            chop=choppiness_series(higher)[-1]
            if daily is None or adx is None: return
            self.master_regime=dict(**metrics,return24h=round(daily,6),adx=round(adx,3),choppiness=chop,
                stressed=daily<=-.03 or (metrics['distancePct']<0 and adx>25))
        except (ValueError,KeyError,TypeError,IndexError):
            return
        except Exception:
            log.warning('Régimen BTC temporalmente no disponible')

    def relative_btc(self,rows):
        own=closed_candles(rows or [],3600,time.time())
        btc=closed_candles(self.btc_rows or [],3600,time.time())
        if len(own)<25 or len(btc)<25: return None
        if own[-1]['time']!=btc[-1]['time'] or own[-25]['time']!=btc[-25]['time'] or own[-1]['time']-own[-25]['time']!=86400:
            return None
        return round(own[-1]['close']/own[-25]['close']-btc[-1]['close']/btc[-25]['close'],6)

    def master_gate(self,symbol,analysis,rows):
        result=dict(allowed=True,policy='btc-4h-relative-strength-v1',reason='Régimen apto o entrada sin ruptura')
        if self.feed is None:
            family={'breakout':'squeeze','trend-pullback':'trend','recovery':'meanrev'}.get(analysis.get('strategy'),'trend')
            allocation=ai_swarm.allocation_gate(family,symbol=symbol,setup=analysis.get('strategy'))
            result.update(allocation)
            if not allocation['allowed']:
                return dict(result,reason='Familia bloqueada por la asignación de régimen vigente')
        if self.feed is None and self.master_regime:
            chop=self.master_regime.get('choppiness')
            ranging=chop is not None and chop>=58 and self.master_regime.get('adx',100)<18
            if ranging and (analysis.get('strategy') not in ('trend-pullback','recovery')
                            or (analysis.get('strategy')=='recovery' and analysis.get('liquiditySweep') is not True)):
                return dict(result,allowed=False,reason='BTC lateral: solo pullback o recuperación tras barrido de liquidez')
        if self.feed is not None or symbol=='BTCUSDT' or analysis.get('strategy')!='breakout': return result
        relative=self.relative_btc(rows)
        if (self.master_regime is None and self.task is not None) or (self.master_regime and self.master_regime['stressed'] and (relative is None or relative<=0)):
            result.update(allowed=False,reason='Ruptura bloqueada: BTC débil o sin confirmar y sin fuerza relativa positiva')
        return dict(result,rsVsBtc24h=relative)

    def setup_correlation(self,rows):
        """Correlación causal de retornos coincidentes; None si falta muestra."""
        def returns(candles):
            bars=closed_candles(candles,self.candle_seconds,time.time())[-73:]
            return {b['time']:math.log(b['close']/a['close']) for a,b in zip(bars,bars[1:])
                    if a['close']>0 and b['close']>0 and b['time']-a['time']==self.candle_seconds}
        own=returns(rows)
        correlations=[]
        if not self.state['positions']: return 0.0
        for p in self.state['positions']:
            cached=self.candle_cache.get((p['symbol'],self.candle_interval))
            other=returns(cached[1]) if cached else {}
            times=sorted(own.keys() & other.keys())
            if len(times)<20: continue
            x,y=[own[t] for t in times],[other[t] for t in times]
            mx,my=sum(x)/len(x),sum(y)/len(y)
            variance=sum((v-mx)**2 for v in x)*sum((v-my)**2 for v in y)
            if variance>0:
                correlations.append(sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt(variance))
        return round(max(0,max(correlations)),4) if correlations else None

    async def review_candidate(self,candidate,data,prefetched=None,prepared=None):
        self.progress['scanProgressAt']=time.time()
        generation=self.state['controlGeneration']
        symbol=candidate['symbol']
        meta=data['metadata'].get(symbol,{})
        base=meta.get('baseAsset','')
        quality=self.market_quality(symbol,data)
        if self.feed is None and base in TOKENIZED_EQUITIES:
            quality=dict(eligible=False,reason='Acción o ETF tokenizado; excluido de la mesa cripto')
        if not quality['eligible']:
            self.evaluated(candidate,'blocked',quality['reason'])
            self.role('Exec','blocked',quality['reason'])
            return
        q=self.quote(symbol,data)
        if not q:
            self.evaluated(candidate,'blocked','Sin cotización bid/ask fresca y válida')
            self.role('Exec','blocked',candidate['reason'])
            return
        bid,ask=q
        identity=await self.wire(symbol,data)
        self.state['wire']=identity
        self.role('Wire','corroborated' if identity['ok'] else 'blocked',identity['detail'])
        if not identity['ok']:
            self.evaluated(candidate,'blocked',identity['detail'])
            self.role('Exec','blocked',identity['detail'])
            return
        if identity['ok'] and self.state['benchmark'] is None:
            self.state['benchmark']=dict(symbol=symbol,qty=self.initial/(ask*(1+self.slippage)*(1+self.fee)),lastBid=bid,startedAt=time.time())
        sym=self.market_symbol(symbol,data)
        existing=next((p for p in self.state['positions'] if p['symbol']==symbol),None)
        if not existing and self.feed is None and (candidate.get('rsEligible') is False or (self.task is not None and candidate.get('rsEligible') is not True)):
            self.evaluated(candidate,'blocked','Entrada larga fuera del percentil 85 de fuerza relativa 24h/72h')
            return
        if not existing and gemini_agent.AGENT and self.task is not None:
            await gemini_agent.AGENT.pretrade(sym)
        interval=existing.get('candleInterval','15m') if existing else self.candle_interval
        seconds={'15m':900,'1h':3600,'4h':14400}[interval]
        rows=prefetched[symbol] if prefetched and symbol in prefetched else await self.candidate_candles(sym,interval)
        analysis=prepared if prepared is not None else tape(rows or [],time.time(),seconds)
        if analysis and time.time()-analysis['bar']>3*seconds:
            analysis=None
        if analysis is None:
            self.evaluated(candidate,'blocked','Velas cerradas insuficientes o antiguas')
            self.role('Tape','blocked','Velas cerradas insuficientes o antiguas')
            return
        # En producción se vuelve a observar el libro después del trabajo de red.
        # Las pruebas y evaluadores sin task conservan la instantánea suministrada.
        if self.task is not None:
            if data.get('scope')!='execution-books' or not 0<=time.time()-data.get('at',0)<=2:
                data=await self.market_books([symbol],data)
            q=self.quote(symbol,data)
            if q is None:
                self.evaluated(candidate,'blocked','El libro de ejecución no es válido')
                return
            bid,ask=q
            quality=self.market_quality(symbol,data)
            if not quality['eligible']:
                self.evaluated(candidate,'blocked',quality['reason'])
                return
        self.chart=analysis['rows']
        self.state['tape']={**{k:v for k,v in analysis.items() if k!='rows'},'candleInterval':interval}
        candidate['signal']=dict(strategy=analysis.get('strategy'),rsi=round(analysis['rsi'],2),
                                 atrPct=round(analysis['atr']/ask*100,3),trend=analysis['trend'],relativeVolume=analysis.get('relativeVolume'))
        self.role('Tape','ready' if analysis['trend'] else 'waiting',f'EMA20/50; RSI {analysis["rsi"]:.0f}; ATR {analysis["atr"]:.5g}')
        extension=(ask-analysis['ema20'])/analysis['atr']
        price_ok=price_allowed(analysis,ask)
        master=self.master_gate(symbol,analysis,rows)
        candidate['masterGate']=master
        price_ok=price_ok and master['allowed']
        self.role('Price','ready' if price_ok else 'waiting',
                  f'Señal confirmada: {analysis.get("strategy") or "continuidad"}' if price_ok else 'Esperando recuperación o tendencia confirmada, RSI 35–78 y extensión moderada')
        existing=next((p for p in self.state['positions'] if p['symbol']==symbol),None)
        if existing:
            expected_key=existing['entryKey']
            if self.feed is None and existing.get('candleInterval')=='1h':
                await self.candidate_candles(sym,'4h')
            if isinstance(self.task,asyncio.Task):
                data=await self.market_books([symbol],data)
            async with self.lock:
                existing=next((p for p in self.state['positions'] if p['entryKey']==expected_key),None)
                if existing is None:
                    self.evaluated(candidate,'waiting','La posición cambió durante el análisis; se conserva su gestión actual')
                    return
                self.role('Size','managing',f'Asignación existente: {existing["notional"]:.2f} {self.currency}; no se solicita otro lote')
                if not analysis['trend'] and analysis['close']<analysis['ema50'] - 2.0 * analysis['atr']:
                    frames=[frame for frame in (data,self.last_risk_data,self.last_data) if frame and self.quote(symbol,frame)]
                    frame=max(frames,key=lambda f:f['at']) if frames else None
                    if frame is None or frame['at']<existing.get('quoteAt',0):
                        self.evaluated(candidate,'blocked','Salida de estructura pendiente de una cotización actual; stops activos')
                        return
                    self.close_position(self.quote(symbol,frame)[0],'Tape detecta pérdida de estructura',symbol)
                    self.evaluated(candidate,'waiting','Posición cerrada por pérdida de estructura')
                    self.role('Exec','waiting','Posición cerrada por cambio de tendencia')
                    return
            self.role('Exec','managing',f'{symbol} ya está abierto; no se duplica')
            self.evaluated(candidate,'managing','Posición abierta: se mantiene su gestión de riesgo')
            return
        if len(self.state['positions'])>=MAX_OPEN_POSITIONS:
            self.evaluated(candidate,'blocked',f'Máximo de {MAX_OPEN_POSITIONS} posiciones simultáneas')
            return
        if self.state['cooldowns'].get(symbol,0)>time.time():
            self.evaluated(candidate,'waiting','En espera tras el último cierre; se evita reentrar inmediatamente')
            return
        open_risk=sum(max(0,p['notional']+p['fee']-p['qty']*p['sl']*(1-self.costs(p)[1])*(1-self.costs(p)[0])) for p in self.state['positions'])
        order=size_order(self.equity(),self.state['cash'],ask,analysis['atr'],meta,
                         min(max(0,self.equity()*TOTAL_RISK_FRACTION*master.get('maxExposureMultiplier',1.0)-open_risk),self.equity()*RISK_FRACTION*master.get('maxExposureMultiplier',1.0)),
                         fee=self.fee,slippage=self.slippage,partial=self.feed is None,min_net_rr=self.min_net_rr,policy_version=self.policy_version)
        if order: order['policyVersion']=self.policy_version
        sl_mult,tp_mult=2,6
        geometry=planned_trade(ask*(1+self.slippage),ask*(1+self.slippage)-sl_mult*analysis['atr'],
                               ask*(1+self.slippage)+tp_mult*analysis['atr'],1,fee_rate=self.fee,exit_slippage=self.slippage) if ask*(1+self.slippage)>sl_mult*analysis['atr'] else None
        candidate['economics']=geometry
        candidate['entryChecks']=dict(signal=price_ok,costs=bool(geometry and geometry['viable'] and geometry['netRewardRisk']>=self.min_net_rr),
                                      size=order is not None,enabled=self.state['enabled'] and not self.state['dailyStopped'])
        size_reason='Lote mínimo o beneficio/riesgo neto insuficiente después de costes'
        self.role('Size','ready' if order else 'blocked',f'Tamaño {order["notional"]:.2f} {self.currency}; máximo {MAX_NOTIONAL_FRACTION:.0%}; riesgo {order["risk"]:.2f}' if order else size_reason)
        if not self.state['enabled'] or self.state['dailyStopped'] or not price_ok or not identity['ok'] or not order:
            self.evaluated(candidate,'waiting',f'Sin confirmación de entrada; RSI {analysis["rsi"]:.0f}, extensión {extension:.1f} ATR' if not price_ok else size_reason if not order else 'Entradas pausadas por control de riesgo')
            self.role('Exec','waiting','Esperando aprobación de Price, Wire, Size y Risk')
            return
        rate=self.risk_conversion()
        economics=gpt_quant_agent.proposal_context(self,order)
        candidate_id=await asyncio.to_thread(self.audit_candidate,candidate,order,economics,rate,analysis['bar']) if rate else None
        if candidate_id is None:
            self.evaluated(candidate,'blocked','Registro o conversión de riesgo no disponible')
            return
        gate=gemini_agent.catalyst_gate(base,'long')
        candidate['catalystGate']=gate
        if not gate['allowed']:
            self.evaluated(candidate,'blocked',gate['reason'])
            self.role('Risk','blocked',gate['reason'])
            return
        key=f'{symbol}:{analysis["bar"]}'
        with self.connect() as con:
            if con.execute('SELECT 1 FROM trades WHERE entry_key=?',(key,)).fetchone():
                self.evaluated(candidate,'waiting','Esta vela ya produjo una operación; no se duplica')
                return
        if not self.quote(symbol,data):
            self.evaluated(candidate,'blocked','La cotización caducó durante el análisis; entrada bloqueada')
            self.role('Exec','blocked','La cotización caducó durante el análisis; entrada bloqueada')
            return
        available=float(data['books'][symbol].get('askQty',0))
        if not math.isfinite(available) or available<=0 or order['qty']>available*.1:
            self.evaluated(candidate,'blocked','Tamaño superior a la liquidez publicada al mejor ask')
            self.role('Exec','blocked','Tamaño superior a la liquidez publicada al mejor ask')
            return
        rate=self.risk_conversion()
        if not rate:
            self.evaluated(candidate,'blocked','Conversión EUR reciente pendiente para el riesgo agregado')
            return
        economics=gpt_quant_agent.proposal_context(self,order)
        if self.feed is None:
            economics.update(strategy=analysis.get('strategy'),symbol=base,
                strategyFamily={'breakout':'squeeze','trend-pullback':'trend','recovery':'meanrev'}.get(analysis.get('strategy'),'trend'))
        audit=gpt_quant_agent.local_verdict(economics,economics['requestedRiskFraction'],
                                           learning_allowed=economics.get('learningAllowed',True))
        candidate['quantAudit']=audit
        hub.broadcast_event(dict(type='agent_thought',desk=self.desk_id,symbol=base,
            agent='Motor-Quant-Local',role='Size',phase='local_audit',detail=audit['rationale'],verdict=audit))
        if not audit['approved']:
            self.evaluated(candidate,'blocked',audit['rationale'])
            self.role('Risk','blocked',audit['rationale'])
            return
        ai_swarm.request_entry_audit(base,'long',economics,
            order['risk']/rate if rate else None,order['notional']/rate if rate else None)
        clearance=ai_swarm.execution_clearance(base,'long',candidate_id=candidate_id)
        candidate['swarmClearance']=clearance
        if clearance['binding']:
            if not clearance['allowed'] or clearance.get('venue')!='spot' or not audit['approved']:
                reason=clearance.get('reason') if not clearance['allowed'] else audit['rationale'] if not audit['approved'] else 'El quorum requiere la ruta MT5 demo'
                self.evaluated(candidate,'blocked',reason)
                self.role('Risk','blocked',reason,provider='GPT-Quant-Risk')
                return
            mult=clearance.get('sizeMultiplier')
            if not isinstance(mult,(int,float)) or not math.isfinite(mult) or not 0<mult<=1:
                self.evaluated(candidate,'blocked','Multiplicador del quorum inválido')
                return
            budget=min(order['risk']*mult,self.equity()*audit['adjusted_risk_fraction'])
            approved_risk=clearance.get('maxRiskEUR')
            approved_notional=clearance.get('maxNotionalEUR')
            if not rate or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (approved_risk,approved_notional)):
                self.evaluated(candidate,'blocked','Falta el presupuesto EUR auditado de esta propuesta')
                return
            budget=min(budget,approved_risk*rate*mult)
            order=size_order(self.equity(),self.state['cash'],ask,analysis['atr'],meta,budget,
                fee=self.fee,slippage=self.slippage,partial=self.feed is None,min_net_rr=self.min_net_rr,policy_version=self.policy_version,
                notional_fraction=min(MAX_NOTIONAL_FRACTION,approved_notional*rate*mult/self.equity()))
            if not order:
                self.evaluated(candidate,'blocked','El tamaño auditado no cumple el lote mínimo')
                return
            order['policyVersion']=self.policy_version
        if not rate:
            self.evaluated(candidate,'blocked','Conversión EUR reciente pendiente para el riesgo agregado')
            return
        derivatives=None
        if self.feed is None and isinstance(self.task, asyncio.Task):
            try:
                from .opportunities import derivatives as read_derivatives
                derivatives=await asyncio.wait_for(read_derivatives(symbol,'long'),3)
            except asyncio.CancelledError:
                raise
            except Exception:
                derivatives=None
        # Network work ends before the commit lock. A pause/resume or reset
        # invalidates this proposal even if the desk is enabled again.
        if isinstance(self.task,asyncio.Task):
            data=await self.market_books([symbol],data)
        async with self.lock:
            if (generation!=self.state['controlGeneration'] or not self.state['enabled']
                    or self.state['dailyStopped'] or self.state.get('flatten')):
                self.evaluated(candidate,'blocked','La decisión caducó por pausa, cierre o cambio de sesión')
                return
            if (len(self.state['positions'])>=MAX_OPEN_POSITIONS
                    or any(p['symbol']==symbol for p in self.state['positions'])
                    or self.maker.pending_entry()
                    or self.state['cooldowns'].get(symbol,0)>time.time()):
                self.evaluated(candidate,'blocked','Cupo, posición, orden pendiente o espera cambiaron durante el análisis')
                return
            q=self.quote(symbol,data)
            rate=self.risk_conversion()
            if (q is None or rate is None or (isinstance(self.task,asyncio.Task)
                    and not 0<=time.time()-data.get('at',0)<=2)):
                self.evaluated(candidate,'blocked','Cotización o conversión caducada antes de confirmar la entrada')
                return
            bid,ask=q
            if not price_allowed(analysis,ask) or not self.market_quality(symbol,data)['eligible']:
                self.evaluated(candidate,'blocked','El precio o la calidad cambiaron antes de confirmar')
                return
            with self.connect() as con:
                duplicate=con.execute('SELECT 1 FROM trades WHERE entry_key=?',(key,)).fetchone()
            if duplicate:
                self.evaluated(candidate,'blocked','Esta vela ya produjo una operación; no se duplica')
                return
            gate=gemini_agent.catalyst_gate(base,'long')
            clearance=ai_swarm.execution_clearance(base,'long',candidate_id=candidate_id)
            if not gate['allowed']:
                self.evaluated(candidate,'blocked','El veto contextual cambió antes de confirmar la entrada')
                return
            if clearance['binding'] and (not clearance['allowed'] or clearance.get('venue')!='spot'
                    or type(clearance.get('sizeMultiplier')) not in (int,float)
                    or not math.isfinite(clearance['sizeMultiplier']) or not 0<clearance['sizeMultiplier']<=1
                    or any(type(clearance.get(k)) not in (int,float) or not math.isfinite(clearance[k])
                           or clearance[k]<=0 for k in ('maxRiskEUR','maxNotionalEUR'))):
                self.evaluated(candidate,'blocked',clearance.get('reason') or 'Quorum o presupuesto ya no válidos')
                return
            open_risk=sum(max(0,p['notional']+p['fee']-p['qty']*p['sl']*(1-self.costs(p)[1])*(1-self.costs(p)[0])) for p in self.state['positions'])
            risk_budget=min(order['risk'],max(0,self.equity()*TOTAL_RISK_FRACTION*master.get('maxExposureMultiplier',1.0)-open_risk),
                            self.equity()*RISK_FRACTION*master.get('maxExposureMultiplier',1.0))
            if clearance['binding']:
                risk_budget=min(risk_budget,clearance['maxRiskEUR']*rate*clearance['sizeMultiplier'],
                                self.equity()*audit['adjusted_risk_fraction'])
            equity=self.equity()
            nominal_cap=min(order['notional'],equity*MAX_NOTIONAL_FRACTION)
            if clearance['binding']:
                nominal_cap=min(nominal_cap,clearance['maxNotionalEUR']*rate*clearance['sizeMultiplier'])
            order=size_order(equity,self.state['cash'],ask,analysis['atr'],meta,risk_budget,
                fee=self.fee,slippage=self.slippage,partial=self.feed is None,min_net_rr=self.min_net_rr,policy_version=self.policy_version,
                notional_fraction=nominal_cap/equity if equity>0 else 0)
            available=float(data['books'][symbol].get('askQty',0))
            if not order or not math.isfinite(available) or available<=0 or order['qty']>available*.1:
                self.evaluated(candidate,'blocked','El presupuesto, filtros o liquidez cambiaron antes de confirmar')
                return
            order['policyVersion']=self.policy_version
            reservation=None
            if rate:
                decision=portfolio_risk.assess(symbol,'long',order['risk']/rate,order['notional']/rate,
                                               source='desk',bot_id='desk' if self.desk_id=='crypto' else self.desk_id,reserve=True)
                self.state['portfolioDecision']={k:v for k,v in decision.items() if k!='token'}
                if not decision['allowed']:
                    self.evaluated(candidate,'blocked',decision['reason'])
                    self.role('Risk','blocked',decision['reason'])
                    return
                reservation=decision.get('token')
            position=dict(order,symbol=symbol,entryKey=key,entryTime=time.time(),quoteAt=data['at'],atr=analysis['atr'],
                          initialDist=order['entry']-order['sl'],best=order['entry'],lastBid=bid,status='open',
                          marketFilters=meta.get('filters',[]),
                          evidence=dict(tape=self.state['tape'],wire=identity,selection=self.state['selection'],catalystGate=gate))
            position['quantAudit']=audit
            position['candidateId']=candidate_id
            position['observedArm']='rules_plus_ai' if clearance['binding'] else 'rules_local'
            position['initialRisk']=order['risk']
            position['entrySlippage']=max(0,order['entry']-ask)*order['qty']
            position['strategy']=analysis.get('strategy')
            regime=(derivatives or {}).get('regime') or {}
            adjustment=regime.get('flowAdjustment') or 0.0
            cmf=(chaikin_money_flow(analysis['rows']) or [None])[-1]
            position['entryContext']=dict(extension_atr=extension,spread_to_atr=(ask-bid)/analysis['atr'],
                cmf20=cmf,flowAdjustment=adjustment if isinstance(adjustment,(int,float)) and math.isfinite(adjustment) else 0.0,
                rsScore=candidate.get('rsScore'),rsPercentile=candidate.get('rsPercentile'))
            if isinstance(derivatives, dict):
                if isinstance(derivatives.get('fundingRate'), (int, float)):
                    position['entryContext']['funding']=derivatives['fundingRate']
                if isinstance(derivatives.get('oiChange1hPct'), (int, float)):
                    position['entryContext']['oi_delta']=derivatives['oiChange1hPct']
            position['confirmations']=[dict(system='Régimen',vote=float(bool(analysis.get('higherRegimeConfirmed')))),
                dict(system='Disparador de precio',vote=float(bool(analysis.get('entryAllowed')))),
                dict(system='Flujo CMF',vote=modulate_flow(cmf or 0.0, position['entryContext']['flowAdjustment']))]
            maker_route=(clearance['binding'] and clearance.get('profile')=='maker') or master.get('requireMakerEntry',False)
            if maker_route and (self.feed is not None or self.task is None):
                portfolio_risk.release(reservation)
                self.evaluated(candidate,'blocked','La asignación requiere una entrada Post-Only con cola observada')
                return
            if (self.feed is None and self.task is not None and (maker_route or analysis.get('strategy') in ('trend-pullback','recovery'))
                    and (maker_route or key not in self.state.get('makerFallback',{}))):
                try:
                    posted=self.maker.post(position,'buy',position['qty'])
                except BaseException:
                    portfolio_risk.release(reservation)
                    raise
                if posted:
                    portfolio_risk.release(reservation)
                    self.evaluated(candidate,'pending','Orden limitada simulada pendiente; espera operaciones agresoras observadas')
                    return
                if maker_route or self.maker.last_reject=='cvd':
                    portfolio_risk.release(reservation)
                    reason=('Flujo taker vendiendo contra la compra limitada (CVD < -0.15)' if self.maker.last_reject=='cvd'
                            else 'El perfil maker requiere una cola válida; no se sustituye por mercado')
                    self.evaluated(candidate,'blocked',reason)
                    self.role('Exec','blocked',reason)
                    return
            self.state['cash']-=order['notional']+order['fee']
            self.state['positions'].append(position)
            self.state['position']=position
            try:
                with self.connect() as con:
                    con.execute('INSERT INTO trades(entry_key,payload) VALUES (?,?)',(key,json.dumps(position)))
                    con.execute("INSERT OR REPLACE INTO state VALUES ('desk',?)",(json.dumps(self.state),))
            except Exception:
                self.state['positions'].remove(position)
                self.state['cash']+=order['notional']+order['fee']
                self.focus_position()
                raise
            finally:
                portfolio_risk.release(reservation)
            self.role('Exec','executed',f'Compra SIMULADA de {base}; comisión y deslizamiento incluidos')
            self.evaluated(candidate,'executed','Operación simulada abierta; stop, objetivo, comisión y deslizamiento aplicados')
            self.role('Risk','managing','Stop y objetivo establecidos al abrir')
            telemetry.changed(self.desk_id)

    async def cycle(self):
        self.progress['cycleStartedAt']=time.time()
        started=time.monotonic()
        data=await self.market_snapshot()
        async with self.lock:
            self.last_data=data
            self.refresh_conversion(data)
            self.manage(data)
            scan_due=(self.state['enabled'] or self.state['selected']) and time.time()-self.state['lastScan']>=SCAN_INTERVAL_S
            if scan_due:
                self.state['lastScan']=time.time()
        if scan_due:
            async with self.scan_lock:
                await self.scan(data)
        async with self.lock:
            if time.time()-self.last_curve>=60:
                self.last_curve=time.time()
                with self.connect() as con:
                    con.execute('INSERT OR REPLACE INTO equity VALUES (?,?,?)',(int(self.last_curve),self.equity(),self.hold_equity()))
            self.save()
            self.last_error=None
            if scan_due: self.cycle_duration=time.monotonic()-started
            self.progress['cycleCompletedAt']=time.time()

    async def risk_cycle(self):
        self.progress['riskStartedAt']=time.time()
        symbols=list({p['symbol'] for p in self.state['positions']} | {o['symbol'] for o in self.maker.orders})
        if not symbols:
            self.risk_error=None
            self.progress['riskCompletedAt']=time.time()
            self.reconcile_attribution()
            return
        data=await self.market_books(symbols,self.last_data or {})
        async with self.lock:
            self.last_risk_data=data
            self.manage(data)
            self.reconcile_attribution()
            self.risk_error=None
            self.save()
            self.progress['riskCompletedAt']=time.time()

    async def run_risk(self):
        while True:
            try:
                await self.risk_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.risk_error=f'Libro de vigilancia sin actualizar ({type(exc).__name__})'
                self.role('Risk','blocked',self.risk_error)
            await asyncio.sleep(2)

    async def run(self):
        while True:
            try: await self.cycle()
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.last_error=f'Fuentes temporalmente no disponibles ({type(exc).__name__}); se conservan cuenta y controles'
                self.role('Risk','blocked',self.last_error)
            await asyncio.sleep(5)

    async def control(self,action):
        async with self.lock:
            if action not in ('start','pause','close'): raise ValueError('Acción inválida')
            if action in ('pause','close'):
                self.maker.reconcile(self.last_risk_data or self.last_data or {},cutoff=time.time())
            if action=='start': self.state['enabled']=not self.state['dailyStopped']
            elif action=='pause': self.state['enabled']=False
            elif action=='close':
                self.state['enabled']=False
                self.state['flatten']=True
            self.state['controlGeneration']+=1
            if action in ('pause','close'):
                self.maker.cancel_entries()
            self.save()
        return self.public()

    def public(self):
        with self.connect() as con:
            trades=[json.loads(r[0]) for r in con.execute('SELECT payload FROM trades ORDER BY id DESC LIMIT 100')]
            events=[dict(id=i,at=t,role=r,status=s,detail=d) for i,t,r,s,d in con.execute('SELECT id,ts,role,status,detail FROM events ORDER BY id DESC LIMIT 30')]
            curve=[dict(time=t,equity=e,benchmark=b) for t,e,b in con.execute('SELECT ts,equity,benchmark FROM equity ORDER BY ts DESC LIMIT 1440')][::-1]
        with self.connect() as con:
            count,wins,pnl=con.execute('''SELECT COUNT(*),COALESCE(SUM(json_extract(payload,'$.pnl')>0),0),COALESCE(SUM(json_extract(payload,'$.pnl')),0)
                FROM trades WHERE json_extract(payload,'$.status')='closed' ''').fetchone()
            policy_count,policy_wins,policy_pnl=con.execute('''SELECT COUNT(*),COALESCE(SUM(json_extract(payload,'$.pnl')>0),0),COALESCE(SUM(json_extract(payload,'$.pnl')),0)
                FROM trades WHERE json_extract(payload,'$.status')='closed' AND json_extract(payload,'$.policyVersion')=?''',(self.policy_version,)).fetchone()
        candidates=[dict(c,selected=c['symbol']==self.state['selected']) for c in self.state['candidates']]
        filtered=sum(not c['eligible'] for c in candidates)
        eligible=len(candidates)-filtered
        reasons=Counter(c.get('qualityReason',c['reason']) for c in candidates if not c['eligible'])
        exits=exit_policy(self.policy_version) if self.feed is None else {}
        return dict(id=self.desk_id,name={'crypto':'Cripto','forex':'Forex','stocks':'Acciones'}[self.desk_id],currency=self.currency,
                    session=self.feed.session() if self.feed else dict(open=True,label='Abierto 24/7'),
                    mode='paper',experimental=True,enabled=self.state['enabled'],initial=self.initial,
                    funding=self.state.get('funding'),startedAt=self.state['startedAt'],
                    equity=round(self.equity(),2),cash=round(self.state['cash'],2),benchmark=round(self.hold_equity(),2),
                    selected=self.state['selected'],selection=self.state['selection'],candidates=candidates,
                    benchmarkSymbol=(self.state['benchmark'] or {}).get('symbol'),
                    examined=len(candidates),eligible=eligible,filtered=filtered,discarded=filtered,
                    notSelected=sum(c['eligible'] and not c['selected'] for c in candidates),
                    filterReasons=[dict(reason=reason,count=count) for reason,count in reasons.most_common()],
                    scope=self.feed.scope if self.feed else 'Binance Spot · USDT',scanBatchSize=SCAN_BATCH_SIZE,
                    scanReviewed=self.state.get('scanReviewed',0),maxPositions=MAX_OPEN_POSITIONS,
                    scanIntervalSeconds=SCAN_INTERVAL_S,candleInterval=self.candle_interval,
                    chartInterval=(self.state.get('tape') or {}).get('candleInterval',self.candle_interval),
                    policy=dict(version=self.policy_version,costVersion=ECONOMICS_VERSION,maxNotionalPct=MAX_NOTIONAL_FRACTION*100,minNetRewardRisk=self.min_net_rr,
                                riskPct=RISK_FRACTION*100,totalRiskPct=TOTAL_RISK_FRACTION*100,minVolumeUSD=MIN_QUOTE_VOLUME,
                                maxHoldSeconds=MAX_HOLD_S if self.feed is None else 6*3600,
                                partialAtR=exits.get('partialAtR'),trailAtr=exits.get('trailAtr',2.5),
                                strategyProfile=aggressive.VERSION,riskProfile=aggressive.CAPACITY_VERSION,stopAtr=2,referenceTargetAtr=6,
                                partialFraction=exits.get('partialFraction'),
                                trailAfterR=exits.get('trailAfterR',2),residualStopR=exits.get('residualStopR'),
                                netResidualStop=exits.get('netResidualStop',False),
                                dynamicChandelier=exits.get('dynamicChandelier',False),automaticPromotion=False,
                                chandelierInterval='4h' if self.feed is None else None,
                                longRsMinPercentile=85 if self.feed is None else None,
                                profitableTrendMayExtendHold=self.feed is None,
                                minNetRewardRiskBasis='reference-6atr',
                                selectorCoverage=self.state.get('selectorCoverage'),catalystGate='fresh-opposing-catalyst-v2',
                                strategies=['trend-pullback','breakout','recovery'],validated=False),
                    monitoring=dict(riskIndependent=True,riskError=self.risk_error,
                                    progress=dict(self.progress),riskConversion=self.state.get('riskConversion'),
                                    riskConversionFresh=self.risk_conversion() is not None,
                                    attributionError=self.state.get('attributionError'),
                                    reviewedMarkets=sum(c.get('checkedAt',0)>=time.time()-self.candle_seconds for c in candidates if c['eligible']),
                                    eligibleMarkets=eligible,
                                    quoteAt=(self.last_risk_data or {}).get('at'),scanDurationSeconds=round(self.cycle_duration,3),
                                    dataEfficiency=dict(self.efficiency)),
                    roles=[dict(name=r,**self.state['roles'].get(r,dict(status='waiting',detail='Pendiente',provider='Gemini' if r=='Selector' else 'Reglas deterministas'))) for r in ROLES],
                    position=next((p for p in self.state['positions'] if p['symbol']==self.state['selected']),None),positions=self.state['positions'],trades=trades,events=events,curve=curve,candles=self.chart,
                    maker=self.maker.public(),
                    stats=dict(closed=count,wins=wins,pnl=round(pnl,2),
                               realizedPartialPnlOpen=round(sum(p.get('realizedPnl',0) for p in self.state['positions']),6)),
                    experiment=dict(version=self.policy_version,**self.state.get('policyEpochs',{}).get(self.policy_version,{}),
                                    closed=policy_count,wins=policy_wins,pnl=round(policy_pnl,4),
                                    opened=sum(p.get('policyVersion')==self.policy_version for p in self.state['positions'])),
                    error=self.last_error or self.risk_error,updatedAt=int(max((self.last_data or {}).get('at',0),(self.last_risk_data or {}).get('at',0))) or None,
                    wire=self.state.get('wire'),fees=dict(perSide=self.fee,slippage=self.slippage,assumptions=True),
                    gemini=gemini_agent.AGENT.status() if gemini_agent.AGENT else None)


DESK=None
DESKS={}


def start():
    global DESK
    if DESK is None: DESK=Desk()
    DESKS['crypto']=DESK
    for market in ('forex','stocks'):
        if market not in DESKS: DESKS[market]=Desk(market,DB_PATH.with_name(f'desk-{market}.sqlite'))
    for key,engine in DESKS.items():
        if engine.task is None or engine.task.done(): engine.task=asyncio.create_task(engine.run(),name=f'seven-role-{key}')
        if engine.risk_task is None or engine.risk_task.done(): engine.risk_task=asyncio.create_task(engine.run_risk(),name=f'desk-risk-{key}')


async def stop():
    for engine in list(DESKS.values()) or ([DESK] if DESK else []):
        for attr in ('task','risk_task'):
            task=getattr(engine,attr)
            if task:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
                setattr(engine,attr,None)
