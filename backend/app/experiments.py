"""Append-only research registry. No account, network or order access.

The registry records every holdout claim *before* it is read. Overlap is checked
by source/symbol/interval and time, across code versions and experiment names.
Hash chaining and database triggers detect/prevent application-level rewriting;
they are not a claim of tamper resistance against a filesystem administrator.
"""

import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .validation import promotion_decision

REGISTRY_VERSION = 'research-ledger-v1'
DB_PATH = Path(__file__).resolve().parents[2] / '.runtime' / 'research-ledger.sqlite'
CODE_SCOPE = 'all backend/app Python sources and backend/requirements.lock when present; runtime data excluded'
_decision_experiments = {}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def registry_path(owner_db):
    """Keep explicitly isolated owner databases and their telemetry together."""
    configured=Path(os.getenv('TRADING_DATA_DB',str(Path(__file__).resolve().parent.parent/'data.db')))
    return DB_PATH if Path(owner_db).resolve() == Path(configured).resolve() else Path(owner_db).with_name('research-ledger.sqlite')


def log_decision(owner, symbol, bar_time, base_score, overlays, decision,
                 cost_model, *, observed_at=None, db_path=None):
    """Record point-in-time attribution; overlays are shadow unless verified replayed.

    Pass only structured public inputs/decisions, never credentials or raw prompts.
    A candidate is stored once per closed bar. Separate execution events may be
    stored with decision.stage='execution' to distinguish candidates from fills.
    """
    if (not isinstance(owner, str) or not owner or not isinstance(symbol, str) or not symbol
            or type(bar_time) not in (int, float) or not math.isfinite(bar_time)
            or bar_time > time.time() + 5 or not isinstance(decision, dict)):
        raise ValueError('A decision requires an owner, symbol and observed finite bar timestamp')
    canonical(dict(baseScore=base_score, overlays=overlays, decision=decision))
    path = str(Path(db_path or DB_PATH).resolve())
    key = (path, owner)
    eid = _decision_experiments.get(key)
    if eid is not None:
        try:
            get_experiment(eid, db_path)
        except ValueError:
            eid = None
    if eid is None:
        eid = register_experiment('point-in-time:'+owner,
            dict(parameters=dict(owner=owner, attribution='base-vs-shadow-v1'),
                 costModel=cost_model,trialCount=0,executionEquivalent=False),db_path=db_path)['id']
        _decision_experiments[key] = eid
    con = _connect(db_path)
    try:
        with con:
            con.execute('BEGIN IMMEDIATE')
            stage = decision.get('stage','candidate')
            exists = con.execute('''SELECT 1 FROM research_events WHERE kind='decision'
                AND json_extract(payload,'$.owner')=? AND json_extract(payload,'$.symbol')=? AND json_extract(payload,'$.barTime')=?
                AND json_extract(payload,'$.decision.stage')=? LIMIT 1''', (owner,symbol,bar_time,stage)).fetchone()
            if exists:
                return dict(experimentId=eid, duplicate=True)
            now = time.time()
            payload = dict(owner=owner,symbol=symbol,barTime=bar_time,baseScore=base_score,
                           overlays=overlays,decision=dict(decision,stage=stage),
                           attribution='observational-shadow; no counterfactual profit inferred')
            return _append(con,eid,'decision',payload,now if observed_at is None else observed_at,now)
    finally:
        con.close()


