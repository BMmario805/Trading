"""Seven bounded preregistered local research plans; never places orders.

One frozen hypothesis per plan, at most five datasets / 10,000 bars each.
Missing point-in-time execution/news evidence returns an explicit pending state.
No provider calls, universe search, account changes or invented observations.
"""

from copy import deepcopy
import math
import time
import uuid

from . import experiments
from .economics import cost_profile
from .execution import closed_candles
from .indicators import adx_series, atr_series
from .research_strategies import ResearchStrategy, signal_series
from .training import compact_metrics, expectancy_uncertainty, simulate, stressed_costs
from .validation import fixed_cohort_costs

PLAN_VERSION = 'preregistered-plans-v1'
PLANS = {
    'A1': dict(name='Tendencia diaria',interval='1d',kind='donchian',lookback=55,
               hypothesis='Ruptura diaria 55 barras, larga, con costes netos y riesgo fijo'),
    'A2': dict(name='Tendencia cuatro horas',interval='4h',kind='donchian',lookback=40,
               hypothesis='La ruptura 4h se evalúa independientemente de la señal diaria'),
    'A3': dict(name='Fuerza relativa',interval='1d',kind='relative-strength',lookback=60,
               hypothesis='El líder de momentum diario positivo y superior al benchmark fijo'),
    'A4': dict(name='Régimen de tendencia',interval='4h',kind='regime',lookback=40,
               hypothesis='ADX causal mayor o igual a20 filtra la ruptura 4h'),
    'A5': dict(name='Maker frente a taker',interval=None,kind='paired-execution',lookback=None,
               hypothesis='Diferencia neta emparejada con fills, colas y comisiones observados'),
    'A6': dict(name='Ablación IA y derivados',interval=None,kind='paired-ai',lookback=None,
               hypothesis='Comparación emparejada base/overlay con inputs disponibles al decidir'),
    'A7': dict(name='Noticias disponibles al decidir',interval=None,kind='paired-news',lookback=None,
               hypothesis='Noticias fechadas antes de decidir mejoran el resultado neto emparejado'),
}
SECONDS = {'1d':86400,'4h':14400}


def capture_paired_predictions(symbol, policy, costs, risk, currency, inputs, *, db_path=None):
    """Freeze contracts and prospective receipts using cached inputs only.

    A5 needs an observed maker fill and a previously frozen quote-based taker
    alternative. No touch-based maker fill or rejected-entry profit is supplied.
    """
    if (type(risk) not in (int,float) or not math.isfinite(risk) or risk<=0
            or costs.get('version')!='net-costs-v1'
            or any(type(costs.get(k)) not in (int,float) or not math.isfinite(costs[k]) or not 0<=costs[k]<1
                   for k in ('feeRate','slippageRate','spreadPct','carryDailyRate'))):
        raise ValueError('Finite net costs and positive comparable risk are required')
    contracts=[]
    news=inputs.get('news',{})
    arms=[('A5','taker','maker'),('A6','rules_local','rules_plus_pulse'),
          ('A6','rules_local','rules_plus_commander'),('A6','rules_local','rules_plus_ai')]
    if news.get('available') is True:
        arms.append(('A7','rules_local','rules_plus_pulse'))
    code=experiments.code_fingerprint()
    con=experiments._connect(db_path)
    try:
        with con:
            con.execute('BEGIN IMMEDIATE')
            for plan_id,base,overlay in arms:
                contract=dict(basePolicyHash=experiments.fingerprint([policy,base]),
                              overlayPolicyHash=experiments.fingerprint([policy,overlay]),
                              baseArm=base,overlayArm=overlay,currency=currency,riskBudget=risk,
                              costVersion=costs['version'])
                plan=next(p for p in list_plans() if p['id']==plan_id)
                manifest=dict(plan=plan,parameters=plan['parameters'],symbols=[symbol],benchmark=None,
                    costModel=dict(costs,mode='spot',carryDailyRate=0.0),trialCount=1,
                    pairedContract=contract,executionEquivalent=False,analysis='fixed hypothesis; no parameter search',
                    registryVersion=experiments.REGISTRY_VERSION,codeHash=code,codeScope=experiments.CODE_SCOPE)
                digest=experiments.fingerprint(manifest)
                old=con.execute('SELECT id FROM research_experiments WHERE manifest_hash=? LIMIT 1',(digest,)).fetchone()
                now=time.time()
                eid=old['id'] if old else uuid.uuid4().hex
                if old is None:
                    con.execute('INSERT INTO research_experiments VALUES (?,?,?,?,?)',
                                (eid,plan_id,now,experiments.canonical(manifest),digest))
                    experiments._append(con,eid,'preregistered',dict(planId=plan_id,manifestHash=digest),now,now)
                payload=dict(contract,symbol=symbol,decisionTime=now,inputs=inputs)
                if plan_id=='A7': payload['publishedAt']=news['publishedAt']
                event=experiments._append(con,eid,'prediction',payload,now,now)
                contracts.append(dict(experimentId=eid,planId=plan_id,prediction=event))
    finally:
        con.close()
    return contracts


