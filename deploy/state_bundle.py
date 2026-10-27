"""Exportación acotada y copias SQLite consistentes, sin copiar logs ni claves MT5."""

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import tarfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


DATABASES = ('desk.sqlite', 'desk-forex.sqlite', 'desk-stocks.sqlite', 'gemini.sqlite', 'mt5-demo.sqlite',
             'swarm.sqlite', 'research-ledger.sqlite', 'team-workflow.sqlite', 'decision-audit.sqlite')
SOURCE_FILES = ('.dockerignore', 'Dockerfile', 'compose.yaml', 'backend/requirements.txt',
                'backend/requirements-cloud.txt', 'backend/requirements.lock',
                'frontend/package.json', 'frontend/package-lock.json',
                'frontend/index.html', 'frontend/tsconfig.json', 'frontend/tsconfig.app.json',
                'frontend/tsconfig.node.json', 'frontend/vite.config.ts')
DATASET_PATTERN = re.compile(r'runtime/research-data/([0-9a-f]{64})\.json\Z')
MAX_DATASET_FILES = 1024
MAX_DATASET_BYTES = 8 * 1024 * 1024
MAX_DATASET_TOTAL_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024


def _is_link(path):
    # Windows junctions are not reported by is_symlink().
    return path.is_symlink() or path.is_junction()


def _reject_link_path(path):
    path = Path(path).absolute()
    if any(_is_link(part) for part in (path, *path.parents)):
        raise ValueError('Backup and restore paths cannot contain links')
    return path.resolve()


def _sha256(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _check_database(path):
    try:
        # A backup is self-contained: do not read an unverified WAL or create sidecars.
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as con:
            if con.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Backup database integrity failed: ' + path.name)
    except sqlite3.Error as exc:
        raise ValueError('Backup database integrity failed: ' + path.name) from exc


def _dataset_sources(root):
    directory = _reject_link_path(root / '.runtime/research-data')
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError('Invalid dataset archive directory')
    files, total = [], 0
    for source in directory.iterdir():
        if _is_link(source) or not source.is_file():
            raise ValueError('Dataset archive contains a linked or invalid path')
        # Unpublished writer temporaries are not immutable snapshots.
        if source.name.startswith('.snapshot-'):
            continue
        relative = 'runtime/research-data/' + source.name
        match = DATASET_PATTERN.fullmatch(relative)
        if match is None:
            raise ValueError('Unexpected dataset archive filename')
        size = source.stat().st_size
        total += size
        files.append((source, relative))
        if (len(files) > MAX_DATASET_FILES or size > MAX_DATASET_BYTES
                or total > MAX_DATASET_TOTAL_BYTES):
            raise ValueError('Dataset archive backup limit exceeded; no snapshots may be omitted')
        if _sha256(source) != match[1]:
            raise ValueError('Dataset content does not match its hash filename')
    return sorted(files, key=lambda item: item[1])


def _verify_dataset_references(snapshot, files):
    relative = 'runtime/research-ledger.sqlite'
    if relative not in files:
        return
    try:
        with closing(sqlite3.connect((snapshot / relative).as_uri() + '?mode=ro&immutable=1', uri=True)) as con:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_events'").fetchone():
                return
            for row in con.execute("SELECT payload FROM research_events WHERE kind='dataset_snapshot'"):
                if len(row[0]) > 8192:
                    raise ValueError('Oversized dataset reference in research ledger')
                receipt = json.loads(row[0])
                if not isinstance(receipt, dict):
                    raise ValueError('Invalid dataset reference in research ledger')
                name, digest = receipt.get('artifact'), receipt.get('sha256')
                path = 'runtime/research-data/' + name if isinstance(name, str) else ''
                match = DATASET_PATTERN.fullmatch(path)
                if match is None or match[1] != digest or path not in files:
                    raise ValueError('Research ledger references a missing or invalid dataset snapshot')
    except (sqlite3.Error, TypeError) as exc:
        raise ValueError('Invalid dataset references in research ledger') from exc


def sqlite_snapshot(source, destination):
    source, destination = _reject_link_path(source), _reject_link_path(destination)
    if not source.is_file() or destination.exists():
        raise ValueError('SQLite backup requires an existing source and a new destination')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)) as origin:
        with closing(sqlite3.connect(destination)) as target:
            origin.backup(target, pages=256, sleep=.05)
            # backup() includes committed source WAL pages; emit one portable file.
            target.execute('PRAGMA journal_mode=DELETE')
    _check_database(destination)


