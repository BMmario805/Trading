"""Histórico de sentimiento en SQLite para correlacionarlo con el precio.

Cada vez que se calcula sentimiento fresco (no cacheado) se guarda una fila;
el frontend lo muestra como evolución temporal.
"""

import os
import sqlite3
import time
from pathlib import Path

from .database import Connection

DB_PATH = Path(os.getenv("TRADING_DATA_DB", str(Path(__file__).resolve().parent.parent / "data.db")))


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, factory=Connection)
    con.execute(
        """CREATE TABLE IF NOT EXISTS sentiment_history (
            symbol TEXT NOT NULL,
            ts INTEGER NOT NULL,
            bullish_pct REAL NOT NULL,
            sample INTEGER NOT NULL
        )"""
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_senti_symbol_ts"
        " ON sentiment_history(symbol, ts)"
    )
    return con


def record(symbol: str, bullish_pct: float, sample: int) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO sentiment_history VALUES (?, ?, ?, ?)",
            (symbol, int(time.time()), bullish_pct, sample),
        )


def get_history(symbol: str, hours: int = 72) -> list[dict]:
    since = int(time.time()) - hours * 3600
    with _conn() as con:
        rows = con.execute(
            "SELECT ts, bullish_pct, sample FROM sentiment_history"
            " WHERE symbol = ? AND ts >= ? ORDER BY ts",
            (symbol, since),
        ).fetchall()
    return [{"ts": t, "bullishPct": b, "sample": s} for t, b, s in rows]