def collect_paired_outcomes(receipts, outcomes, *, db_path=None):
    """Join only complete comparable net outcomes to their frozen predictions."""
    for receipt in receipts:
        prediction=receipt['prediction'];frozen=prediction['payload']
        base=outcomes.get(frozen['baseArm']);overlay=outcomes.get(frozen['overlayArm'])
        if not base or not overlay:
            continue
        if any(o['initialRisk']<=0 or o['initialRisk']>frozen['riskBudget']*(1+1e-9) or o['currency']!=frozen['currency']
               for o in (base,overlay)):
            continue
        eid=receipt['experimentId']
        # Outcome retries are idempotent even after a process restart.
        if any(e['payload'].get('predictionEventHash')==prediction['eventHash']
               for e in experiments.observations(eid,time.time(),limit=10000,db_path=db_path)):
            continue
        payload=dict(frozen,predictionEventHash=prediction['eventHash'],
                     inputAvailableAt=prediction['availableAt'],
                     baseNet=base['netPnl'],overlayNet=overlay['netPnl'],
                     baseRisk=frozen['riskBudget'],overlayRisk=frozen['riskBudget'],
                     baseFilledRisk=base['initialRisk'],overlayFilledRisk=overlay['initialRisk'],
                     baseClosedAt=base['observedAt'],overlayClosedAt=overlay['observedAt'],
                     baseCostVersion=frozen['costVersion'],overlayCostVersion=frozen['costVersion'],
                     sharedExecution=base.get('executionId')==overlay.get('executionId'),
                     executionEquivalent=False)
        experiments.record_observation(eid,{'A5':'paired-execution','A6':'paired-ai','A7':'paired-news'}[receipt['planId']],
                                       payload,db_path=db_path)


def list_plans():
    return [dict(id=key,version=PLAN_VERSION,**deepcopy(value),trialCount=1,
                 parameters=dict(slAtr=2,tpAtr=4,trailAtr=2,maxHoldBars=48,
                                 threshold=.6,riskPerTrade=1,capitalReference=100,
                                 minimumTrades=20,maximumDrawdown=30),
                 status='preregisterable',automaticPromotion=False)
            for key,value in PLANS.items()]


