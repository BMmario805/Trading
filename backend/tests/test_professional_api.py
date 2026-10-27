import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import main,experiments,team_workflow,ai_swarm,subscription_bridge,research_plans


class ProfessionalApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.root=Path(self.folder.name)
        self.enterContext(patch.object(experiments,'DB_PATH',self.root/'research-ledger.sqlite'))
        self.enterContext(patch.object(ai_swarm,'DB_PATH',self.root/'swarm.sqlite'))
        self.enterContext(patch.object(team_workflow,'DB_PATH',self.root/'team-workflow.sqlite'))
        self.enterContext(patch.object(subscription_bridge,'health_snapshot',return_value=dict(providers=[])))
        self.enterContext(patch.object(main,'_local_research_lock',asyncio.Lock()))
        self.enterContext(patch.object(main,'_local_research_tasks',set()))
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test')

    async def asyncTearDown(self):
        await asyncio.gather(*tuple(main._local_research_tasks),return_exceptions=True)
        await self.client.aclose()
        self.folder.cleanup()

    async def register(self):
        response=await self.client.post('/api/research/plans/A1/preregister',json=dict(symbols=['BTC']))
        self.assertEqual(response.status_code,200)
        return response.json()['id']

    async def test_read_only_status_does_not_create_ledger(self):
        for url in ('/api/research/plans','/api/professional/status','/api/team/health'):
            self.assertEqual((await self.client.get(url)).status_code,200)
        self.assertEqual(list(self.root.iterdir()),[])

    async def test_cached_workflow_has_receipts_and_cannot_authorize_execution(self):
        request=dict(brief='Verificar protocolo sin evidencia inventada',symbols=['BTC'],radarMode='cached',idempotencyKey='api-cached-test')
        first=await self.client.post('/api/team/tasks',json=request)
        self.assertEqual(first.status_code,200)
        task=first.json()
        self.assertTrue(task['workflowCompleted'])
        self.assertEqual(task['status'],'blocked')
        self.assertFalse(task['executionAuthorized'])
        self.assertEqual(len({receipt['stage'] for receipt in task['stages']}),7)
        again=(await self.client.post('/api/team/tasks',json=request)).json()
        self.assertEqual(again['taskId'],task['taskId'])

    async def test_unknown_experiment_does_not_write_dataset(self):
        result=await self.client.post('/api/research/experiments/unknown/evaluate',json=dict(datasets=[]))
        self.assertEqual(result.status_code,400)
        self.assertEqual(list(self.root.iterdir()),[])

    async def test_cancelled_client_keeps_thread_admission_lease(self):
        eid=await self.register()
        entered=threading.Event();release=threading.Event()
        def slow(*_,**__):
            entered.set()
            release.wait(4)
            return dict(status='fixture-completed')
        with patch.object(research_plans,'evaluate_plan',side_effect=slow):
            first=asyncio.create_task(self.client.post(f'/api/research/experiments/{eid}/evaluate',json=dict(datasets=[])))
            for _ in range(100):
                if entered.is_set():break
                await asyncio.sleep(.01)
            self.assertTrue(entered.is_set())
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):await first
            try:
                second=await self.client.post(f'/api/research/experiments/{eid}/evaluate',json=dict(datasets=[]))
                self.assertEqual(second.status_code,409)
            finally:release.set()
            await asyncio.gather(*tuple(main._local_research_tasks),return_exceptions=True)
        self.assertFalse(main._local_research_lock.locked())
