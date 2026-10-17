"""Costes explícitos y funciones puras compartidas por ejecución y evaluación.

Los perfiles son escenarios conservadores para simulación, no tarifas verificadas
de una cuenta. El bróker/adaptador puede proporcionar sus costes observados.
Todos los importes de las funciones se expresan primero en moneda cotizada.
"""

import math

ECONOMICS_VERSION = 'net-costs-v1'


def _direction(value):
    if value in (1, 'long', 'buy'):
        return 1
    if value in (-1, 'short', 'sell'):
        return -1
    raise ValueError('Dirección inválida')


def execution_fill(price, direction, entering, spread_pct=0.0, slippage_pct=.0002):
    """Desde un precio medio: compra al ask y vende al bid, con deslizamiento.

    Si price ya es ask/bid se debe pasar spread_pct=0 para no duplicarlo.
    """
    values=(price,spread_pct,slippage_pct)
    if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in values):
        raise ValueError('Precio o fricción no finitos')
    if price<=0 or not 0<=spread_pct<1 or not 0<=slippage_pct<1:
        raise ValueError('Precio o fricción fuera de rango')
    sign=_direction(direction)*(1 if entering else -1)
    return price*(1+sign*spread_pct/2)*(1+sign*slippage_pct)


def net_outcome(entry, exit_price, qty, direction=1, entry_fee=0.0,
                exit_fee_rate=.001, quote_to_account=1.0, carry=0.0):
    """PnL total neto: precios ya ejecutados, entry_fee/carry en moneda cotizada."""
    values=(entry,exit_price,qty,entry_fee,exit_fee_rate,quote_to_account,carry)
    if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in values):
        raise ValueError('Importe no finito')
    if min(entry,exit_price,qty,quote_to_account)<=0 or min(entry_fee,exit_fee_rate,carry)<0 or exit_fee_rate>=1:
        raise ValueError('Importe o coste fuera de rango')
    return ((_direction(direction)*(exit_price-entry)*qty)-entry_fee-exit_price*qty*exit_fee_rate-carry)*quote_to_account


def cost_profile(source, category):
    """Escenario de costes por nominal; carry diario incluye financiación/préstamo.

    Lab conserva margen como modalidad hipotética explícita, nunca lo presenta
    como una ejecución Spot disponible. La viabilidad contractual MT5 se obtiene
    de su adaptador, no de estos precios/fracciones genéricos.
    """
    crypto=source=='binance' or category=='cripto'
    fees=.001 if crypto else .0002
    spreads={'cripto':.0005,'metales':.0002,'acciones':.0003,'indices':.0002,'forex':.0001,'futuros':.0002}
    return dict(version=ECONOMICS_VERSION,feeRate=fees,slippageRate=.0002,
                spreadPct=spreads.get(category,.0005),carryDailyRate=.0003 if crypto else .0002,
                mode='simulated-margin' if source=='lab' else 'spot' if crypto else 'simulated-cfd',
                verified=False,assumption='Escenario conservador; costes de cuenta sin verificar')


def planned_trade(entry, stop, target, qty, direction=1, fee_rate=.001,
                  exit_slippage=.0002, exit_spread=0.0, quote_to_account=1.0,
                  carry=0.0, entry_fee=None):
    """Economía en TP/SL después de todas las fricciones declaradas."""
    fee=qty*entry*fee_rate if entry_fee is None else entry_fee
    target_fill=execution_fill(target,direction,False,exit_spread,exit_slippage)
    stop_fill=execution_fill(stop,direction,False,exit_spread,exit_slippage)
    reward=net_outcome(entry,target_fill,qty,direction,fee,fee_rate,quote_to_account,carry)
    loss=max(0.0,-net_outcome(entry,stop_fill,qty,direction,fee,fee_rate,quote_to_account,carry))
    return dict(version=ECONOMICS_VERSION,netTarget=reward,netRisk=loss,
                netRewardRisk=reward/loss if loss>0 else None,
                breakEvenWinRate=loss/(loss+reward) if reward>0 and loss>0 else None,
                entryFee=fee*quote_to_account,carry=carry*quote_to_account,
                viable=reward>0 and loss>0)
