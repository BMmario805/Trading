# 📈 Panel de Trading en Tiempo Real

> **Mesa Siete activa (14-10-2026):** [funcionamiento y límites](reports/MESA-SIETE.md).
> Página inicial con Gemini como selector y seis roles deterministas, hasta 81
> mercados, velas reales y libro independiente de 1.000 USDT simulados. Incluye
> comparación con comprar y mantener, controles e historial persistente.
> Es experimental: todavía no tiene una ventaja rentable demostrada.

> **Gemini activo (14-10-2026):** [configuración y funcionamiento](reports/GEMINI.md).
> Clasifica textos de oro/BTC/ETH en observación, con historial, caché y límites
> persistentes. No cambia las órdenes ni las probabilidades del motor actual.

> **Motor v3 (14-10-2026):** [cambios y comprobaciones](reports/MOTOR-V3.md).
> Todos los bots exigen validación de esta versión; el modo «a prueba» ya no abre
> entradas en la cuenta principal. Scalper/swing usan tres bloques 40/40/20 y el
> backtest aplica el filtro de sobreextensión. XAUUSD prioriza MT5 DEMO conectado.
> Estas reglas sustituyen las descripciones antiguas de exploración y señales.
> [Agentes: alta, costes y funcionamiento continuo](reports/AGENTES.md).

> **MetaTrader 5 demo:** [conexión, activación y límites](reports/METATRADER-DEMO.md).

> **Ampliación:** siete bots, diez criptomonedas adicionales y autoentrenamiento por instrumento.
> [Detalles y resultados](reports/AMPLIACION.md).
>
> **Revisión del motor (2026-09-09):** consulta [la auditoría y las evaluaciones](reports/AUDITORIA.md).
> Los bots siguen operando con dinero simulado. Ahora usan velas cerradas, cotizaciones
> recientes, stops con gaps y validación reforzada. Las calibraciones antiguas requieren
> reentrenamiento. El sentimiento es contextual y el simulador por velas no reproduce
> todos los filtros de cartera del vivo. Estas precisiones sustituyen las afirmaciones
> de equivalencia exacta y los umbrales de validación descritos más abajo.
> Ninguna de las seis combinaciones evaluadas en esta revisión superó la validación.


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
│       ├── indicators.py   SMA, EMA, RSI, MACD, ATR, estocástico, CCI, MFI,
│       │                   OBV, SuperTrend, Keltner, Donchian, PSAR, squeeze…
│       ├── systems.py      sistemas clásicos: pivotes, S/R, Fibonacci, velas
│       │                   japonesas, divergencias, VWAP, Ichimoku, ORB…
│       ├── strategy.py     señal por vela de cada bot (vivo = backtest),
│       │                   régimen ADX+Choppiness, KAMA, Vortex y SMC
│       ├── sentiment.py    StockTwits + Reddit (VADER) + Fear & Greed
│       ├── bots.py         6 bots de paper trading con SL/TP/trailing dinámico
│       │                   y filtro por meta-modelo (meta-labeling)
│       ├── training.py     backtest + walk-forward con embargo por símbolo
│       ├── learning.py     autoaprendizaje: vetos, tamaños, vetos horarios y
│       │                   meta-modelo online entrenado con cada operación
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

**Seis bots** operan 24/7 con **dinero simulado sobre precios reales** (no hay
bróker: ninguna operación es real), cada uno con una escuela clásica distinta:

- **Scalper** (1m/5m): lógica adaptativa por régimen — el ADX pondera entre
  seguimiento de tendencia (cruces de medias, filtro EMA200, MACD, ruptura de
  20 velas confirmada por volumen relativo, VWAP de sesión, **SuperTrend**,
  **OBV**, marco 5m) y reversión en rango (RSI, %B de Bollinger, **estocástico**,
  **Williams %R** y los sistemas de price action que solo votan cuando ocurren).
- **Swing** (1h/1d): la misma mezcla por régimen con SMA20/50, Ichimoku,
  **canal de Donchian**, SuperTrend, OBV, **MFI** y **CCI**, y más peso de la
  opinión pública.
- **Tendencia (Turtle)** (1h/1d): rupturas del canal de **Donchian 55/20**
  (las reglas de Dennis/Eckhardt) confirmadas por volumen, SuperTrend,
  **Parabolic SAR** y régimen EMA50/200, con la convicción escalada por ADX;
  stop 2×ATR y trailing ancho para dejar correr la tendencia.
