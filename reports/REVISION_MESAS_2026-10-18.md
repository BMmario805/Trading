# Revisión de mercados y tres Mesas Siete

18 de octubre de 2026 · Europe/Madrid. Ejecución exclusivamente simulada en las tres mesas.

## Resultado de la revisión

Se han creado tres mesas independientes, cada una con los siete roles Selector,
Tape, Price, Wire, Size, Exec y Risk. Cripto conserva la cuenta y todo el historial
anterior. Forex y acciones comienzan con **100 USD virtuales cada una**, separados
del capital cripto, las siete cuentas del Laboratorio y la demo MT5. No se ha
aportado dinero ni se ha conectado una pasarela de órdenes reales.

| Mesa | Universo | Funcionamiento |
|---|---|---|
| Cripto | Catálogo dinámico de pares Spot USDT; unos 500 mercados examinados y alrededor de 80–90 elegibles durante la observación | Activa 24/7, compras con efectivo, hasta tres posiciones |
| Forex | EURUSD, GBPUSD, AUDUSD y NZDUSD | Armada para sesión semanal; requiere referencia USD reciente |
| Acciones | 20 acciones y ETF de EE. UU., incluidos AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, AMD, AVGO, JPM, V, MA, COST, WMT, XOM, UNH, NFLX, PLTR, SPY y QQQ | Sesión regular de Nueva York, corroborada también con metadatos de la fuente |

Las cotizaciones de forex/acciones son referencias de Yahoo. Sus diferenciales,
comisiones, deslizamientos y fracciones de acciones son supuestos explícitos. No
son libros ejecutables ni reproducen un contrato de bróker. El catálogo forex usa
pares cotizados en USD para evitar inventar conversiones del PnL en JPY/CHF/CAD.
Los festivos se bloquean si no hay sesión y precio corroborados; el rótulo horario
general no constituye un calendario bursátil completo.

## Operaciones y estrategias encontradas

Al primer corte, cripto tenía **29 cierres, seis ganadores y −7,50 USDT netos**.
Veinte stops sumaban −11,7246 USDT; cinco objetivos, +4,4404 USDT; cuatro salidas
por tiempo, −0,2192 USDT. La sesión inicial de 100 EUR conservaba aproximadamente
104,47 USDT de patrimonio sobre 112,0953 USDT iniciales.

La regla anterior consideraba suficiente comprar con descuento respecto a EMA20
y RSI entre 15 y 75, incluso con EMA20 por debajo de EMA50 y sin recuperación
confirmada. Admitía RSI hasta 80 y hasta −4 ATR de extensión. Eso permitía comprar
caídas sin confirmación. Los objetivos de 1,8 ATR en 5 minutos dejaban fuera de la
viabilidad económica muchas monedas grandes por sus costes de ida y vuelta.

El Laboratorio sigue comparando siete familias: Scalper, Swing, Turtle, RSI2, ORB,
Squeeze y Flujo. En la lectura de esta revisión sus resultados realizados eran:

| Estrategia | Cierres | Ganadores | PnL realizado EUR |
|---|---:|---:|---:|
| Scalper | 0 | 0 | 0,00 |
| Swing | 6 | 1 | −0,86 |
| Turtle | 5 | 0 | −3,27 |
| RSI2 | 3 | 3 | +0,58 |
| ORB | 0 | 0 | 0,00 |
| Squeeze | 5 | 3 | −4,11 |
| Flujo | 2 | 1 | −0,29 |

Las muestras son pequeñas. Tres ganancias de RSI2 no prueban una ventaja. Siguen
existiendo posiciones antiguas del Laboratorio calculadas con costes anteriores;
el panel las distingue como resultado bruto. No se han eliminado sus pérdidas ni
se han reiniciado cuentas para mejorar la apariencia de los resultados. MT5 está
conectado a una cuenta DEMO y continúa como motor separado.

## Cambios de cripto

- Velas cerradas de **15 minutos** y tres configuraciones: retroceso en tendencia
  con cierre alcista, ruptura de 20 velas con volumen relativo ≥1,2 y recuperación
  de EMA20 con pendiente positiva y proximidad a EMA50.
- RSI **35–72**, extensión entre **−1 y +1,5 ATR**, y distancia máxima de 2 ATR
  entre precio y cierre que originó la señal. Una moneda que simplemente baja no
  satisface la entrada.
- Volumen mínimo de **2 millones de USDT diarios**, spread máximo de 10 puntos
  básicos y participación máxima del 10 % de la cantidad publicada al mejor ask.
  El volumen de 24 h no se presenta como profundidad ejecutable.
- Exclusión de stablecoins, productos apalancados conocidos y **bStocks** conocidos.
  Se detectaron SNDKB y SOXLB entre los pares Spot: son exposición a acciones/ETF,
  no criptomonedas ordinarias. La lista explícita debe mantenerse actualizada;
  no se aplica un veto genérico al sufijo B o UP.
- Riesgo neto objetivo máximo **0,5 % por entrada y 1,5 % en la mesa**, nominal
  máximo 1/6 del patrimonio, hasta tres posiciones y límite diario del 5 %.
- Stop inicial **1,5 ATR**, objetivo **3 ATR**, beneficio/riesgo **neto ≥1,2**.
  Se conservan comisiones del 0,1 % por lado y deslizamiento del 0,02 % por lado
  como hipótesis de cripto. No son tarifas verificadas de una cuenta real.
- Trailing desde 1R, con protección del punto de equilibrio neto y distancia
  de 1,5 ATR. Permanencia máxima de seis horas en entradas nuevas; las posiciones
  heredadas conservan su límite anterior de 45 minutos y sus costes guardados.