def code_fingerprint():
    root = Path(__file__).resolve().parent
    files={str(path.relative_to(root)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest()
           for path in sorted(root.rglob('*.py')) if '.runtime' not in path.relative_to(root).parts}
    lock=root.parent/'requirements.lock'
    if lock.is_file():
        files['../requirements.lock']=hashlib.sha256(lock.read_bytes()).hexdigest()
    return fingerprint(files)


def dataset_manifest(candles, symbol, interval, source, seconds):
    rows = [[row[k] for k in ('time', 'open', 'high', 'low', 'close', 'volume')] for row in candles]
    return dict(symbol=symbol, interval=interval, source=source, bars=len(rows),
                start=rows[0][0] if rows else None,
                end=rows[-1][0] + seconds if rows else None, dataHash=fingerprint(rows))


def _connect(db_path=None, *, read_only=False):
    path = Path(db_path or DB_PATH)
    if read_only:
        if not path.is_file():
            return None
        con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'research_experiments', 'research_events', 'research_holdouts'} <= tables:
            con.close()
            return None
        return con
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=10000')
    con.execute('''CREATE TABLE IF NOT EXISTS research_experiments
        (id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, frozen_at REAL NOT NULL,
         manifest TEXT NOT NULL, manifest_hash TEXT NOT NULL)''')
    con.execute('''CREATE TABLE IF NOT EXISTS research_events
        (seq INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL,
         kind TEXT NOT NULL, observed_at REAL NOT NULL, available_at REAL NOT NULL,
         payload TEXT NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL UNIQUE)''')
    con.execute('''CREATE TABLE IF NOT EXISTS research_holdouts
        (id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, stream TEXT NOT NULL,
         start REAL NOT NULL, end REAL NOT NULL, data_hash TEXT NOT NULL, reused INTEGER NOT NULL)''')
    for table in ('research_experiments', 'research_events', 'research_holdouts'):
        for action in ('UPDATE', 'DELETE'):
            con.execute(f'''CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action}
                BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Research ledger is append-only'); END''')
    return con


def _append(con, experiment_id, kind, payload, observed_at, available_at):
    observed_at,available_at=float(observed_at),float(available_at)
    if (not all(math.isfinite(value) for value in (observed_at, available_at))
            or observed_at > available_at or available_at > time.time() + 5):
        raise ValueError('Evidence requires finite observation and actual availability timestamps')
    if not con.execute('SELECT 1 FROM research_experiments WHERE id=?', (experiment_id,)).fetchone():
        raise ValueError('Unknown preregistered experiment')
    previous = con.execute('SELECT event_hash FROM research_events ORDER BY seq DESC LIMIT 1').fetchone()
    previous = previous[0] if previous else '0' * 64
    event = dict(experimentId=experiment_id, kind=kind, observedAt=observed_at,
                 availableAt=available_at, payload=payload, previousHash=previous)
    digest = fingerprint(event)
    con.execute('INSERT INTO research_events(experiment_id,kind,observed_at,available_at,payload,previous_hash,event_hash) VALUES (?,?,?,?,?,?,?)',
                (experiment_id, kind, observed_at, available_at, canonical(payload), previous, digest))
    return dict(event, eventHash=digest)


def register_experiment(plan_id, manifest, frozen_at=None, db_path=None):
    now = time.time()
    stamp = float(now if frozen_at is None else frozen_at)
    if not math.isfinite(stamp) or abs(stamp - now) > 5:
        raise ValueError('Experiment freeze time cannot be backdated or future-dated')
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise ValueError('A plan identity is required')
    if (not isinstance(manifest, dict) or not isinstance(manifest.get('parameters'), dict)
            or not manifest['parameters'] or not isinstance(manifest.get('costModel'), dict)):
        raise ValueError('A fixed parameter set and versioned cost model are required')
    if not manifest['costModel'].get('version'):
        raise ValueError('A cost model version is required')
    trials = manifest.get('trialCount', 1)
    if type(trials) is not int or trials < 0:
        raise ValueError('Trial count must be a nonnegative integer')
    current_code = code_fingerprint()
    if manifest.get('codeHash', current_code) != current_code:
        raise ValueError('A preregistration must identify the current code')
    payload = dict(manifest, registryVersion=REGISTRY_VERSION,
                   codeHash=current_code, codeScope=CODE_SCOPE, trialCount=trials)
    eid = uuid.uuid4().hex
    con = _connect(db_path)
    try:
        with con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT INTO research_experiments VALUES (?,?,?,?,?)',
                        (eid, plan_id, stamp, canonical(payload), fingerprint(payload)))
            _append(con, eid, 'preregistered', dict(planId=plan_id, manifestHash=fingerprint(payload)), stamp, stamp)
    finally:
        con.close()
    return dict(id=eid, planId=plan_id, frozenAt=stamp, manifest=payload, manifestHash=fingerprint(payload))


