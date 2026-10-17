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
    # Preserve the original broker clock for reset cutoffs and audit. Existing ts
    # values remain valid until the next history sync supplies an explicit offset.
    columns = {r[1] for r in con.execute('PRAGMA table_info(mt5_deals)')}
    if 'raw_ts' not in columns:
        con.execute('ALTER TABLE mt5_deals ADD COLUMN raw_ts INTEGER')
        con.execute('ALTER TABLE mt5_deals ADD COLUMN offset_secs INTEGER NOT NULL DEFAULT 0')
        con.execute('UPDATE mt5_deals SET raw_ts=ts WHERE raw_ts IS NULL')
    if 'offset_assigned' not in columns:
        con.execute('ALTER TABLE mt5_deals ADD COLUMN offset_assigned INTEGER NOT NULL DEFAULT 0')
    con.execute('''CREATE TABLE IF NOT EXISTS mt5_account_ledger (
        owner TEXT, ticket INTEGER, amount REAL NOT NULL, PRIMARY KEY(owner,ticket))''')
    con.execute('''CREATE TABLE IF NOT EXISTS mt5_sync_evidence (
        owner TEXT PRIMARY KEY, payload TEXT NOT NULL)''')
    return con


def session(owner):
    with connection() as con:
        row=con.execute('SELECT payload FROM mt5_sessions WHERE owner=?',(owner,)).fetchone()
    return json.loads(row[0]) if row else None


def capital(owner, account, positions, now=None):
    settings=session(owner)
    if settings is None:
        return None
    if account.currency!=settings['currency']:
        raise ValueError('La divisa del presupuesto virtual no coincide con la cuenta demo')
    with connection() as con:
        rows=con.execute('SELECT kind,profit,commission,swap,fee FROM mt5_deals WHERE owner=? AND raw_ts>=?',
                         (owner,settings['cutoffMsc'])).fetchall()
    realized=sum(sum(r[1:]) for r in rows if r[0] not in TRANSFERS)
    balance=settings['initial']+realized
    floating=sum(p.profit+p.swap for p in positions)
    readiness = entry_readiness(owner, now, account=account, positions=positions) if now is not None else {}
    return dict(initial=settings['initial'],balance=balance,equity=balance+floating,
                currency=settings['currency'],startedAt=settings['startedAt'], **readiness)


def entry_readiness(owner, now, *, account=None, positions=None):
    """A virtual budget is spendable only after a fresh, reconciled broker read."""
    sync = sync_info(owner)
    with connection() as con:
        row = con.execute('SELECT payload FROM mt5_sync_evidence WHERE owner=?', (owner,)).fetchone()
    evidence = json.loads(row[0]) if row else {}
    age = now - sync['updatedAt']
    fresh = bool(sync['updatedAt'] and -5 <= age <= 30 and not sync['error'])
    reconciled = evidence.get('reconciled') is True
    unchanged = True
    if account is not None:
        unchanged = abs(account.balance+getattr(account,'credit',0)-evidence.get('brokerBalanceCredit',float('inf'))) <= .02
    if positions is not None:
        fingerprint = sorted([[int(p.ticket),float(p.volume)] for p in positions])
        unchanged = unchanged and fingerprint == evidence.get('positions')
    reconciled = reconciled and unchanged
    reason = (sync['error'] or 'Historial o saldo MT5 sin sincronización reciente') if not fresh else (
        None if reconciled else 'Saldo del bróker sin reconciliar con su historial')
    return dict(entryReady=fresh and reconciled, synchronized=fresh, reconciled=reconciled,
                syncUpdatedAt=sync['updatedAt'], syncError=reason,
                reconciliationDifference=evidence.get('difference'))


def sync_info(owner):
    with connection() as con:
        row = con.execute('SELECT updated_at,last_full,error FROM mt5_sync WHERE owner=?', (owner,)).fetchone()
    return dict(updatedAt=row[0], lastFull=row[1], error=row[2]) if row else dict(updatedAt=0, lastFull=0, error=None)


