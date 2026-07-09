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
│       ├── indicators.py   SMA, EMA, RSI, MACD, ATR y señal técnica
│       ├── sentiment.py    StockTwits + Reddit (VADER) + Fear & Greed
│       ├── bots.py         bots de paper trading (scalper + swing) con SL/TP
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
  XAUUSD apenas lleva etiquetas Bullish/Bearish; los otros dos sí), r/Gold, r/Forex,
  r/Daytrading y r/wallstreetbets en Reddit, y las **Ideas y Minds de TradingView**
  del símbolo XAUUSD (recuento Long/Short explícito + VADER sobre los pensamientos).

### Fuentes de datos (todas gratuitas, sin API key)

| Fuente | Uso | Notas |
|---|---|---|
| Binance (`data-api.binance.vision`) | Velas y ticks de cripto y PAXG | ~2 s de latencia por sondeo |
| Swissquote (feed público) | Spot XAU/USD en vivo | Sondeo cada 2 s |
| Yahoo Finance (chart API) | Acciones, índices, forex, futuros | Sondeo cada 6 s |
| StockTwits | Opinión de traders con etiqueta Bullish/Bearish | Vía `curl_cffi` (Cloudflare rechaza el TLS de httpx) |
| TradingView Ideas | Ideas de trading con dirección explícita del autor | `direction` embebido en el HTML: 1=Long, 2=Short, 0=neutral; se ignoran ideas de >14 días |
| TradingView Minds | Pensamientos de la comunidad, puntuados con VADER | API pública `api/v1/minds/?symbol=`, vía `curl_cffi` |
| Reddit (RSS de búsqueda) | Posts recientes analizados con VADER | El endpoint JSON exige OAuth; el RSS sigue abierto pero con rate-limit por IP |
| alternative.me | Fear & Greed Index (solo cripto) | — |

### Probabilidad combinada

`/api/analysis/{símbolo}` mezcla **60 % señal técnica** (cruce SMA20/50, precio vs SMA50,
RSI-14, MACD) y **40 % opinión pública** (media ponderada por tamaño de muestra de las
fuentes anteriores) en una probabilidad de dirección alcista. *No es asesoramiento financiero.*

### 🤖 Bots de trading automático (paper trading)

Dos bots operan 24/7 con **dinero simulado sobre precios reales** (no hay bróker:
ninguna operación es real). La señal por vela sale de `strategy.py` con **lógica
adaptativa por régimen**: el ADX pondera entre seguimiento de tendencia (cruces
de medias, MACD, ruptura de 20 velas, marco superior) y reversión en rango (RSI
en extremos, %B de Bollinger). Se mezcla con la opinión pública cacheada y, si
**cruza** el umbral (solo el inicio de la señal, nunca a mitad del movimiento),
abre posición con **SL/TP por ATR**, **margen máximo de 100 € por operación** y
**trailing stop** que pasa a breakeven al ganar 1R y luego sigue al precio.
Filtro de coste: no se opera si el ATR no cubre 3× el spread. XAUUSD es el foco:
umbral rebajado y aún más durante el solapamiento Londres-NY.

| | Bot Scalper (corto plazo) | Bot Swing (largo plazo) |
|---|---|---|
| Plazo | minutos (cierre forzoso a los 45 min) | horas/días (cierre forzoso a los 5 días) |
| Velas | 1m + tendencia 5m | 1h + tendencia diaria |
| Universo | XAUUSD, PAXG, cripto y forex líquidos | 19 mercados (oro, cripto, acciones, índices…) |
| Peso opinión pública | 15 % | 40 % |
| Por defecto | umbral 0,45 · SL 1,5×ATR · TP 2,25×ATR · trail 1,2×ATR | umbral 0,40 · SL 2,5×ATR · TP 4×ATR · trail 1,5×ATR |
| Cadencia | escanea cada 15 s, vigila cada 5 s | escanea cada 4 min, vigila cada 20 s |

**Entrenamiento (🎓)**: el botón *Entrenar* lanza una **optimización walk-forward**
por símbolo sobre histórico real (el scalper usa 21 días de velas 1m — XAUUSD se
entrena con PAXG de Binance, oro 24/7; el swing usa 1 año de velas 1h). Se
optimizan solo los parámetros de ejecución (umbral, SL, TP, trailing) en el 70 %
inicial de los datos y se validan en el 30 % final que la optimización nunca vio:
**solo se aplican al bot si la validación es rentable** (defensa estándar contra
el sobreajuste). Los parámetros validados se guardan por símbolo en `bot_params`
y sustituyen a los de por defecto. *Nada de esto es asesoramiento financiero.*

**Gráfico de rendimiento (📊)**: bajo las tarjetas de los bots, curva de PnL
acumulado (verde sobre cero, rojo bajo cero) de las operaciones cerradas, con
filtros por bot, mercado y rango de fechas (hoy / 7 días / 30 días / todo /
fechas personalizadas) y resumen del tramo filtrado (PnL, nº de operaciones,
acierto, mejor y peor operación).

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
- `GET /api/bots` — estado de los bots (equity, posiciones abiertas, estadísticas)
- `POST /api/bots/{id}/start|stop|reset` — controlar un bot (`scalper` | `swing`)
- `GET /api/bots/{id}/trades?limit=50` — historial de operaciones cerradas
- `POST /api/bots/{id}/train?symbol=` — entrenamiento walk-forward en segundo plano
- `GET /api/bots/{id}/training` — progreso y resultados del entrenamiento
- `GET /api/bots/performance?bot=&symbol=&from=&to=` — curva de PnL acumulado
  de operaciones cerradas, filtrable por bot, mercado y fechas (epoch s)

## Mejoras futuras

- WebSocket nativo de Binance (latencia <100 ms) en lugar de sondeo
- [Finnhub](https://finnhub.io/) o [Twelve Data](https://twelvedata.com/) con API key gratuita
  para streaming real de acciones/forex
- OAuth de Reddit (app "script" gratuita) para eliminar el rate-limit del RSS
- Más foros de traders (ForexFactory, Investing.com) y sentimiento de noticias
- ~~Histórico de sentimiento en base de datos~~ ✔ hecho (SQLite + gráfico de evolución)
- Velas spot XAU/USD reales con una API key gratuita de Twelve Data (800 req/día)
