"""Recovery drills use disposable books only; they never start an engine."""

import hashlib
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('restore_state_bundle', ROOT / 'deploy/state_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class RestoreSafetyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.base = Path(self.folder.name)
        self.root = self.base / 'project'
        (self.root / 'backend').mkdir(parents=True)
        (self.root / '.runtime').mkdir()
        self.create_book(self.root / 'backend/data.db', 'market-data')
        self.snapshot = self.base / 'snapshot'

    def create_book(self, path, value, *, wal=False):
        con = sqlite3.connect(path)
        if wal:
            self.addCleanup(con.close)
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('PRAGMA wal_autocheckpoint=0')
        con.execute('CREATE TABLE receipts (value TEXT NOT NULL)')
        con.execute('INSERT INTO receipts VALUES (?)', (value,))
        con.commit()
        if not wal:
            con.close()
        return con

    def read_book(self, path):
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as con:
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
            return [row[0] for row in con.execute('SELECT value FROM receipts')]

    def write_manifest(self, manifest):
        (self.snapshot / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def make_link(self, target, link, *, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest('This environment cannot create symbolic links: ' + str(exc))

    def test_all_books_restore_to_staging_with_committed_wal_and_without_secrets(self):
        for name in bundle.DATABASES:
            con = self.create_book(self.root / '.runtime' / name, name, wal=True)
            self.assertTrue(Path(str(self.root / '.runtime' / name) + '-wal').is_file())
            # Another connection must see the committed receipt, never this unfinished write.
            con.execute('INSERT INTO receipts VALUES (?)', ('uncommitted',))
        self.assertTrue({'research-ledger.sqlite', 'swarm.sqlite', 'decision-audit.sqlite',
                         'team-workflow.sqlite'} <= set(bundle.DATABASES))
        (self.root / '.runtime/research-latest-result.json').write_text('{"status":"fixture"}', encoding='utf-8')
        (self.root / '.runtime/gemini.env').write_text('GEMINI_API_KEY=test-only', encoding='utf-8')
        (self.root / '.runtime/mt5-demo.json').write_text('password=test-only', encoding='utf-8')
        manifest = bundle.snapshot_state(self.root, self.snapshot)
        expected = {'data/data.db', 'runtime/research-latest-result.json'} | {
            'runtime/' + name for name in bundle.DATABASES}
        self.assertEqual({entry['path'] for entry in manifest['files']}, expected)
        self.assertFalse(manifest['includesGeminiKey'])
        before = {path.relative_to(self.snapshot).as_posix() for path in self.snapshot.rglob('*')}
        destination = self.base / 'restored'
        result = bundle.restore_snapshot(self.snapshot, destination)
        self.assertEqual(result, dict(status='restored-to-staging', files=len(expected),
                                      activated=False, reconciliationRequired=True))
        self.assertEqual(self.read_book(destination / 'data/data.db'), ['market-data'])
        for name in bundle.DATABASES:
            self.assertEqual(self.read_book(destination / 'runtime' / name), [name])
        self.assertEqual(bundle.verify_snapshot(destination), manifest)
        self.assertTrue((destination / 'backups').is_dir())
        self.assertFalse((destination / 'runtime/gemini.env').exists())
        self.assertFalse((destination / 'runtime/mt5-demo.json').exists())
        self.assertEqual(before, {path.relative_to(self.snapshot).as_posix() for path in self.snapshot.rglob('*')})
        self.assertFalse(any(path.name.endswith(('-wal', '-shm', '-journal')) for path in destination.rglob('*')))

    def test_credentials_require_explicit_inclusion_and_matching_manifest_flag(self):
        (self.root / '.runtime/gemini.env').write_text('GEMINI_API_KEY=test-only', encoding='utf-8')
        manifest = bundle.snapshot_state(self.root, self.snapshot, include_gemini=True)
        self.assertTrue(manifest['includesGeminiKey'])
        bundle.restore_snapshot(self.snapshot, self.base / 'restore-with-key')
        manifest['includesGeminiKey'] = False
        self.write_manifest(manifest)
        with self.assertRaises(ValueError):
            bundle.verify_snapshot(self.snapshot)

    def test_research_hash_chain_and_swarm_audit_receipts_survive_recovery(self):
        from app import ai_swarm, decision_audit, experiments

        registry = self.root / '.runtime/research-ledger.sqlite'
        experiment = experiments.register_experiment('recovery-fixture',
            dict(parameters={'fixed': 1}, costModel={'version': 'fixture-cost-v1'}, trialCount=2),
            db_path=registry)
        receipt = experiments.record_observation(experiment['id'], 'recovery-fixture',
                                                {'receipt': 'keep'}, db_path=registry)
        audit = self.root / '.runtime/decision-audit.sqlite'
        candidate = decision_audit.record_candidate('recovery-fixture', 'BTCUSDT', 'long',
            strategy_version='fixture-v1', cost_version='fixture-cost-v1',
            risk_policy='fixture', economics={}, db_path=audit)
        decision_audit.record_decision(candidate, 'rules_local', True,
                                      size_multiplier=.5, db_path=audit)
        swarm = self.root / '.runtime/swarm.sqlite'
        with patch.object(ai_swarm, 'DB_PATH', swarm), closing(ai_swarm.connection()) as con:
            con.execute("INSERT INTO decisions(symbol,ts,prompt_version,payload) VALUES ('BTCUSDT',1,1,?)",
                        ('{"receipt":"keep"}',))
            con.commit()
        bundle.snapshot_state(self.root, self.snapshot)
        destination = self.base / 'restored-evidence'
        bundle.restore_snapshot(self.snapshot, destination)
        recovered_registry = destination / 'runtime/research-ledger.sqlite'
        self.assertTrue(experiments.verify_ledger(recovered_registry))
        self.assertEqual(experiments.get_experiment(experiment['id'], recovered_registry), experiment)
        self.assertEqual(experiments.list_experiments(db_path=recovered_registry)[0]['events'], 2)
        with closing(sqlite3.connect(recovered_registry)) as con:
            self.assertEqual(con.execute('SELECT event_hash FROM research_events ORDER BY seq DESC LIMIT 1').fetchone()[0],
                             receipt['eventHash'])
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute('DELETE FROM research_events')
        status = decision_audit.health_snapshot(destination / 'runtime/decision-audit.sqlite')
        self.assertTrue(status['available'])
        self.assertEqual((status['candidates'], status['decisions'], status['outcomes']), (1, 1, 0))
        with closing(sqlite3.connect(destination / 'runtime/swarm.sqlite')) as con:
            self.assertEqual(con.execute('SELECT symbol,payload FROM decisions').fetchall(),
                             [('BTCUSDT', '{"receipt":"keep"}')])

    def test_modified_file_is_rejected_before_creating_destination(self):
        bundle.snapshot_state(self.root, self.snapshot)
        with (self.snapshot / 'data/data.db').open('ab') as target:
            target.write(b'tampered')
        destination = self.base / 'restored'
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            bundle.restore_snapshot(self.snapshot, destination)
        self.assertFalse(destination.exists())

    def test_valid_hash_does_not_substitute_for_sqlite_integrity(self):
        manifest = bundle.snapshot_state(self.root, self.snapshot)
        payload = b'this is not a SQLite database'
        (self.snapshot / 'data/data.db').write_bytes(payload)
        manifest['files'][0]['sha256'] = hashlib.sha256(payload).hexdigest()
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError, 'integrity failed'):
            bundle.verify_snapshot(self.snapshot)

    def test_duplicate_and_traversal_paths_are_rejected(self):
        manifest = bundle.snapshot_state(self.root, self.snapshot)
        manifest['files'].append(dict(manifest['files'][0]))
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            bundle.verify_snapshot(self.snapshot)
        manifest['files'].pop()
        for path in ('../outside.db', 'runtime/../../outside.db', '/tmp/outside.db',
                     'C:\\outside.db', '..\\outside.db', 'runtime\\swarm.sqlite',
                     'runtime/../data/data.db'):
            with self.subTest(path=path):
                manifest['files'][0]['path'] = path
                self.write_manifest(manifest)
                with self.assertRaisesRegex(ValueError, 'Unexpected'):
                    bundle.restore_snapshot(self.snapshot, self.base / 'restored')
                self.assertFalse((self.base / 'restored').exists())

    def test_malformed_and_oversized_manifests_are_rejected(self):
        manifest = bundle.snapshot_state(self.root, self.snapshot)
        invalid = [[], {}, {**manifest, 'files': []}, {**manifest, 'files': [None]},
                   {**manifest, 'files': [{'path': [], 'sha256': '0' * 64}]},
                   {**manifest, 'files': [{'path': 'data/data.db', 'sha256': 'x' * 64}]},
                   {**manifest, 'files': manifest['files'] * 100},
                   {**manifest, 'includesGeminiKey': 'false'},
                   {**manifest, 'includesGeminiKey': True}]
        for value in invalid:
            with self.subTest(manifest=value):
                self.write_manifest(value)
                with self.assertRaises(ValueError):
                    bundle.verify_snapshot(self.snapshot)
        (self.snapshot / 'manifest.json').write_text(' ' * (bundle.MAX_MANIFEST_BYTES + 1), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'oversized'):
            bundle.verify_snapshot(self.snapshot)

    def test_unlisted_wal_and_files_are_rejected_even_if_main_hash_matches(self):
        bundle.snapshot_state(self.root, self.snapshot)
        for relative in ('data/data.db-wal', 'data/data.db-shm', 'runtime/mt5-demo.json', 'extra.txt'):
            with self.subTest(path=relative):
                path = self.snapshot / relative
                path.write_bytes(b'unverified')
                try:
                    with self.assertRaisesRegex(ValueError, 'Unlisted'):
                        bundle.verify_snapshot(self.snapshot)
                finally:
                    path.unlink()

    def archive_dataset(self, *, close=100):
        from app import data_archive
        rows = [dict(time=1000 + i * 14400, open=100, high=102, low=99,
                     close=close, volume=1) for i in range(80)]
        return data_archive.archive_dataset(dict(symbol='SOLUSDT', interval='4h',
            source='recovery-fixture', candles=rows), root=self.root / '.runtime/research-data')

    def register_dataset_reference(self, receipt):
        from app import experiments
        registry = self.root / '.runtime/research-ledger.sqlite'
        experiment = experiments.register_experiment('recovery-fixture',
            dict(parameters={'fixed': 1}, costModel={'version': 'fixture'}), db_path=registry)
        experiments.record_observation(experiment['id'], 'dataset_snapshot', receipt, db_path=registry)
        return experiment

    def test_datasets_and_their_registry_references_survive_restore(self):
        from app import experiments
        receipt = self.archive_dataset()
        unreferenced = self.archive_dataset(close=101)
        experiment = self.register_dataset_reference(receipt)
        (self.root / '.runtime/research-data/.snapshot-unpublished').write_bytes(b'not published')
        manifest = bundle.snapshot_state(self.root, self.snapshot)
        files = {entry['path'] for entry in manifest['files']}
        destination = self.base / 'restored-datasets'
        bundle.restore_snapshot(self.snapshot, destination)
        for record in (receipt, unreferenced):
            relative = 'runtime/research-data/' + record['artifact']
            self.assertIn(relative, files)
            self.assertEqual((destination / relative).read_bytes(),
                             (self.root / '.runtime/research-data' / record['artifact']).read_bytes())
            self.assertEqual(hashlib.sha256((destination / relative).read_bytes()).hexdigest(), record['sha256'])
        self.assertFalse((destination / 'runtime/research-data/.snapshot-unpublished').exists())
        registry = destination / 'runtime/research-ledger.sqlite'
        self.assertTrue(experiments.verify_ledger(registry))
        events = experiments.observations(experiment['id'], 1e12, db_path=registry)
        self.assertEqual(events[-1]['payload']['artifact'], receipt['artifact'])

    def test_missing_referenced_dataset_is_not_reported_as_a_complete_backup(self):
        receipt = self.archive_dataset()
        self.register_dataset_reference(receipt)
        manifest = bundle.snapshot_state(self.root, self.snapshot)
        relative = 'runtime/research-data/' + receipt['artifact']
        (self.snapshot / relative).unlink()
        manifest['files'] = [entry for entry in manifest['files'] if entry['path'] != relative]
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError, 'references a missing'):
            bundle.restore_snapshot(self.snapshot, self.base / 'restored')
        self.assertFalse((self.base / 'restored').exists())

    def test_dataset_filename_must_match_bytes_even_with_an_updated_manifest(self):
        receipt = self.archive_dataset()
        manifest = bundle.snapshot_state(self.root, self.snapshot)
        relative = 'runtime/research-data/' + receipt['artifact']
        (self.snapshot / relative).write_bytes(b'modified')
        for entry in manifest['files']:
            if entry['path'] == relative:
                entry['sha256'] = hashlib.sha256(b'modified').hexdigest()
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError, 'hash filename'):
            bundle.verify_snapshot(self.snapshot)

    def test_source_datasets_with_changed_content_or_unexpected_names_are_rejected(self):
        receipt = self.archive_dataset()
        path = self.root / '.runtime/research-data' / receipt['artifact']
        path.write_bytes(b'modified')
        with self.assertRaisesRegex(ValueError, 'hash filename'):
            bundle.snapshot_state(self.root, self.snapshot)
        path.rename(path.with_name('unverified.json'))
        with self.assertRaisesRegex(ValueError, 'Unexpected dataset'):
            bundle.snapshot_state(self.root, self.base / 'other-snapshot')

    def test_dataset_archive_limits_fail_instead_of_silently_dropping_snapshots(self):
        receipt = self.archive_dataset()
        second = self.archive_dataset(close=101)
        size = (self.root / '.runtime/research-data' / receipt['artifact']).stat().st_size
        second_size = (self.root / '.runtime/research-data' / second['artifact']).stat().st_size
        manifest = bundle.snapshot_state(self.root, self.snapshot)
        self.assertEqual(len(manifest['files']), 3)
        for name, maximum in (('MAX_DATASET_FILES', 1), ('MAX_DATASET_BYTES', size - 1),
                              ('MAX_DATASET_TOTAL_BYTES', size + second_size - 1)):
            with self.subTest(limit=name), patch.object(bundle, name, maximum):
                with self.assertRaisesRegex(ValueError, 'limit exceeded'):
                    bundle.snapshot_state(self.root, self.base / name)
                with self.assertRaisesRegex(ValueError, 'limit exceeded'):
                    bundle.verify_snapshot(self.snapshot)

    def test_dataset_traversal_and_junction_paths_are_rejected(self):
        receipt = self.archive_dataset()
        manifest = bundle.snapshot_state(self.root, self.snapshot)
        relative = 'runtime/research-data/' + receipt['artifact']
        linked_directory = self.snapshot / 'runtime/research-data'
        with patch.object(Path, 'is_junction', autospec=True,
                          side_effect=lambda path: path == linked_directory):
            with self.assertRaises(ValueError):
                bundle.verify_snapshot(self.snapshot)
        for entry in manifest['files']:
            if entry['path'] == relative:
                entry['path'] = 'runtime/research-data/../' + receipt['artifact']
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            bundle.verify_snapshot(self.snapshot)

    def test_existing_destination_is_never_overwritten(self):
        bundle.snapshot_state(self.root, self.snapshot)
        for kind in ('directory', 'file'):
            with self.subTest(kind=kind):
                destination = self.base / kind
                if kind == 'directory':
                    destination.mkdir()
                    marker = destination / 'keep.txt'
                else:
                    marker = destination
                marker.write_text('preserve', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'new destination'):
                    bundle.restore_snapshot(self.snapshot, destination)
                self.assertEqual(marker.read_text(encoding='utf-8'), 'preserve')

    def test_destination_inside_snapshot_is_rejected(self):
        bundle.snapshot_state(self.root, self.snapshot)
        with self.assertRaisesRegex(ValueError, 'outside the snapshot'):
            bundle.restore_snapshot(self.snapshot, self.snapshot / 'restored')
        self.assertFalse((self.snapshot / 'restored').exists())

    def test_symlinked_file_is_rejected(self):
        bundle.snapshot_state(self.root, self.snapshot)
        source = self.snapshot / 'data/data.db'
        target = self.base / 'outside.db'
        shutil.copyfile(source, target)
        source.unlink()
        self.make_link(target, source)
        with self.assertRaises(ValueError):
            bundle.verify_snapshot(self.snapshot)

    def test_junction_directory_is_rejected_without_following_it(self):
        bundle.snapshot_state(self.root, self.snapshot)
        linked_directory = self.snapshot / 'data'
        with patch.object(Path, 'is_junction', autospec=True,
                          side_effect=lambda path: path == linked_directory):
            with self.assertRaises(ValueError):
                bundle.restore_snapshot(self.snapshot, self.base / 'restored')
        self.assertFalse((self.base / 'restored').exists())

    def test_symlinked_directory_is_rejected(self):
        bundle.snapshot_state(self.root, self.snapshot)
        source = self.snapshot / 'data'
        target = self.base / 'outside-data'
        source.rename(target)
        self.make_link(target, source, directory=True)
        with self.assertRaises(ValueError):
            bundle.verify_snapshot(self.snapshot)

    def test_symlinked_manifest_is_rejected(self):
        bundle.snapshot_state(self.root, self.snapshot)
        source = self.snapshot / 'manifest.json'
        target = self.base / 'manifest-outside.json'
        source.rename(target)
        self.make_link(target, source)
        with self.assertRaises(ValueError):
            bundle.verify_snapshot(self.snapshot)

    def test_symlinked_snapshot_root_is_rejected(self):
        bundle.snapshot_state(self.root, self.snapshot)
        alias = self.base / 'snapshot-alias'
        self.make_link(self.snapshot, alias, directory=True)
        with self.assertRaises(ValueError):
            bundle.verify_snapshot(alias)

    def test_symlinked_destination_parent_and_dangling_destination_are_rejected(self):
        bundle.snapshot_state(self.root, self.snapshot)
        target = self.base / 'real-target'
        target.mkdir()
        alias = self.base / 'alias'
        self.make_link(target, alias, directory=True)
        with self.assertRaises(ValueError):
            bundle.restore_snapshot(self.snapshot, alias / 'restored')
        dangling = self.base / 'dangling'
        self.make_link(target / 'missing', dangling, directory=True)
        with self.assertRaises(ValueError):
            bundle.restore_snapshot(self.snapshot, dangling)
        self.assertFalse((target / 'missing').exists())

    def test_sqlite_backup_refuses_to_replace_a_book(self):
        existing = self.base / 'existing.db'
        existing.write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            bundle.sqlite_snapshot(self.root / 'backend/data.db', existing)
        self.assertEqual(existing.read_bytes(), b'preserve')

    def test_verify_cli_needs_no_output_destination(self):
        bundle.snapshot_state(self.root, self.snapshot)
        result = subprocess.run([sys.executable, str(ROOT / 'deploy/state_bundle.py'),
                                 'verify', '--snapshot', str(self.snapshot)],
                                capture_output=True, text=True, check=True, timeout=30)
        self.assertEqual(json.loads(result.stdout), {'status': 'verified', 'files': 1})

    def test_exported_source_bundle_contains_the_dependency_lock(self):
        for relative in bundle.SOURCE_FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('fixture\n', encoding='utf-8')
        archive, _ = bundle.prepare_bundle(self.root, self.base / 'package')
        with bundle.tarfile.open(archive, 'r:gz') as package:
            names = set(package.getnames())
        self.assertIn('mesa-siete/backend/requirements.lock', names)
        self.assertIn('mesa-siete/state/manifest.json', names)


if __name__ == '__main__':
    unittest.main()
