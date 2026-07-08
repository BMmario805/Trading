"""API del panel de trading: precios, velas, sentimiento y análisis."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import SYMBOLS, SYMBOLS_BY_ID
from . import history, market_data
from .hub import hub
from .indicators import technical_analysis
from . import sentiment as sentiment_mod
from .sentiment import get_sentiment

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await market_data.close_client()
    await sentiment_mod.close_session()


app = FastAPI(title="Trading Dashboard API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _symbol(symbol_id: str):
    sym = SYMBOLS_BY_ID.get(symbol_id.upper())
    if not sym:
        raise HTTPException(404, f"Símbolo desconocido: {symbol_id}")
    return sym


@app.get("/api/symbols")
async def list_symbols():
    return [
        {
            "id": s.id,
            "label": s.label,
            "category": s.category,
            "source": s.source,
            "note": s.note,
        }
        for s in SYMBOLS
    ]


@app.get("/api/candles/{symbol_id}")
async def candles(symbol_id: str, interval: str = "1h"):
    try:
        return await market_data.get_candles(_symbol(symbol_id), interval)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Fuente de datos no disponible: {exc}")


@app.get("/api/sentiment/{symbol_id}")
async def sentiment(symbol_id: str):
    return await get_sentiment(_symbol(symbol_id))


@app.get("/api/sentiment/{symbol_id}/history")
async def sentiment_history(symbol_id: str, hours: int = 72):
    return history.get_history(_symbol(symbol_id).id, hours)


@app.get("/api/analysis/{symbol_id}")
async def analysis(symbol_id: str, interval: str = "1h"):
    """Análisis técnico + sentimiento combinados en una probabilidad de subida."""
    sym = _symbol(symbol_id)
    candles_data, senti = await asyncio.gather(
        market_data.get_candles(sym, interval), get_sentiment(sym)
    )
    tech = technical_analysis(candles_data)

    # técnico en [-1,1] -> [0,1]; mezcla 60 % técnico / 40 % opinión pública
    tech_prob = (tech["score"] + 1) / 2
    senti_prob = senti["bullishPct"] / 100
    has_senti = senti["sampleSize"] > 0
    combined = 0.6 * tech_prob + 0.4 * senti_prob if has_senti else tech_prob

    return {
        "symbol": sym.id,
        "interval": interval,
        "technical": tech,
        "sentiment": senti,
        "upProbability": round(combined * 100, 1),
        "verdict": (
            "alcista" if combined >= 0.6 else
            "bajista" if combined <= 0.4 else
            "neutral"
        ),
    }


@app.websocket("/ws")
async def ws_prices(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            action, symbol_id = msg.get("action"), str(msg.get("symbol", "")).upper()
            if action == "subscribe":
                await hub.subscribe(ws, symbol_id)
            elif action == "unsubscribe":
                hub.unsubscribe(ws, symbol_id)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(ws)
