import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import cloud_runtime, mt5_demo

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('state_bundle', ROOT/'deploy/state_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class CloudRuntimeTests(unittest.TestCase):
    def running_services(self, *, now=1000, started=100):
        alive = Mock()
        alive.done.return_value = False
        engines = {name: SimpleNamespace(task=alive, risk_task=alive,
                   progress=dict(startedAt=started, scanProgressAt=now - 1, riskCompletedAt=now - 1))
                   for name in ('crypto', 'forex', 'stocks')}
        lab = {cfg.id: SimpleNamespace(_task=alive, _risk_task=alive) for cfg in cloud_runtime.bots.ALL_BOTS}
        return engines, lab

    def test_health_detects_dead_risk_monitor(self):
        alive, dead = Mock(), Mock()
        alive.done.return_value = False
        dead.done.return_value = True
        engines = {name: SimpleNamespace(task=alive, risk_task=alive) for name in ('crypto', 'forex', 'stocks')}
        lab = {cfg.id: SimpleNamespace(_task=alive, _risk_task=alive) for cfg in cloud_runtime.bots.ALL_BOTS}
        with patch.object(cloud_runtime.desk, 'DESKS', engines), patch.object(cloud_runtime.bots, 'BOTS', lab):
            self.assertEqual(cloud_runtime.health_snapshot()['status'], 'ok')
            engines['crypto'].risk_task = dead
            with self.assertRaises(HTTPException) as exc:
                cloud_runtime.health_snapshot()
            self.assertEqual(exc.exception.status_code, 503)
            self.assertIn('mesa crypto: risk_task', exc.exception.detail['services'])

    def test_live_task_with_stalled_risk_loop_is_unhealthy(self):
        engines, lab = self.running_services()
        engines['crypto'].progress['riskCompletedAt'] = 900
        with patch.object(cloud_runtime.desk, 'DESKS', engines), patch.object(cloud_runtime.bots, 'BOTS', lab), \
             patch.object(cloud_runtime.time, 'time', return_value=1000):
            with self.assertRaises(HTTPException) as exc:
                cloud_runtime.health_snapshot()
            self.assertEqual(exc.exception.status_code, 503)
            self.assertIn('mesa crypto: risk sin progreso', exc.exception.detail['services'])

    def test_new_attempt_does_not_mask_old_success(self):
        engines,lab=self.running_services()
        engines['crypto'].progress.update(riskCompletedAt=1,riskStartedAt=999)
        with patch.object(cloud_runtime.desk,'DESKS',engines),patch.object(cloud_runtime.bots,'BOTS',lab):
            rows=cloud_runtime.progress_snapshot(now=1000)['services']
        risk=next(r for r in rows if r['id']=='crypto' and r['role']=='risk')
        self.assertEqual(risk['state'],'stalled')

    def test_lab_risk_progress_requires_completion(self):
        bot=SimpleNamespace(cfg=SimpleNamespace(scan_seconds=60),running=True,diagnostics={},
            risk_monitor=dict(startedAt=1,lastCompletedAt=1),_last_scan_completed=999)
        with patch.object(cloud_runtime.desk,'DESKS',{}),patch.object(cloud_runtime.bots,'BOTS',{'example':bot}):
            rows=cloud_runtime.progress_snapshot(now=1000)['services']
        self.assertEqual(next(r for r in rows if r['role']=='risk')['state'],'stalled')

    def test_live_task_with_stalled_scan_is_unhealthy(self):
        engines, lab = self.running_services()
        engines['forex'].progress['scanProgressAt'] = 800
        with patch.object(cloud_runtime.desk, 'DESKS', engines), patch.object(cloud_runtime.bots, 'BOTS', lab), \
             patch.object(cloud_runtime.time, 'time', return_value=1000):
            with self.assertRaises(HTTPException) as exc:
                cloud_runtime.health_snapshot()
            self.assertIn('mesa forex: scan sin progreso', exc.exception.detail['services'])

    def test_recent_progress_and_startup_grace_keep_service_healthy(self):
        engines, lab = self.running_services()
        with patch.object(cloud_runtime.desk, 'DESKS', engines), patch.object(cloud_runtime.bots, 'BOTS', lab), \
             patch.object(cloud_runtime.time, 'time', return_value=1000):
            status = cloud_runtime.health_snapshot()
            self.assertEqual(status['status'], 'ok')
            self.assertTrue(all(row['state'] == 'progressing' for row in status['progress']['services']))
            for engine in engines.values():
                engine.progress = {'startedAt': 990}
            status = cloud_runtime.health_snapshot()
            self.assertEqual(status['status'], 'ok')
            self.assertTrue(all(row['state'] == 'warming' for row in status['progress']['services']))

    def test_missing_heartbeat_after_startup_is_stalled(self):
        engines, lab = self.running_services()
        engines['stocks'].progress = {'startedAt': 100}
        with patch.object(cloud_runtime.desk, 'DESKS', engines), patch.object(cloud_runtime.bots, 'BOTS', lab):
            progress = cloud_runtime.progress_snapshot(now=1000)
            stalled = {(row['id'], row['role']) for row in progress['stalled']}
            self.assertEqual(stalled, {('stocks', 'scan'), ('stocks', 'risk')})

    def test_panel_does_not_shadow_api(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'index.html').write_text('<h1>Mesa Siete</h1>', encoding='utf-8')
            app = FastAPI()
            app.get('/api/test')(lambda: {'ok': True})
            with patch.dict('os.environ', {'TRADING_FRONTEND_DIR': folder}):
                cloud_runtime.mount_frontend(app)
            client = TestClient(app)
            self.assertIn('Mesa Siete', client.get('/').text)
            self.assertEqual(client.get('/api/test').json(), {'ok': True})

    def test_linux_never_starts_mt5_terminal(self):
        with patch.object(mt5_demo.sys, 'platform', 'linux'), patch.object(mt5_demo.asyncio, 'create_task') as start, \
             patch.object(mt5_demo, 'STATE', {}):
            mt5_demo.start()
            start.assert_not_called()
            self.assertFalse(mt5_demo.STATE['connected'])

    def test_sqlite_copy_includes_committed_wal_and_excludes_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'project'
            (root/'backend').mkdir(parents=True)
            (root/'.runtime').mkdir()
            (root/'.runtime/mt5-demo.json').write_text('secret', encoding='utf-8')
            (root/'.runtime/gemini.env').write_text('GEMINI_API_KEY=test-only', encoding='utf-8')
            source = sqlite3.connect(root/'backend/data.db')
            try:
                source.execute('PRAGMA journal_mode=WAL')
                source.execute('CREATE TABLE trades (id INTEGER)')
                source.execute('INSERT INTO trades VALUES (42)')
                source.commit()
                manifest = bundle.snapshot_state(root, Path(folder)/'copy')
                with closing(sqlite3.connect(Path(folder)/'copy/data/data.db')) as restored:
                    self.assertEqual(restored.execute('SELECT id FROM trades').fetchone()[0], 42)
                self.assertEqual(len(manifest['files']), 1)
                self.assertFalse((Path(folder)/'copy/runtime/mt5-demo.json').exists())
                self.assertFalse((Path(folder)/'copy/runtime/gemini.env').exists())
            finally:
                source.close()


if __name__ == '__main__':
    unittest.main()