def sync_error(owner, message):
    previous = sync_info(owner)
    with connection() as con:
        con.execute('INSERT OR REPLACE INTO mt5_sync VALUES (?,?,?,?)',
                    (owner, previous['updatedAt'], previous['lastFull'], message))


def ingest(owner, account, positions, deals, now, full=False, clock_offset=0):
    """Importación idempotente y snapshot coherente; una respuesta None no borra datos."""
    if deals is None or positions is None:
        raise ValueError('Sin respuesta válida de MT5')
    info = sync_info(owner)
    offset_value = int(clock_offset or 0)
    offset_assigned = int(clock_offset is not None)
    settings=session(owner)
    all_deals = list(deals)
    if settings:
        deals=[d for d in deals if int(getattr(d,'time_msc',d.time*1000))>=settings['cutoffMsc'] and d.type not in TRANSFERS]
    with connection() as con:
        con.executemany('INSERT INTO mt5_deals '
                        '(owner,ticket,ts,position_id,magic,kind,entry,volume,symbol,profit,commission,swap,fee,raw_ts,offset_secs,offset_assigned) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,ticket) DO UPDATE SET '
                        'ts=excluded.raw_ts-(CASE WHEN mt5_deals.offset_assigned=1 THEN mt5_deals.offset_secs ELSE excluded.offset_secs END)*1000,'
                        'position_id=excluded.position_id,magic=excluded.magic,kind=excluded.kind,'
                        'entry=excluded.entry,volume=excluded.volume,symbol=excluded.symbol,profit=excluded.profit,'
                        'commission=excluded.commission,swap=excluded.swap,fee=excluded.fee,'
                        'raw_ts=excluded.raw_ts,offset_secs=CASE WHEN mt5_deals.offset_assigned=1 THEN mt5_deals.offset_secs ELSE excluded.offset_secs END,'
                        'offset_assigned=MAX(mt5_deals.offset_assigned,excluded.offset_assigned)', [
            (owner, int(d.ticket), int(getattr(d, 'time_msc', d.time*1000))-offset_value*1000, int(d.position_id), int(d.magic),
             int(d.type), int(d.entry), float(d.volume), d.symbol, float(d.profit), float(d.commission),
             float(d.swap), float(getattr(d, 'fee', 0)), int(getattr(d, 'time_msc', d.time*1000)),
             offset_value,offset_assigned) for d in deals])
        con.executemany('INSERT OR REPLACE INTO mt5_account_ledger VALUES (?,?,?)', [
            (owner, int(d.ticket), float(d.profit+d.commission+d.swap+getattr(d, 'fee', 0))) for d in all_deals])
        broker_expected = con.execute('SELECT COALESCE(SUM(amount),0) FROM mt5_account_ledger WHERE owner=?', (owner,)).fetchone()[0]
        broker_actual = account.balance + getattr(account, 'credit', 0)
        difference = broker_actual - broker_expected
        evidence = dict(reconciled=math.isfinite(difference) and abs(difference) <= .02,
                        difference=difference, brokerBalance=account.balance,
                        brokerBalanceCredit=broker_actual,
                        positions=sorted([[int(p.ticket),float(p.volume)] for p in positions]),
                        clockOffsetSeconds=offset_value,clockConfirmed=bool(offset_assigned), checkedAt=int(now), full=bool(full))
        con.execute('INSERT OR REPLACE INTO mt5_sync_evidence VALUES (?,?)', (owner, json.dumps(evidence)))
        pos = [dict(ticket=p.ticket, identifier=p.identifier, magic=p.magic, symbol=p.symbol,
                    profit=p.profit, swap=p.swap, volume=p.volume) for p in positions]
        virtual=None
        if settings:
            rows=con.execute('SELECT kind,profit,commission,swap,fee FROM mt5_deals WHERE owner=? AND raw_ts>=?',(owner,settings['cutoffMsc'])).fetchall()
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
               timestampBasis='utc-normalized; raw_ts and per-deal broker offset retained',
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