- **Reversión (RSI2)** (1h/1d): el clásico de Larry Connors — comprar pánicos
  cortos (RSI(2) ≤ 10) solo con el precio sobre la SMA200 y vender euforias
  bajo ella; ganancia corta y frecuente con stop amplio, salida al recuperarse
  el RSI(2) o cruzar la SMA5.
- **Apertura (ORB)** (5m/15m): ruptura del rango de los **primeros 15 minutos
  de la sesión de NYSE** (hora exacta con cambio de horario), ponderada por
  volumen relativo, solo en las 2,5 h siguientes a la apertura y sin posiciones
  de un día para otro.
- **Volatilidad (Squeeze)** (15m/1h): el squeeze de John Carter — cuando las
  Bollinger se comprimen dentro del canal de Keltner la volatilidad está
  "cargada"; al liberarse entra en la dirección del momento con confirmación
  de SuperTrend, MACD y marco 1h.

Todas las señales se calculan por vela en `strategy.py` (la misma función sirve
al bot en vivo y al backtest). Si la señal **cruza** el umbral (solo el inicio
del movimiento), el bot abre con **SL/TP por ATR** y **trailing stop** que pasa
a breakeven al ganar 1R **y se estrecha (×0,75) tras 2R** para blindar la
ganancia. La salida por señal contraria usa **histéresis a 0,75×umbral** (el
0,5 anterior generaba salidas prematuras en el scalper). Tras una operación
ganadora el cooldown se reduce a 1/4. **Sin topes de presupuesto por
operación**: el tamaño es el **riesgo objetivo como % del balance** (1–2 %
según bot, capitaliza al crecer la cuenta) y el único límite físico es el
margen libre × apalancamiento (×5–×20 según bot). Filtro de coste igual que el
backtest: no se opera si el ATR no cubre 3× el spread. XAUUSD es el foco del
scalper: umbral rebajado y aún más durante el solapamiento Londres-NY.

### 📐 Análisis técnico moderno incorporado

Además de las escuelas clásicas, el motor de señal incluye:

- **Régimen doble ADX + Choppiness Index**: el peso tendencia/rango de cada
  vela combina la fuerza direccional (ADX, reacciona tarde) con la fractalidad
  del tramo (CHOP, reacciona antes).
- **KAMA / Efficiency Ratio (Kaufman)**: media adaptativa que se acelera en
  tendencia y se aplana en rango — filtra el ruido lateral mejor que una EMA.
- **Vortex (VI+/VI-)**: rotación temprana del flujo direccional.
- **Smart Money Concepts (ICT)** detectados de forma determinista y sin mirar
  al futuro: **Fair Value Gaps** (reacción en zonas de desequilibrio),
  **barridos de liquidez** (stop hunts con rechazo en la misma vela) y
  **estructura de mercado BOS/CHoCH** (sesgo por rotura de pivotes).
- **Expansión BBW por percentil**: el bot squeeze también dispara cuando el
  ancho de Bollinger despierta desde el cuartil bajo de su propia historia.
- **Reversión ampliada (meanrev)**: además del RSI(2) de Connors, entra en
  extremos estadísticos de **z-score ±2σ** y suma convicción si hay barrido de
  liquidez u **order block** a favor.
- **Order blocks (SMC)**: la última vela contraria antes de un desplazamiento
  impulsivo (cuerpo ≥1,2×ATR); la reacción en la zona vota a favor.
- **VWAP anclado al último pivote**: mide quién controla el mercado desde el
  último giro (además del VWAP de sesión intradía).
- **Cinta de EMAs (8/13/21/34/55)**: vota solo con la cinta perfectamente
  ordenada — el sello de una tendencia sana.
- **Aroon (25)**: tendencia fresca (up≥70 y down≤30, o el espejo).
- **StochRSI**: extremos de rango más sensibles que el RSI para la reversión.
- **Pullbacks de continuación**: retroceso a la EMA20 en tendencia con
  pendiente y cierre a favor — añade entradas DENTRO de la tendencia, no solo
  en rupturas.

### 🧠 Aprendizaje continuo

Los bots **aprenden de sus operaciones reales** (`learning.py`) con salvaguardas
de tamaño de muestra para no aprender del ruido:

- **Puerta de ventaja validada con modo "a prueba"**: un símbolo validado por
  el walk-forward opera a tamaño completo; uno sin entrenar explora a mitad de
  tamaño; y uno **rechazado o vetado ya no queda bloqueado para siempre**:
  opera **a prueba** con el 35 % del tamaño, umbral ×1,15 y el visto bueno del
  meta-modelo (p ≥ 50 %), máximo 2 posiciones a prueba simultáneas por bot.
  Así genera datos frescos con riesgo mínimo y se gana la reentrada operando,
  no esperando.
