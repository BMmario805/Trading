# Conexión MetaTrader 5 — solo DEMO

> La descripción de conexión inicial que sigue es histórica. Desde el 21 de
> octubre la sesión EUR tiene una aportación virtual hasta 1.000 EUR, separada
> del saldo del bróker y del beneficio. [Cambios y límites actuales](CORRECCIONES_2026-10-21.md#mt5-capital-virtual-y-lotes).

La integración está instalada y conectada a MetaQuotes-Demo, con cuenta en EUR y
100.000 EUR de saldo en la comprobación inicial. La aplicación detectó Algo Trading
desactivado y no envió ninguna orden. El backend tiene configuradas las entradas;
al activar Algo Trading en el terminal empezará a evaluar y enviar las que cumplan
sus filtros. No se garantiza una entrada inmediata.

## Qué hacer en el ordenador y el móvil

1. En MetaTrader 5 del ordenador, activar el botón Algo Trading de la barra superior.
2. En Herramientas > Opciones > Asesores Expertos, mantener permitido el trading
   algorítmico. La opción que deshabilita la negociación a través de Python debe
   permanecer desmarcada (ya se comprobó que la API Python estaba permitida).
3. En el panel web, revisar la sección MetaTrader 5 · Cuenta DEMO compartida.
   Mostrará conexión, balance, patrimonio, posiciones y motivo de espera por bot.
4. En MT5 del móvil, usar la misma cuenta y servidor MetaQuotes-Demo; las posiciones
   se ven en Operaciones y los cierres en Historial. No se necesita publicar localhost.
5. Mantener abiertos MT5 y el backend en el ordenador. Sin el ordenador no hay nuevas
   entradas ni trailing actualizado; SL/TP que haya aceptado el servidor permanecen allí.

## Instrumentos y estrategias

Configurados: XAUUSD, EURUSD, GBPUSD, USDJPY, AAPL, MSFT, NVDA, TSLA, AMZN, META y GOOGL.
Se verificaron sus nombres y descripciones en el terminal. Cada bot usa únicamente la
intersección de estos instrumentos con su universo. Flujo queda sin instrumentos demo
compatibles en esta configuración porque su universo es cripto. El paper trading de los
siete bots se mantiene independiente y no se copia automáticamente a MT5.

Se excluyeron BTC y ETH porque en este servidor son ETF, y SOL porque es una acción de
Emeren, no Solana. SPY y QQQ estaban deshabilitados para trading. Las acciones solo podrán
operar cuando el bróker tenga mercado abierto y cotizaciones recientes.

El motor demo utiliza las señales de strategy.py con velas cerradas y precios de MT5.
El tamaño se calcula en lotes del bróker, usando order_calc_profit para convertir el
riesgo a EUR, y order_calc_margin para comprobar margen. Envía SL/TP junto a la entrada.
Las salidas utilizan stops, take-profit, trailing y duración máxima. No replica todas
las reglas ni el meta-modelo del paper trading: son historiales distintos.

## Límites iniciales compartidos

- Riesgo base: 0,1% del patrimonio por operación, con reserva del 10% para fricción.
- Riesgo agregado hasta los stops: máximo 1% del patrimonio de la cuenta.
- Máximo tres posiciones en toda la cuenta, incluyendo posiciones manuales.
- No abre otra posición si ya existe una del mismo instrumento.
- Bloquea entradas si hay posiciones sin stop o riesgo/margen desconocido.
- Pausa entradas al perder un 2% de patrimonio desde la primera observación del día UTC.
- Cooldown de cinco minutos por instrumento después de detectar un cierre.

Los gaps pueden exceder los límites estimados. Los costes del bróker no se conocen
perfectamente antes de ejecutar. Estos ajustes iniciales sirven para probar la integración.

## Cuenta, persistencia y control

Cada envío vuelve a comprobar que sea la cuenta DEMO y servidor configurados, con modo
hedging. Una cuenta real, una cuenta diferente o permisos deshabilitados bloquean envíos.
No se guardan contraseñas: se usa la sesión abierta de MT5.

Los magic numbers 9260900–9260906 identifican estrategias y los comentarios cx-bot-fecha
identifican entradas. Solo se gestionan posiciones de estos identificadores; los límites
compartidos también consideran posiciones manuales. No utilizar esos magic numbers en
otros robots de la misma cuenta.

Las intenciones se guardan antes del envío para evitar duplicaciones. Una respuesta
incierta bloquea entradas posteriores de ese símbolo: requiere revisar posiciones e
historial en el terminal y reconciliar el registro antes de reanudar; no se reenvía a ciegas.
Las posiciones de los bots se recuperan desde MT5 al reiniciar. Los stops iniciales y el
trailing se conservan en el registro local. No borrar .runtime mientras haya operaciones.

Configuración local: .runtime/mt5-demo.json. Registro: .runtime/mt5-demo.sqlite.
Ambos están fuera de Git. El botón Pausar entradas en MT5 mantiene la gestión de posiciones.
Los botones de los bots de paper trading no controlan la cuenta MT5, ni sus resets la borran.

## Autoentrenamiento específico del bróker

Evalúa hasta 5.000 velas por combinación, una tarea a la vez, iniciando como máximo una
cada cinco minutos y reentrenando cada combinación después de seis horas. Usa los mismos
filtros de validación reforzada. Las calibraciones de Binance/Yahoo no se importan a MT5.
Si aún no hay validación, los bots compatibles exploran en DEMO con parámetros base y el
riesgo reducido de esta cuenta. El backtest sigue siendo una aproximación por velas y no
reproduce lotes, comisiones, margen o restricciones del bróker con exactitud; su resultado
no acredita rentabilidad real. El volumen puede ser volumen de ticks cuando no hay volumen real.

## Verificación

45 pruebas pasan, incluidas cuenta real bloqueada, cambio de cuenta después de comprobar
una orden, permisos, lotes mínimos, presupuesto de riesgo, datos antiguos, posiciones
manuales y prevención de duplicados después de reiniciar. Frontend compilado.
GET /api/mt5 a través del frontend respondió conectado a demo, con 100.000 EUR y cero
posiciones. No se probó una ejecución de orden en el servidor: Algo Trading seguía apagado.

Documentación oficial: https://www.mql5.com/en/docs/python_metatrader5

## Sincronización de rendimiento (9 septiembre 2026)

El dashboard usa ahora GET /api/mt5/performance para el rendimiento general y por bot.
Importa ejecuciones por ticket sin duplicados y registra balance/patrimonio cada diez
segundos en el diario local, aislado por servidor y cuenta. El patrimonio histórico se
recoge desde esta activación; no se inventa la evolución anterior. Las ejecuciones
conservan las fechas proporcionadas por el servidor. La consulta incluye un día de
margen superior para servidores cuyo reloj está adelantado respecto al equipo.

El resultado incluye beneficio, comisión, swap y fee. Depósitos, retiradas, créditos y
bonus no se cuentan como ganancias. La apertura identifica al bot incluso si se cierra
desde el móvil con magic cero; los cierres parciales no duplican posiciones cerradas.
Las operaciones manuales y ajustes se muestran separados para reconciliar la cuenta.
Las tarjetas incluyen resultado contabilizado y flotante; la curva por bot representa
el resultado neto contabilizado. El laboratorio paper queda plegado e independiente.

Verificación actual: 55 pruebas unitarias y compilación frontend correctas. API de la
demo devuelve balance y patrimonio 100.000 EUR, aportaciones 100.000 EUR, resultado cero
y diferencia de conciliación cero. Sin órdenes forzadas para probar las gráficas.
La inspección visual automatizada no estuvo disponible por un fallo del navegador de
automatización; se verificaron la compilación y los datos mediante el proxy del frontend.
