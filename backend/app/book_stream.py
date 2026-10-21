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
                    timestampBasis='local-receipt', endpoint='Binance public bookTicker')

    async def _subscriptions(self, socket):
        active = set()
        serial = 0
        while True:
            self.changed.clear()
            desired = set(self.wanted)
            for method, symbols in (('UNSUBSCRIBE', active-desired), ('SUBSCRIBE', desired-active)):
                if symbols:
                    serial += 1
                    await socket.send(json.dumps(dict(method=method, params=[s.lower()+'@bookTicker' for s in sorted(symbols)], id=serial), ensure_ascii=False))
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


BOOKS = BookStream()
