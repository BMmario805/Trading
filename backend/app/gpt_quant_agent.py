"""GPT-Quant-Risk: contrato estructurado y límites cuantitativos locales.

El enjambre comparte transporte y presupuesto con este auditor: no hay una
segunda llamada OpenAI por operación. Los modelos solo pueden reducir riesgo.
"""
import math
from pydantic import BaseModel, ConfigDict, Field
from .hub import hub


class QuantVerdict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    approved: bool
    adjusted_risk_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    kelly_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    friction_warning: str
    rationale: str


def structured_format(report_model):
    # El contrato de Grok conserva sus nombres. Todos los campos son obligatorios
    # en el transporte, incluidos los nullable, como exige Structured Outputs.
    schema = report_model.model_json_schema()
    schema['additionalProperties'] = False
    schema['required'] = list(schema['properties'])
    def clean(node):
        if isinstance(node, dict):
            node.pop('default', None)
            # Compatible también con modelos de razonamiento sin constraints.
            for key in ('minimum', 'maximum', 'minLength', 'maxLength'):
                node.pop(key, None)
            for value in node.values():
                clean(value)
        elif isinstance(node, list):
            for value in node:
                clean(value)
    clean(schema)
    return dict(type='json_schema', json_schema=dict(name='gpt_quant_risk', strict=True, schema=schema))


def thought(symbol, phase, detail, *, desk='crypto', **metrics):
    hub.broadcast_event(dict(type='agent_thought', desk=desk, symbol=symbol,
                             agent='GPT-Quant-Risk', role='Size', phase=phase,
                             detail=detail, **metrics))


def _number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def local_verdict(economics, requested_risk_fraction, *, portfolio=None, learning_allowed=True):
    """Repite vetos con números locales; Kelly no se infiere de la convicción."""
    from .ai_swarm import economics_block
    requested = _number(requested_risk_fraction)
    blocked, reason, cap = economics_block(economics)
    if requested is None or not 0 < requested <= 1:
        blocked, reason = True, 'Fracción de riesgo desconocida'
    if not learning_allowed:
        blocked, reason = True, 'Veto del aprendizaje vigente'
    if portfolio is not None and portfolio.get('allowed') is not True:
        blocked, reason = True, portfolio.get('reason', 'Cartera sin autorización')
    if any(economics.get(key) is True for key in ('drawdownBlocked', 'dailyStopped', 'runtimeBlocked')):
        blocked, reason = True, 'Control de drawdown, pérdida diaria o proceso activo'
    kelly = 0.0
    sample = _number(economics.get('sampleSize'))
    plan = economics.get('planned') or {}
    rr = _number(plan.get('netRewardRisk'))
    win = _number(economics.get('winRate'))
    # Se usa el payoff REAL de cierres netos, nunca el objetivo teórico del plan.
    payoff = _number(economics.get('payoffRatio'))
    if sample is not None and sample >= 30 and win is not None and 0 <= win <= 100 and payoff and payoff > 0:
        p = win / 100
        kelly = min(1.0, max(0.0, p - (1-p)/payoff))
        cap = min(cap, kelly * .25 / requested) if requested else 0
        if kelly <= 0:
            blocked, reason = True, 'Kelly neto no positivo en los cierres observados'
    warning = ('Muestra insuficiente: tamaño máximo 0,5; Kelly sin validar'
               if sample is None or sample < 30 else
               'Kelly sin payoff observado; se conserva el límite local' if payoff is None else '')
    if rr is None or rr < 1.5:
        warning = 'Fricción elevada respecto al objetivo neto. ' + warning
    return QuantVerdict(approved=not blocked,
                        adjusted_risk_fraction=0.0 if blocked else min(requested, requested*cap),
                        kelly_score=kelly, friction_warning=warning.strip(),
                        rationale=reason or 'Economía neta y controles locales superados').model_dump()


def audit_report(report, economics, symbol):
    """Anexa el veredicto de cinco campos sin romper mesa-siete-v1."""
    requested = economics.get('requestedRiskFraction', .005)
    verdict = local_verdict(economics, requested)
    model_size = _number(report.get('sizeMultiplier')) or 0
    if report.get('approved') is not True or report.get('veto') is True or report.get('drawdownBlocked') is True:
        verdict.update(approved=False, adjusted_risk_fraction=0.0, rationale=report.get('reason', 'Veto del modelo'))
    verdict['adjusted_risk_fraction'] = min(verdict['adjusted_risk_fraction'], max(0, model_size)*requested)
    if verdict['adjusted_risk_fraction'] <= 0:
        verdict['approved'] = False
    report.update(verdict)
    report.update(veto=not verdict['approved'],
                  sizeMultiplier=verdict['adjusted_risk_fraction']/requested if requested else 0)
    thought(symbol, 'verdict', verdict['rationale'], approved=verdict['approved'],
            verdict=verdict, conviction=report.get('asymmetryScore'))
    return report


def proposal_context(engine, order):
    """Historial de ESTA mesa y política; nunca mezcla la muestra del laboratorio."""
    import json
    from . import learning
    with engine.connect() as con:
        rows = [json.loads(row[0]) for row in con.execute(
            "SELECT payload FROM trades WHERE json_extract(payload,'$.status')='closed' "
            "AND json_extract(payload,'$.policyVersion')=? ORDER BY id DESC LIMIT 200",
            (engine.policy_version,))]
    sample = learning.closed_net_sample(rows)
    plan = order['economics']  # calculado por economics.planned_trade, con costes
    economics = dict(planned=plan, **sample,
                     requestedRiskFraction=order['risk']/engine.equity(),
                     dailyStopped=bool(engine.state.get('dailyStopped')))
    if economics['breakEvenWinRate'] is None:
        economics['breakEvenWinRate']=(plan.get('breakEvenWinRate') or 0)*100
    # learning.py mantiene los vetos del laboratorio por bot. Solo se consulta
    # un bot si la propuesta identifica ese historial; la mesa usa sus cierres.
    bot_id = order.get('learningBotId')
    if bot_id:
        economics['learningAllowed'] = learning.gate(bot_id, order['symbol'], 'long')[0]
    return economics