def record_observation(experiment_id, kind, payload, observed_at=None, available_at=None, db_path=None):
    now = time.time()
    observed = now if observed_at is None else float(observed_at)
    source_available = now if available_at is None else float(available_at)
    available = max(now, source_available)
    if (not all(math.isfinite(x) for x in (observed, source_available))
            or observed > now + 5 or available > now + 5):
        raise ValueError('Point-in-time observations cannot be registered as available in the future')
    payload = dict(payload, sourceAvailableAt=source_available)
    con = _connect(db_path)
    try:
        with con:
            con.execute('BEGIN IMMEDIATE')
            return _append(con, experiment_id, kind, payload, observed, available)
    finally:
        con.close()


def claim_holdout(experiment_id, dataset, db_path=None):
    required = ('source', 'symbol', 'interval', 'start', 'end', 'dataHash')
    if not isinstance(dataset, dict) or any(dataset.get(k) is None for k in required):
        raise ValueError('Complete, bounded holdout identity is required')
    if (not all(type(dataset[k]) in (int, float) and math.isfinite(dataset[k]) for k in ('start', 'end'))
            or dataset['end'] <= dataset['start'] or dataset['end'] > time.time() + 5):
        raise ValueError('Holdout must contain observed, finite timestamps')
    if (not dataset['source'] or any(not isinstance(dataset[k], str) or not dataset[k].strip()
                                     for k in ('symbol', 'interval', 'dataHash'))):
        raise ValueError('A source, symbol, interval and data hash are required')
    stream = fingerprint({k: dataset[k] for k in ('source', 'symbol', 'interval')})
    con = _connect(db_path)
    try:
        with con:
            con.execute('BEGIN IMMEDIATE')
            exp = con.execute('SELECT frozen_at FROM research_experiments WHERE id=?', (experiment_id,)).fetchone()
            if exp is None:
                raise ValueError('Unknown preregistered experiment')
            reused = bool(con.execute('SELECT 1 FROM research_holdouts WHERE stream=? AND start<? AND end>? LIMIT 1',
                                      (stream, dataset['end'], dataset['start'])).fetchone())
            independent = dataset['start'] >= exp['frozen_at'] and not reused
            claim_id = uuid.uuid4().hex
            claim = dict(id=claim_id, experimentId=experiment_id, allowed=not reused,
                         reused=reused, independentFuture=independent, dataset=dataset,
                         classification='previously_seen' if reused else 'future' if independent else 'historical')
            con.execute('INSERT INTO research_holdouts VALUES (?,?,?,?,?,?,?)',
                        (claim_id, experiment_id, stream, dataset['start'], dataset['end'], dataset['dataHash'], int(reused)))
            _append(con, experiment_id, 'holdout_claim', claim, time.time(), time.time())
            return claim
    finally:
        con.close()


def finalize_evidence(experiment_id, metrics, checks, claim, equivalence=False, costs_verified=False, db_path=None):
    if (not isinstance(metrics, dict) or not isinstance(checks, (list, tuple))
            or any(not isinstance(check, dict) for check in checks) or not isinstance(claim, dict)):
        raise ValueError('Structured metrics, checks and a genuine holdout claim are required')
    con = _connect(db_path)
    try:
        with con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('''SELECT h.*,e.frozen_at,e.manifest FROM research_holdouts h JOIN research_experiments e
                ON e.id=h.experiment_id WHERE h.id=? AND h.experiment_id=?''',(claim.get('id'),experiment_id)).fetchone()
            if row is None or row['data_hash']!=claim.get('dataset',{}).get('dataHash'):
                raise ValueError('Evidence requires a genuine registered holdout claim')
            registered = con.execute("""SELECT payload FROM research_events WHERE experiment_id=?
                AND kind='holdout_claim' AND json_extract(payload,'$.id')=?""",
                (experiment_id, claim['id'])).fetchone()
            if registered is None or json.loads(registered['payload']) != claim:
                raise ValueError('A registered holdout claim cannot be altered')
            if con.execute("SELECT 1 FROM research_events WHERE kind='evaluation' AND json_extract(payload,'$.claimId')=? LIMIT 1",(claim['id'],)).fetchone():
                raise ValueError('A holdout claim can be finalized only once')
            manifest=json.loads(row['manifest'])
            claim=dict(claim,reused=bool(row['reused']),independentFuture=row['start']>=row['frozen_at'] and not row['reused'])
            passed=bool(checks) and all(c.get('passed') is True for c in checks)
            now=time.time()
            event=_append(con,experiment_id,'evaluation',dict(metrics=metrics,checks=checks,claimId=claim['id']),now,now)
            decision=promotion_decision(checks_passed=passed,
                independent_future=claim['independentFuture'],
                execution_equivalent=bool(equivalence is True and manifest.get('executionEquivalent') is True
                    and manifest.get('equivalenceVersion') and manifest.get('codeHash') == code_fingerprint()),
                costs_verified=bool(costs_verified is True and manifest.get('costModel',{}).get('verified') is True),
                holdout_reused=claim['reused'],experiment_id=experiment_id,evidence_id=event['eventHash'])
            _append(con,experiment_id,'promotion_decision',decision,now,now)
            return decision
    finally:
        con.close()


