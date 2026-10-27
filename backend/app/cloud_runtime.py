"""Supervisión ligera y panel compilado para el despliegue de una sola instancia."""

import os
import time
from pathlib import Path

from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles

from . import bots, desk


def progress_snapshot(now=None):
    """Task liveness and actual loop progress are different observations."""
    now=time.time() if now is None else now
    rows=[]
    for name,engine in desk.DESKS.items():
        progress=getattr(engine,'progress',{})
        started=progress.get('startedAt',now)
        for role,keys,limit in (('scan',('scanProgressAt','cycleCompletedAt'),120),
                                ('risk',('riskCompletedAt',),20)):
            stamps=[progress.get(k) for k in keys if isinstance(progress.get(k),(int,float))]
            latest=max(stamps) if stamps else None
            age=max(0,now-latest) if latest is not None else None
            state='unknown' if not progress else 'warming' if now-started<90 else 'stalled' if age is None or age>limit else 'progressing'
            rows.append(dict(source='desk',id=name,role=role,state=state,ageSeconds=age,limitSeconds=limit))
    for name,bot in bots.BOTS.items():
        monitor=getattr(bot,'risk_monitor',{})
        if not isinstance(monitor,dict) or not monitor: continue
        started=monitor.get('startedAt',now)
        latest=monitor.get('lastCompletedAt')
        age=max(0,now-latest) if isinstance(latest,(int,float)) else None
        state='warming' if now-started<90 else 'stalled' if age is None or age>30 else 'progressing'
        rows.append(dict(source='lab',id=name,role='risk',state=state,ageSeconds=age,limitSeconds=30))
        diagnostics=getattr(bot,'diagnostics',{})
        stamps=[d.get('at') for d in diagnostics.values() if isinstance(d,dict) and isinstance(d.get('at'),(int,float))]
        completed=getattr(bot,'_last_scan_completed',None)
        if isinstance(completed,(int,float)): stamps.append(completed)
        age=max(0,now-max(stamps)) if stamps else None
        limit=max(120,getattr(bot.cfg,'scan_seconds',60)*2+60)
        state='paused' if not getattr(bot,'running',False) else 'warming' if now-started<90 else 'stalled' if age is None or age>limit else 'progressing'
        rows.append(dict(source='lab',id=name,role='scan',state=state,ageSeconds=age,limitSeconds=limit))
    return dict(observedAt=now,services=rows,stalled=[r for r in rows if r['state']=='stalled'])


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
    progress=progress_snapshot()
    failed.extend(f"{'mesa' if row['source']=='desk' else 'laboratorio'} {row['id']}: {row['role']} sin progreso" for row in progress['stalled'])
    if failed:
        raise HTTPException(503, detail={'status': 'degraded', 'services': failed})
    return {'status': 'ok', 'mode': 'paper', 'desks': len(desk.DESKS), 'bots': len(bots.BOTS),
            'progress': progress}


def mount_frontend(app):
    directory = os.getenv('TRADING_FRONTEND_DIR')
    if directory:
        path = Path(directory)
        if not (path / 'index.html').is_file():
            raise RuntimeError('Falta el panel compilado en TRADING_FRONTEND_DIR')
        app.mount('/', StaticFiles(directory=path, html=True), name='dashboard')
