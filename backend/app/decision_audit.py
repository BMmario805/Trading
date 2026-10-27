"""Prospective candidate/decision ledger for paired AI ablations.

Recording is observational. No orders, balances, budgets, models or strategies
are changed here. Decision agreement is not incremental profit. Outcomes must
come from observed fills and are never fabricated for a rejected candidate.
"""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = None  # Default follows the swarm book, including isolated test books.
VERSION = 'paired-decision-audit-v1'
MAX_CANDIDATES_PER_DAY = 10_000
ARMS = ('rules_local', 'rules_plus_pulse', 'rules_plus_commander', 'rules_plus_ai')
ECONOMIC_FIELDS = ('sampleSize','expectancyNet','profitFactor','winRate','breakEvenWinRate',
                   'requestedRiskFraction','strategyFamily','strategy','symbol','payoffRatio')
PLAN_FIELDS = ('version','viable','netTarget','netRisk','netRewardRisk','fees','carry',
               'entryFee','exitFee','slippage','breakEvenWinRate')
COST_FIELDS = ('version','feeRate','slippageRate','spreadPct','carryDailyRate','carryMinHours')


def _number(value):
    return float(value) if type(value) in (float, int) and math.isfinite(value) else None


def _json(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _path():
    if DB_PATH is not None:
        return Path(DB_PATH)
    from .ai_swarm import DB_PATH as swarm_path
    return Path(swarm_path).with_name('decision-audit.sqlite')


def storage_for(book_path):
    """Derive a ledger beside its owning book, including temporary test books."""
    return Path(book_path).with_name('decision-audit.sqlite')


@contextmanager
def _connect(write=False, db_path=None):
    path=Path(db_path) if db_path is not None else _path()
    if write:
        path.parent.mkdir(parents=True,exist_ok=True)
        # Observability must not stall the engine if another writer holds it.
        con=sqlite3.connect(path,timeout=.05)
        con.executescript('''
            CREATE TABLE IF NOT EXISTS audit_candidates (
                candidate_id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL,
                observed_at REAL NOT NULL, cohort TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit_decisions (
                candidate_id TEXT NOT NULL, arm TEXT NOT NULL, observed_at REAL NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(candidate_id,arm));
            CREATE TABLE IF NOT EXISTS audit_outcomes (
                candidate_id TEXT NOT NULL, arm TEXT NOT NULL, observed_at REAL NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(candidate_id,arm));
            CREATE INDEX IF NOT EXISTS idx_audit_candidate_date ON audit_candidates(observed_at);
        ''')
    else:
        con=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
        con.execute('PRAGMA query_only=ON')
    try:
        yield con
        if write: con.commit()
    finally:
        con.close()


def record_candidate(source, symbol, direction, *, strategy_version, cost_version,
                     risk_policy, economics, risk_eur=None, notional_eur=None,
                     snapshot_at=None, correlation_id=None, candidate_key=None, db_path=None,
                     cost_model=None):
    """Return an immutable candidate ID; optional stable key deduplicates retries."""
    now=time.time()
    snapshot_at=now if snapshot_at is None else _number(snapshot_at)
    if (direction not in ('long','short') or snapshot_at is None or abs(now-snapshot_at)>86400
            or not all(isinstance(v,str) and 0<len(v)<=128 for v in
                       (source,symbol,strategy_version,cost_version,risk_policy))):
        raise ValueError('Invalid candidate contract')
    economics=economics if isinstance(economics,dict) else {}
    safe={k:economics[k] for k in ECONOMIC_FIELDS if k in economics
          and (economics[k] is None or type(economics[k]) in (str,int,float,bool))}
    plan=economics.get('planned') if isinstance(economics.get('planned'),dict) else {}
    cost_model=cost_model if isinstance(cost_model,dict) else economics.get('costModel') or {}
    cost_model={k:v for k,v in cost_model.items() if k in COST_FIELDS
                and (v is None or type(v) in (str,int,float,bool))} if isinstance(cost_model,dict) else {}
    safe['planned']={k:v for k,v in plan.items() if k in PLAN_FIELDS
                     and (v is None or type(v) in (str,int,float,bool))}
    cohort=dict(source=source,strategyVersion=strategy_version,costVersion=cost_version,
                riskPolicy=risk_policy,costModel=cost_model)
    payload=dict(version=VERSION,source=source,symbol=symbol,direction=direction,
                 observedAt=now,snapshotAt=snapshot_at,correlationId=correlation_id,
                 strategyVersion=strategy_version,costVersion=cost_version,riskPolicy=risk_policy,
                 costModel=cost_model,
                 economics=safe,riskEUR=_number(risk_eur),notionalEUR=_number(notional_eur),
                 comparableRiskCosts=bool(_number(risk_eur) is not None and risk_eur>0
                                          and _number(notional_eur) is not None and notional_eur>0
                                          and _number(plan.get('netRisk')) is not None
                                          and _number(cost_model.get('feeRate')) is not None
                                          and _number(cost_model.get('slippageRate')) is not None),
                 executionSubmitted=False)
    # A caller key names one opportunity, not a rolling market or symbol.
    digest=_hash(dict(cohort=cohort,key=candidate_key,symbol=symbol,direction=direction,
                      snapshotAt=None if candidate_key else snapshot_at,economics=safe,
                      riskEUR=payload['riskEUR'],notionalEUR=payload['notionalEUR']))
    candidate_id='candidate-'+uuid.uuid4().hex
    with _connect(write=True, db_path=db_path) as con:
        con.execute('BEGIN IMMEDIATE')
        old=con.execute('SELECT candidate_id FROM audit_candidates WHERE digest=?',(digest,)).fetchone()
        if old:return old[0]
        day=int(now//86400)*86400
        if con.execute('SELECT COUNT(*) FROM audit_candidates WHERE observed_at>=?',(day,)).fetchone()[0]>=MAX_CANDIDATES_PER_DAY:
            return None
        con.execute('INSERT INTO audit_candidates VALUES (?,?,?,?,?)',
                    (candidate_id,digest,now,_hash(cohort),_json(payload)))
    return candidate_id


def record_decision(candidate_id, arm, allowed, *, size_multiplier=0.0, blockers=None,
                    execution_mode='shadow', prompt_version=None, model_versions=None,
                    decision_at=None, db_path=None):
    """One arm receipt per opportunity. Repeated conflicting results are refused."""
    size=_number(size_multiplier)
    if arm not in ARMS or type(allowed) is not bool or size is None or not 0<=size<=1:
        raise ValueError('Invalid arm decision')
    if execution_mode not in ('shadow','paper','demo','counterfactual'):
        raise ValueError('Invalid observation mode')
    payload=dict(candidateId=candidate_id,arm=arm,allowed=allowed,sizeMultiplier=size,
                 blockers=[str(v)[:160] for v in (blockers or [])[:12]],
                 observedAt=time.time() if decision_at is None else decision_at,
                 executionMode=execution_mode,promptVersion=prompt_version,
                 modelVersions=model_versions or {},profitabilityProven=False,
                 outcomeAvailable=False,version=VERSION)
    with _connect(write=True, db_path=db_path) as con:
        if not con.execute('SELECT 1 FROM audit_candidates WHERE candidate_id=?',(candidate_id,)).fetchone():
            raise ValueError('Unknown candidate')
        old=con.execute('SELECT payload FROM audit_decisions WHERE candidate_id=? AND arm=?',(candidate_id,arm)).fetchone()
        if old:
            prior=json.loads(old[0]);new=dict(payload)
            prior.pop('observedAt',None);new.pop('observedAt',None)
            if prior!=new:raise ValueError('Conflicting decision for immutable candidate arm')
            return
        con.execute('INSERT INTO audit_decisions VALUES (?,?,?,?)',
                    (candidate_id,arm,payload['observedAt'],_json(payload)))


def record_outcome(candidate_id, arm, *, net_pnl, currency, initial_risk, fees,
                   slippage, carry, outcome_type, observed_at=None, db_path=None):
    """Explicit observed evidence only; no synthetic PnL for an unfilled arm."""
    values=[_number(v) for v in (net_pnl,initial_risk,fees,slippage,carry)]
    if (any(v is None for v in values) or initial_risk<=0
            or outcome_type not in ('observed-paper','broker-demo','counterfactual-observed')
            or currency not in ('EUR','USD','USDT')):
        raise ValueError('Incomplete observed outcome')
    payload=dict(candidateId=candidate_id,arm=arm,netPnl=net_pnl,currency=currency,
                 initialRisk=initial_risk,fees=fees,slippage=slippage,carry=carry,
                 outcomeType=outcome_type,observedAt=observed_at or time.time(),version=VERSION)
    with _connect(write=True, db_path=db_path) as con:
        if not con.execute('SELECT 1 FROM audit_decisions WHERE candidate_id=? AND arm=?',(candidate_id,arm)).fetchone():
            raise ValueError('Outcome has no decision receipt')
        old=con.execute('SELECT payload FROM audit_outcomes WHERE candidate_id=? AND arm=?',(candidate_id,arm)).fetchone()
        if old:
            prior=json.loads(old[0]); current=dict(payload)
            prior.pop('observedAt',None); current.pop('observedAt',None)
            if prior==current:return
            raise ValueError('Outcome is immutable')
        con.execute('INSERT INTO audit_outcomes VALUES (?,?,?,?)',
                    (candidate_id,arm,payload['observedAt'],_json(payload)))


def health_snapshot(db_path=None):
    result=dict(version=VERSION,readOnly=True,available=False,candidates=0,decisions=0,
                outcomes=0,pairedDecisionCandidates=0,incrementalAdvantageMeasured=False,
                limitations=['Decision agreement is not net profit',
                             'Paired observed fills with comparable risk and costs are required',
                             'No synthetic counterfactual outcomes are generated'])
    try:
        with _connect(db_path=db_path) as con:
            for key,table in [('candidates','audit_candidates'),('decisions','audit_decisions'),('outcomes','audit_outcomes')]:
                result[key]=con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
            result['pairedDecisionCandidates']=con.execute('SELECT COUNT(*) FROM (SELECT candidate_id FROM audit_decisions GROUP BY candidate_id HAVING COUNT(*)>=2)').fetchone()[0]
            result['decisionsByArm']=dict(con.execute('SELECT arm,COUNT(*) FROM audit_decisions GROUP BY arm'))
        result['available']=True
    except (OSError,sqlite3.Error):
        pass
    return result