def snapshot_state(root, destination, *, data_db=None, include_gemini=False):
    root, destination = _reject_link_path(root), _reject_link_path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    sources = [(Path(data_db) if data_db else root / 'backend/data.db', 'data/data.db')]
    sources.extend((root / '.runtime' / name, 'runtime/' + name) for name in DATABASES)
    files = []
    for source, relative in sources:
        if source.is_file():
            target = destination / relative
            sqlite_snapshot(source, target)
            files.append(relative)
    for name in ('research-latest-result.json', 'gemini.env'):
        if name == 'gemini.env' and not include_gemini:
            continue
        source = root / '.runtime' / name
        if source.is_file():
            _reject_link_path(source)
            target = destination / 'runtime' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            files.append('runtime/' + name)
    for source, relative in _dataset_sources(root):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        files.append(relative)
    for name in ('runtime', 'data', 'backups'):
        (destination / name).mkdir(exist_ok=True)
    manifest = dict(createdAt=datetime.now(timezone.utc).isoformat(),
                    consistency='SQLite consistent per book; not an atomic cross-book snapshot',
                    includesGeminiKey=include_gemini and (destination/'runtime/gemini.env').exists(),
                    files=[dict(path=name, sha256=_sha256(destination/name))
                           for name in files])
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    verify_snapshot(destination)
    return manifest


def verify_snapshot(snapshot):
    """Check a bounded manifest and every database without executing content."""
    snapshot = _reject_link_path(snapshot)
    manifest_path = snapshot / 'manifest.json'
    if _is_link(manifest_path) or not manifest_path.is_file() or manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError('Missing, linked or oversized backup manifest')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or type(manifest.get('includesGeminiKey')) is not bool:
        raise ValueError('Invalid backup manifest')
    entries = manifest.get('files')
    allowed = {'data/data.db', 'runtime/research-latest-result.json'} | {'runtime/' + name for name in DATABASES}
    if manifest['includesGeminiKey']:
        allowed.add('runtime/gemini.env')
    if not isinstance(entries, list) or not 1 <= len(entries) <= len(allowed) + MAX_DATASET_FILES:
        raise ValueError('Empty or oversized backup manifest')
    seen = set()
    dataset_count, dataset_bytes = 0, 0
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError('Invalid backup entry')
        relative, checksum = item.get('path'), item.get('sha256')
        match = DATASET_PATTERN.fullmatch(relative) if isinstance(relative, str) else None
        if not isinstance(relative, str) or (relative not in allowed and match is None) or relative in seen:
            raise ValueError('Unexpected or duplicate backup path')
        if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum):
            raise ValueError('Invalid backup checksum')
        seen.add(relative)
        source = snapshot / relative
        if _is_link(source) or _is_link(source.parent) or not source.is_file() or snapshot not in source.resolve().parents:
            raise ValueError('Backup path escaped its snapshot')
        if match:
            size = source.stat().st_size
            dataset_count += 1
            dataset_bytes += size
            if (dataset_count > MAX_DATASET_FILES or size > MAX_DATASET_BYTES
                    or dataset_bytes > MAX_DATASET_TOTAL_BYTES):
                raise ValueError('Dataset archive backup limit exceeded')
            if match[1] != checksum:
                raise ValueError('Dataset hash filename does not match its checksum')
        if _sha256(source) != checksum:
            raise ValueError('Backup checksum mismatch: ' + relative)
        if relative.endswith(('.db','.sqlite')):
            _check_database(source)
    if manifest['includesGeminiKey'] != ('runtime/gemini.env' in seen):
        raise ValueError('Backup credential flag does not match its files')
    _verify_dataset_references(snapshot, seen)
    # Reject unlisted sidecars, files and linked directories, even with a valid DB hash.
    directories = {'runtime', 'data', 'backups', 'runtime/research-data'}
    for directory in (snapshot, *(snapshot / name for name in sorted(directories))):
        if directory != snapshot and not directory.exists():
            continue
        if _is_link(directory) or not directory.is_dir():
            raise ValueError('Invalid backup directory')
        for child in directory.iterdir():
            relative = child.relative_to(snapshot).as_posix()
            if _is_link(child) or relative not in seen | {'manifest.json'} | directories:
                raise ValueError('Unlisted or linked backup path: ' + relative)
    return manifest


