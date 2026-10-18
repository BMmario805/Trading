"""Mesa experimental de siete roles. Libro spot SIMULADO sin pasarela de órdenes."""

import asyncio
from collections import Counter
from contextlib import closing
import json
import logging
import math
import sqlite3
import time
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from . import gemini_agent, market_data, opportunities, portfolio_risk
from .config import Symbol
from .database import Connection
from .execution import closed_candles
from .indicators import atr, ema, rsi
from .economics import planned_trade,ECONOMICS_VERSION

DB_PATH = Path(__file__).resolve().parents[2]/'.runtime'/'desk.sqlite'
INITIAL = 1000.0
FEE = .001
SLIPPAGE = .0002
ROLES = ('Selector','Tape','Price','Wire','Size','Exec','Risk')
log=logging.getLogger('desk')
# Señales experimentales confirmadas; sin validación económica para promoción.
ENTRY_RSI_MIN = 35
ENTRY_RSI_MAX = 72
ENTRY_EXTENSION_MIN = -1.0
ENTRY_EXTENSION_MAX = 1.5
STABLES = {'USDT','USDC','FDUSD','TUSD','DAI','USDP','USD1','USDD','EUR','AEUR','EURI',
           'RLUSD','USDE','USDS','PYUSD','USDG','USDD','USDF','USDX','USDSB','EURCV'}
# Nombres completos de productos apalancados legacy, no un filtro por sufijo:
# JUP (Jupiter), por ejemplo, es un activo spot ordinario.
LEVERAGED_BASES = {
    asset+suffix for asset in ('BTC','ETH','BNB','XRP','ADA','DOT','LINK','LTC','BCH',
                              'EOS','TRX','XLM','UNI','SUSHI','YFI','1INCH','AAVE','SXP','FIL','XTZ')
    for suffix in ('UP','DOWN')
} | {asset+suffix for asset in ('BTC','ETH','EOS','XRP','BNB','TRX') for suffix in ('BULL','BEAR')}
# bStocks publicados por Binance en junio/julio de 2026. Son exposición a
# acciones/ETF, incluso el ETF apalancado SOXL; no son criptomonedas ordinarias.
TOKENIZED_EQUITIES={ticker+'B' for ticker in ('AAPL','CRCL','MU','NVDA','SNDK','TSLA',
    'SPCX','AMD','EWY','INTC','MSTR','LITE','META','MSFT','PLTR','QQQ','CBRS','COIN',
    'DRAM','GLW','GOOGL','NBIS','QCOM','SOXL','SPY','WDC')}
SCAN_BATCH_SIZE = 10
MAX_OPEN_POSITIONS = 3
SCAN_INTERVAL_S = 20
CANDLE_INTERVAL = '15m'
CANDLE_SECONDS = 900
MAX_HOLD_S = 6*3600
MAX_NOTIONAL_FRACTION = 1/6  # Conserva el límite reducido solicitado en el archivo local.
MIN_NET_REWARD_RISK = 1.2
POLICY_VERSION = 'desk-confirmed-v2'
MIN_QUOTE_VOLUME = 2_000_000
RISK_FRACTION = .005
TOTAL_RISK_FRACTION = .015
SELECTION_INTERVAL = 6*3600  # Gemini propone prioridades; el escaneo no espera este turno.


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


def universe(data):
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
    return sorted(candidates,key=lambda c:(-c['volumeUSD'],c['symbol']))


def tape(rows, now):
    rows=closed_candles(rows,CANDLE_SECONDS,now)
    if len(rows)<60 or now-rows[-1]['time']>3*CANDLE_SECONDS:
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