- **Vetos por rendimiento en vivo**: con ≥8 operaciones recientes y expectativa
  < −0,15R el símbolo queda vetado; con ≥6 operaciones, ≤30 % de acierto y PnL
  negativo se veta **solo esa dirección** (p. ej. "cortos de MSFT"). El veto se
  levanta cuando un entrenamiento posterior revalida la ventaja — la reentrada
  se gana, no se espera.
- **Tamaño adaptativo**: multiplicador por símbolo `1 + expectativa(R)` acotado
  a [0,5×, 1,5×] con ≥10 operaciones (Kelly fraccionado conservador).
- **Vetos horarios**: franjas UTC con ≥10 operaciones y expectativa < −0,25R
  quedan vetadas (p. ej. la iliquidez del rollover asiático).
- **Meta-modelo (meta-labeling, López de Prado)**: una **regresión logística
  online** aprende, operación a operación, en qué contextos las señales del
  bot ganan o pierden. Las features son el desglose de confirmaciones guardado
  al abrir (cada sistema técnico, codificado a favor/en contra de la
  dirección), la categoría, la dirección y la hora. No decide la dirección:
  decide **cuánto fiarse de la señal** — con ≥30 operaciones aprendidas filtra
  las señales con p(éxito) < 35 % y modula el tamaño de las demás (×0,5–×1,4
  según convicción). Se bootstrapea con todo el historial al arrancar y se
  actualiza con cada cierre; visible en `GET /api/bots/{id}/learning`
  (bloque `model`, con las features más influyentes).
- **Reentrenamiento automático**: un planificador reentrena cada bot cada
  4–12 h (según el marco temporal) con histórico fresco, sin intervención
  manual. El walk-forward usa **embargo de 30 velas** entre entrenamiento y
  validación (purged walk-forward) y una rejilla que incluye umbrales bajos
  (0,20–0,25): si la validación los sostiene, el bot opera más a menudo; si
  no, se descartan solos. Todo visible en la tarjeta del bot (línea 🧠) y en
  `GET /api/bots/{id}/learning`.

**Medidas de seguridad (🛡️, estándar prop-firm)** — aplicadas a cada entrada:

- **Circuit breaker diario**: si la pérdida realizada del día UTC alcanza el
  **5–8 % del balance** (según bot), deja de abrir posiciones hasta el día
  siguiente (las abiertas se siguen gestionando). Estado visible en la tarjeta.
- **Regla de las dos pérdidas**: tras 2 pérdidas consecutivas el tamaño de las
  siguientes entradas se reduce al **50 %** hasta la primera ganadora.
- **Cooldown escalonado**: el tiempo de espera para reentrar en un símbolo se
  duplica con cada pérdida consecutiva en él (tope ×8).
- **Exposición correlacionada**: máximo de posiciones simultáneas de la misma
  categoría y dirección (2 el scalper, 3 el swing) — BTC+ETH+SOL largos son en
  la práctica una sola apuesta, no tres.
- **Anti-persecución**: no se entra si el precio está a más de 2×ATR (scalper)
  / 2,5×ATR (swing) de la EMA20 en la dirección de la operación (comprar techos
  estirados = peor stop y retroceso probable).
- **Solo señal fresca**: la entrada exige el **cruce** del umbral (igual que el
  backtest con el que se entrena) y una última vela reciente (nada de operar
  con el mercado cerrado o el feed caído).

| Bot | Velas | Universo | Riesgo/op | Apalancamiento |
|---|---|---|---|---|
| Scalper | 1m + 5m | oro, cripto y forex líquidos | 1 % | ×20 |
| Swing | 1h + 1d | 19 mercados | 2 % | ×5 |
| Tendencia (Turtle) | 1h + 1d | 12 mercados con tendencia | 2 % | ×5 |
| Reversión (RSI2) | 1h + 1d | índices, big tech, BTC/ETH | 1,5 % | ×5 |
| Apertura (ORB) | 5m + 15m | acciones, índices y futuros US | 1 % | ×10 |
| Volatilidad (Squeeze) | 15m + 1h | oro, cripto y forex 24/7 | 1,5 % | ×10 |

