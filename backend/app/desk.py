"""Mesa experimental de siete roles. Libro spot SIMULADO sin pasarela de órdenes."""

import asyncio
import json
import math
import re
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
STABLES = {'USDT','USDC','FDUSD','TUSD','DAI','USDP','USD1','USDD','EUR','AEUR','EURI','PAXG'}
OFFICIAL = {
    'SOL': ('https://solana.com/','solana'),
    'ETH': ('https://ethereum.org/community/','ethdotorg'),
    'LINK': ('https://chain.link/community','chainlink'),
    'SUI': ('https://www.sui.io/community','SuiNetwork'),
    'BNB': ('https://www.bnbchain.org/en','BNBCHAIN'),
    'AVAX': ('https://www.avax.network/','avax'),
    'DOGE': ('https://dogecoin.com/','dogecoin'),
}


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
        if meta.get('quoteAsset')!='USDT' or base in STABLES or base.endswith(('UP','DOWN','BULL','BEAR')):
            continue
        if meta.get('status')!='TRADING' or not meta.get('isSpotTradingAllowed'):
            continue
        sym=Symbol(base,base,'cripto','binance',symbol)
        quality=opportunities.assess(sym,data)
        ticker=data['tickers'].get(symbol,{})
        try:
            change=float(ticker['priceChangePercent'])
            if not math.isfinite(change): continue
        except (KeyError,ValueError,TypeError):
            continue
        identity_ref=base in OFFICIAL
        candidates.append(dict(symbol=symbol,base=base,eligible=quality['eligible'] and identity_ref,
                               change24h=round(change,2),volumeUSD=round(quality.get('quoteVolume24h',0)),
                               spreadBp=round(quality.get('spreadPct',100)*100,2),
                               reason=quality['reason'] if not quality['eligible'] or identity_ref else 'Sin referencia oficial para Wire'))
    return sorted(candidates,key=lambda c:(-c['volumeUSD'],c['symbol']))[:81]