def restore_snapshot(snapshot, destination):
    """Restore only to a NEW staging directory. Never overwrite a running book."""
    snapshot, destination = _reject_link_path(snapshot), _reject_link_path(destination)
    manifest = verify_snapshot(snapshot)
    if destination.exists():
        raise ValueError('Restore requires a new destination')
    if snapshot == destination or snapshot in destination.parents:
        raise ValueError('Restore destination must be outside the snapshot')
    destination.mkdir(parents=True, exist_ok=False)
    for item in manifest['files']:
        target = destination / item['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(snapshot / item['path'], target)
    for name in ('runtime', 'data', 'backups'):
        (destination / name).mkdir(exist_ok=True)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    verify_snapshot(destination)
    return dict(status='restored-to-staging', files=len(manifest['files']),
                activated=False, reconciliationRequired=True)


def prepare_bundle(root, destination, include_gemini=False):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    package = destination / 'mesa-siete'
    package.mkdir(parents=True, exist_ok=False)
    paths = [root / relative for relative in SOURCE_FILES]
    for directory in ('backend/app', 'frontend/src', 'frontend/public', 'deploy'):
        paths.extend(path for path in (root/directory).rglob('*')
                     if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc')
    for source in paths:
        if not source.is_file() or source.is_symlink():
            if source in [root / relative for relative in SOURCE_FILES]:
                raise RuntimeError(f'Falta un archivo de despliegue: {source.name}')
            continue
        target = package / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    manifest = snapshot_state(root, package/'state', include_gemini=include_gemini)
    archive = destination / 'mesa-siete.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(package, arcname='mesa-siete')
    return archive, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('bundle', 'backup', 'verify', 'restore'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--data-db', type=Path)
    parser.add_argument('--include-gemini', action='store_true')
    parser.add_argument('--snapshot',type=Path)
    args = parser.parse_args()
    if args.action != 'verify' and args.output is None:
        parser.error('--output is required')
    if args.action in ('verify','restore'):
        if args.snapshot is None: parser.error('--snapshot is required')
        result=verify_snapshot(args.snapshot) if args.action=='verify' else restore_snapshot(args.snapshot,args.output)
        print(json.dumps(dict(status='verified',files=len(result['files'])) if args.action=='verify' else result))
    elif args.action == 'bundle':
        archive, manifest = prepare_bundle(args.root, args.output, args.include_gemini)
        print(json.dumps(dict(archive=str(archive), databases=len(manifest['files']),
                              includesGeminiKey=manifest['includesGeminiKey'])))
    else:
        stamp = datetime.now(timezone.utc).strftime('snapshot-%Y%m%dT%H%M%S%fZ')
        manifest = snapshot_state(args.root, args.output/stamp, data_db=args.data_db,
                                  include_gemini=args.include_gemini)
        # Retención acotada a directorios generados por esta utilidad, bajo /backups.
        parent = args.output.resolve()
        copies = sorted(path for path in parent.glob('snapshot-*')
                        if path.is_dir() and not path.is_symlink() and (path/'manifest.json').is_file())
        for old in copies[:-14]:
            if old.resolve().parent != parent:
                raise RuntimeError('Ruta de retención fuera del directorio de copias')
            shutil.rmtree(old)
        print(json.dumps(dict(snapshot=stamp, files=len(manifest['files']))))


if __name__ == '__main__':
    main()
