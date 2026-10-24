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
from . import gemini_agent, ai_swarm
from . import desk
from . import mt5_demo
from . import mt5_performance
from . import open_positions, telemetry
from . import portfolio_risk, research_jobs, performance_review, runtime_guard
from .sentiment import get_opinions, get_sentiment
from .cloud_runtime import health_snapshot, mount_frontend
from .book_stream import BOOKS

logging.basicConfig(level=logging.INFO)
logging.getLogger('httpx').setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    runtime_guard.acquire()
    try:
        BOOKS.start()
        bots.start_all()
        learning.start_scheduler()
        mt5_demo.start()
        gemini_agent.start()
        desk.start()
        telemetry.start()
        try:
            yield
        finally:
            await telemetry.stop()
            await hub.stop()
            await research_jobs.stop()
            await desk.stop()
            await gemini_agent.stop()
            await mt5_demo.stop()
            await learning.stop_scheduler()
            await training.stop_all()
            await bots.stop_all()
            await BOOKS.stop()
            await market_data.close_client()
            await sentiment_mod.close_session()
            await tradingview.close_session()
    finally:
        runtime_guard.release()


app = FastAPI(title="Trading Dashboard API", lifespan=lifespan)


@app.get('/api/health')
async def health_status():
    return health_snapshot()


@app.get('/api/market-feed')
async def market_feed_status():
    return BOOKS.status()


@app.get('/api/performance/review')
async def performance_review_status():
    return performance_review.snapshot()


@app.get('/api/portfolio/risk')
async def portfolio_risk_status():
    return portfolio_risk.public_snapshot()


@app.get('/api/research')
async def research_status():
    return research_jobs.status()


@app.post('/api/research/run')
async def research_start():
    return research_jobs.start()


@app.get('/api/desk')
async def desk_status():
    return desk.DESK.public() if desk.DESK else {'mode':'paper','enabled':False}


@app.get('/api/desks')
async def desks_status():
    return [engine.public() for engine in desk.DESKS.values()]


@app.get('/api/desks/{market}')
async def market_desk_status(market: str):
    if market not in desk.DESKS: raise HTTPException(404,'Mesa desconocida')
    return desk.DESKS[market].public()


@app.post('/api/desks/{market}/{action}')
async def market_desk_control(market: str, action: str):
    if market not in desk.DESKS: raise HTTPException(404,'Mesa desconocida')
    if action not in ('start','pause','close'): raise HTTPException(400,'Acción inválida')
    return await desk.DESKS[market].control(action)


@app.get('/api/positions/open')
async def open_positions_status():
    return open_positions.snapshot()


@app.post('/api/desk/reset')
async def reset_desk():
    if desk.DESK is None:
        raise HTTPException(503,'Mesa no iniciada')
    try:
        return await desk.DESK.reset()
    except ValueError as exc:
        raise HTTPException(503,str(exc)) from exc


@app.post('/api/desk/{action}')
async def desk_control(action: str):
    if desk.DESK is None:
        raise HTTPException(503,'Mesa no iniciada')
    if action not in ('start','pause','close'):
        raise HTTPException(400,'Acción inválida')
    return await desk.DESK.control(action)


@app.get('/api/agents/gemini')
async def gemini_status():
    if gemini_agent.AGENT is None:
        return {'configured': False, 'enabled': False, 'mode': 'observation'}
    agent = gemini_agent.AGENT
    return {**agent.status(), 'observations': [agent.snapshot(s) for s in agent.settings.symbols]}


@app.get('/api/agents/gemini/{symbol_id}')
async def gemini_observation(symbol_id: str):
    return gemini_agent.snapshot(_symbol(symbol_id).id)


@app.get('/api/agents/swarm')
async def swarm_status():
    ai_swarm.start()
    return ai_swarm.snapshot_status()


@app.get('/api/agents/swarm/{symbol_id}')
async def swarm_observation(symbol_id: str):
    ai_swarm.start()
    return ai_swarm.public_symbol(_symbol(symbol_id).id)


@app.post('/api/agents/swarm/evaluate')
async def swarm_evaluate(body: ai_swarm.EvaluateRequest):
    ai_swarm.start()
    if ai_swarm.SWARM is None:
        raise HTTPException(503, 'Enjambre no iniciado')
    try:
        return await ai_swarm.SWARM.evaluate(
            body.symbol, body.direction, entry=body.entry, stop=body.stop, target=body.target,
            qty=body.qty, risk_eur=body.riskEUR, notional_eur=body.notionalEUR)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/api/mt5')
async def mt5_status():
    return dict(mt5_demo.STATE)


@app.get('/api/mt5/performance')
async def mt5_performance_api(bot: str | None = None, symbol: str | None = None,
                              from_ts: int | None = Query(None, alias='from'),
                              to_ts: int | None = Query(None, alias='to'), perBot: bool = False):
    if not mt5_demo.CONFIG_PATH.exists():
        raise HTTPException(404, 'Cuenta MT5 no configurada')
    import json
    cfg = json.loads(mt5_demo.CONFIG_PATH.read_text(encoding='utf-8'))
    owner = f"{cfg['server']}:{cfg['login']}:"
    bot_names = [dict(id=c.id, name=c.name, magic=mt5_demo.MAGICS[c.id]) for c in bots.ALL_BOTS]
    return mt5_performance.performance(owner, bot_names, bot, symbol, from_ts, to_ts, perBot)


@app.post('/api/mt5/{action}')
async def mt5_control(action: str):
    if action not in ('start', 'stop'):
        raise HTTPException(400, 'Acción no válida')
    try:
        mt5_demo.set_enabled(action == 'start')
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    return {'enabled': action == 'start'}

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
        market_data.get_candles(sym, interval), get_sentiment(sym),
        return_exceptions=True,
    )
    if isinstance(candles_data, Exception):
        raise HTTPException(502, "No se han podido obtener las velas para el análisis")
    if isinstance(senti, Exception):
        logging.getLogger(__name__).warning("Sentimiento no disponible para %s: %s", sym.id, senti)
        senti = {
            "symbol": sym.id, "bullishPct": 50, "bearishPct": 50,
            "label": "no disponible", "sampleSize": 0, "sources": [],
            "errors": ["Opinión pública temporalmente no disponible"],
            "updatedAt": None,
        }
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
        "gemini": gemini_agent.snapshot(sym.id),
        "swarm": ai_swarm.public_symbol(sym.id),
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
    await hub.connect(ws)
    telemetry.changed('all')
    try:
        while True:
            msg = await ws.receive_json()
            if not isinstance(msg, dict):
                continue
            action, symbol_id = msg.get("action"), str(msg.get("symbol", "")).upper()
            if action == "subscribe":
                await hub.subscribe(ws, symbol_id)
            elif action == "unsubscribe":
                hub.unsubscribe(ws, symbol_id)
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)


@app.get("/api/markets/quality")
async def markets_quality():
    from . import opportunities
    data = await opportunities.snapshot()
    return {"updatedAt": data["at"], "markets": [opportunities.assess(s, data) for s in SYMBOLS if s.source == "binance"]}


# Las rutas API y WebSocket se registran antes del panel estático.
mount_frontend(app)
