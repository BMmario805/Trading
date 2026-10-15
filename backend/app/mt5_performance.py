"""Rendimiento procedente de MT5, separado por cuenta y ticket de ejecución."""

import json
import math
import sqlite3
from pathlib import Path

from .database import Connection

DB_PATH = Path(__file__).resolve().parents[2] / '.runtime' / 'mt5-demo.sqlite'
TRANSFERS = {2, 3, 6}  # balance (depósito/retirada), crédito, bonus


def connection():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, factory=Connection)
    con.execute('''CREATE TABLE IF NOT EXISTS mt5_deals (
        owner TEXT, ticket INTEGER, ts INTEGER, position_id INTEGER, magic INTEGER,
        kind INTEGER, entry INTEGER, volume REAL, symbol TEXT, profit REAL,
        commission REAL, swap REAL, fee REAL, PRIMARY KEY(owner, ticket))''')
    con.execute('''CREATE TABLE IF NOT EXISTS mt5_snapshots (
        owner TEXT, ts INTEGER, balance REAL, equity REAL, credit REAL, currency TEXT,
        positions TEXT, PRIMARY KEY(owner, ts))''')
    con.execute('''CREATE TABLE IF NOT EXISTS mt5_sync (
        owner TEXT PRIMARY KEY, updated_at INTEGER, last_full INTEGER, error TEXT)''')
    con.execute('''CREATE TABLE IF NOT EXISTS mt5_sessions (
        owner TEXT PRIMARY KEY, payload TEXT NOT NULL)''')
    return con


def session(owner):
    with connection() as con:
        row=con.execute('SELECT payload FROM mt5_sessions WHERE owner=?',(owner,)).fetchone()
    return json.loads(row[0]) if row else None


def capital(owner, account, positions):
    settings=session(owner)
    if settings is None:
        return None
    if account.currency!=settings['currency']:
        raise ValueError('La divisa del presupuesto virtual no coincide con la cuenta demo')
    with connection() as con:
        rows=con.execute('SELECT kind,profit,commission,swap,fee FROM mt5_deals WHERE owner=? AND ts>=?',
                         (owner,settings['cutoffMsc'])).fetchall()
    realized=sum(sum(r[1:]) for r in rows if r[0] not in TRANSFERS)
    balance=settings['initial']+realized
    floating=sum(p.profit+p.swap for p in positions)
    return dict(initial=settings['initial'],balance=balance,equity=balance+floating,
                currency=settings['currency'],startedAt=settings['startedAt'])


def sync_info(owner):
    with connection() as con:
        row = con.execute('SELECT updated_at,last_full,error FROM mt5_sync WHERE owner=?', (owner,)).fetchone()
    return dict(updatedAt=row[0], lastFull=row[1], error=row[2]) if row else dict(updatedAt=0, lastFull=0, error=None)


def sync_error(owner, message):
    previous = sync_info(owner)
    with connection() as con:
        con.execute('INSERT OR REPLACE INTO mt5_sync VALUES (?,?,?,?)',
                    (owner, previous['updatedAt'], previous['lastFull'], message))


