"""Mesa experimental de siete roles. Libro spot SIMULADO sin pasarela de órdenes."""

import asyncio
from collections import Counter
from contextlib import closing
import json
import math
import sqlite3
import time
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from . import gemini_agent, market_data, opportunities
from .config import Symbol
from .database import Connection
from .execution import closed_candles
from .indicators import atr, ema, rsi

DB_PATH = Path(__file__).resolve().parents[2]/'.runtime'/'desk.sqlite'
INITIAL = 1000.0
FEE = .001
SLIPPAGE = .0002
ROLES = ('Selector','Tape','Price','Wire','Size','Exec','Risk')
# Perfil temporal de entradas más frecuentes; no cambia tamaño, stops ni límites diarios.
ENTRY_RSI_MIN = 15
ENTRY_RSI_MAX = 80
ENTRY_EXTENSION_MIN = -4.0
ENTRY_EXTENSION_MAX = 2.25
STABLES = {'USDT','USDC','FDUSD','TUSD','DAI','USDP','USD1','USDD','EUR','AEUR','EURI'}
# Nombres completos de productos apalancados legacy, no un filtro por sufijo:
# JUP (Jupiter), por ejemplo, es un activo spot ordinario.
LEVERAGED_BASES = {
    asset+suffix for asset in ('BTC','ETH','BNB','XRP','ADA','DOT','LINK','LTC','BCH',
                              'EOS','TRX','XLM','UNI','SUSHI','YFI','1INCH','AAVE','SXP','FIL','XTZ')
    for suffix in ('UP','DOWN')
} | {asset+suffix for asset in ('BTC','ETH','EOS','XRP','BNB','TRX') for suffix in ('BULL','BEAR')}
SCAN_BATCH_SIZE = 10
SELECTION_INTERVAL = 6*3600  # Gemini propone prioridades; el escaneo no espera este turno.


def fresh_snapshot(data):
    try:
        age=time.time()-float(data['at'])
        return math.isfinite(age) and -5<=age<=120
    except (KeyError,ValueError,TypeError):
        return False


def connect():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(DB_PATH,factory=Connection)
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
        quality=opportunities.assess(sym,data)
        ticker=data['tickers'].get(symbol,{})
        eligible=quality['eligible'] and fresh_snapshot(data)
        reason=quality['reason'] if fresh_snapshot(data) else 'Cotización antigua o fecha inválida'
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
    rows=closed_candles(rows,900,now)
    if len(rows)<60 or now-rows[-1]['time']>3*900:
        return None
    closes=[c['close'] for c in rows]
    e20,e50=ema(closes,20),ema(closes,50)
    a=atr(rows)
    strength=rsi(closes)[-1]
    if not a or strength is None: return None
    # Se admite proximidad entre medias, además de descuento/reversión.
    in_uptrend = e20[-1] >= e50[-1] - a * .5 and closes[-1] >= e50[-1] - a * .5
    in_discount = closes[-1] <= e20[-1] + a * 1.5 and ENTRY_RSI_MIN <= strength <= 75
    trend = in_uptrend or in_discount
    pullback = min(c['low'] for c in rows[-5:]) <= e20[-1] + a * 1.75
    momentum = closes[-1] > closes[-2] and closes[-1] >= e20[-1]
    return dict(bar=rows[-1]['time'],close=closes[-1],ema20=e20[-1],ema50=e50[-1],
                atr=a,rsi=strength,trend=trend,pullback=pullback,momentum=momentum,rows=rows[-90:])


def size_order(equity, cash, ask, a, info):
    fill=ask*(1+SLIPPAGE)
    stop=fill-1.5*a
    if stop<=0: return None
    risk_per_unit=fill*(1+FEE)-stop*(1-SLIPPAGE)*(1-FEE)
    budget=min(equity/3,cash/(1+FEE),equity*.01/risk_per_unit*fill)
    lot=next((f for f in info.get('filters',[]) if f['filterType']=='LOT_SIZE'),{})
    step=Decimal(str(lot.get('stepSize','.00000001')))
    if step<=0: raise ValueError('Paso inválido')
    qty=float((Decimal(str(budget/fill))/step).to_integral_value(rounding=ROUND_DOWN)*step)
    filters=info.get('filters',[])
    minimum=max([float(f.get('minNotional',0)) for f in filters if f['filterType'] in ('MIN_NOTIONAL','NOTIONAL')] or [0])
    if qty<float(lot.get('minQty',0)) or qty*fill<minimum or qty<=0:
        return None
    return dict(qty=qty,entry=fill,notional=qty*fill,fee=qty*fill*FEE,
                sl=stop,tp=fill+2.5*a,risk=qty*risk_per_unit)


