"""Hub de precios en vivo: sondea las fuentes y difunde ticks por WebSocket.

Cada símbolo con al menos un suscriptor tiene una tarea de sondeo propia
(2 s para Binance, 6 s para Yahoo). Al quedarse sin suscriptores la tarea
se detiene sola.
"""

import asyncio
import logging

from fastapi import WebSocket

from .config import POLL_INTERVAL, SYMBOLS_BY_ID
from . import market_data

log = logging.getLogger("hub")


class PriceHub:
    def __init__(self) -> None:
        self._subs: dict[str, set[WebSocket]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._last: dict[str, dict] = {}

    async def subscribe(self, ws: WebSocket, symbol_id: str) -> None:
        if symbol_id not in SYMBOLS_BY_ID:
            return
        self._subs.setdefault(symbol_id, set()).add(ws)
        if last := self._last.get(symbol_id):
            await ws.send_json(last)
        if symbol_id not in self._tasks:
            self._tasks[symbol_id] = asyncio.create_task(self._poll(symbol_id))

    def unsubscribe(self, ws: WebSocket, symbol_id: str | None = None) -> None:
        ids = [symbol_id] if symbol_id else list(self._subs)
        for sid in ids:
            self._subs.get(sid, set()).discard(ws)

    async def _poll(self, symbol_id: str) -> None:
        sym = SYMBOLS_BY_ID[symbol_id]
        interval = POLL_INTERVAL[sym.source]
        try:
            while self._subs.get(symbol_id):
                try:
                    tick = await market_data.get_price(sym)
                    msg = {"type": "tick", **tick}
                    self._last[symbol_id] = msg
                    dead = []
                    for ws in self._subs.get(symbol_id, set()):
                        try:
                            await ws.send_json(msg)
                        except Exception:
                            dead.append(ws)
                    for ws in dead:
                        self.unsubscribe(ws)
                except Exception as exc:
                    log.warning("fallo obteniendo %s: %s", symbol_id, exc)
                await asyncio.sleep(interval)
        finally:
            self._tasks.pop(symbol_id, None)


hub = PriceHub()
