"""Supervisión ligera y panel compilado para el despliegue de una sola instancia."""

import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles

from . import bots, desk


def health_snapshot():
    failed = []
    if set(desk.DESKS) != {'crypto', 'forex', 'stocks'}:
        failed.append('mesas sin iniciar')
    for name, engine in desk.DESKS.items():
        for attr in ('task', 'risk_task'):
            task = getattr(engine, attr, None)
            if task is None or task.done():
                failed.append(f'mesa {name}: {attr}')
    if len(bots.BOTS) != len(bots.ALL_BOTS):
        failed.append('laboratorio sin iniciar')
    for name, bot in bots.BOTS.items():
        for attr in ('_task', '_risk_task'):
            task = getattr(bot, attr, None)
            if task is None or task.done():
                failed.append(f'laboratorio {name}: {attr}')
    if failed:
        raise HTTPException(503, detail={'status': 'degraded', 'services': failed})
    return {'status': 'ok', 'mode': 'paper', 'desks': len(desk.DESKS), 'bots': len(bots.BOTS)}


def mount_frontend(app):
    directory = os.getenv('TRADING_FRONTEND_DIR')
    if directory:
        path = Path(directory)
        if not (path / 'index.html').is_file():
            raise RuntimeError('Falta el panel compilado en TRADING_FRONTEND_DIR')
        app.mount('/', StaticFiles(directory=path, html=True), name='dashboard')