def tape(rows, now):
    rows=closed_candles(rows,900,now)
    if len(rows)<60 or now-rows[-1]['time']>3*900:
        return None
    closes=[c['close'] for c in rows]
    e20,e50=ema(closes,20),ema(closes,50)
    a=atr(rows)
    strength=rsi(closes)[-1]
    if not a or strength is None: return None
    # Activo en tendencia alcista O en zona de descuento/reversión válida (RSI 20..72)
    in_uptrend = e20[-1] > e50[-1] and closes[-1] > e50[-1]
    in_discount = closes[-1] <= e20[-1] + a * 1.2 and 20 <= strength <= 68
    trend = in_uptrend or in_discount
    pullback = min(c['low'] for c in rows[-5:]) <= e20[-1] + a * 1.5
    return dict(bar=rows[-1]['time'],close=closes[-1],ema20=e20[-1],ema50=e50[-1],
                atr=a,rsi=strength,trend=trend,pullback=pullback,rows=rows[-90:])


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
        self.state=json.loads(row[0]) if row else dict(cash=INITIAL,enabled=True,position=None,
            selected=None,selectionAt=0,selection=None,candidates=[],benchmark=None,startedAt=time.time(),
            day=int(time.time()//86400),dayEquity=INITIAL,dailyStopped=False,roles={},lastScan=0)
        self.task=None
        self.lock=asyncio.Lock()
        self.wire_cache={}
        self.last_data=None
        self.chart=[]
        self.last_curve=0
        self.last_error=None
        self.save()

    def save(self):
        with connect() as con:
            con.execute("INSERT OR REPLACE INTO state VALUES ('desk',?)",(json.dumps(self.state),))

    def role(self,name,status,detail):
        value=dict(status=status,detail=detail,at=time.time(),provider='Gemini' if name=='Selector' else 'Reglas deterministas')
        previous=self.state['roles'].get(name,{})
        self.state['roles'][name]=value
        if previous.get('status')!=status or previous.get('detail')!=detail:
            with connect() as con:
                con.execute('INSERT INTO events(ts,role,status,detail) VALUES (?,?,?,?)',(value['at'],name,status,detail))

    def quote(self,symbol,data):
        try:
            book=data['books'][symbol]
            bid,ask=float(book['bidPrice']),float(book['askPrice'])
            if time.time()-data['at']>120 or not all(math.isfinite(v) and v>0 for v in (bid,ask)) or ask<bid:
                return None
            return bid,ask
        except (KeyError,ValueError,TypeError): return None

    def equity(self):
        p=self.state['position']
        return self.state['cash']+(p['qty']*p['lastBid']*(1-SLIPPAGE)*(1-FEE) if p else 0)

    def hold_equity(self):
        b=self.state['benchmark']
        return b['qty']*b['lastBid']*(1-SLIPPAGE)*(1-FEE) if b else INITIAL

    async def wire(self,base):
        cached=self.wire_cache.get(base)
        if cached and time.time()-cached[0]<3600: return cached[1]
        official=OFFICIAL.get(base)
        if not official:
            result=dict(ok=False,detail='Sin referencia oficial registrada; no se presume autenticidad',url=None)
        else:
            url,handle=official
            try:
                response=await market_data.client().get(url,follow_redirects=True)
                response.raise_for_status()
                from urllib.parse import urlparse
                if (urlparse(str(response.url)).hostname or '').removeprefix('www.')!=(urlparse(url).hostname or '').removeprefix('www.'):
                    raise ValueError('Redirección a otro dominio')
                page=response.text.replace('\\/','/')
                handles=re.findall(r'''https?://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)(?=[/?#"'\s<]|$)''',page)
                ok=handle.casefold() in {h.casefold() for h in handles}
                result=dict(ok=ok,url=url,handle=handle,checkedAt=time.time(),
                            detail=f'@{handle} enlazada desde el sitio oficial' if ok else 'No se encontró el enlace oficial esperado; entrada bloqueada')
            except Exception:
                result=dict(ok=False,url=url,detail='No se pudo consultar la referencia oficial; entrada bloqueada')
        self.wire_cache[base]=(time.time(),result)
        return result

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
        self.state['candidates']=candidates
        agent=gemini_agent.AGENT
        reselect=self.state['enabled'] and not self.state['position'] and (not self.state['selected'] or now-self.state['selectionAt']>=6*3600)
        if reselect:
            self.state['selectionPending']=True
            self.role('Selector','waiting',f'{len(candidates)} mercados examinados; esperando selección de Gemini')
            choice=await agent.choose_market([{k:c[k] for k in ('symbol','eligible','change24h','volumeUSD','spreadBp')} for c in candidates]) if agent and candidates else None
            if choice is None:
                # Fallback determinista al candidato elegible de mayor liquidez si Gemini está en cooldown
                top_elig = next((c for c in candidates if c['eligible']), None)
                if top_elig:
                    choice = dict(symbol=top_elig['symbol'], confidence=0.8, reason='Selección por liquidez y menor spread (turno en caché)')
                else:
                    return
            self.state.update(selection=choice,selectionAt=now,selectionPending=False)
            if choice['symbol'] is None:
                self.state['selected']=None
                self.role('Selector','waiting',f'Elegido efectivo: {choice["reason"]}')
                return
            self.state['selected']=choice['symbol']
            self.role('Selector','selected',f'{choice["symbol"]}: {choice["reason"]}')
            self.save()
        symbol=self.state['selected']
        if not symbol: return
        meta=data['metadata'].get(symbol,{})
        base=meta.get('baseAsset','')
        quality=opportunities.assess(Symbol(base,base,'cripto','binance',symbol),data)
        if not quality['eligible']:
            self.role('Exec','blocked',quality['reason'])
            return
        q=self.quote(symbol,data)
        if not q: return
        bid,ask=q
        identity=await self.wire(base)
        self.state['wire']=identity
        self.role('Wire','corroborated' if identity['ok'] else 'blocked',identity['detail'])
        if identity['ok'] and self.state['benchmark'] is None:
            self.state['benchmark']=dict(symbol=symbol,qty=INITIAL/(ask*(1+SLIPPAGE)*(1+FEE)),lastBid=bid,startedAt=now)
        sym=Symbol(base,base,'cripto','binance',symbol)
        analysis=tape(await market_data.get_candles(sym,'15m',250),time.time())
        if analysis is None:
            self.role('Tape','blocked','Velas cerradas insuficientes o antiguas')
            return
        self.chart=analysis['rows']
        self.state['tape']={k:v for k,v in analysis.items() if k!='rows'}
        self.role('Tape','ready' if analysis['trend'] else 'waiting',f'EMA20/50; RSI {analysis["rsi"]:.0f}; ATR {analysis["atr"]:.5g}')
        extension=(ask-analysis['ema20'])/analysis['atr']
        price_ok=analysis['trend'] and analysis['pullback'] and 20<=analysis['rsi']<=72 and -3.5<=extension<=1.8 and abs(ask-analysis['close'])<=2.0*analysis['atr']
        self.role('Price','ready' if price_ok else 'waiting','Retroceso y zona de valor confirmados' if price_ok else 'Esperando retroceso; no perseguir el rebote')
        order=size_order(self.equity(),self.state['cash'],ask,analysis['atr'],meta)
        self.role('Size','ready' if order else 'blocked',f'Tamaño {order["notional"]:.2f} USDT; máximo 1/3; riesgo {order["risk"]:.2f}' if order else 'Lote mínimo incompatible con presupuesto')
        if self.state['position']:
            if not analysis['trend'] and analysis['close']<analysis['ema50'] - 2.0 * analysis['atr']:
                self.close_position(bid,'Tape detecta pérdida de estructura')
                self.role('Exec','waiting','Posición cerrada por cambio de tendencia')
                return
            self.role('Exec','managing','Una posición abierta; no se acumulan entradas')
            return
        if not self.state['enabled'] or self.state['dailyStopped'] or not price_ok or not identity['ok'] or not order:
            self.role('Exec','waiting','Esperando aprobación de Price, Wire, Size y Risk')
            return
        key=f'{symbol}:{analysis["bar"]}'
        with connect() as con:
            if con.execute('SELECT 1 FROM trades WHERE entry_key=?',(key,)).fetchone(): return
        if not self.quote(symbol,data):
            self.role('Exec','blocked','La cotización caducó durante el análisis; entrada bloqueada')
            return
        available=float(data['books'][symbol].get('askQty',0))
        if not math.isfinite(available) or available<=0 or order['qty']>available*.5:
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
        return dict(mode='paper',experimental=True,enabled=self.state['enabled'],initial=INITIAL,
                    equity=round(self.equity(),2),cash=round(self.state['cash'],2),benchmark=round(self.hold_equity(),2),
                    selected=self.state['selected'],selection=self.state['selection'],candidates=candidates,
                    benchmarkSymbol=(self.state['benchmark'] or {}).get('symbol'),
                    examined=len(candidates),discarded=sum(not c['selected'] for c in candidates),
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
