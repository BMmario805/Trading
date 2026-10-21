"""Comprueba que siguen vivos la API y los ciclos de ejecución y riesgo."""

import json
import sys
from urllib.request import urlopen

try:
    with urlopen('http://127.0.0.1:8000/api/health', timeout=5) as response:
        state = json.load(response)
    if state.get('status') != 'ok':
        raise RuntimeError('servicio degradado')
except Exception as exc:
    print(f'Healthcheck: {type(exc).__name__}', file=sys.stderr)
    sys.exit(1)
