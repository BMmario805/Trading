# 📈 Panel de Trading en Tiempo Real

Dashboard de mercados con gráficos en vivo (cripto, acciones, índices, forex y futuros),
análisis técnico automático y agregación de la opinión pública de traders sobre la
dirección del precio.

## Arquitectura

```
Trading/
├── backend/          API FastAPI (Python)
│   └── app/
│       ├── config.py       catálogo de símbolos y fuentes
│       ├── market_data.py  velas y precios (Binance + Yahoo Finance)
│       ├── indicators.py   SMA, EMA, RSI, MACD y señal técnica
│       ├── sentiment.py    StockTwits + Reddit (VADER) + Fear & Greed
│       ├── hub.py          difusión de precios en vivo por WebSocket
│       └── main.py         endpoints REST + /ws
└── frontend/         React + Vite + TypeScript + Lightweight Charts v5
```

### Foco: oro (XAU/USD)

- **XAUUSD (Oro Spot)** — símbolo por defecto. Precio en vivo real del feed público de
  Swissquote (sondeo cada 2 s, media bid/ask del mejor perfil de spread). Como no hay
  histórico spot gratuito, las velas son las de los futuros GC=F **reescaladas a la base
  spot actual**, de modo que la última vela enlaza con el tick vivo (indicativo, no exacto).
- **PAXG (Oro 24/7)** — oro tokenizado (1 PAXG = 1 oz) en Binance: velas y ticks reales
  las 24 horas, también en fin de semana.
- **GC (futuros)** — el contrato COMEX clásico vía Yahoo.
- Sentimiento del oro: agrega los streams XAUUSD + GC_F + GLD de StockTwits (el stream
  XAUUSD apenas lleva etiquetas Bullish/Bearish; los otros dos sí) y r/Gold, r/Forex,
  r/Daytrading y r/wallstreetbets en Reddit.

### Fuentes de datos (todas gratuitas, sin API key)

| Fuente | Uso | Notas |
|---|---|---|
| Binance (`data-api.binance.vision`) | Velas y ticks de cripto y PAXG | ~2 s de latencia por sondeo |
| Swissquote (feed público) | Spot XAU/USD en vivo | Sondeo cada 2 s |
| Yahoo Finance (chart API) | Acciones, índices, forex, futuros | Sondeo cada 6 s |
| StockTwits | Opinión de traders con etiqueta Bullish/Bearish | Vía `curl_cffi` (Cloudflare rechaza el TLS de httpx) |
| Reddit (RSS de búsqueda) | Posts recientes analizados con VADER | El endpoint JSON exige OAuth; el RSS sigue abierto pero con rate-limit por IP |
| alternative.me | Fear & Greed Index (solo cripto) | — |

### Probabilidad combinada

`/api/analysis/{símbolo}` mezcla **60 % señal técnica** (cruce SMA20/50, precio vs SMA50,
RSI-14, MACD) y **40 % opinión pública** (media ponderada por tamaño de muestra de las
fuentes anteriores) en una probabilidad de dirección alcista. *No es asesoramiento financiero.*

## Cómo ejecutarlo

```powershell
# Terminal 1 — backend (puerto 8000)
backend\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --port 8000

# Terminal 2 — frontend (puerto 5173)
cd frontend
npm run dev
```

O simplemente: `.\start.ps1`

Primera vez: `python -m venv backend\.venv`, `backend\.venv\Scripts\pip install -r backend\requirements.txt`
y `npm install` dentro de `frontend/`.

Abrir <http://localhost:5173>.

## API

- `GET /api/symbols` — catálogo de instrumentos
- `GET /api/candles/{id}?interval=1m|5m|15m|1h|4h|1d` — velas OHLCV
- `GET /api/sentiment/{id}` — opinión pública agregada (caché de 5 min)
- `GET /api/sentiment/{id}/history?hours=72` — histórico de sentimiento (SQLite `backend/data.db`)
- `GET /api/analysis/{id}?interval=` — técnico + sentimiento + probabilidad
- `WS /ws` — `{"action":"subscribe","symbol":"BTC"}` → ticks en vivo

## Mejoras futuras

- WebSocket nativo de Binance (latencia <100 ms) en lugar de sondeo
- [Finnhub](https://finnhub.io/) o [Twelve Data](https://twelvedata.com/) con API key gratuita
  para streaming real de acciones/forex
- OAuth de Reddit (app "script" gratuita) para eliminar el rate-limit del RSS
- Más foros de traders (ForexFactory, Investing.com) y sentimiento de noticias
- ~~Histórico de sentimiento en base de datos~~ ✔ hecho (SQLite + gráfico de evolución)
- Velas spot XAU/USD reales con una API key gratuita de Twelve Data (800 req/día)
