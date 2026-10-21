"""Mejores bid/ask públicos: conexión compartida, caducidad y respaldo REST externo.

bookTicker no incluye fecha del motor: la antigüedad se mide desde recepción,
sin prometer latencia de extremo a extremo ni reconstruir profundidad de mercado.
"""

import asyncio
import json
import logging
import math
import os
import time
import uuid
from collections import deque

from websockets.asyncio.client import connect

log = logging.getLogger('book-stream')
ENDPOINTS = ('wss://data-stream.binance.vision/ws', 'wss://stream.binance.com:443/ws')
MAX_SYMBOLS = 512
MAX_AGE = 2.0


class BookStream:
    def __init__(self):
        self.task = None
        self.connected = False
        self.wanted = {}
        self.books = {}
        self.changed = asyncio.Event()
        self.messages = self.reconnects = self.hits = self.fallbacks = 0
        self.error = None
        self.trade_wanted = {}
        self.trades = {}
        self.generation = uuid.uuid4().hex

    def watch_trades(self,symbols):
        before=set(self.trade_wanted)
        now=time.monotonic()
        for symbol in symbols:
            if isinstance(symbol,str) and symbol.isalnum() and symbol.endswith('USDT'):
                self.trade_wanted[symbol]=now
        self.trade_wanted={s:t for s,t in sorted(self.trade_wanted.items(),key=lambda x:x[1],reverse=True)
                           if now-t<120}
        self.trade_wanted=dict(list(self.trade_wanted.items())[:8])
        self.trades={s:rows for s,rows in self.trades.items() if s in self.trade_wanted}
        self.watch(self.trade_wanted)
        if before!=set(self.trade_wanted): self.changed.set()

    def observed_trades(self,symbol,placed_at,last_id,generation):
        if not self.connected or generation!=self.generation: return None
        rows=list(self.trades.get(symbol,[]))
        if last_id>=0 and rows and rows[0]['id']>last_id+1: return None
        return [dict(e) for e in rows if e['at']>placed_at and e['id']>last_id]

    def watch(self, symbols):
        before = set(self.wanted)
        now = time.monotonic()
        for symbol in symbols:
            if isinstance(symbol, str) and symbol.isalnum() and symbol.endswith('USDT'):
                self.wanted[symbol] = now
        self.wanted = dict(sorted(self.wanted.items(), key=lambda item: item[1], reverse=True)[:MAX_SYMBOLS])
        if set(self.wanted) != before:
            self.changed.set()

    def ingest(self, payload):
        if not isinstance(payload, dict):
            return False
        payload = payload.get('data', payload)
        if not isinstance(payload, dict) or payload.get('s') not in self.wanted:
            return False
        symbol = payload['s']
        if payload.get('e')=='trade':
            if symbol not in self.trade_wanted: return False
            try:
                event=dict(symbol=symbol,id=payload['t'],at=payload['T']/1000,
                           price=float(payload['p']),quantity=float(payload['q']),isBuyerMaker=payload['m'])
                if type(event['id']) is not int or type(event['isBuyerMaker']) is not bool: return False
                if not all(math.isfinite(event[k]) and event[k]>0 for k in ('at','price','quantity')): return False
                if not -2<=time.time()-event['at']<=10: return False
                rows=self.trades.setdefault(symbol,deque(maxlen=2048))
                if rows and event['id']<=rows[-1]['id']: return False
                if rows and event['id']!=rows[-1]['id']+1:
                    self.generation=uuid.uuid4().hex  # Una discontinuidad cancela órdenes pendientes.
                    self.trades.clear()
                    rows=self.trades.setdefault(symbol,deque(maxlen=2048))
                rows.append(event)
                return True
            except (KeyError,TypeError,ValueError): return False
        try:
            bid, ask, bqty, aqty = (float(payload[k]) for k in ('b', 'a', 'B', 'A'))
            update = payload['u']
            if type(update) is not int or update < 0:
                return False
            if not all(math.isfinite(v) and v > 0 for v in (bid, ask, bqty, aqty)) or ask < bid:
                return False
        except (KeyError, TypeError, ValueError):
            return False
        old = self.books.get(symbol)
        if old and update <= old['updateId']:
            return False
        self.books[symbol] = dict(symbol=symbol, bidPrice=str(bid), askPrice=str(ask),
                                  bidQty=str(bqty), askQty=str(aqty), updateId=update,
                                  receivedAt=time.time(), monotonicAt=time.monotonic())
        self.messages += 1
        return True

    def fresh(self, symbols):
        if not self.connected:
            return {}
        now = time.monotonic()
        return {s: dict(self.books[s]) for s in symbols if s in self.books
                and 0 <= now-self.books[s]['monotonicAt'] <= MAX_AGE}

    def status(self):
        return dict(connected=self.connected, watched=len(self.wanted), fresh=len(self.fresh(self.wanted)),
                    messages=self.messages, reconnects=self.reconnects, streamQuotes=self.hits,
                    restFallbacks=self.fallbacks, error=self.error, maximumQuoteAgeSeconds=MAX_AGE,
                    timestampBasis='local-receipt', endpoint='Binance public bookTicker',
                    observedTradeSymbols=len(self.trade_wanted),observedTradeBufferLimit=2048)

    async def _subscriptions(self, socket):
        active = set()
        serial = 0
        while True:
            self.changed.clear()
            desired = {s.lower()+'@bookTicker' for s in self.wanted} | {s.lower()+'@trade' for s in self.trade_wanted}
            for method, streams in (('UNSUBSCRIBE', active-desired), ('SUBSCRIBE', desired-active)):
                if streams:
                    serial += 1
                    await socket.send(json.dumps(dict(method=method, params=sorted(streams), id=serial), ensure_ascii=False))
                    await asyncio.sleep(.6)  # Reserva capacidad para los pong del servidor.
            active = desired
            await self.changed.wait()

    async def _read(self, socket):
        async for raw in socket:
            payload = json.loads(raw)
            if isinstance(payload, dict) and (payload.get('e') == 'serverShutdown' or 'code' in payload):
                raise RuntimeError('El proveedor solicita reconectar o rechaza la suscripción')
            self.ingest(payload)

    async def run(self):
        attempts = 0
        while True:
            if not self.wanted:
                await self.changed.wait()
            try:
                async with connect(ENDPOINTS[attempts % len(ENDPOINTS)], open_timeout=8,
                                   ping_interval=20, ping_timeout=20, close_timeout=3,
                                   max_size=262144, max_queue=1) as socket:
                    self.books.clear()
                    self.trades.clear()
                    self.generation=uuid.uuid4().hex
                    self.connected = True
                    self.error = None
                    reader = asyncio.create_task(self._read(socket))
                    writer = asyncio.create_task(self._subscriptions(socket))
                    try:
                        done, _ = await asyncio.wait((reader, writer), return_when=asyncio.FIRST_COMPLETED)
                        for job in done:
                            job.result()
                    finally:
                        self.connected = False
                        self.books.clear()
                        for job in (reader, writer):
                            job.cancel()
                        await asyncio.gather(reader, writer, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = type(exc).__name__
                log.warning('Conexión de libros no disponible; se conserva REST (%s)', self.error)
            finally:
                self.connected = False
                self.books.clear()
                self.trades.clear()
            attempts += 1
            self.reconnects += 1
            await asyncio.sleep(min(60, 2 ** min(attempts, 6)))

    def start(self):
        if os.getenv('TRADING_BOOK_STREAM', '1') != '0' and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self.run(), name='binance-book-stream')

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self.connected = False
        self.books.clear()
        self.trades.clear()


BOOKS = BookStream()