def ingest(owner, account, positions, deals, now, full=False):
    """Importación idempotente y snapshot coherente; una respuesta None no borra datos."""
    if deals is None or positions is None:
        raise ValueError('Sin respuesta válida de MT5')
    info = sync_info(owner)
    settings=session(owner)
    if settings:
        deals=[d for d in deals if int(getattr(d,'time_msc',d.time*1000))>=settings['cutoffMsc'] and d.type not in TRANSFERS]
    with connection() as con:
        con.executemany('INSERT OR REPLACE INTO mt5_deals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', [
            (owner, int(d.ticket), int(getattr(d, 'time_msc', d.time*1000)), int(d.position_id), int(d.magic),
             int(d.type), int(d.entry), float(d.volume), d.symbol, float(d.profit), float(d.commission),
             float(d.swap), float(getattr(d, 'fee', 0))) for d in deals])
        pos = [dict(ticket=p.ticket, identifier=p.identifier, magic=p.magic, symbol=p.symbol,
                    profit=p.profit, swap=p.swap, volume=p.volume) for p in positions]
        virtual=None
        if settings:
            rows=con.execute('SELECT kind,profit,commission,swap,fee FROM mt5_deals WHERE owner=? AND ts>=?',(owner,settings['cutoffMsc'])).fetchall()
            balance=settings['initial']+sum(sum(r[1:]) for r in rows if r[0] not in TRANSFERS)
            virtual=(balance,balance+sum(p.profit+p.swap for p in positions),0)
        con.execute('INSERT OR REPLACE INTO mt5_snapshots VALUES (?,?,?,?,?,?,?)',
                    (owner, int(now)//10*10, *(virtual or (account.balance,account.equity,account.credit)),
                     account.currency, json.dumps(pos)))
        con.execute('INSERT OR REPLACE INTO mt5_sync VALUES (?,?,?,?)',
                    (owner, int(now), int(now) if full else info['lastFull'], None))


