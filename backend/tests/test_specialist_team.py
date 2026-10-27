import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ai_swarm, specialist_team, subscription_bridge


ROOT = Path(__file__).resolve().parents[2]


def listing(**extra):
    row = dict(symbol='BTC', bias=0.4, fakeout=False, trapDirection='none', headlineRisk='elevated',
               catalystType='listing', citations=['https://www.reuters.com/markets/a', 'https://www.bloomberg.com/news/b'],
               reason='Listado nuevo en un recinto de primer nivel')
    row.update(extra)
    return row


def sources(now, report):
    return [dict(url=url, publishedAt=now) for url in report['citations']]


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(ai_swarm, 'DB_PATH', Path(self.tmp.name) / 'swarm.sqlite')
        self.db.start()

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def test_profiles_name_inputs_tools_output_and_success(self):
        self.assertEqual(set(specialist_team.PROFILES), {'news-verifier', 'quant-researcher', 'technical-reviewer'})
        for name, profile in specialist_team.PROFILES.items():
            text = (ROOT / '.grok' / 'agents' / f'{name}.md').read_text(encoding='utf-8')
            self.assertIn(profile['success'], text)
            self.assertIn(f'name: {name}', text)
            self.assertFalse(profile['marketBridgeSubagents'])
            self.assertIn('grok-desk-commander', profile['reuses'])
            self.assertTrue(profile['inputs'])
            self.assertTrue(profile['tools'])
            self.assertTrue(profile['output'])
        bridge = Path(subscription_bridge.__file__).read_text(encoding='utf-8')
        self.assertIn('--no-subagents', bridge)
        self.assertIn("'XAI_API_KEY'", bridge)
        self.assertIn('Bash(*)', bridge)
        specs = ai_swarm.MultiAgentSwarm(ai_swarm.Settings(transport='subscription')).specs()
        self.assertEqual([spec.agent_id for spec in specs],
                         ['grok-x-pulse', 'grok-flow-scalper', 'chatgpt-quant-risk', 'grok-desk-commander'])

    def test_verifier_requires_dates_sources_and_rejects_contradictions_and_recycles(self):
        now = 1_700_000_000.0
        report = listing()
        verdict = specialist_team.verify_news(report, now, sources(now, report))
        self.assertTrue(verdict['usable'])
        self.assertEqual(verdict['searches'], 0)
        self.assertEqual(verdict['checks']['domains'], ['bloomberg.com', 'reuters.com'])
        conflict = listing(headlineRisk='event', catalystType='retail_noise')
        bad = specialist_team.verify_news(conflict, now, sources(now, conflict))
        self.assertIn('event-ruido', bad['checks']['contradictions'])
        self.assertFalse(bad['usable'])
        later = now + specialist_team.RECYCLE_AFTER + 10
        recycled = specialist_team.verify_news(report, later, sources(later, report))
        self.assertTrue(recycled['checks']['recycled'])
        self.assertFalse(recycled['usable'])
        self.assertEqual(recycled['searches'], 0)

    def test_cache_hit_does_not_spend_the_joint_budget_and_the_fifth_waits(self):
        now = 1_700_000_000.0
        report = listing(headlineRisk='elevated', catalystType='none', citations=[])
        first = specialist_team.observe_radar([report], now)
        second = specialist_team.observe_radar([report], now + 10)
        self.assertEqual(len(first), 1)
        self.assertTrue(second[0]['cacheHit'])
        self.assertEqual(second[0]['searches'], 0)
        rows = []
        for symbol in ('ETH', 'SOL', 'LINK', 'AVAX'):
            rows.append(listing(symbol=symbol, headlineRisk='elevated', catalystType='none', citations=[], reason=symbol))
        # BTC ya ocupó un hueco. Tres caben y el cuarto del día (AVAX, el quinto trabajo) no.
        done = specialist_team.observe_radar(rows, now + 20)
        self.assertEqual([item.get('status') for item in done], [None, None, None, 'budget'])
        calm = listing(headlineRisk='none', catalystType='none', citations=[], convictionMultiplier=1)
        self.assertEqual(specialist_team.observe_radar([calm], now + 30), [])
        disputed = specialist_team.observe_radar(
            [calm], now + 40, commander=dict(disagreements=['El titular no coincide con el precio']))
        self.assertEqual(disputed[0]['status'], 'budget')

    def test_researcher_stays_idle_without_a_concrete_hypothesis(self):
        now = 1_700_000_000.0
        vague = specialist_team.propose_experiment(dict(statement='a lo mejor sube'), now)
        self.assertFalse(vague['activated'])
        self.assertFalse(vague['applyToLive'])
        live = specialist_team.propose_experiment(dict(
            statement='La expectancyNet de BTC en 4h cambia de signo tras los costes',
            metric='expectancyNet', universe='BTC', horizon='4h', direction='long', minSample=30,
            applyToLive=True), now)
        self.assertTrue(live['activated'])
        self.assertFalse(live['success'])
        self.assertFalse(live['applyToLive'])
        leaked = specialist_team.propose_experiment(dict(
            statement='La expectancyNet de BTC en 4h cambia de signo tras los costes',
            metric='expectancyNet', universe='BTC', horizon='4h', direction='long', minSample=30),
            now, features=['pnl'])
        self.assertFalse(leaked['success'])
        proposal = specialist_team.propose_experiment(dict(
            statement='La expectancyNet de BTC en 4h cambia de signo tras los costes',
            metric='expectancyNet', universe='BTC', horizon='4h', direction='long', minSample=30), now)
        self.assertTrue(proposal['success'])
        self.assertEqual(proposal['costs'], 'net-costs-v1')
        self.assertFalse(proposal['oos']['holdoutUsedForSelection'])
        self.assertEqual(proposal['oos']['holdout'], 0.2)
        self.assertIn('atr_pct', proposal['causalFeatures'])
        self.assertFalse(proposal['applyToLive'])
        again = specialist_team.propose_experiment(dict(
            statement='La expectancyNet de BTC en 4h cambia de signo tras los costes',
            metric='expectancyNet', universe='BTC', horizon='4h', direction='long', minSample=30), now + 5)
        self.assertTrue(again['cacheHit'])
        self.assertEqual(again['searches'], 0)

    def test_reviewer_adds_a_regression_only_for_a_demonstrable_failure(self):
        idle = specialist_team.accept_review({})
        self.assertFalse(idle['activated'])
        self.assertFalse(idle['success'])
        blocked = specialist_team.accept_review(dict(
            failingTest='tests.test_subscription_bridge.BridgeContractTests.test_commands_deny_actions_and_use_supported_clients',
            observed='AssertionError: --no-subagents ausente',
            regressionTest='backend/tests/test_subscription_bridge.py',
            files=['backend/app/subscription_bridge.py']))
        self.assertTrue(blocked['demonstrable'])
        self.assertFalse(blocked['success'])
        accepted = specialist_team.accept_review(dict(
            failingTest='tests.test_specialist_team.ProfileTests.test_reviewer_adds_a_regression_only_for_a_demonstrable_failure',
            observed='AssertionError: el fallo no queda cubierto',
            regressionTest='backend/tests/test_specialist_team.py',
            files=['backend/tests/test_specialist_team.py']))
        self.assertTrue(accepted['success'])
        self.assertFalse(accepted['marketBridgeSubagents'])

    def test_stored_radar_activates_the_verifier_without_a_new_search(self):
        now = 1_700_000_000.0
        calm = dict(symbol='ETH', bias=0.1, convictionMultiplier=1.0, fakeout=False, trapDirection='none',
                    headlineRisk='none', catalystType='none', catalysts=[], citations=[], reason='Sin titular')
        event = dict(symbol='BTC', bias=0.2, convictionMultiplier=1.0, fakeout=False, trapDirection='none',
                     headlineRisk='event', catalystType='macro', catalysts=['CPI'],
                     citations=['https://www.reuters.com/cpi', 'https://www.bloomberg.com/cpi'],
                     reason='Dato macro inminente')
        ai_swarm.store_pulse_batch(ai_swarm.parse_batch_pulse({'items': [calm]}, ['ETH']), now)
        with specialist_team._connect() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM specialist_cache').fetchone()[0], 0)
        ai_swarm.store_pulse_batch(ai_swarm.parse_batch_pulse({'items': [event]}, ['BTC']), now + 1)
        with specialist_team._connect() as con:
            row = con.execute('SELECT searches, payload FROM specialist_cache').fetchone()
        self.assertEqual(row[0], 0)
        self.assertIn('"searches": 0', row[1])