def preregister_plan(plan_id, *, symbols, benchmark=None, cost_model=None, paired_contract=None, db_path=None):
    if plan_id not in PLANS:
        raise ValueError('Unknown plan; choose A1..A7')
    if not isinstance(symbols,(list,tuple)) or not 1<=len(symbols)<=5:
        raise ValueError('Freeze one to five unique symbols')
    if any(not isinstance(s,str) or not s or len(s)>32 for s in symbols):
        raise ValueError('Invalid symbol identity')
    if len(set(symbols))!=len(symbols):
        raise ValueError('Freeze one to five unique symbols')
    if plan_id=='A3' and (not benchmark or benchmark not in symbols or len(symbols)<2):
        raise ValueError('A3 requires an explicit benchmark among the frozen symbols')
    plan=next(p for p in list_plans() if p['id']==plan_id)
    costs=dict(cost_model or cost_profile('binance','cripto'))
    if (not isinstance(costs.get('version'),str) or not costs['version']
            or any(type(costs.get(k)) not in (int,float) or not math.isfinite(costs[k]) or not 0<=costs[k]<1
                   for k in ('feeRate','spreadPct','slippageRate','carryDailyRate'))):
        raise ValueError('A versioned finite cost model with explicit rates is required')
    costs.update(mode='spot',carryDailyRate=0.0)
    if paired_contract is not None:
        if plan_id not in ('A5','A6','A7') or not isinstance(paired_contract,dict):
            raise ValueError('Only paired plans accept a frozen paired contract')
        if (any(not isinstance(paired_contract.get(k),str) or not paired_contract[k].strip()
                for k in ('basePolicyHash','overlayPolicyHash','currency'))
                or type(paired_contract.get('riskBudget')) not in (int,float)
                or not math.isfinite(paired_contract['riskBudget']) or paired_contract['riskBudget']<=0):
            raise ValueError('Freeze both policy identities, currency and comparable positive risk')
        paired_contract=dict(paired_contract,costVersion=costs['version'])
    return experiments.register_experiment(plan_id,dict(plan=plan,parameters=plan['parameters'],
        symbols=list(symbols),benchmark=benchmark,costModel=costs,trialCount=1,
        evaluationProtocol=experiments.PURGED_PROTOCOL,
        evaluationFamily=experiments.PLAN_FAMILIES.get(plan_id),
        temporalParameters=dict(folds=3,holdoutFraction=.2,purgeBars=plan['parameters']['maxHoldBars'],embargoBars=1),
        pairedContract=paired_contract,executionEquivalent=False,
        analysis='fixed hypothesis; no parameter search'),db_path=db_path)


def _validated_rows(dataset, interval):
    rows=dataset.get('candles')
    if not isinstance(rows,list) or not 80<=len(rows)<=10000:
        raise ValueError('Each local dataset requires 80..10000 bars')
    if dataset.get('interval')!=interval or not dataset.get('source') or not dataset.get('symbol'):
        raise ValueError('Dataset source, symbol and frozen interval are required')
    for row in rows:
        if not isinstance(row,dict) or any(type(row.get(k)) not in (int,float) or not math.isfinite(row[k])
           for k in ('time','open','high','low','close','volume')) or row['volume']<0:
            raise ValueError('Malformed local OHLCV data')
        if (row['time']<0 or min(row[k] for k in ('open','high','low','close'))<=0
                or not row['low']<=min(row['open'],row['close'])<=max(row['open'],row['close'])<=row['high']):
            raise ValueError('OHLC prices and chronology must describe valid observations')
    clean=closed_candles(rows,SECONDS[interval],time.time())
    if len(clean)!=len(rows) or [r['time'] for r in clean]!=[r['time'] for r in rows]:
        raise ValueError('Use ordered, unique, valid closed bars only')
    return clean


