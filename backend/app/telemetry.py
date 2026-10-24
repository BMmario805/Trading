"""Reconciliación del servidor; el navegador recibe eventos, sin pedirlos."""
import asyncio
import json
import logging
import time
from .hub import hub

log = logging.getLogger('telemetry')
_task = None
_wake = None
_book_at = {}
_dirty_desks = set()


def book_changed(symbol):
    if not hub.connected or time.monotonic()-_book_at.get(symbol,0)<.25:
        return
    from . import desk
    for engine in desk.DESKS.values():
        if any(o['symbol']==symbol for o in engine.maker.orders):
            _book_at[symbol]=time.monotonic()
            engine.maker.publish()


def changed(desk_id=None):
    if desk_id:
        _dirty_desks.add(desk_id)
    if _wake is not None:
        _wake.set()


async def _run():
    from . import desk, open_positions, ai_swarm
    previous = {}
    while True:
        _wake.clear()
        if hub.connected:
            dirty = set(_dirty_desks)
            _dirty_desks.clear()
            producers = [(('desk_snapshot', key), engine.public) for key, engine in desk.DESKS.items()
                         if key in dirty or 'all' in dirty]
            producers += [(('position_update', 'all'), open_positions.snapshot),
                          (('swarm_status', 'all'), ai_swarm.snapshot_status)]
            for (kind, key), produce in producers:
                try:
                    data = produce()
                    comparable = {k:v for k,v in data.items() if k!='updatedAt'}
                    signature = json.dumps(comparable, sort_keys=True, allow_nan=False)
                    if signature != previous.get((kind, key)):
                        hub.broadcast_event(dict(type=kind, desk=key, data=data))
                        previous[kind, key] = signature
                    if kind=='desk_snapshot':
                        desk.DESKS[key].maker.publish()
                except Exception:
                    log.exception('No se pudo publicar %s de %s', kind, key)
        try:
            await asyncio.wait_for(_wake.wait(), timeout=1)
        except TimeoutError:
            pass


def start():
    global _task, _wake
    hub.start()
    _wake = asyncio.Event()
    _dirty_desks.add('all')
    _task = asyncio.create_task(_run(), name='mesa-telemetry')


async def stop():
    global _task, _wake
    if _task:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
    _task = _wake = None