**Entrenamiento (🎓)**: el botón *Entrenar* (o el planificador automático) lanza
una **optimización walk-forward** por símbolo sobre histórico real (intradía:
21–90 días de velas 1m/5m/15m — XAUUSD se entrena con PAXG de Binance, oro
24/7; marcos de horas: 1 año de velas 1h). Se optimizan solo los parámetros de
ejecución (umbral, SL, TP, trailing) en el 70 % inicial de los datos y se
validan en el 30 % final que la optimización nunca vio: **solo se aplican si la
validación es rentable** (defensa estándar contra el sobreajuste), y la
puntuación penaliza el drawdown (a igual PnL gana la curva más suave). El
backtest replica la mecánica del vivo **incluidas las salidas por señal
contraria y el filtro de coste**: lo entrenado es exactamente lo operado. Los
parámetros validados se guardan por símbolo en `bot_params` y sustituyen a los
de por defecto. *Nada de esto es asesoramiento financiero.*

**Detalle de operación (🔍)**: cada operación (abierta o cerrada) es clicable y
abre un modal con las **confirmaciones de la señal** — el desglose por sistema
(medias, MACD, RSI, Bollinger, VWAP/Ichimoku, velas japonesas, S/R, Fibonacci,
divergencias, marco superior, opinión pública) con el voto de cada uno — y el
**gráfico redibujado del momento de la entrada**: las 150 velas que vio el bot
con soportes/resistencias, líneas de tendencia, Fibonacci y medias, más las
líneas de entrada, stop-loss y take-profit. La instantánea se congela en
`bot_trades.snapshot` al abrir la posición (las operaciones anteriores a esta
mejora solo muestran los motivos en texto).

**Sistemas en el gráfico principal**: la barra «Sistemas» sobre el gráfico
activa/desactiva medias (EMA20/50), bandas de Bollinger, VWAP de sesión
(intradía), soportes/resistencias (S×n / R×n = nº de toques), líneas de
tendencia por pivotes, retrocesos de Fibonacci, **SuperTrend**, **canales de
Keltner**, **canal de Donchian**, **Parabolic SAR** (puntos), **nube de
Ichimoku** (senkou A/B) y **rango de apertura** (intradía), calculados por el
backend en `GET /api/overlays/{símbolo}` con las mismas rutinas que usan los
bots. El botón **Heikin-Ashi** cambia las velas a su versión suavizada (también
en vivo, tick a tick) y el desplegable de **indicador** abre un subpanel bajo
el precio con RSI, MACD (histograma + líneas), estocástico %K/%D, ADX, MFI,
CCI, OBV, momento (ROC) o ATR con sus niveles de referencia
(`GET /api/indicators/{símbolo}`).

**Gráfico de rendimiento (📊)**: bajo las tarjetas de los bots, curva de PnL
acumulado (verde sobre cero, rojo bajo cero) de las operaciones cerradas, con
filtros por bot (los seis, poblados dinámicamente), mercado y rango de fechas
(hoy / 7 días / 30 días / todo / fechas personalizadas) y resumen del tramo
filtrado (PnL, nº de operaciones, acierto, mejor y peor operación). El botón
**«Línea por bot»** cambia a una curva independiente por bot, cada una con su
color y una leyenda con su PnL (`?perBot=true` en el endpoint).

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

- `GET /api/desk` — siete roles, mercados, libro simulado, curvas y registro.
- `POST /api/desk/start|pause|close` — activar entradas, pausarlas o pedir cierre simulado.
- `GET /api/symbols` — catálogo de instrumentos
- `GET /api/candles/{id}?interval=1m|5m|15m|1h|4h|1d` — velas OHLCV
- `GET /api/sentiment/{id}` — opinión pública agregada (caché de 5 min)
- `GET /api/sentiment/{id}/history?hours=72` — histórico de sentimiento (SQLite `backend/data.db`)
- `GET /api/analysis/{id}?interval=` — técnico + sentimiento + probabilidad
- `WS /ws` — `{"action":"subscribe","symbol":"BTC"}` → ticks en vivo
- `GET /api/bots` — estado de los bots (equity, posiciones abiertas, estadísticas, aprendizaje)
- `POST /api/bots/{id}/start|stop|reset` — controlar un bot (`scalper` | `swing` | `trend` | `meanrev` | `orb` | `squeeze`)
- `GET /api/bots/{id}/learning` — lo aprendido: vetos, multiplicadores, ventaja validada y estadísticas por símbolo/dirección/hora
- `GET /api/bots/{id}/trades?limit=50` — historial de operaciones cerradas
- `GET /api/bots/{id}/trades/{trade_id}` — detalle de una operación: confirmaciones
  por sistema + snapshot (velas y trazados del momento de la entrada)
- `GET /api/overlays/{id}?interval=` — trazados de los sistemas para el gráfico:
  soportes/resistencias, líneas de tendencia, Fibonacci, EMA20/50, Bollinger y VWAP
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