def performance(owner, bots, bot=None, symbol=None, since=None, until=None, per_bot=False):
    """PnL neto contabilizado por MT5; las transferencias no son rentabilidad."""
    magic_names = {b['magic']: b['id'] for b in bots}
    names = {b['id']: b['name'] for b in bots} | {'manual': 'Manual / otros robots', 'account': 'Ajustes de cuenta'}
    with connection() as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute('SELECT * FROM mt5_deals WHERE owner=? ORDER BY ts,ticket', (owner,))]
        snapshots = [dict(r) for r in con.execute('SELECT * FROM mt5_snapshots WHERE owner=? ORDER BY ts', (owner,))]
    sync = sync_info(owner)
    latest = snapshots[-1] if snapshots else None
    open_positions = json.loads(latest['positions']) if latest else []
    ownership = {}
    # La apertura determina el dueño; un cierre manual con magic=0 no cambia la atribución.
    for r in rows:
        if r['kind'] in (0, 1) and r['entry'] in (0, 2) and r['position_id']:
            ownership.setdefault(r['position_id'], magic_names.get(r['magic'], 'manual'))
    for p in open_positions:
        ownership.setdefault(p['identifier'], magic_names.get(p['magic'], 'manual'))
    events, groups = [], {}
    cash_flows = 0.0
    for r in rows:
        r['seconds'] = r['ts']//1000
        r['amount'] = r['profit']+r['commission']+r['swap']+r['fee']
        if r['kind'] in TRANSFERS:
            cash_flows += r['amount']
            continue
        r['bot'] = ownership.get(r['position_id'], magic_names.get(r['magic'], 'manual' if r['kind'] in (0, 1) else 'account'))
        events.append(r)
        if r['position_id']:
            group = groups.setdefault(r['position_id'], dict(bot=r['bot'], symbol=r['symbol'], amount=0, entered=0, exited=0, closedAt=0))
            if r['symbol']:
                group['symbol'] = r['symbol']
            group['amount'] += r['amount']
            if r['kind'] in (0, 1) and r['entry'] == 0:
                group['entered'] += r['volume']
            elif r['kind'] in (0, 1) and r['entry'] in (1, 3):
                group['exited'] += r['volume']
                group['closedAt'] = r['seconds']
    live_ids = {p['identifier'] for p in open_positions}
    closed = [g for pid,g in groups.items() if pid not in live_ids and g['entered'] > 0
              and g['exited'] >= g['entered']-1e-8]

    def selected(r, selected_bot=bot, time_key='seconds'):
        return ((not selected_bot or r['bot'] == selected_bot) and (not symbol or r['symbol'] == symbol)
                and (since is None or r[time_key] >= since) and (until is None or r[time_key] <= until))

    def result(selected_bot):
        relevant = [r for r in events if selected(r, selected_bot)]
        trades = [r for r in closed if selected(r, selected_bot, 'closedAt')]
        points, cumulative = [], 0.0
        if relevant:
            points.append(dict(ts=relevant[0]['seconds']-1, cumPnl=0, pnl=0, symbol='', bot=selected_bot or ''))
        for r in relevant:
            cumulative += r['amount']
            point = dict(ts=r['seconds'], cumPnl=round(cumulative, 2), pnl=round(r['amount'], 2), symbol=r['symbol'], bot=r['bot'])
            if points and points[-1]['ts'] == point['ts']:
                point['pnl'] = round(points[-1]['pnl']+point['pnl'], 2)
                points[-1] = point
            else:
                points.append(point)
        end = min(until, latest['ts']) if latest and until is not None else latest['ts'] if latest else None
        if end is not None and (since is None or end >= since):
            if not points:
                points.append(dict(ts=max(since or end-10, end-10), cumPnl=0, pnl=0, symbol='', bot=selected_bot or ''))
            if end > points[-1]['ts']:
                points.append(dict(ts=end, cumPnl=round(cumulative, 2), pnl=0, symbol='', bot=selected_bot or ''))
        wins = sum(g['amount'] > 0 for g in trades)
        losses = sum(g['amount'] < 0 for g in trades)
        summary = dict(trades=len(trades), wins=wins, losses=losses, winRate=round(100*wins/len(trades), 1) if trades else None,
                       pnl=round(cumulative, 2), best=round(max((g['amount'] for g in trades), default=0), 2),
                       worst=round(min((g['amount'] for g in trades), default=0), 2))
        return dict(points=points, summary=summary)

    out = result(bot)
    currency = latest['currency'] if latest else None
    # Las tarjetas resumen toda la cuenta desde el histórico disponible, no el filtro del gráfico.
    realized = {key: 0.0 for key in names}
    floating = {key: 0.0 for key in names}
    position_counts = {key: 0 for key in names}
    for r in events:
        realized[r['bot']] += r['amount']
    for p in open_positions:
        owner_bot = ownership[p['identifier']]
        floating[owner_bot] += p['profit']+p['swap']
        position_counts[owner_bot] += 1
    account_float = latest['equity']-latest['balance']-latest['credit'] if latest else 0
    floating['account'] += account_float-sum(floating.values())
    cards = [dict(id=key, name=name, realizedPnl=round(realized[key], 2), unrealizedPnl=round(floating[key], 2),
                  totalPnl=round(realized[key]+floating[key], 2), openPositions=position_counts[key],
                  closedTrades=sum(g['bot']==key for g in closed)) for key,name in names.items()]
    history = [dict(ts=s['ts'], balance=s['balance'], equity=s['equity']) for s in snapshots
               if (since is None or s['ts'] >= since) and (until is None or s['ts'] <= until)]
    if len(history) > 2000:
        last = history[-1]
        history = history[::math.ceil(len(history)/1999)]
        if history[-1]['ts'] != last['ts']:
            history.append(last)
    settings=session(owner)
    out.update(currency=currency, session=settings, updatedAt=sync['updatedAt'], syncError=sync['error'], source='mt5',
               bots=[dict(id=key, name=value) for key,value in names.items()],
               symbols=sorted({r['symbol'] for r in rows if r['symbol']} | {p['symbol'] for p in open_positions}),
               cards=cards, accountHistory=history, account=dict(
                   balance=latest['balance'] if latest else None, equity=latest['equity'] if latest else None,
                   realizedPnl=round(sum(realized.values()), 2), unrealizedPnl=round(account_float, 2),
                   totalPnl=round(sum(realized.values())+account_float, 2), netFunding=round(cash_flows+(settings or {}).get('initial',0), 2),
                   reconciliationDifference=round(latest['balance']+latest['credit']-(session(owner) or {}).get('initial',0)-cash_flows-sum(realized.values()), 2) if latest else None,
                   historySince=snapshots[0]['ts'] if snapshots else None))
    if per_bot:
        out['series'] = [dict(id=key, name=name, **result(key)) for key,name in names.items() if not bot or key==bot]
    return out