def _paired_plan(exp, db_path):
    plan_id=exp['planId']
    kind={'A5':'paired-execution','A6':'paired-ai','A7':'paired-news'}[plan_id]
    events=experiments.observations(exp['id'],time.time(),limit=10000,db_path=db_path)
    samples=[]
    rejected=0
    seen=set()
    predictions={event['eventHash']:event for event in events if event['kind']=='prediction'}
    order={event['eventHash']:index for index,event in enumerate(events)}
    contract=exp['manifest'].get('pairedContract')
    def finite(value):
        return type(value) in (int,float) and math.isfinite(value)
    for event in events:
        if event['kind']!=kind:
            continue
        item=event['payload']
        decision=item.get('decisionTime')
        known=item.get('inputAvailableAt')
        # Inputs and paired outcomes must be two events: an outcome recorded later
        # cannot retroactively turn its input into a point-in-time prediction.
        prediction=item.get('predictionEventHash')
        inputs=predictions.get(prediction)
        frozen=inputs['payload'] if inputs else {}
        valid=(isinstance(contract,dict) and inputs is not None
               and order[inputs['eventHash']]<order[event['eventHash']]
               and all(finite(v) for v in (decision,known,inputs['observedAt'],inputs['availableAt'],event['observedAt'],event['availableAt']))
               and exp['frozenAt']<=inputs['availableAt']<=decision and known==inputs['availableAt']
               and inputs['observedAt']<=inputs['availableAt']
               and frozen.get('symbol')==item.get('symbol') and item.get('symbol') in exp['manifest']['symbols']
               and finite(frozen.get('decisionTime')) and frozen['decisionTime']==decision
               and item.get('currency')==frozen.get('currency')==contract.get('currency')
               and finite(frozen.get('riskBudget')) and frozen['riskBudget']>0
               and finite(item.get('baseRisk')) and finite(item.get('overlayRisk'))
               and item['baseRisk']==item['overlayRisk']==frozen['riskBudget']==contract.get('riskBudget')
               and item.get('baseCostVersion')==item.get('overlayCostVersion')==frozen.get('costVersion')==exp['manifest']['costModel']['version']
               and all(isinstance(frozen.get(k),str) and bool(frozen[k]) and item.get(k)==frozen[k]
                       and frozen[k]==contract.get(k)
                       for k in ('basePolicyHash','overlayPolicyHash'))
               and all(finite(item.get(k)) for k in ('baseNet','overlayNet','baseClosedAt','overlayClosedAt'))
               and decision<=item['baseClosedAt']<=min(event['observedAt'],event['availableAt'])
               and decision<=item['overlayClosedAt']<=min(event['observedAt'],event['availableAt']))
        if plan_id=='A7':
            published=item.get('publishedAt')
            valid=valid and finite(published) and frozen.get('publishedAt')==published and published<=known
        opportunity=experiments.fingerprint([item.get(k) for k in ('symbol','decisionTime')]) if valid else None
        if opportunity in seen:
            valid=False
        if not valid:
            rejected+=1
            continue
        seen.add(opportunity)
        delta=item['overlayNet']-item['baseNet']
        if not math.isfinite(delta) or not math.isfinite(sum(samples)+delta):
            rejected+=1
            continue
        samples.append(delta)
    uncertainty=expectancy_uncertainty(samples)
    result=dict(experimentId=exp['id'],planId=plan_id,status='descriptive_pairs' if samples else 'awaiting_point_in_time_data',
                samples=len(samples),rejected=rejected,incrementalNet=sum(samples) if samples else None,
                pairedContractFrozen=isinstance(contract,dict),eventLimitReached=len(events)==10000,
                uncertainty=uncertainty,automaticPromotion=False,
                limitation='Paired observed outcomes are descriptive; execution equivalence and verified costs remain required')
    experiments.record_observation(exp['id'],'paired_evaluation',result,db_path=db_path)
    return result