- Espera de una vela tras un cierre positivo y cuatro tras uno no positivo,
  persistida tras reiniciar. Descargas de candidatos en paralelo, con cuatro
  peticiones simultáneas como máximo; decisiones y cambios de cuenta en serie.
- Vigilancia de stops independiente, cotización de ejecución renovada antes de
  entrar, límites de espera y reintentos espaciados para mercados cuya fuente falla.
- Controles, efectivo, operaciones y estadísticas separados por mesa. El panel
  distingue los resultados de la política nueva de los cierres heredados.

## Diagnóstico histórico

Se fijaron las reglas antes de consultar 21 días de datos de 15 minutos, con los
últimos siete días separados para evaluación. El archivo completo conserva los
16 mercados inicialmente descargados, incluidos dos bStocks detectados después
y excluidos de la mesa cripto. Para los **14 instrumentos cripto** restantes:

| Variante | Media del retorno por cuenta independiente de 100 USDT | Cierres |
|---|---:|---:|
| Reglas nuevas, primeros 14 días | −1,91 % | 368 |
| Reglas nuevas, últimos siete días | −0,92 % | 144 |
| Reglas nuevas, últimos siete días, costes dobles | −0,50 % | 52 |
| Reglas anteriores adaptadas a los mismos 15 minutos, últimos siete días | −3,44 % | 850 |

**Las reglas nuevas no han demostrado rentabilidad.** La disminución de la pérdida
también refleja menor riesgo, menos operaciones y un plazo de permanencia distinto;
no identifica por separado una mejora de la señal. Con costes dobles se admiten
menos operaciones: su menor pérdida no implica que cobrar más comisiones mejore
las mismas operaciones. El tramo de evaluación es un diagnóstico reservado en
esta ejecución, no una certificación de validación del motor v4.

El replay entra en la apertura siguiente y usa el stop antes del objetivo cuando
ambos se tocan en una vela. Incluye costes y valora la posición final. No reproduce
latencia, profundidad histórica, selección histórica del universo, límite global
de cartera ni todas las salidas por pérdida de estructura. La selección con el
catálogo actual introduce sesgo de supervivencia. Las cuentas por símbolo no son
el resultado de una cartera ejecutando todos los activos simultáneamente. Los
datos originales se conservan localmente para repetir la evaluación.

Resultados detallados: [diagnóstico JSON](desk-crypto-review-2026-10-18.json).

## Verificación y observación

Pasan **264 pruebas automáticas**, incluida separación de cuentas, API antigua de
cripto, controles por mesa, recuperación de estado, espera tras pérdidas, fechas
de cotización, fin de semana, cambios de hora, sesión de acciones no corroborada,
acciones tokenizadas, recuperación de precio, costes y replay sin usar velas futuras.
El frontend compila y el análisis estático conserva tres advertencias anteriores.

Durante el funcionamiento se reprodujo y corrigió un fallo con `牛来USDT`: el
endpoint de libros devolvía HTTP 400 con el nombre escapado como secuencias JSON
ASCII; aceptaba HTTP 200 al enviarlo como Unicode UTF-8. La consulta de ejecución
y la vigilancia usan ahora esta codificación, con una prueba de regresión.
El fallo de ese mercado no interrumpía el análisis de los demás.

Se comprobaron visualmente las pestañas Cripto, Forex y Acciones. Forex y acciones
mostraron sus cuentas de 100 USD y quedaron esperando la apertura durante el
domingo. La simulación cripto se reactivó conservando el historial. Durante la
primera observación se cerraron las dos posiciones heredadas, AVA y GTC; el total
pasó a 31 cierres, siete ganadores y −7,52 USDT realizados. Estos cierres no son
resultado de las señales nuevas.

La observación posterior se guarda con fechas, cobertura, tiempo del escaneo,
posiciones y errores en `.runtime/review-20261018/monitor.jsonl`. Se conserva una
copia del libro previa a la actualización y las instantáneas iniciales de las
cuentas. El bot queda armado para entradas simuladas cuando se cumplan los filtros;
no se fuerza una compra para fabricar actividad.

En la vigilancia acotada de seis minutos se tomaron 18 muestras. La mesa se mantuvo
activa, sin error general del motor. En la comprobación final había **83 mercados
elegibles, 82 ya analizados recientemente**, un último escaneo de **3,401 segundos**,
**cero entradas nuevas** y **104,57 USDT de patrimonio**. No hubo una señal admitida
por todos los controles: por ejemplo, JST presentaba ruptura pero una extensión
de 2,8 ATR, superior al límite de 1,5 ATR. Tras desplegar la corrección Unicode,
`牛来USDT` pasó en el propio motor de error a espera por señal, con RSI 36 y
extensión −1,4 ATR. No se observó un cierre de una operación de la política nueva;
los seis minutos no permiten juzgar su rendimiento futuro.

La vigilancia de esta revisión termina aquí; las tres mesas siguen funcionando
en el servicio local. La ejecución 24/7 requiere que el equipo permanezca encendido
y conectado.

## Fuentes oficiales consultadas

- [Datos públicos de Binance](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md).
- [API Spot y filtros de instrumentos](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md).
- [Listado oficial de bStocks de junio](https://www.binance.com/es/support/announcement/detail/5646e3f9ea6b4c989cb76aa18bd99245).
- [Listado oficial que incluye el ETF tokenizado SOXLB](https://www.binance.com/en-NZ/support/announcement/detail/6a55706a042c4a7ebedc2a0899744088).
- [Horario y calendario NYSE](https://www.nyse.com/trade/hours-calendars).
- [Horario forex de referencia](https://www.oanda.com/us-en/trading/hours-of-operation/).