class Desk:
    def __init__(self):
        with connect() as con:
            row=con.execute("SELECT payload FROM state WHERE key='desk'").fetchone()
        self.state=json.loads(row[0]) if row else dict(initial=INITIAL,cash=INITIAL,enabled=True,position=None,
            selected=None,selectionAt=0,selection=None,candidates=[],benchmark=None,startedAt=time.time(),
            day=int(time.time()//86400),dayEquity=INITIAL,dailyStopped=False,roles={},lastScan=0)
        self.task=None
        self.lock=asyncio.Lock()
        self.last_data=None
        self.chart=[]
        self.last_curve=0
        self.last_error=None
        self.save()

    @property
    def initial(self):
        return self.state.get('initial',INITIAL)

    async def reset(self):
        """Nueva sesión paper financiada con 100 EUR; conserva el libro anterior."""
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
            archive=DB_PATH.with_name(f'desk-session-{time.time_ns()}.sqlite')
            with connect() as con:
                with closing(sqlite3.connect(archive)) as backup:
                    con.backup(backup)
                state=dict(initial=initial,cash=initial,enabled=True,position=None,
                    selected=None,selectionAt=0,selection=None,candidates=[],benchmark=None,
                    startedAt=now,day=int(now//86400),dayEquity=initial,dailyStopped=False,
                    roles={},lastScan=0,funding=dict(currency='EUR',amount=100.0,
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
            self.role('Risk','ready','Nueva sesión desde 100 EUR; tamaño proporcional al capital, riesgo objetivo 1%')
            self.save()
        return self.public()

    def save(self):
        with connect() as con:
            con.execute("INSERT OR REPLACE INTO state VALUES ('desk',?)",(json.dumps(self.state),))

    def role(self,name,status,detail,provider=None):
        value=dict(status=status,detail=detail,at=time.time(),provider=provider or ('Gemini' if name=='Selector' else 'Reglas deterministas'))
        previous=self.state['roles'].get(name,{})
        self.state['roles'][name]=value
        if previous.get('status')!=status or previous.get('detail')!=detail:
            with connect() as con:
                con.execute('INSERT INTO events(ts,role,status,detail) VALUES (?,?,?,?)',(value['at'],name,status,detail))

    def quote(self,symbol,data):
        try:
            book=data['books'][symbol]
            bid,ask=float(book['bidPrice']),float(book['askPrice'])
            if not fresh_snapshot(data) or not all(math.isfinite(v) and v>0 for v in (bid,ask)) or ask<bid:
                return None
            return bid,ask
        except (KeyError,ValueError,TypeError): return None

    def equity(self):
        p=self.state['position']
        return self.state['cash']+(p['qty']*p['lastBid']*(1-SLIPPAGE)*(1-FEE) if p else 0)

    def hold_equity(self):
        b=self.state['benchmark']
        return b['qty']*b['lastBid']*(1-SLIPPAGE)*(1-FEE) if b else self.initial

    async def wire(self,symbol,data):
        """Corrobora el instrumento y su fuente; no certifica redes sociales."""
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

    def close_position(self,bid,reason):
        p=self.state['position']
        if not p: return
        exit_price=bid*(1-SLIPPAGE)
        proceeds=p['qty']*exit_price*(1-FEE)
        pnl=proceeds-p['notional']-p['fee']
        closed=dict(p,status='closed',exitPrice=exit_price,exitTime=time.time(),pnl=pnl,
                    exitFee=p['qty']*exit_price*FEE,exitReason=reason)
        self.state['cash']+=proceeds
        self.state['position']=None
        with connect() as con:
            con.execute('UPDATE trades SET payload=? WHERE entry_key=?',(json.dumps(closed),p['entryKey']))
            con.execute("INSERT OR REPLACE INTO state VALUES ('desk',?)",(json.dumps(self.state),))
        self.role('Risk','closed',f'{reason}; resultado neto {pnl:+.2f} USDT')

    def manage(self,data):
        p=self.state['position']
        if not p:
            self.state.pop('flatten',None)
            if not self.state['dailyStopped']:
                self.role('Risk','ready','Control diario activo; riesgo objetivo 1% y exposición máxima 1/3 al entrar')
        if p:
            q=self.quote(p['symbol'],data)
            if not q:
                self.role('Risk','blocked','Sin precio fresco: no se simula un cierre a un precio inventado')
                return
            bid=q[0]
            p['lastBid']=bid
            if self.state.get('flatten') or bid<=p['sl'] or bid>=p['tp'] or time.time()-p['entryTime']>=6*3600:
                self.close_position(bid,'Cierre solicitado' if self.state.get('flatten') else 'Stop' if bid<=p['sl'] else 'Objetivo' if bid>=p['tp'] else 'Tiempo máximo')
                self.state.pop('flatten',None)
            else:
                p['best']=max(p['best'],bid)
                if bid>=p['entry']+p['initialDist']:
                    p['sl']=max(p['sl'],p['entry']*(1+FEE*2),p['best']-1.2*p['atr'])
                self.role('Risk','managing',f'Stop activo; riesgo inicial {p["risk"]:.2f} USDT')
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
            if self.state['position']:
                q=self.quote(self.state['position']['symbol'],data)
                if q: self.close_position(q[0],'Límite diario de pérdida')
            self.role('Risk','blocked','Pérdida diaria del 5%: entradas detenidas')

    async def scan(self,data):
        now=time.time()
        candidates=universe(data)
        previous={c['symbol']:c for c in self.state['candidates']}
        for candidate in candidates:
            old=previous.get(candidate['symbol'],{})
            if candidate['eligible'] and old.get('eligible') and old.get('checkedAt'):
                candidate.update({k:old[k] for k in ('status','reason','checkedAt') if k in old})
        self.state['candidates']=candidates
        self.state['scanReviewed']=0
        position=self.state['position']
        if position:
            # Una posición conserva su símbolo, controles y contabilidad hasta el cierre.
            self.state['selected']=position['symbol']
            candidate=next((c for c in candidates if c['symbol']==position['symbol']),None)
            if candidate is None:
                self.role('Exec','blocked','El mercado de la posición no está disponible; se mantiene su control de riesgo')
                return
            try:
                await self.review_candidate(candidate,data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.evaluated(candidate,'error',f'Análisis de la posición temporalmente no disponible ({type(exc).__name__}); se conservan stop y objetivo')
                self.role('Exec','waiting',candidate['reason'])
            self.state['scanReviewed']=1
            return
        if not self.state['enabled'] or self.state['dailyStopped']:
            return
        eligible=[c for c in candidates if c['eligible']]
        if not eligible:
            self.state['selected']=None
            self.state['selectionPending']=False
            self.role('Selector','waiting','Ningún mercado pasa los controles de cotización, spread y volumen',provider='Reglas deterministas')
            return
        cursor=int(self.state.get('scanCursor',0))%len(eligible)
        rotated=eligible[cursor:]+eligible[:cursor]
        batch=rotated[:SCAN_BATCH_SIZE]
        agent=gemini_agent.AGENT
        choice=None
        if agent and (not self.state.get('selection') or now-self.state.get('selectionAt',0)>=SELECTION_INTERVAL):
            self.state['selectionPending']=True
            self.role('Selector','waiting',f'{len(candidates)} mercados examinados; Gemini prioriza un lote de {len(batch)}')
            try:
                # El universo completo permanece público, pero nunca se envían cientos de filas al modelo.
                choice=await agent.choose_market([{k:c[k] for k in ('symbol','eligible','change24h','volumeUSD','spreadBp')} for c in batch])
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
        for candidate in batch:
            self.state['scanReviewed']+=1
            # Persistir el avance incluso si una entrada o un error termina esta vuelta.
            self.state['scanCursor']=(cursor+self.state['scanReviewed'])%len(eligible)
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
                await self.review_candidate(candidate,data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.evaluated(candidate,'error',f'Análisis temporalmente no disponible ({type(exc).__name__}); se continúa con otros mercados')
                self.role('Exec','waiting',candidate['reason'])
            if self.state['position']:
                break

    def evaluated(self,candidate,status,reason):
        candidate.update(status=status,reason=reason,checkedAt=time.time())

    async def review_candidate(self,candidate,data):
        symbol=candidate['symbol']
        meta=data['metadata'].get(symbol,{})
        base=meta.get('baseAsset','')
        quality=opportunities.assess(Symbol(base,base,'cripto','binance',symbol),data)
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
            self.state['benchmark']=dict(symbol=symbol,qty=self.initial/(ask*(1+SLIPPAGE)*(1+FEE)),lastBid=bid,startedAt=time.time())
        sym=Symbol(base,base,'cripto','binance',symbol)
        analysis=tape(await market_data.get_candles(sym,'15m',250),time.time())
        if analysis is None:
            self.evaluated(candidate,'blocked','Velas cerradas insuficientes o antiguas')
            self.role('Tape','blocked','Velas cerradas insuficientes o antiguas')
            return
        self.chart=analysis['rows']
        self.state['tape']={k:v for k,v in analysis.items() if k!='rows'}
        self.role('Tape','ready' if analysis['trend'] else 'waiting',f'EMA20/50; RSI {analysis["rsi"]:.0f}; ATR {analysis["atr"]:.5g}')
        extension=(ask-analysis['ema20'])/analysis['atr']
        setup_ok=analysis['pullback'] or analysis.get('momentum',False)
        price_ok=(analysis['trend'] and setup_ok and ENTRY_RSI_MIN<=analysis['rsi']<=ENTRY_RSI_MAX
                  and ENTRY_EXTENSION_MIN<=extension<=ENTRY_EXTENSION_MAX
                  and abs(ask-analysis['close'])<=2.0*analysis['atr'])
        self.role('Price','ready' if price_ok else 'waiting',
                  'Retroceso o impulso moderado confirmado' if price_ok else 'Esperando señal dentro del rango de RSI y extensión permitido')
        order=size_order(self.equity(),self.state['cash'],ask,analysis['atr'],meta)
        self.role('Size','ready' if order else 'blocked',f'Tamaño {order["notional"]:.2f} USDT; máximo 1/3; riesgo {order["risk"]:.2f}' if order else 'Lote mínimo incompatible con presupuesto')
        if self.state['position']:
            if not analysis['trend'] and analysis['close']<analysis['ema50'] - 2.0 * analysis['atr']:
                self.close_position(bid,'Tape detecta pérdida de estructura')
                self.evaluated(candidate,'waiting','Posición cerrada por pérdida de estructura')
                self.role('Exec','waiting','Posición cerrada por cambio de tendencia')
                return
            self.role('Exec','managing','Una posición abierta; no se acumulan entradas')
            self.evaluated(candidate,'managing','Posición abierta: se mantiene su gestión de riesgo')
            return
        if not self.state['enabled'] or self.state['dailyStopped'] or not price_ok or not identity['ok'] or not order:
            self.evaluated(candidate,'waiting','Sin señal de Price' if not price_ok else 'Lote mínimo incompatible con presupuesto' if not order else 'Entradas pausadas por control de riesgo')
            self.role('Exec','waiting','Esperando aprobación de Price, Wire, Size y Risk')
            return
        key=f'{symbol}:{analysis["bar"]}'
        with connect() as con:
            if con.execute('SELECT 1 FROM trades WHERE entry_key=?',(key,)).fetchone():
                self.evaluated(candidate,'waiting','Esta vela ya produjo una operación; no se duplica')
                return
        if not self.quote(symbol,data):
            self.evaluated(candidate,'blocked','La cotización caducó durante el análisis; entrada bloqueada')
            self.role('Exec','blocked','La cotización caducó durante el análisis; entrada bloqueada')
            return
        available=float(data['books'][symbol].get('askQty',0))
        if not math.isfinite(available) or available<=0 or order['qty']>available*.5:
            self.evaluated(candidate,'blocked','Tamaño superior a la liquidez publicada al mejor ask')
            self.role('Exec','blocked','Tamaño superior a la liquidez publicada al mejor ask')
            return
        position=dict(order,symbol=symbol,entryKey=key,entryTime=time.time(),atr=analysis['atr'],
                      initialDist=order['entry']-order['sl'],best=order['entry'],lastBid=bid,status='open',
                      evidence=dict(tape=self.state['tape'],wire=identity,selection=self.state['selection']))
        self.state['cash']-=order['notional']+order['fee']
        self.state['position']=position
        with connect() as con:
            con.execute('INSERT INTO trades(entry_key,payload) VALUES (?,?)',(key,json.dumps(position)))
            con.execute("INSERT OR REPLACE INTO state VALUES ('desk',?)",(json.dumps(self.state),))
        self.role('Exec','executed',f'Compra SIMULADA de {base}; comisión y deslizamiento incluidos')
        self.evaluated(candidate,'executed','Operación simulada abierta; stop, objetivo, comisión y deslizamiento aplicados')
        self.role('Risk','managing','Stop y objetivo establecidos al abrir')

    async def cycle(self):
        async with self.lock:
            data=await opportunities.snapshot()
            self.last_data=data
            self.manage(data)
            if (self.state['enabled'] or self.state['selected']) and time.time()-self.state['lastScan']>=60:
                self.state['lastScan']=time.time()
                await self.scan(data)
            if time.time()-self.last_curve>=60:
                self.last_curve=time.time()
                with connect() as con:
                    con.execute('INSERT OR REPLACE INTO equity VALUES (?,?,?)',(int(self.last_curve),self.equity(),self.hold_equity()))
            self.save()
            self.last_error=None

    async def run(self):
        while True:
            try: await self.cycle()
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.last_error=f'Fuentes temporalmente no disponibles ({type(exc).__name__}); se conservan cuenta y controles'
                self.role('Risk','blocked',self.last_error)
            await asyncio.sleep(10)

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
        with connect() as con:
            trades=[json.loads(r[0]) for r in con.execute('SELECT payload FROM trades ORDER BY id DESC LIMIT 100')]
            events=[dict(id=i,at=t,role=r,status=s,detail=d) for i,t,r,s,d in con.execute('SELECT id,ts,role,status,detail FROM events ORDER BY id DESC LIMIT 30')]
            curve=[dict(time=t,equity=e,benchmark=b) for t,e,b in con.execute('SELECT ts,equity,benchmark FROM equity ORDER BY ts DESC LIMIT 1440')][::-1]
        with connect() as con:
            count,wins,pnl=con.execute('''SELECT COUNT(*),COALESCE(SUM(json_extract(payload,'$.pnl')>0),0),COALESCE(SUM(json_extract(payload,'$.pnl')),0)
                FROM trades WHERE json_extract(payload,'$.status')='closed' ''').fetchone()
        candidates=[dict(c,selected=c['symbol']==self.state['selected']) for c in self.state['candidates']]
        filtered=sum(not c['eligible'] for c in candidates)
        eligible=len(candidates)-filtered
        reasons=Counter(c.get('qualityReason',c['reason']) for c in candidates if not c['eligible'])
        return dict(mode='paper',experimental=True,enabled=self.state['enabled'],initial=self.initial,
                    funding=self.state.get('funding'),startedAt=self.state['startedAt'],
                    equity=round(self.equity(),2),cash=round(self.state['cash'],2),benchmark=round(self.hold_equity(),2),
                    selected=self.state['selected'],selection=self.state['selection'],candidates=candidates,
                    benchmarkSymbol=(self.state['benchmark'] or {}).get('symbol'),
                    examined=len(candidates),eligible=eligible,filtered=filtered,discarded=filtered,
                    notSelected=sum(c['eligible'] and not c['selected'] for c in candidates),
                    filterReasons=[dict(reason=reason,count=count) for reason,count in reasons.most_common()],
                    scope='Binance Spot · USDT',scanBatchSize=SCAN_BATCH_SIZE,
                    scanReviewed=self.state.get('scanReviewed',0),maxPositions=1,
                    roles=[dict(name=r,**self.state['roles'].get(r,dict(status='waiting',detail='Pendiente',provider='Gemini' if r=='Selector' else 'Reglas deterministas'))) for r in ROLES],
                    position=self.state['position'],trades=trades,events=events,curve=curve,candles=self.chart,
                    stats=dict(closed=count,wins=wins,pnl=round(pnl,2)),
                    error=self.last_error,updatedAt=int(self.last_data['at']) if self.last_data else None,
                    wire=self.state.get('wire'),fees=dict(perSide=FEE,slippage=SLIPPAGE),
                    gemini=gemini_agent.AGENT.status() if gemini_agent.AGENT else None)


DESK=None


def start():
    global DESK
    if DESK is None: DESK=Desk()
    if DESK.task is None or DESK.task.done(): DESK.task=asyncio.create_task(DESK.run(),name='seven-role-desk')


async def stop():
    if DESK and DESK.task:
        DESK.task.cancel()
        try: await DESK.task
        except asyncio.CancelledError: pass
        DESK.task=None