def evaluate_plan(experiment_id, datasets=(), *, db_path=None):
    """Evaluate frozen local inputs once. Returns evidence, never executes a trade."""
    exp=experiments.get_experiment(experiment_id,db_path)
    if exp['planId'] not in PLANS or exp['manifest'].get('codeHash')!=experiments.code_fingerprint():
        raise ValueError('Frozen plan/code mismatch; preregister a new experiment')
    if exp['planId'] in ('A5','A6','A7'):
        if datasets:
            raise ValueError('This plan consumes point-in-time registry events, not fabricated OHLC fills')
        return _paired_plan(exp,db_path)
    if not isinstance(datasets,(list,tuple)) or not 1<=len(datasets)<=5:
        raise ValueError('Provide one to five local datasets')
    manifest=exp['manifest'];plan=manifest['plan'];params=manifest['parameters']
    if any(not isinstance(d,dict) or not isinstance(d.get('symbol'),str) for d in datasets):
        raise ValueError('Each dataset must have a symbol identity')
    by_symbol={d.get('symbol'):d for d in datasets}
    if len(by_symbol)!=len(datasets) or set(by_symbol)!=set(manifest['symbols']):
        raise ValueError('Evaluate exactly the preregistered symbol set')
    prepared={symbol:_validated_rows(data,plan['interval']) for symbol,data in by_symbol.items()}
    scores={}
    if exp['planId']=='A3':
        stamps=[r['time'] for r in next(iter(prepared.values()))]
        if any([r['time'] for r in rows]!=stamps for rows in prepared.values()):
            raise ValueError('Relative strength requires aligned point-in-time observations')
        scores={symbol:[0.0]*len(stamps) for symbol in prepared}
        lookback=plan['lookback'];benchmark=manifest['benchmark']
        for index in range(lookback,len(stamps)):
            returns={symbol:rows[index]['close']/rows[index-lookback]['close']-1 for symbol,rows in prepared.items()}
            choices=[s for s in prepared if s!=benchmark and returns[s]>max(0,returns[benchmark])]
            if choices:
                winner=min(choices,key=lambda s:(-returns[s],s))
                scores[winner][index]=1.0
    else:
        strategy=ResearchStrategy(exp['planId'],'donchian',plan['lookback'])
        for symbol,rows in prepared.items():
            score=signal_series(strategy,rows)
            if exp['planId']=='A4':
                strength=adx_series(rows)
                score=[s if a is not None and a>=20 else 0 for s,a in zip(score,strength)]
            scores[symbol]=score
    results=[]
    for symbol,rows in prepared.items():
        protocol=experiments.purged_walk_forward(len(rows),plan['lookback'],params['maxHoldBars'],
            manifest['temporalParameters']['embargoBars'])
        start=protocol['holdout'][0]
        subset=experiments.dataset_manifest(rows[start:],symbol,plan['interval'],by_symbol[symbol]['source'],SECONDS[plan['interval']])
        claim=experiments.claim_holdout(exp['id'],subset,db_path)
        if not claim['allowed']:
            results.append(dict(symbol=symbol,status='previously_seen',claim=claim))
            continue
        if not protocol['sufficient']:
            results.append(dict(symbol=symbol,status='insufficient_data',protocol=protocol,claim=claim))
            continue
        costs=manifest['costModel'];atrs=atr_series(rows)
        def replay(model,lo=start,hi=len(rows)):
            return simulate(rows[:hi],scores[symbol][:hi],atrs[:hi],model['spreadPct'],params['threshold'],
                params['slAtr'],params['tpAtr'],params['trailAtr'],params['maxHoldBars'],1,
                params['riskPerTrade'],params['capitalReference']/(1+model['feeRate']),lo,hi,
                cost_model=model,allow_short=False,cooldown_seconds_base=SECONDS[plan['interval']])
        folds=[];validation_pnls=[]
        for fold in protocol['folds']:
            normal_fold=replay(costs,*fold['test'])
            stressed_fold=replay(stressed_costs(costs),*fold['test'])
            validation_pnls.extend(normal_fold['tradePnls'])
            folds.append(dict(**fold,normal=compact_metrics(normal_fold),stress=compact_metrics(stressed_fold),
                              uncertainty=expectancy_uncertainty(normal_fold['tradePnls'])))
        validation_uncertainty=expectancy_uncertainty(validation_pnls)
        experiments.record_observation(exp['id'],'walk_forward',dict(symbol=symbol,protocol=protocol,
            folds=folds,uncertainty=validation_uncertainty,trialCount=manifest['trialCount']),db_path=db_path)
        normal=replay(costs);stress=replay(stressed_costs(costs))
        uncertainty=expectancy_uncertainty(normal['tradePnls'])
        checks=[dict(id='sample',passed=normal['trades']>=params['minimumTrades']),
                dict(id='net',passed=normal['pnl']>0),dict(id='stress',passed=stress['pnl']>0),
                dict(id='uncertainty',passed=uncertainty['lower95'] is not None and uncertainty['lower95']>0),
                dict(id='drawdown',passed=normal['maxDrawdown']<=params['maximumDrawdown']),
                dict(id='walk_forward',passed=all(f['normal']['pnl']>0 and f['stress']['pnl']>0 for f in folds))]
        promotion=experiments.finalize_evidence(exp['id'],compact_metrics(normal),checks,claim,
            equivalence=False,costs_verified=costs.get('verified') is True,db_path=db_path)
        results.append(dict(symbol=symbol,status=promotion['status'],normal=compact_metrics(normal),
                            stress=compact_metrics(stress),uncertainty=uncertainty,checks=checks,
                            fixedCohortStress=fixed_cohort_costs(normal.get('executionEvents',[]),stressed_costs(costs)),
                            claim=claim,promotion=promotion))
        results[-1].update(protocol=protocol,folds=folds,validationUncertainty=validation_uncertainty,
                           uncertaintyScope='Holdout only; validation samples remain separate')
    return dict(version=PLAN_VERSION,experimentId=exp['id'],planId=exp['planId'],results=results,
                automaticPromotion=False,capitalScope='Independent reference per series; no aggregate portfolio return',
                stressScope='Policy rerun at stressed costs; trades and quantities may change',
                accumulatedTrials=experiments.accumulated_trials(db_path=db_path))
