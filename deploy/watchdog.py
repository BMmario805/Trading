"""Docker reinicia procesos caídos; este vigilante recupera un proceso degradado."""

import json
import subprocess


result = subprocess.run(['docker', 'inspect', 'mesa-siete'], capture_output=True, text=True)
if result.returncode == 0:
    state = json.loads(result.stdout)[0]['State']
    if state.get('Running') and state.get('Health', {}).get('Status') == 'unhealthy':
        subprocess.run(['docker', 'restart', '--time', '75', 'mesa-siete'], check=True)
        print('Motor reiniciado por fallo persistente de supervisión')
