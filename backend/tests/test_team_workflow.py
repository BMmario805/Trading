import asyncio
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from app import ai_swarm, decision_audit, public_sources, specialist_team, subscription_bridge, team_workflow


def hypothesis():
    return dict(statement='La expectancyNet de BTC en 4h debe superar cero después de costes netos',
                metric='expectancyNet', universe='BTC', horizon='4h', direction='long', minSample=50)


def request(**changes):
    return dict(dict(brief='Comprobar una hipótesis de continuación con costes completos',
                     symbols=['BTC'], hypothesis=hypothesis()), **changes)


def calm_report():
    return dict(symbol='BTC', bias=.1, convictionMultiplier=1.0, fakeout=False,
                trapDirection='none',headlineRisk='none',catalystType='none',
                catalysts=[],citations=[],reason='Sin catalizador atribuido')


def official_report():
    return dict(calm_report(),headlineRisk='elevated',catalystType='macro',
        catalysts=['official policy statement'],reason='An official policy statement is available for research',
        citations=['https://www.sec.gov/news/a','https://www.federalreserve.gov/newsevents/a'])


def source_body(*,published=True):
    stamp=datetime.now(timezone.utc).isoformat()
    meta='<meta property="article:published_time" content="'+stamp+'">' if published else ''
    return ('<html><head>'+meta+'</head><body>'+(
        'An official policy statement is available for research. These are publisher claims requiring independent corroboration. '*3
        )+'</body></html>').encode()


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.patches=[patch.object(ai_swarm,'DB_PATH',self.root/'swarm.sqlite'),
                      patch.object(team_workflow,'ROOT',self.root),
                      patch.object(team_workflow,'HANDOFFS',self.root/'.grok'/'handoffs'),
                      patch.object(team_workflow,'DB_PATH',None),
                      patch.object(decision_audit,'DB_PATH',None)]
        for p in self.patches:p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def store_calm(self):
        parsed=ai_swarm.parse_batch_pulse({'items':[calm_report()]},['BTC'])
        ai_swarm.store_pulse_batch(parsed)

    async def test_encargo_to_proposal_end_to_end_with_cached_radar_and_no_clients(self):
        self.store_calm()
        finding=dict(failingTest='tests.test_team_workflow.observed_failure',observed='AssertionError: receipt missing',
                     regressionTest='backend/tests/test_team_workflow.py',files=['backend/tests/test_team_workflow.py'])
        with patch.object(subscription_bridge,'complete',AsyncMock(side_effect=AssertionError('No provider'))), \
             patch('subprocess.Popen',side_effect=AssertionError('No client process')):
            result=await team_workflow.submit_task(request(finding=finding))
        self.assertEqual(result['status'],'proposal_ready')
        done=[r for r in result['stages'] if r['status']!='started']
        self.assertEqual([r['stage'] for r in done],list(team_workflow.STAGES))
        self.assertEqual({r['correlationId'] for r in done},{result['correlationId']})
        self.assertTrue(all(r['actor']['desktopBot'] is False for r in done))
        self.assertFalse(result['executionAuthorized'])
        self.assertFalse(result['applyToLive'])
        self.assertFalse(result['profitabilityProven'])
        self.assertFalse(result['providerE2EVerified'])
        self.assertEqual(result['proposal']['validationState'],'not_executed')
        self.assertEqual(result['proposal']['reviewState'],'contract_only')
        self.assertEqual(team_workflow.task_snapshot(result['taskId'])['status'],'proposal_ready')

    async def test_claimed_urls_and_dates_do_not_become_verified_evidence(self):
        now=time.time()
        report=dict(calm_report(),headlineRisk='elevated',catalystType='listing',
                    citations=['https://one.example/news','https://two.example/news'])
        verdict=specialist_team.verify_news(report,now,sources=[dict(url=u,publishedAt=now,
            retrievedAt=now,provenance='retrieved',contentSha256='a'*64) for u in report['citations']])
        self.assertTrue(verdict['contractPassed'])
        self.assertFalse(verdict['evidenceVerified'])
        self.assertFalse(verdict['evidence']['contentIndependentlyChecked'])
        reader=AsyncMock(return_value=dict(status='completed',reports=[report]))
        result=await team_workflow.submit_task(request(),radar_reader=reader)
        self.assertEqual(result['status'],'blocked')
        self.assertIn('news_evidence_not_verified',result['proposal']['blockers'])
        self.assertFalse(result['executionAuthorized'])

    async def test_retrieved_official_content_delivers_only_a_research_proposal_with_pending_facts(self):
        raw=source_body()
        with patch.object(public_sources,'_download',return_value=(raw,{'content-type':'text/html'})) as fetch, \
             patch.object(subscription_bridge,'complete',AsyncMock(side_effect=AssertionError('No LLM call'))):
            result=await team_workflow.submit_task(request(),radar_reader=AsyncMock(
                return_value=dict(status='completed',reports=[official_report()])))
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(result['status'],'proposal_ready')
        self.assertFalse(result['executionAuthorized'])
        proposal=result['proposal']
        self.assertEqual(proposal['scope'],'research_only')
        self.assertEqual(proposal['newsEvidenceState'],'captured_claims_pending')
        self.assertTrue(proposal['retrievalVerified'])
        self.assertFalse(proposal['factVerified'])
        self.assertIn('claim_not_corroborated',proposal['verificationBlockers'])
        self.assertIn('editorial_independence_unverified',proposal['verificationBlockers'])
        self.assertTrue(any(c['matchedCapturedContent'] for c in proposal['claimsPending']))
        verdict=next(r['result'] for r in result['stages'] if r['stage']=='verificador' and r['status']=='research_only')
        self.assertEqual(verdict['retrievedUrls'],2)
        self.assertEqual(verdict['uniqueContents'],1)
        self.assertEqual(verdict['independentlyVerifiedUrls'],0)
        for receipt in verdict['retrievalReceipts']:
            path=self.root/'public-sources'/receipt['artifact']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),receipt['contentSha256'])

    async def test_captured_content_without_a_publication_date_remains_visibly_blocked(self):
        with patch.object(public_sources,'_download',return_value=(source_body(published=False),{'content-type':'text/html'})):
            result=await team_workflow.submit_task(request(),radar_reader=AsyncMock(
                return_value=dict(status='completed',reports=[official_report()])))
        self.assertEqual(result['status'],'blocked')
        self.assertTrue(result['proposal']['retrievalVerified'])
        self.assertIn('news_evidence_not_verified',result['proposal']['blockers'])
        self.assertIn('publication_date_missing_or_stale',result['proposal']['verificationBlockers'])
        self.assertFalse(result['proposal']['factVerified'])

    async def test_retrieval_does_not_expand_the_four_task_daily_budget_or_retry_urls(self):
        with patch.object(public_sources,'_download',return_value=(source_body(),{'content-type':'text/html'})) as fetch:
            reader=AsyncMock(return_value=dict(status='completed',reports=[official_report()]))
            for _ in range(3):
                result=await team_workflow.submit_task(request(),radar_reader=reader)
                self.assertEqual(result['status'],'proposal_ready')
            # The joint specialist cap also counts the first research proposal.
            # Four accepted tasks do not promise four successful proposals.
            fourth=await team_workflow.submit_task(request(),radar_reader=reader)
            self.assertEqual(fourth['status'],'blocked')
            self.assertIn('news_evidence_not_verified',fourth['proposal']['blockers'])
            verdict=next(stage['result'] for stage in fourth['stages']
                         if stage['stage']=='verificador' and stage['status']=='blocked')
            self.assertEqual(verdict['verdicts'][0]['status'],'budget')
            self.assertFalse(fourth['executionAuthorized'])
            self.assertEqual(fetch.call_count,8)
            fifth=await team_workflow.submit_task(request(),radar_reader=reader)
            self.assertEqual(fifth['error'],'daily_task_budget')
            self.assertEqual(fetch.call_count,8)

    async def test_timeout_is_received_retried_once_and_never_loops(self):
        async def stalled(_):
            await asyncio.sleep(1)
        reader=AsyncMock(side_effect=stalled)
        result=await team_workflow.submit_task(request(),radar_reader=reader,stage_timeout=.005)
        timeouts=[r for r in result['stages'] if r['stage']=='radar' and r['status']=='timeout']
        self.assertEqual(reader.await_count,2)
        self.assertEqual([r['attempt'] for r in timeouts],[1,2])
        self.assertEqual(result['status'],'blocked')
        self.assertFalse(timeouts[-1]['result']['retryable'])

    async def test_cancelled_task_keeps_a_correlated_receipt_and_never_restarts(self):
        started=asyncio.Event()
        async def stalled(_):
            started.set()
            await asyncio.Event().wait()
        task=asyncio.create_task(team_workflow.submit_task(request(taskId='task-cancel',correlationId='flow-cancel'),radar_reader=stalled))
        await asyncio.wait_for(started.wait(),1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        saved=team_workflow.task_snapshot('task-cancel')
        self.assertEqual(saved['status'],'cancelled')
        self.assertFalse(saved['executionAuthorized'])
        receipt=saved['stages'][-1]
        self.assertEqual((receipt['stage'],receipt['status'],receipt['attempt']),('radar','cancelled',1))
        self.assertEqual(receipt['correlationId'],'flow-cancel')

    async def test_error_receipt_redacts_exception_text_and_does_not_retry(self):
        reader=AsyncMock(side_effect=ValueError('raw-private-provider-output'))
        result=await team_workflow.submit_task(request(),radar_reader=reader)
        self.assertEqual(reader.await_count,1)
        self.assertEqual(result['status'],'blocked')
        self.assertNotIn('raw-private-provider-output',json.dumps(result))
        error=next(r for r in result['stages'] if r['stage']=='radar' and r['status']=='error')
        self.assertEqual(error['result']['errorType'],'ValueError')

    async def test_live_application_or_unknown_fields_cannot_enter_the_workflow(self):
        with self.assertRaises(ValueError):
            await team_workflow.submit_task(request(applyToLive=True))
        result=await team_workflow.submit_task(request(hypothesis=dict(hypothesis(),applyToLive=True)),
            radar_reader=AsyncMock(return_value=dict(status='completed',reports=[calm_report()])))
        self.assertEqual(result['status'],'blocked')
        self.assertFalse(result['executionAuthorized'])

    async def test_subscription_radar_reuses_existing_transport_and_is_not_retried(self):
        self.store_calm()
        fake=type('Runtime',(),{})()
        fake.settings=type('Settings',(),{'transport':'subscription'})()
        fake.refresh_pulse_batch=AsyncMock(return_value=None)
        with patch.object(ai_swarm,'SWARM',fake):
            result=await team_workflow.submit_task(request(radarMode='subscription'))
        self.assertEqual(fake.refresh_pulse_batch.await_count,1)
        self.assertFalse(result['providerE2EVerified'])
        self.assertFalse(result['executionAuthorized'])

    async def test_idempotency_and_daily_budget_do_not_repeat_work(self):
        reader=AsyncMock(return_value=dict(status='completed',reports=[calm_report()]))
        first=await team_workflow.submit_task(request(idempotencyKey='stable-request'),radar_reader=reader)
        second=await team_workflow.submit_task(request(idempotencyKey='stable-request'),radar_reader=reader)
        self.assertEqual(first['taskId'],second['taskId'])
        self.assertEqual(reader.await_count,1)
        with self.assertRaises(team_workflow.WorkflowConflict):
            await team_workflow.submit_task(request(idempotencyKey='stable-request',brief='Otro encargo incompatible'),radar_reader=reader)
        for _ in range(3):await team_workflow.submit_task(request(),radar_reader=reader)
        blocked=await team_workflow.submit_task(request(),radar_reader=reader)
        self.assertEqual(blocked['error'],'daily_task_budget')
        self.assertEqual(reader.await_count,4)

    async def test_health_and_missing_task_are_read_only_and_ignore_auth(self):
        with patch.object(subscription_bridge,'status',side_effect=AssertionError('No auth read')), \
             patch.object(subscription_bridge,'executable',return_value=None), \
             patch('subprocess.Popen',side_effect=AssertionError('No clients')):
            result=team_workflow.health_snapshot()
        self.assertTrue(result['readOnly'])
        self.assertFalse(result['integration']['desktopBotsConnected'])
        self.assertEqual(result['storage'],'not_created')
        self.assertIsNone(team_workflow.task_snapshot('missing'))
        self.assertFalse((self.root/'team-workflow.sqlite').exists())
        self.assertFalse((self.root/'decision-audit.sqlite').exists())
        self.assertFalse((self.root/'swarm.sqlite').exists())

    async def test_patch_only_swarm_book_isolates_workflow_and_attribution(self):
        self.store_calm()
        await team_workflow.submit_task(request())
        candidate=decision_audit.record_candidate('test','BTC','long',strategy_version='test-v1',
            cost_version='net-costs-v1',risk_policy='test-only',economics={})
        self.assertTrue(candidate.startswith('candidate-'))
        self.assertEqual(team_workflow._path(),self.root/'team-workflow.sqlite')
        self.assertEqual(decision_audit._path(),self.root/'decision-audit.sqlite')
        self.assertTrue((self.root/'team-workflow.sqlite').exists())
        self.assertTrue((self.root/'decision-audit.sqlite').exists())

    async def test_desktop_is_pending_without_a_task_matched_receipt(self):
        reader=AsyncMock(return_value=dict(status='completed',reports=[calm_report()]))
        result=await team_workflow.submit_task(request(requireDesktopReceipt=True),radar_reader=reader)
        self.assertEqual(result['status'],'blocked')
        self.assertIn('desktop_receipt_pending',result['proposal']['blockers'])


class DesktopReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.folder=self.root/'.grok'/'handoffs';self.folder.mkdir(parents=True)
        self.patches=[patch.object(team_workflow,'ROOT',self.root),patch.object(team_workflow,'HANDOFFS',self.folder)]
        for p in self.patches:p.start()
        artifact=self.folder/'nonce.txt';artifact.write_text('local test challenge',encoding='utf-8')
        self.challenge=dict(protocol=team_workflow.HANDOFF_PROTOCOL,correlationId='flow-proof',
            taskId='desktop-read-proof',nonce='nonce-with-enough-entropy',agent='technical-reviewer',
            artifact='.grok/handoffs/nonce.txt',sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),createdAt=time.time())
        (self.folder/'challenge.json').write_text(json.dumps(self.challenge),encoding='utf-8')

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def receipt(self,**changes):
        r=dict(self.challenge,status='completed',observedAt=time.time(),errors=[])
        r.update(changes)
        (self.folder/'receipt-proof.json').write_text(json.dumps(r),encoding='utf-8')

    def test_nonce_artifact_proof_only_accredits_the_named_agent(self):
        self.receipt()
        result=team_workflow.desktop_receipt_snapshot()
        self.assertTrue(result['desktopBotsConnected'])
        self.assertEqual(result['desktopAgentsSeen'],['technical-reviewer'])
        self.assertNotIn('nonce',result)
        self.assertNotIn('sha256',result)

    def test_multiple_agent_challenges_are_discovered_and_filtered_per_task(self):
        self.receipt()
        challenges=self.folder/'challenges';challenges.mkdir()
        for agent in ('market-radar','news-verifier','quant-researcher'):
            challenge=dict(self.challenge,taskId='task-'+agent,agent=agent,nonce='different-nonce-'+agent)
            (challenges/(agent+'.json')).write_text(json.dumps(challenge),encoding='utf-8')
            if agent!='quant-researcher':
                receipt=dict(challenge,status='completed',observedAt=time.time(),errors=[])
                (self.folder/('receipt-'+agent+'.json')).write_text(json.dumps(receipt),encoding='utf-8')
        health=team_workflow.desktop_receipt_snapshot()
        self.assertEqual(health['desktopAgentsSeen'],['market-radar','news-verifier','technical-reviewer'])
        self.assertEqual(health['pendingAgents'],['quant-researcher'])
        self.assertFalse(health['allExpectedDesktopAgentsConnected'])
        own=team_workflow.desktop_receipt_snapshot(task_id='task-quant-researcher',correlation_id='flow-proof')
        self.assertFalse(own['desktopBotsConnected'])
        self.assertEqual(own['expectedDesktopAgents'],['quant-researcher'])
        reviewer=team_workflow.desktop_receipt_snapshot(task_id='desktop-read-proof',correlation_id='flow-proof')
        self.assertEqual(reviewer['desktopAgentsSeen'],['technical-reviewer'])
        self.assertTrue(reviewer['allExpectedDesktopAgentsConnected'])

    def test_missing_wrong_nonce_agent_hash_and_expiry_fail_closed(self):
        self.assertFalse(team_workflow.desktop_receipt_snapshot()['desktopBotsConnected'])
        for changes in [dict(nonce='wrong-nonce'),dict(agent='quant-researcher'),dict(sha256='0'*64),
                        dict(observedAt=time.time()-4000),dict(errors=['observed failure'])]:
            self.receipt(**changes)
            self.assertFalse(team_workflow.desktop_receipt_snapshot()['desktopBotsConnected'])
        self.receipt()
        self.assertFalse(team_workflow.desktop_receipt_snapshot(task_id='other-task')['desktopBotsConnected'])

    def test_traversal_malformed_and_non_data_files_are_not_read(self):
        self.challenge['artifact']='../../outside.txt'
        (self.folder/'challenge.json').write_text(json.dumps(self.challenge),encoding='utf-8')
        self.receipt()
        self.assertEqual(team_workflow.desktop_receipt_snapshot()['state'],'blocked')
        (self.folder/'challenge.json').write_text('[]',encoding='utf-8')
        self.assertEqual(team_workflow.desktop_receipt_snapshot()['state'],'blocked')


class DecisionAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.db=self.root/'decision-audit.sqlite'
        self.patch=patch.object(ai_swarm,'DB_PATH',self.root/'swarm.sqlite');self.patch.start()

    def tearDown(self):
        self.patch.stop();self.tmp.cleanup()

    def candidate(self,**changes):
        args=dict(strategy_version='fixed-v1',cost_version='net-costs-v1',risk_policy='risk-v1',
                  economics=dict(planned=dict(viable=True,netRisk=1,netRewardRisk=2)),
                  risk_eur=1,notional_eur=20,candidate_key='one-observed-opportunity')
        args.update(changes)
        return decision_audit.record_candidate('test','BTC','long',**args)

    def test_paired_arms_use_one_immutable_candidate_and_do_not_invent_profit(self):
        candidate=self.candidate()
        self.assertEqual(candidate,self.candidate())
        decision_audit.record_decision(candidate,'rules_local',True,size_multiplier=1)
        decision_audit.record_decision(candidate,'rules_plus_ai',False,blockers=['unverified news'])
        health=decision_audit.health_snapshot()
        self.assertEqual((health['candidates'],health['decisions'],health['outcomes']),(1,2,0))
        self.assertEqual(health['pairedDecisionCandidates'],1)
        self.assertFalse(health['incrementalAdvantageMeasured'])
        with self.assertRaises(ValueError):decision_audit.record_decision(candidate,'rules_plus_ai',True)
        with self.assertRaises(ValueError):decision_audit.record_outcome(candidate,'rules_plus_pulse',net_pnl=1,
            currency='EUR',initial_risk=1,fees=.1,slippage=.1,carry=0,outcome_type='counterfactual-observed')

    def test_explicit_book_path_overrides_global_and_never_creates_swarm(self):
        other=self.root/'isolated-desk'/'desk.sqlite'
        ledger=decision_audit.storage_for(other)
        candidate=self.candidate(db_path=ledger)
        decision_audit.record_decision(candidate,'rules_local',False,db_path=ledger)
        self.assertEqual(decision_audit.health_snapshot(db_path=ledger)['candidates'],1)
        self.assertFalse(self.db.exists())
        self.assertFalse((self.root/'swarm.sqlite').exists())


if __name__=='__main__':unittest.main()
