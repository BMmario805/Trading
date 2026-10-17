"""Trabajos de investigación acotados, separados de ejecución y saldos."""

import asyncio
import json
import time
from pathlib import Path

from . import research

RESULT_PATH=Path(__file__).resolve().parents[2]/'.runtime'/'research-latest-result.json'
_task=None
_state=dict(status='idle',startedAt=None,finishedAt=None,error=None,result=None)


def status():
    if _state['status']=='idle' and RESULT_PATH.exists():
        try:
            result=json.loads(RESULT_PATH.read_text(encoding='utf-8'))
            if result.get('version')==research.RESEARCH_VERSION and result.get('engineVersion')==research.ENGINE_VERSION:
                _state.update(status='completed',result=result,finishedAt=result.get('generatedAt'))
        except (OSError,ValueError):
            pass
    return dict(_state)


async def _run():
    try:
        # Presupuesto fijo: 9 datasets, 12 variantes, nunca envía órdenes.
        result=await research.run_research(days=30,max_bars=9000)
        content=json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2)
        RESULT_PATH.parent.mkdir(parents=True,exist_ok=True)
        archive=RESULT_PATH.with_name(f'research-{time.time_ns()}.json')
        archive.write_text(content,encoding='utf-8')
        temporary=RESULT_PATH.with_suffix('.tmp')
        temporary.write_text(content,encoding='utf-8')
        temporary.replace(RESULT_PATH)
        _state.update(status='completed',finishedAt=time.time(),result=result)
    except asyncio.CancelledError:
        _state.update(status='failed',finishedAt=time.time(),error='Comparación interrumpida; informe anterior conservado')
        raise
    except Exception as exc:
        _state.update(status='failed',finishedAt=time.time(),error=f'Comparación no disponible ({type(exc).__name__})')


def start():
    global _task
    if _task is not None and not _task.done():
        return status()
    # Reabrir la misma prueba no crea un holdout nuevo. Limita repeticiones
    # accidentales y conserva cada informe terminado como un archivo distinto.
    previous=status()
    if previous.get('finishedAt') and time.time()-previous['finishedAt']<3600:
        return previous
    _state.update(status='running',startedAt=time.time(),finishedAt=None,error=None)
    _task=asyncio.create_task(_run(),name='bounded-strategy-research')
    return status()


async def stop():
    global _task
    if _task and not _task.done():
        _task.cancel()
        try: await _task
        except asyncio.CancelledError: pass
    _task=None
