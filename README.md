# 📈 Panel de Trading en Tiempo Real

> **Correcciones (21-10-2026):** [cambios, límites y rectificación del diagnóstico](reports/CORRECCIONES_2026-10-21.md).
> Nuevas entradas Cripto en 1 h, parcial en 1,5 R netos y trailing 2,5 ATR hasta
> 48 h; selección exclusiva tras revisar todo el universo. Motor v5, Trend en
> tres bloques, modo a prueba limitado a una posición al 15 % del riesgo habitual
> y veto local por catalizadores recientes. La DEMO EUR amplía el capital virtual
> mediante una aportación registrada, conservando historial. Simulador maker y
> reintentos Oracle preparados por separado; sin contratación ni despliegue remoto.
> Las notas siguientes describen estados históricos y no sustituyen este informe.

> **Rendimiento y continuidad (18-10-2026):** [diagnóstico, mejoras y prioridades](reports/PLAN_RENDIMIENTO_2026-10-18.md).
> Vigilancia independiente de stops también en el Laboratorio, reutilización de velas
> cerradas, consultas de libro por lote y diagnóstico económico con resultados netos.
> Protección temporal contra suspensión automática en Windows mientras vive el servicio;
> las suspensiones explícitas y apagados siguen interrumpiendo la ejecución local.
> Comparación de un año en 1 h/4 h y exploración de tres años en 4 h: ninguna promoción.

> **Tres Mesas Siete (18-10-2026):** [revisión de operaciones, cambios y límites](reports/REVISION_MESAS_2026-10-18.md).
> Cripto, Forex y Acciones tienen siete roles, cuentas y controles independientes.
> Cripto conserva su historial y opera en simulación con velas de 15 minutos,
> universo Spot USDT dinámico y riesgo objetivo del 0,5 % por entrada.
> Las otras dos mesas comienzan con 100 USD virtuales cada una y respetan la sesión.
> El diagnóstico histórico sigue sin demostrar rentabilidad; no hay órdenes reales.

> **Estado actual (15-10-2026):** [mejoras económicas y arquitectura](reports/MEJORAS_ECONOMICAS_2026-10-15.md)
> e [investigación con resultados reales](reports/INVESTIGACION_SISTEMAS_2026-10-15.md).
> Mesa Siete recorre los mercados Spot USDT elegibles con cotizaciones reales y órdenes
> simuladas. Conserva su sesión financiada desde 100 EUR, hasta tres posiciones,
> máximo 1/6 por entrada y vigilancia de stops independiente del análisis de candidatos.
> El Laboratorio mantiene siete cuentas independientes y MT5 un presupuesto virtual
> de 100 EUR sobre una cuenta DEMO; el conjunto no es una única inversión de 100 EUR.
> Costes netos, límites agregados y comparador de doce reglas visibles en el panel.
> Ninguna regla nueva ha demostrado una ventaja suficiente para promoción.

> **Gemini activo (14-10-2026):** [configuración y funcionamiento](reports/GEMINI.md).
> Clasifica textos de oro/BTC/ETH en observación, con historial, caché y límites
> persistentes. No cambia las órdenes ni las probabilidades del motor actual.

> **Motor v4 (15-10-2026):** costes completos bajo supuestos explícitos, tres bloques
> cronológicos con embargo, holdout reservado, costes dobles y vecinos. Las
> validaciones de versiones anteriores ya no autorizan parámetros. El modo a prueba
> mantiene tamaño reducido; no equivale a una estrategia validada. El replay MT5
> exige contrato, margen, lotes, costes y conversión verificables para validar.
> Los informes anteriores, incluido [motor v3](reports/MOTOR-V3.md), son históricos.
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

La cocina recibe roles, conclusiones de agentes, órdenes maker y posiciones por
el mismo WebSocket `/ws`. Recupera instantáneas al reconectar y conserva HTTP
como reconciliación cada 30 segundos. Las colas por navegador están acotadas;
un cliente lento no retiene el motor de riesgo. Los mensajes de agentes son
resúmenes y veredictos publicados, no razonamiento interno.

El auditor `GPT-Quant-Risk` comparte llamada y presupuesto con el enjambre de
Grok. Usa [Structured Outputs de OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)
y vuelve a aplicar los vetos en código. Su veredicto añade `approved`,
`adjusted_risk_fraction`, `kelly_score`, `friction_warning` y `rationale`.
Kelly solo se calcula con cierres netos y payoff observado; menos de 30 cierres
limitan el tamaño al 50 %. El historial de cada mesa y política se calcula por
separado del laboratorio.