def size_order(equity, cash, ask, a, info, risk_budget=None, *, fee=FEE,slippage=SLIPPAGE):
    if not all(math.isfinite(v) and v>0 for v in (equity,cash,ask,a)): return None
    fill=ask*(1+slippage)
    stop=fill-1.5*a
    if stop<=0: return None
    risk_per_unit=fill*(1+fee)-stop*(1-slippage)*(1-fee)
    allowed_risk=min(equity*RISK_FRACTION,risk_budget) if risk_budget is not None else equity*RISK_FRACTION
    budget=min(equity*MAX_NOTIONAL_FRACTION,cash/(1+fee),max(0,allowed_risk)/risk_per_unit*fill)
    lot=next((f for f in info.get('filters',[]) if f['filterType']=='LOT_SIZE'),{})
    step=Decimal(str(lot.get('stepSize','.00000001')))
    if step<=0: raise ValueError('Paso inválido')
    qty=float((Decimal(str(budget/fill))/step).to_integral_value(rounding=ROUND_DOWN)*step)
    filters=info.get('filters',[])
    minimum=max([float(f.get('minNotional',0)) for f in filters if f['filterType'] in ('MIN_NOTIONAL','NOTIONAL')] or [0])
    if qty<float(lot.get('minQty',0)) or qty*fill<minimum or qty<=0:
        return None
    max_qty=float(lot.get('maxQty',0))
    if max_qty>0 and qty>max_qty: return None
    target=fill+3*a
    plan=planned_trade(fill,stop,target,qty,fee_rate=fee,exit_slippage=slippage)
    if not plan['viable'] or plan['netRewardRisk']<MIN_NET_REWARD_RISK:
        return None
    return dict(qty=qty,entry=fill,notional=qty*fill,fee=qty*fill*fee,
                sl=stop,tp=target,risk=qty*risk_per_unit,economics=plan,
                costPolicy=dict(version=ECONOMICS_VERSION,feeRate=fee,slippageRate=slippage),policyVersion=POLICY_VERSION,
                maxHoldSeconds=MAX_HOLD_S)


