"""Solicitud temporal de actividad para el servicio local en Windows.

No modifica planes de energía ni fuerza la pantalla encendida. Se libera al
cerrar el proceso. La suspensión manual, cierre de tapa, apagado y fallos de
red siguen requiriendo protección externa para una ejecución real.
"""
import ctypes
import logging
import os
import sys
import threading

CONTINUOUS=0x80000000
SYSTEM_REQUIRED=0x00000001
_api=None
_previous=None
_owner=None
_state=dict(supported=sys.platform=='win32',active=False,error=None)
log=logging.getLogger('runtime-guard')


def _native_api():
    api=ctypes.WinDLL('kernel32',use_last_error=True).SetThreadExecutionState
    api.argtypes=[ctypes.c_uint]
    api.restype=ctypes.c_uint
    return api


def acquire():
    global _api,_previous,_owner
    if _state['active'] or sys.platform!='win32': return status()
    if os.getenv('TRADING_KEEP_AWAKE','1')=='0': return status()
    try:
        api=_native_api()
        previous=api(CONTINUOUS|SYSTEM_REQUIRED)
        if not previous: raise OSError('Windows rechazó la solicitud de actividad')
        _api,_previous,_owner=api,previous,threading.get_ident()
        _state.update(active=True,error=None)
        log.info('Protección temporal contra suspensión automática activa mientras funciona el servicio')
    except Exception as exc:
        _state.update(active=False,error=type(exc).__name__)
        log.warning('Protección de actividad no disponible: %s',exc)
    return status()


def release():
    global _api,_previous,_owner
    if not _state['active']: return
    if threading.get_ident()!=_owner:
        _state['error']='La protección debe liberarse desde el hilo que la solicitó'
        return
    if _api((_previous or CONTINUOUS)|CONTINUOUS):
        _state.update(active=False,error=None)
        _api=_previous=_owner=None
    else:
        _state['error']='No se pudo restaurar la solicitud de energía anterior'


def status():
    return dict(_state,scope='Proceso del servicio local',displayRequired=False,manualSleepProtected=False,
                detail='Evita suspensión automática por inactividad; la suspensión manual y los apagados siguen deteniendo la vigilancia')