def list_experiments(limit=100, db_path=None):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('Experiment list limit must be 1..500')
    con = _connect(db_path, read_only=True)
    if con is None:
        return []
    try:
        rows = con.execute('SELECT * FROM research_experiments ORDER BY frozen_at DESC LIMIT ?', (limit,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item['manifest'] = json.loads(item['manifest'])
            item['events'] = con.execute('SELECT COUNT(*) FROM research_events WHERE experiment_id=?', (row['id'],)).fetchone()[0]
            evidence = con.execute('''SELECT * FROM research_events WHERE experiment_id=?
                AND kind IN ('evaluation','promotion_decision','paired_evaluation','training_result','research_result')
                ORDER BY seq DESC LIMIT 1''', (row['id'],)).fetchone()
            item['latestEvidence'] = (dict(kind=evidence['kind'], observedAt=evidence['observed_at'],
                availableAt=evidence['available_at'], payload=json.loads(evidence['payload']),
                eventHash=evidence['event_hash']) if evidence else None)
            out.append(item)
        return out
    finally:
        con.close()


def get_experiment(experiment_id, db_path=None):
    con = _connect(db_path, read_only=True)
    if con is None:
        raise ValueError('Unknown preregistered experiment')
    try:
        row = con.execute('SELECT * FROM research_experiments WHERE id=?', (experiment_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown preregistered experiment')
        return dict(id=row['id'], planId=row['plan_id'], frozenAt=row['frozen_at'],
                    manifest=json.loads(row['manifest']), manifestHash=row['manifest_hash'])
    finally:
        con.close()


def observations(experiment_id, available_before, limit=1000, db_path=None):
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError('Observation limit must be 1..10000')
    if type(available_before) not in (int, float) or not math.isfinite(available_before):
        raise ValueError('A finite point-in-time cutoff is required')
    con = _connect(db_path, read_only=True)
    if con is None:
        return []
    try:
        return [dict(kind=row['kind'], observedAt=row['observed_at'], availableAt=row['available_at'],
                     payload=json.loads(row['payload']), eventHash=row['event_hash'])
                for row in con.execute('SELECT * FROM research_events WHERE experiment_id=? AND available_at<=? ORDER BY seq LIMIT ?',
                                       (experiment_id, available_before, limit))]
    finally:
        con.close()


def accumulated_trials(plan_id=None, db_path=None):
    con = _connect(db_path, read_only=True)
    if con is None:
        return 0
    try:
        query = 'SELECT manifest FROM research_experiments'
        rows = con.execute(query + (' WHERE plan_id=?' if plan_id else ''), (plan_id,) if plan_id else ()).fetchall()
        return sum(int(json.loads(row[0]).get('trialCount', 1)) for row in rows)
    finally:
        con.close()


def verify_ledger(db_path=None):
    con = _connect(db_path, read_only=True)
    if con is None:
        return None
    try:
        previous = '0' * 64
        for row in con.execute('SELECT * FROM research_events ORDER BY seq'):
            event = dict(experimentId=row['experiment_id'], kind=row['kind'], observedAt=row['observed_at'],
                         availableAt=row['available_at'], payload=json.loads(row['payload']), previousHash=previous)
            if row['previous_hash'] != previous or fingerprint(event) != row['event_hash']:
                return False
            previous = row['event_hash']
        return all(fingerprint(json.loads(row['manifest'])) == row['manifest_hash']
                   for row in con.execute('SELECT manifest,manifest_hash FROM research_experiments'))
    finally:
        con.close()