class Desk:
    def __init__(self, market='crypto', db_path=None):
        self.desk_id=market
        self.db_path=Path(db_path or DB_PATH)
        self.currency='USDT' if market=='crypto' else 'USD'
        self.feed=None
        if market!='crypto':
            from .cash_markets import CashMarketFeed
            self.feed=CashMarketFeed(market)
        self.fee=FEE if self.feed is None else self.feed.fee
        self.slippage=SLIPPAGE if self.feed is None else self.feed.slippage
        with self.connect() as con:
            row=con.execute("SELECT payload FROM state WHERE key='desk'").fetchone()
        self.state=json.loads(row[0]) if row else dict(initial=INITIAL,cash=INITIAL,enabled=True,position=None,
            selected=None,selectionAt=0,selection=None,candidates=[],benchmark=None,startedAt=time.time(),
            day=int(time.time()//86400),dayEquity=INITIAL,dailyStopped=False,roles={},lastScan=0)
        self.state.setdefault('positions',[self.state['position']] if self.state.get('position') else [])
        self.state.setdefault('cooldowns',{})
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
        self.chart=[]
        self.last_curve=0
        self.last_error=None
        self.candle_cache={}
        self.efficiency=dict(candleDownloads=0,candleCacheHits=0,bookBatches=0)
        epochs=self.state.setdefault('policyEpochs',{})
        epochs.setdefault(POLICY_VERSION,dict(at=time.time(),equity=self.equity()))
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

    async def candidate_candles(self,sym):
        """Una descarga por vela cerrada; un feed retrasado se vuelve a consultar.

        Solo se reutiliza histórico, nunca cotizaciones de ejecución ni controles
        de cartera. Al cambiar de vela no se sirve el análisis anterior.
        """
        now=time.time()
        expected=int(now//CANDLE_SECONDS)*CANDLE_SECONDS-CANDLE_SECONDS
        cached=self.candle_cache.get(sym.source_symbol)
        if cached and cached[0]==expected:
            self.efficiency['candleCacheHits']+=1
            return cached[1]
        rows=await market_data.get_candles(sym,CANDLE_INTERVAL,250)
        self.efficiency['candleDownloads']+=1
        completed=closed_candles(rows,CANDLE_SECONDS,time.time())
        if completed and completed[-1]['time']==expected:
            self.candle_cache[sym.source_symbol]=(expected,rows)
            if len(self.candle_cache)>512:
                self.candle_cache.pop(next(iter(self.candle_cache)))
        return rows

    @property
    def initial(self):
        return self.state.get('initial',INITIAL)

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
                    roles={},lastScan=0,cooldowns={},policyEpochs={POLICY_VERSION:dict(at=now,equity=initial)},funding=dict(currency='EUR',amount=100.0,
                    netQuotePerEuro=rate,bid=quote[0],at=data['at'],source='Binance EURUSDT',
                    fee=FEE,slippage=SLIPPAGE),previousSession=archive.name)
                con.execute('DELETE FROM events')
                con.execute('DELETE FROM trades')
                con.execute('DELETE FROM equity')
                con.execute("INSERT OR REPLACE INTO state VALUES ('desk',?)",(json.dumps(state),))
                con.execute('INSERT INTO equity VALUES (?,?,?)',(int(now),initial,initial))
            self.state=state
            self.last_data=data
            self.chart=[]
            self.last_curve=now
            self.last_error=None
            self.role('Risk','ready','Nueva sesión desde 100 EUR; tamaño proporcional al capital, riesgo objetivo 0,5%')
            self.save()
        return self.public()

    def save(self):
        self.focus_position()
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO state VALUES ('desk',?)",(json.dumps(self.state),))

    def focus_position(self):
        positions=self.state['positions']
        self.state['position']=next((p for p in positions if p['symbol']==self.state['selected']),
                                    positions[-1] if positions else None)

    def role(self,name,status,detail,provider=None):
        value=dict(status=status,detail=detail,at=time.time(),provider=provider or ('Gemini' if name=='Selector' else 'Reglas deterministas'))
        previous=self.state['roles'].get(name,{})
        self.state['roles'][name]=value
        if previous.get('status')!=status or previous.get('detail')!=detail:
            with self.connect() as con:
                con.execute('INSERT INTO events(ts,role,status,detail) VALUES (?,?,?,?)',(value['at'],name,status,detail))

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
        fee,slip=self.costs(p)
        exit_price=bid*(1-slip)
        proceeds=p['qty']*exit_price*(1-fee)
        pnl=proceeds-p['notional']-p['fee']
        closed=dict(p,status='closed',exitPrice=exit_price,exitTime=time.time(),pnl=pnl,
                    exitFee=p['qty']*exit_price*fee,exitReason=reason)
        self.state['cash']+=proceeds
        self.state['positions'].remove(p)
        self.state['cooldowns'][p['symbol']]=time.time()+CANDLE_SECONDS*(4 if pnl<=0 else 1)
        self.focus_position()
        with self.connect() as con:
            con.execute('UPDATE trades SET payload=? WHERE entry_key=?',(json.dumps(closed),p['entryKey']))
            con.execute("INSERT OR REPLACE INTO state VALUES ('desk',?)",(json.dumps(self.state),))
        self.role('Risk','closed',f'{reason}; resultado neto {pnl:+.2f} {self.currency}')

    def manage(self,data):
        if not self.state['positions']:
            self.state.pop('flatten',None)
            if not self.state['dailyStopped']:
                self.role('Risk','ready','Riesgo objetivo 0,5%; riesgo conjunto 1,5%; exposición máxima 1/6 al entrar')
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
            hold=p.get('maxHoldSeconds',45*60)
            if self.state.get('flatten') or bid<=p['sl'] or bid>=p['tp'] or time.time()-p['entryTime']>=hold:
                self.close_position(bid,'Cierre solicitado' if self.state.get('flatten') else 'Stop' if bid<=p['sl'] else 'Objetivo' if bid>=p['tp'] else 'Tiempo máximo',p['symbol'])
            else:
                p['best']=max(p['best'],bid)
                if bid>=p['entry']+1.0*p['initialDist']:
                    fee,slip=self.costs(p)
                    stop=max(p['sl'],(p['notional']+p['fee'])/(p['qty']*(1-slip)*(1-fee)),p['best']-1.5*p['atr'])
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
        previous={c['symbol']:c for c in self.state['candidates']}
        for candidate in candidates:
            old=previous.get(candidate['symbol'],{})
            if candidate['eligible'] and old.get('eligible') and old.get('checkedAt') and old.get('policyVersion')==POLICY_VERSION:
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
            self.role('Exec','managing','Cupo completo: se gestionan las tres posiciones')
            return
        if not self.state['enabled'] or self.state['dailyStopped']:
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
        batch=rotated[:SCAN_BATCH_SIZE]
        agent=gemini_agent.AGENT if self.feed is None else None
        choice=None
        if agent and (not self.state.get('selection') or now-self.state.get('selectionAt',0)>=SELECTION_INTERVAL):
            self.state['selectionPending']=True
            self.role('Selector','waiting',f'{len(candidates)} mercados examinados; Gemini prioriza un lote de {len(batch)}')
            try:
                # El universo completo permanece público, pero nunca se envían cientos de filas al modelo.
                choice=await asyncio.wait_for(agent.choose_market([{k:c[k] for k in ('symbol','eligible','change24h','volumeUSD','spreadBp')} for c in batch]),timeout=8)
            except asyncio.CancelledError:
                raise
            except Exception:
                choice=None  # Una fuente auxiliar no debe detener el resto del escaneo.
            finally:
                self.state['selectionPending']=False
            self.state['selectionAt']=now
        if choice and choice.get('symbol') in {c['symbol'] for c in batch}:
            batch.sort(key=lambda c:c['symbol']!=choice['symbol'])
        else:
            choice=None
        # Descargas acotadas en paralelo; las decisiones y el libro se actualizan en serie.
        prefetched={}
        if self.task is not None:
            semaphore=asyncio.Semaphore(4)
            async def download(candidate):
                async with semaphore:
                    try:
                        rows=await asyncio.wait_for(self.candidate_candles(self.market_symbol(candidate['symbol'],data)),timeout=5)
                        return candidate['symbol'],rows
                    except Exception:
                        return candidate['symbol'],None
            prefetched=dict(await asyncio.gather(*(download(c) for c in batch)))
            if self.feed is None:
                # Un libro reciente compartido por el lote; el monitor de riesgo
                # sigue teniendo su tarea independiente y su tiempo máximo.
                try:
                    data=await self.market_books([c['symbol'] for c in batch],data)
                    self.efficiency['bookBatches']+=1
                except Exception:
                    log.warning('Libro del lote no disponible en %s; se consultan mercados individualmente',self.desk_id)
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
            selected_by_ai=choice is not None and candidate['symbol']==choice['symbol']
            selection=dict(choice,source='gemini') if selected_by_ai else dict(
                symbol=candidate['symbol'],source='rules',reason='Revisión rotativa por liquidez; sin estimación de confianza')
            self.state['selection']=selection
            self.role('Selector','selected',f'{candidate["symbol"]}: {selection["reason"]}',
                      provider='Gemini' if selected_by_ai else 'Reglas deterministas')
            try:
                await asyncio.wait_for(self.review_candidate(candidate,data,prefetched),timeout=8)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception('Error analizando candidato %s en %s',candidate['symbol'],self.desk_id)
                self.evaluated(candidate,'error',f'Análisis temporalmente no disponible ({type(exc).__name__}); se continúa con otros mercados')
                self.role('Exec','waiting',candidate['reason'])
            if len(self.state['positions'])>=MAX_OPEN_POSITIONS:
                break

    def evaluated(self,candidate,status,reason):
        candidate.update(status=status,reason=reason,checkedAt=time.time(),policyVersion=POLICY_VERSION)
        if status=='error':
            candidate['errorCount']=candidate.get('errorCount',0)+1
            candidate['retryAfter']=time.time()+min(900,60*2**min(candidate['errorCount'],4))
        else:
            candidate.pop('retryAfter',None)
            candidate.pop('errorCount',None)

    async def review_candidate(self,candidate,data,prefetched=None):
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
        rows=prefetched[symbol] if prefetched and symbol in prefetched else await self.candidate_candles(sym)
        analysis=tape(rows or [],time.time())
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
        self.state['tape']={k:v for k,v in analysis.items() if k!='rows'}
        candidate['signal']=dict(strategy=analysis.get('strategy'),rsi=round(analysis['rsi'],2),
                                 atrPct=round(analysis['atr']/ask*100,3),trend=analysis['trend'])
        self.role('Tape','ready' if analysis['trend'] else 'waiting',f'EMA20/50; RSI {analysis["rsi"]:.0f}; ATR {analysis["atr"]:.5g}')
        extension=(ask-analysis['ema20'])/analysis['atr']
        setup_ok=analysis.get('entryAllowed',analysis['pullback'] or analysis.get('momentum',False))
        price_ok=(analysis['trend'] and setup_ok and ENTRY_RSI_MIN<=analysis['rsi']<=ENTRY_RSI_MAX
                  and ENTRY_EXTENSION_MIN<=extension<=ENTRY_EXTENSION_MAX
                  and abs(ask-analysis['close'])<=2.0*analysis['atr'])
        self.role('Price','ready' if price_ok else 'waiting',
                  f'Señal confirmada: {analysis.get("strategy") or "continuidad"}' if price_ok else 'Esperando recuperación o tendencia confirmada, RSI 35–72 y extensión moderada')
        existing=next((p for p in self.state['positions'] if p['symbol']==symbol),None)
        if existing:
            self.role('Size','managing',f'Asignación existente: {existing["notional"]:.2f} USDT; no se solicita otro lote')
            if not analysis['trend'] and analysis['close']<analysis['ema50'] - 2.0 * analysis['atr']:
                self.close_position(bid,'Tape detecta pérdida de estructura',symbol)
                self.evaluated(candidate,'waiting','Posición cerrada por pérdida de estructura')
                self.role('Exec','waiting','Posición cerrada por cambio de tendencia')
                return
            self.role('Exec','managing',f'{symbol} ya está abierto; no se duplica')
            self.evaluated(candidate,'managing','Posición abierta: se mantiene su gestión de riesgo')
            return
        if len(self.state['positions'])>=MAX_OPEN_POSITIONS:
            self.evaluated(candidate,'blocked','Máximo de tres posiciones simultáneas')
            return
        if self.state['cooldowns'].get(symbol,0)>time.time():
            self.evaluated(candidate,'waiting','En espera tras el último cierre; se evita reentrar inmediatamente')
            return
        open_risk=sum(max(0,p['notional']+p['fee']-p['qty']*p['sl']*(1-self.costs(p)[1])*(1-self.costs(p)[0])) for p in self.state['positions'])
        order=size_order(self.equity(),self.state['cash'],ask,analysis['atr'],meta,max(0,self.equity()*TOTAL_RISK_FRACTION-open_risk),fee=self.fee,slippage=self.slippage)
        geometry=planned_trade(ask*(1+self.slippage),ask*(1+self.slippage)-1.5*analysis['atr'],
                               ask*(1+self.slippage)+3*analysis['atr'],1,fee_rate=self.fee,exit_slippage=self.slippage) if ask*(1+self.slippage)>1.5*analysis['atr'] else None
        candidate['economics']=geometry
        candidate['entryChecks']=dict(signal=price_ok,costs=bool(geometry and geometry['viable'] and geometry['netRewardRisk']>=MIN_NET_REWARD_RISK),
                                      size=order is not None,enabled=self.state['enabled'] and not self.state['dailyStopped'])
        size_reason='Lote mínimo o beneficio/riesgo neto insuficiente después de costes'
        self.role('Size','ready' if order else 'blocked',f'Tamaño {order["notional"]:.2f} {self.currency}; máximo 1/6; riesgo {order["risk"]:.2f}' if order else size_reason)
        if not self.state['enabled'] or self.state['dailyStopped'] or not price_ok or not identity['ok'] or not order:
            self.evaluated(candidate,'waiting',f'Sin confirmación de entrada; RSI {analysis["rsi"]:.0f}, extensión {extension:.1f} ATR' if not price_ok else size_reason if not order else 'Entradas pausadas por control de riesgo')
            self.role('Exec','waiting','Esperando aprobación de Price, Wire, Size y Risk')
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
        rate=(self.state.get('funding') or {}).get('netQuotePerEuro')
        if self.feed and not rate:
            self.evaluated(candidate,'blocked','Conversión EUR/USD reciente pendiente para el riesgo agregado')
            return
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
                      evidence=dict(tape=self.state['tape'],wire=identity,selection=self.state['selection']))
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

    async def cycle(self):
        started=time.monotonic()
        data=await self.market_snapshot()
        async with self.lock:
            self.last_data=data
            if self.feed and self.feed.quote_per_euro and not self.state.get('funding'):
                rate=self.feed.quote_per_euro
                self.state['funding']=dict(currency='EUR',amount=self.initial/rate,netQuotePerEuro=rate,
                                           at=time.time(),source='Yahoo EURUSD; cuenta inicial 100 USD')
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

    async def risk_cycle(self):
        symbols=[p['symbol'] for p in self.state['positions']]
        if not symbols:
            self.risk_error=None
            return
        data=await self.market_books(symbols,self.last_data or {})
        async with self.lock:
            self.last_risk_data=data
            self.manage(data)
            self.risk_error=None
            self.save()

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
            if action=='start': self.state['enabled']=not self.state['dailyStopped']
            elif action=='pause': self.state['enabled']=False
            elif action=='close':
                self.state['enabled']=False
                self.state['flatten']=True
            else: raise ValueError('Acción inválida')
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
                FROM trades WHERE json_extract(payload,'$.status')='closed' AND json_extract(payload,'$.policyVersion')=?''',(POLICY_VERSION,)).fetchone()
        candidates=[dict(c,selected=c['symbol']==self.state['selected']) for c in self.state['candidates']]
        filtered=sum(not c['eligible'] for c in candidates)
        eligible=len(candidates)-filtered
        reasons=Counter(c.get('qualityReason',c['reason']) for c in candidates if not c['eligible'])
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
                    scanIntervalSeconds=SCAN_INTERVAL_S,candleInterval=CANDLE_INTERVAL,
                    policy=dict(version=POLICY_VERSION,costVersion=ECONOMICS_VERSION,maxNotionalPct=MAX_NOTIONAL_FRACTION*100,minNetRewardRisk=MIN_NET_REWARD_RISK,
                                riskPct=RISK_FRACTION*100,totalRiskPct=TOTAL_RISK_FRACTION*100,minVolumeUSD=MIN_QUOTE_VOLUME,
                                strategies=['trend-pullback','breakout','recovery'],validated=False),
                    monitoring=dict(riskIndependent=True,riskError=self.risk_error,
                                    reviewedMarkets=sum(c.get('checkedAt',0)>=time.time()-CANDLE_SECONDS for c in candidates if c['eligible']),
                                    eligibleMarkets=eligible,
                                    quoteAt=(self.last_risk_data or {}).get('at'),scanDurationSeconds=round(self.cycle_duration,3),
                                    dataEfficiency=dict(self.efficiency)),
                    roles=[dict(name=r,**self.state['roles'].get(r,dict(status='waiting',detail='Pendiente',provider='Gemini' if r=='Selector' else 'Reglas deterministas'))) for r in ROLES],
                    position=next((p for p in self.state['positions'] if p['symbol']==self.state['selected']),None),positions=self.state['positions'],trades=trades,events=events,curve=curve,candles=self.chart,
                    stats=dict(closed=count,wins=wins,pnl=round(pnl,2)),
                    experiment=dict(version=POLICY_VERSION,**self.state.get('policyEpochs',{}).get(POLICY_VERSION,{}),
                                    closed=policy_count,wins=policy_wins,pnl=round(policy_pnl,4),
                                    opened=sum(p.get('policyVersion')==POLICY_VERSION for p in self.state['positions'])),
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
