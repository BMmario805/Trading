"""Precios y telemetría por /ws, con una cola y un escritor por cliente.
Un navegador lento se desconecta y recupera instantáneas al reconectar.
"""
import asyncio
from collections import deque
import copy
import logging
import time
import uuid
from fastapi import WebSocket
from .config import POLL_INTERVAL, SYMBOLS_BY_ID
from . import market_data

log = logging.getLogger('hub')

class PriceHub:
    def __init__(self):
        self._subs = {}
        self._tasks = {}
        self._last = {}
        self._clients = {}
        self._writers = {}
        self._latest = {}
        self._thoughts = deque(maxlen=80)
        self._loop = None
        self._sequence = 0
        self.stream_id = uuid.uuid4().hex

    @property
    def connected(self):
        return bool(self._clients)

    def start(self):
        self._loop = asyncio.get_running_loop()

    async def connect(self, ws: WebSocket):
        self.start()
        queue = asyncio.Queue(maxsize=256)
        self._clients[ws] = queue
        self._writers[ws] = asyncio.create_task(self._write(ws, queue), name='ws-writer')
        messages = [*self._latest.values(), *self._thoughts]
        for message in sorted(messages, key=lambda m: m['sequence']):
            queue.put_nowait(message)

    async def _write(self, ws, queue):
        try:
            while True:
                await asyncio.wait_for(ws.send_json(await queue.get()), timeout=3)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            self.disconnect(ws)
            try:
                await asyncio.wait_for(ws.close(code=1013), timeout=1)
            except Exception:
                pass

    def disconnect(self, ws):
        self.unsubscribe(ws)
        self._clients.pop(ws, None)
        task = self._writers.pop(ws, None)
        if task and task is not asyncio.current_task():
            task.cancel()

    def _enqueue(self, ws, message):
        queue = self._clients.get(ws)
        if queue is None:
            return
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            self.disconnect(ws)

    def broadcast_event(self, payload: dict):
        """No bloquea; acepta también los productores del hilo de MT5."""
        if self._loop is None or self._loop.is_closed():
            return
        message = copy.deepcopy(payload)
        try:
            if asyncio.get_running_loop() is self._loop:
                self._publish(message)
                return
        except RuntimeError:
            pass
        self._loop.call_soon_threadsafe(self._publish, message)

    def _publish(self, message):
        self._sequence += 1
        message.update(sequence=self._sequence, streamId=self.stream_id)
        message.setdefault('at', time.time())
        kind = message.get('type')
        if kind in ('desk_snapshot', 'position_update', 'swarm_status', 'orderbook_pulse'):
            self._latest[(kind, message.get('desk', 'all'))] = message
        elif kind == 'agent_thought':
            self._thoughts.append(message)
        for ws in tuple(self._clients):
            self._enqueue(ws, message)

    async def subscribe(self, ws, symbol_id):
        if symbol_id not in SYMBOLS_BY_ID:
            return
        if ws not in self._clients:
            await self.connect(ws)
        self._subs.setdefault(symbol_id, set()).add(ws)
        if last := self._last.get(symbol_id):
            self._enqueue(ws, last)
        if symbol_id not in self._tasks:
            self._tasks[symbol_id] = asyncio.create_task(self._poll(symbol_id))

    def unsubscribe(self, ws, symbol_id=None):
        for sid in [symbol_id] if symbol_id else list(self._subs):
            self._subs.get(sid, set()).discard(ws)

    async def _poll(self, symbol_id):
        sym = SYMBOLS_BY_ID[symbol_id]
        try:
            while self._subs.get(symbol_id):
                try:
                    message = {'type': 'tick', **await market_data.get_price(sym)}
                    self._last[symbol_id] = message
                    for ws in tuple(self._subs.get(symbol_id, ())):
                        self._enqueue(ws, message)
                except Exception as exc:
                    log.warning('fallo obteniendo %s: %s', symbol_id, exc)
                await asyncio.sleep(POLL_INTERVAL[sym.source])
        finally:
            self._tasks.pop(symbol_id, None)

    async def stop(self):
        tasks = [*self._tasks.values(), *self._writers.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._writers.clear()
        self._clients.clear()
        self._subs.clear()
        self._loop = None

hub = PriceHub()
