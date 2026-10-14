"""Lecturas del terminal serializadas con el motor DEMO, sin enviar órdenes."""

import logging

log = logging.getLogger(__name__)
_reader = None


def set_reader(reader):
    global _reader
    _reader = reader


async def read(kind, interval=None, count=400):
    if _reader is None:
        return None
    try:
        return await _reader(kind, interval, count)
    except Exception as exc:
        log.debug("Datos MT5 no disponibles: %s", exc)
        return None