El modo configurado usa las suscripciones: `SWARM_TRANSPORT=subscription` en
`.runtime/swarm.env`. Grok Build y Codex ejecutan las evaluaciones con sus
sesiones oficiales, sin copiar cookies ni exportar tokens. No necesita claves
de API y no recurre a una API de pago al fallar la sesión o agotarse el cupo.
Consume la cuota incluida en los planes; no añade capacidad ilimitada.

Los clientes deben estar instalados y autenticados (`grok login` y
`codex login`, eligiendo ChatGPT). Se prefiere el ejecutable de la app Codex
frente a un npm global antiguo. Pueden fijarse rutas con `SWARM_GROK_BIN` y
`SWARM_CODEX_BIN`. Los procesos no reciben las variables de claves de API,
Codex exige una sesión ChatGPT y Grok se detiene si detecta configuración BYOK.
En `mesa-siete-v2`, flujo y riesgo de cada entrada se validan localmente en
Python. El libro vuelve a comprobarse después del commander: disponibilidad,
antigüedad máxima de 2 segundos y spread máximo de 8 bps en cripto. El pulso
solo tiene búsqueda/lectura públicas. No publica razonamiento interno.

El transporte de suscripción usa `PROMPT_PULSE_BATCH` y el contrato estricto
`BatchPulseReport.items`, con hasta cinco símbolos y cobertura completa del
lote solicitado. Cada lectura identifica el tipo de catalizador y admite
`trapDirection="both"`, que veta ambos lados también en la compuerta contextual.
El JSON con campos adicionales, tipos incorrectos o citas sin HTTPS se rechaza.
Los informes almacenados con contratos anteriores se descartan y se renuevan.

Tres perfiles especializados reutilizan ese radar y el commander, sin una
búsqueda propia si el lote sigue en caché. El verificador de noticias entra
solo con incertidumbre (titular elevado o evento, fakeout, trampa bilateral,
catalizador fresco sin dos fuentes). El investigador cuantitativo entra solo
con una hipótesis concreta y deja el experimento sin aplicar. El revisor
técnico entra solo ante un fallo demostrable y pide una regresión. Los tres
comparten cuatro trabajos nuevos por día UTC. No cambian el modo demo, los
topes de riesgo ni el puente: ese proceso sigue con `--no-subagents` y sin
recurrir a una API de pago. Los perfiles que Grok descubre están en
`.grok/agents/` y la activación local en `backend/app/specialist_team.py`.

Codex se reserva para asignación de familias por régimen (caché de 4 horas) y
post mortem de los últimos 40 cierres netos (12 horas o 10 cierres nuevos).
La caché y sus intentos se guardan en `.runtime/swarm.sqlite`; comparten los
límites de cuota existentes. Los vetos sugeridos por el post mortem son
informativos. La asignación solo puede vetar familias o reducir exposición
cuando el enjambre está en modo paper armado.

`PROMPT_REGIME_STRATEGIST` devuelve el régimen, las familias permitidas, el
tope de riesgo y la exigencia de maker; un veto de rupturas o una entrada
Post-Only obligatoria se respeta también al caducar una orden. El auditor usa
`PROMPT_FORENSIC_AUDITOR` y `ForensicAuditReport`, con R neta calculada desde
el riesgo inicial registrado, hora UTC y contexto causal de entrada. Sus
recomendaciones solo se conservan cuando cada segmento dispone de ocho
cierres y expectativa inferior a −0,20 R. La API de aprendizaje las muestra
en `forensicAudit` como sugerencias; el tope `sizeCap=0.50` para muestras de
menos de 30 cierres se calcula localmente, fuera del JSON del modelo.

La mesa cripto selecciona largos en el percentil 85 o superior de fuerza
relativa frente a BTC, ajustada por ATR a 24 y 72 horas. Pullback y recuperación
de 1h priorizan órdenes maker de 180 segundos. Las posiciones nuevas pueden
superar 48 horas si su beneficio neto flotante supera 1R y el cierre de 4h
sigue sobre EMA50; tras 2R usan Chandelier de 2,8 ATR con barras cerradas.
El meta-modelo v5 añade interacciones y puede recibir una semilla OOS al 30%
de su tasa habitual, solo tras tres folds positivos y validación final; esa
semilla no cuenta como experiencia en vivo ni contiene cierres del holdout.

