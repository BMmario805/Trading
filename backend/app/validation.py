"""One promotion contract for research, paper policies and execution adapters.

Passing historical filters is useful research, never new independent evidence.
This module is pure: callers must persist the evidence and its provenance.
"""

import math

PROMOTION_VERSION = 'promotion-contract-v1'


def cooldown_seconds(base, pnl, loss_streak):
    """The same clock policy for live paper management and historical events."""
    return base * min(8, 2 ** loss_streak) if pnl < 0 else base / 4


def fixed_cohort_costs(executions, model):
    """Reprice identical observed paths/quantities; do not refilter or resize."""
    from .economics import execution_fill,net_outcome
    pnls=[]
    currencies=set()
    for event in executions:
        side=event['direction'];qty=event['qty']
        if (any(type(event.get(key)) not in (int,float) or not math.isfinite(event[key])
                for key in ('qty','entryMid','exitMid','entryTime','exitTime'))
                or event['exitTime']<event['entryTime']):
            raise ValueError('A fixed cohort requires finite observed paths and ordered timestamps')
        conversion=event.get('quoteToAccount',1.0)
        if type(conversion) not in (int,float) or not math.isfinite(conversion) or conversion<=0:
            raise ValueError('A fixed cohort requires a positive finite currency conversion')
        currencies.add(event.get('accountCurrency') or event.get('quoteCurrency') or 'quote')
        if len(currencies)>1:
            raise ValueError('A fixed cohort cannot aggregate different currencies')
        entry=execution_fill(event['entryMid'],side,True,model.get('spreadPct',0),model.get('slippageRate',0))
        exit_price=execution_fill(event['exitMid'],side,False,model.get('spreadPct',0),model.get('slippageRate',0))
        carry=entry*qty*model.get('carryDailyRate',0)*max(0,event['exitTime']-event['entryTime'])/86400
        pnls.append(net_outcome(entry,exit_price,qty,side,entry*qty*model.get('feeRate',0),model.get('feeRate',0),
                                quote_to_account=conversion,carry=carry))
    return dict(trades=len(pnls),pnl=round(sum(pnls),6),fixedCohort=True,
                currency=next(iter(currencies)) if currencies else None,
                scope='Same entry/exit references, times and quantities; cost repricing only')


def promotion_decision(*, checks_passed, independent_future=False,
                       execution_equivalent=False, costs_verified=False,
                       holdout_reused=False, experiment_id=None,
                       evidence_id=None):
    checks_passed=checks_passed is True
    independent_future=independent_future is True
    execution_equivalent=execution_equivalent is True
    costs_verified=costs_verified is True
    holdout_reused=holdout_reused is not False
    reasons = []
    if not checks_passed:
        reasons.append('Predeclared economic and sample checks did not pass')
    if holdout_reused:
        reasons.append('Holdout overlaps previously consulted observations')
    if not independent_future:
        reasons.append('Independent observations after candidate freeze are required')
    if not execution_equivalent:
        reasons.append('Replay and deployed policy are not verified equivalent')
    if not costs_verified:
        reasons.append('Execution costs are scenarios, not verified account costs')
    if not isinstance(experiment_id,str) or not experiment_id or not isinstance(evidence_id,str) or not evidence_id:
        reasons.append('Registered experiment and persisted evidence identities are required')
    promotable = bool(checks_passed and independent_future and execution_equivalent
                      and costs_verified and not holdout_reused and isinstance(experiment_id,str)
                      and experiment_id and isinstance(evidence_id,str) and evidence_id)
    if promotable:
        status = 'forward_eligible'
    elif checks_passed:
        status = 'historical_pass' if not independent_future else 'research_only'
    else:
        status = 'rejected'
    return dict(version=PROMOTION_VERSION, status=status, promotable=promotable,
                reasons=reasons, independentFuture=bool(independent_future),
                executionEquivalent=bool(execution_equivalent), costsVerified=bool(costs_verified),
                holdoutReused=bool(holdout_reused), experimentId=experiment_id, evidenceId=evidence_id)


def promotion_allowed(value):
    """Legacy booleans and old reports never acquire a new evidence contract."""
    return (isinstance(value, dict) and value.get('version') == PROMOTION_VERSION
            and value.get('status') == 'forward_eligible' and value.get('promotable') is True
            and value.get('independentFuture') is True and value.get('executionEquivalent') is True
            and value.get('costsVerified') is True and value.get('holdoutReused') is False
            and bool(value.get('experimentId')) and bool(value.get('evidenceId')))
