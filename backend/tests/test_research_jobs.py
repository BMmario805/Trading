import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from app import research_jobs as jobs


class ResearchJobsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patches=[patch.object(jobs,'RESULT_PATH',Path(self.tmp.name)/'latest.json'),
                      patch.object(jobs,'_task',None),patch.object(jobs,'_state',dict(
                          status='idle',startedAt=None,finishedAt=None,error=None,result=None))]
        for p in self.patches:p.start()

    async def asyncTearDown(self):
        await jobs.stop()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    async def test_repeated_request_is_single_job_and_saves_archive(self):
        gate=asyncio.Event()
        async def compare(**kwargs):
            await gate.wait()
            return dict(version=jobs.research.RESEARCH_VERSION,engineVersion=jobs.research.ENGINE_VERSION,
                        generatedAt=time.time(),results=[])
        with patch.object(jobs.research,'run_research',side_effect=compare) as compare_mock:
            self.assertEqual(jobs.start()['status'],'running')
            self.assertEqual(jobs.start()['status'],'running')
            gate.set()
            await jobs._task
            self.assertEqual(jobs.status()['status'],'completed')
            self.assertEqual(compare_mock.call_count,1)
            self.assertEqual(len(list(Path(self.tmp.name).glob('research-*.json'))),1)
            self.assertEqual(json.loads(jobs.RESULT_PATH.read_text())['results'],[])
            jobs.start()
            self.assertEqual(compare_mock.call_count,1)

    async def test_failed_job_preserves_previous_report(self):
        previous=dict(version=jobs.research.RESEARCH_VERSION,engineVersion=jobs.research.ENGINE_VERSION,
                      generatedAt=time.time()-4000,results=[dict(reason='anterior')])
        jobs.RESULT_PATH.write_text(json.dumps(previous),encoding='utf-8')
        self.assertEqual(jobs.status()['result'],previous)
        with patch.object(jobs.research,'run_research',side_effect=ValueError('offline')):
            jobs.start()
            await jobs._task
        self.assertEqual(jobs.status()['status'],'failed')
        self.assertEqual(jobs.status()['result'],previous)
        self.assertEqual(json.loads(jobs.RESULT_PATH.read_text()),previous)
