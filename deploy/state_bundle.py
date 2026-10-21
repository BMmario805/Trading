"""Exportación acotada y copias SQLite consistentes, sin copiar logs ni claves MT5."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import tarfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


DATABASES = ('desk.sqlite', 'desk-forex.sqlite', 'desk-stocks.sqlite', 'gemini.sqlite', 'mt5-demo.sqlite')
SOURCE_FILES = ('.dockerignore', 'Dockerfile', 'compose.yaml', 'backend/requirements.txt',
                'backend/requirements-cloud.txt', 'frontend/package.json', 'frontend/package-lock.json',
                'frontend/index.html', 'frontend/tsconfig.json', 'frontend/tsconfig.app.json',
                'frontend/tsconfig.node.json', 'frontend/vite.config.ts')


def sqlite_snapshot(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)) as origin:
        with closing(sqlite3.connect(destination)) as target:
            origin.backup(target, pages=256, sleep=.05)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError(f'Copia SQLite no válida: {destination.name}')


def snapshot_state(root, destination, *, data_db=None, include_gemini=False):
    root, destination = Path(root).resolve(), Path(destination).resolve()
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
            target = destination / 'runtime' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            files.append('runtime/' + name)
    for name in ('runtime', 'data', 'backups'):
        (destination / name).mkdir(exist_ok=True)
    manifest = dict(createdAt=datetime.now(timezone.utc).isoformat(),
                    includesGeminiKey=include_gemini and (destination/'runtime/gemini.env').exists(),
                    files=[dict(path=name, sha256=hashlib.sha256((destination/name).read_bytes()).hexdigest())
                           for name in files])
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


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
    parser.add_argument('action', choices=('bundle', 'backup'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data-db', type=Path)
    parser.add_argument('--include-gemini', action='store_true')
    args = parser.parse_args()
    if args.action == 'bundle':
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
