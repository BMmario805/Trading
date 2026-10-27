"""Pure monetary risk shared by MT5 sizing, publication and portfolio admission."""
import math

RISK_MODEL_VERSION='mt5-contract-risk-v2'


def risk_components(stop_loss, volume, commission_roundtrip_per_lot=None, *, commission_paid=0.0):
    """Stop loss is monetary for this volume; paid entry costs are in equity."""
    if not all(type(v) in (int,float) and math.isfinite(v) for v in (stop_loss,volume,commission_paid)):
        raise ValueError('Importes contractuales no finitos')
    if stop_loss<0 or volume<=0 or commission_paid<0: raise ValueError('Importes contractuales inválidos')
    commission=commission_roundtrip_per_lot
    if commission is not None and (type(commission) not in (int,float) or not math.isfinite(commission) or commission<0):
        raise ValueError('Comisión del contrato inválida')
    pending=max(0.0,commission*volume-commission_paid) if commission is not None else 0.0
    friction=stop_loss*.1
    return dict(risk=stop_loss+friction+pending,priceRisk=stop_loss,
                frictionReserve=friction,commissionPending=pending,
                commissionPaid=commission_paid,commissionKnown=commission is not None,
                version=RISK_MODEL_VERSION,gapRiskBounded=False)


def contract_risk(api, costs, kind, symbol, volume, entry, stop, *, commission_paid=0.0):
    values=(volume,entry,stop,commission_paid)
    if not all(type(v) in (int,float) and math.isfinite(v) for v in values):
        raise ValueError('Importes contractuales no finitos')
    if min(volume,entry,stop)<=0 or commission_paid<0:
        raise ValueError('Importes contractuales inválidos')
    profit=api.order_calc_profit(kind,symbol,volume,entry,stop)
    if type(profit) not in (int,float) or not math.isfinite(profit):
        raise ValueError('Riesgo contractual sin cuantificar')
    # Paid entry commissions are already in balance/equity. Only unpaid costs
    # consume this forward reserve; the separate friction allowance remains.
    return risk_components(max(0.0,-profit),volume,costs.get('roundTripCommissionPerLot'),commission_paid=commission_paid)
