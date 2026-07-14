"""API del panel de trading: precios, velas, sentimiento y análisis."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import SYMBOLS, SYMBOLS_BY_ID
from . import bots, history, learning, market_data, systems, training
from .hub import hub
from .indicators import technical_analysis
from . import sentiment as sentiment_mod
from . import tradingview
from .sentiment import get_opinions, get_sentiment

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    bots.start_all()
    learning.start_scheduler()   # reentrenamiento walk-forward automático
    yield
    await learning.stop_scheduler()
    await training.stop_all()
    await bots.stop_all()
    await market_data.close_client()
    await sentiment_mod.close_session()
    await tradingview.close_session()


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


@app.get("/api/opinions/{symbol_id}")
async def opinions(symbol_id: str):
    """Publicaciones individuales (autor, texto, gráfico, votos) por fuente."""
    return await get_opinions(_symbol(symbol_id))


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


@app.get("/api/indicators/{symbol_id}")
async def indicators_panel(symbol_id: str, interval: str = "1h"):
    """Series de osciladores para el subpanel del gráfico: RSI, MACD,
    estocástico, ADX, MFI, CCI, OBV, momento (ROC) y ATR."""
    sym = _symbol(symbol_id)
    try:
        candles_data = await market_data.get_candles(sym, interval)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Fuente de datos no disponible: {exc}")
    return {
        "symbol": sym.id,
        "interval": interval,
        **systems.indicator_panel(candles_data),
    }


@app.get("/api/overlays/{symbol_id}")
async def overlays(symbol_id: str, interval: str = "1h"):
    """Trazados de los sistemas de trading para dibujar sobre el gráfico:
    soportes/resistencias, líneas de tendencia, Fibonacci, medias, Bollinger
    y VWAP de sesión (intradía)."""
    sym = _symbol(symbol_id)
    try:
        candles_data = await market_data.get_candles(sym, interval)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Fuente de datos no disponible: {exc}")
    return {
        "symbol": sym.id,
        "interval": interval,
        **systems.chart_overlays(candles_data, intraday=interval in ("1m", "5m", "15m")),
    }


def _bot(bot_id: str) -> bots.TradingBot:
    bot = bots.BOTS.get(bot_id)
    if not bot:
        raise HTTPException(404, f"Bot desconocido: {bot_id}")
    return bot


@app.get("/api/bots")
async def list_bots():
    """Estado de los bots de paper trading (dinero simulado, precios reales)."""
    return [bot.status() for bot in bots.BOTS.values()]


@app.get("/api/bots/performance")
async def bots_performance(
    bot: str | None = None,
    symbol: str | None = None,
    from_ts: int | None = Query(None, alias="from"),
    to_ts: int | None = Query(None, alias="to"),
    per_bot: bool = Query(False, alias="perBot"),
):
    """PnL acumulado de operaciones cerradas, filtrable por bot/símbolo/fechas.
    Con perBot=true añade una serie acumulada independiente por bot (una línea
    por bot en el gráfico)."""
    return bots.performance(
        bot, symbol.upper() if symbol else None, from_ts, to_ts, per_bot
    )


@app.post("/api/bots/{bot_id}/start")
async def start_bot(bot_id: str):
    _bot(bot_id).set_running(True)
    return _bot(bot_id).status()


@app.post("/api/bots/{bot_id}/stop")
async def stop_bot(bot_id: str):
    """Pausa el bot: deja de abrir posiciones y de vigilar las abiertas."""
    _bot(bot_id).set_running(False)
    return _bot(bot_id).status()


@app.post("/api/bots/{bot_id}/reset")
async def reset_bot(bot_id: str):
    """Borra el historial de operaciones y devuelve el balance inicial."""
    bot = _bot(bot_id)
    bot.reset()
    return bot.status()


@app.get("/api/bots/{bot_id}/trades")
async def bot_trades(bot_id: str, limit: int = 50):
    return _bot(bot_id).trades(limit)


@app.get("/api/bots/{bot_id}/trades/{trade_id}")
async def bot_trade_detail(bot_id: str, trade_id: int):
    """Detalle de una operación con sus confirmaciones y el gráfico dibujado
    (velas + sistemas) del momento en que el bot decidió entrar."""
    _bot(bot_id)
    detail = bots.trade_detail(bot_id, trade_id)
    if not detail:
        raise HTTPException(404, f"Operación {trade_id} no encontrada")
    return detail


@app.get("/api/bots/{bot_id}/learning")
async def bot_learning(bot_id: str):
    """Lo que el bot ha aprendido de sus operaciones reales: vetos activos,
    multiplicadores de tamaño, ventaja validada por símbolo y estadísticas
    (por símbolo, dirección, hora y motivo de salida) de la ventana reciente."""
    _bot(bot_id)
    return learning.insights(bot_id)


@app.post("/api/bots/{bot_id}/train")
async def train_bot(bot_id: str, symbol: str | None = None):
    """Entrenamiento walk-forward en segundo plano (todo el universo o un símbolo)."""
    bot = _bot(bot_id)
    symbols = [symbol.upper()] if symbol else None
    return training.start_training(bot, symbols)


@app.get("/api/bots/{bot_id}/training")
async def bot_training(bot_id: str):
    """Progreso del entrenamiento en curso + últimos resultados guardados."""
    _bot(bot_id)
    return {
        **training.training_status(bot_id),
        "saved": [r for r in training.load_results() if r["bot"] == bot_id],
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