La configuración local limita la observación a 8 ciclos/día, 2/h, con al menos
15 minutos entre ciclos. La observación periódica rota un símbolo cada tres
horas; las propuestas de entrada comparten ese cupo. Se mantiene
`SWARM_ENABLED=1`, `SWARM_MODE=shadow` y `SWARM_ARM=0`. Las llamadas pueden tardar
decenas de segundos, mientras la vigilancia de stops continúa independiente.
El panel distingue la preparación local de una respuesta real del proveedor.

El transporte API anterior sigue disponible solo eligiendo explícitamente
`SWARM_TRANSPORT=api` y configurando sus claves. No se selecciona como respaldo
automático. La configuración local y las claves no se incluyen en Git.

En sombra las revisiones no alteran las entradas simuladas existentes. La
autorización del enjambre solo es vinculante en modo paper armado, dentro de
su universo BTC/LINK/SPY; SPY conserva la ruta MT5 demo. Los cierres y stops
siguen independientes. Antes de admitir una entrada se comprueban de nuevo
la antigüedad del quorum, el libro actual y la cartera. El enjambre no envía
órdenes por sí mismo.

```powershell
# Terminal 1 — backend (puerto 8000)
backend\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --port 8000

# Terminal 2 — frontend (puerto 5173)
cd frontend
npm run dev
```

O simplemente: `.\start.ps1`

Primera vez (Python 3.13): `python -m venv backend\.venv`, `backend\.venv\Scripts\pip install -r backend\requirements.lock`
y `npm ci` dentro de `frontend/`.

Abrir <http://localhost:5173>.

## API

- `GET /api/desk` — siete roles, mercados, libro simulado, curvas y registro.
- `POST /api/desk/start|pause|close` — activar entradas, pausarlas o pedir cierre simulado.
- `GET /api/symbols` — catálogo de instrumentos
- `GET /api/candles/{id}?interval=1m|5m|15m|1h|4h|1d` — velas OHLCV
- `GET /api/sentiment/{id}` — opinión pública agregada (caché de 5 min)
- `GET /api/sentiment/{id}/history?hours=72` — histórico de sentimiento (SQLite `backend/data.db`)
- `GET /api/analysis/{id}?interval=` — técnico + sentimiento + puntuación sin calibrar
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

## Investigación profesional y comprobación del equipo

La mesa de investigación del panel permite registrar A1–A7, consultar evidencia y
crear encargos acotados. Trabaja en paper/demo. Un resultado histórico o un acuse
de Grok no autoriza operaciones ni acredita rentabilidad.

- `GET /api/professional/status`: versiones, progreso, conversiones y limitaciones.
- `GET /api/team/health`: entregas de los cinco roles, con caducidad de una hora.
- `POST /api/team/tasks`: flujo finito e idempotente; máximo cuatro tareas por día.
- `GET /api/team/tasks/{id}`: etapas, bloqueos y propuesta de investigación.
- `GET /api/research/plans`: catálogo A1–A7 y registro de experimentos.
- `POST /api/research/plans/{id}/preregister`: congelar hipótesis y universo.
- `POST /api/research/experiments/{id}/evaluate`: evaluación local acotada;
  A5–A7 requieren eventos emparejados disponibles al decidir.

El registro conserva hashes del código, entradas archivadas, intentos y decisiones.
Los periodos reservados consultados no se reutilizan como evidencia independiente.
Las capturas públicas registran bytes y fecha; la corroboración factual se declara
por separado. Los perfiles locales y los bots de escritorio tienen estados distintos.

Validación local: ejecutar `backend\.venv\Scripts\python -m pytest tests -q`
desde `backend/`; ejecutar `npm run build` y `npm run lint` desde `frontend/`.
Para copia y recuperación en un directorio nuevo, consultar [deploy/README.md](deploy/README.md).
Estado, resultados y límites: [informe de implementación](reports/IMPLEMENTACION_PROFESIONAL_2026-10-27.md).

## Ampliaciones futuras

- WebSocket público de Binance ya disponible; su latencia debe medirse, sin prometer un máximo de 100 ms
- [Finnhub](https://finnhub.io/) o [Twelve Data](https://twelvedata.com/) con API key gratuita
  para streaming real de acciones/forex
- OAuth de Reddit (app "script" gratuita) para eliminar el rate-limit del RSS
- Más foros de traders (ForexFactory, Investing.com) y sentimiento de noticias
- ~~Histórico de sentimiento en base de datos~~ ✔ hecho (SQLite + gráfico de evolución)
- Velas spot XAU/USD reales con una API key gratuita de Twelve Data (800 req/día)
