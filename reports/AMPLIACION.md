# Ampliación de mercados y autoentrenamiento

## Qué se añadió

- Diez criptomonedas con pares USDT: BNB, ADA, LINK, AVAX, LTC, BCH, DOT, NEAR, SUI y TRX.
  Todas fueron verificadas en Binance como TRADING y habilitadas para spot.
- Swing, Tendencia y Squeeze incorporan las diez; Scalper incorpora las cinco primeras.
  Sus topes de posiciones y riesgo no aumentan. Las entradas rotan entre mercados para
  no dar siempre prioridad a los primeros de la lista, y se atienden antes las salidas.
- Séptimo bot: Flujo (CMF), sobre velas de 15 minutos y tendencia de 1 hora. Combina
  Chaikin Money Flow de 20 velas, EMA20/50, pendiente, volumen relativo y rupturas o
  recuperación de la EMA20. Usa riesgo base del 0,5% y máximo cuatro posiciones.
  Solo permite entradas en instrumentos con parámetros validados. Los bots anteriores
  conservan su modo exploratorio reducido para mercados sin validación.
- Fuente adicional de contexto: comunicados oficiales de política monetaria de la
  Reserva Federal. Aparecen en opiniones y confirmaciones, con enlaces y fechas,
  sin asignarles una dirección de precio inventada. Se descartan duplicados,
  fechas futuras y noticias de más de 30 días. Son noticias publicadas, no un calendario
  anticipado de eventos ni un modelo de sentimiento sobre cada activo.
- Fuente de ejecución adicional: estado del mercado, mejor bid/ask y volumen de 24 horas
  de Binance. Antes de abrir, los bots exigen mercado activo, al menos 10 millones USDT
  negociados y spread no superior al 0,1%, ni al spread estimado por su simulador.
  Estos límites son heurísticos; volumen negociado no equivale a profundidad ejecutable.
  La ausencia de esta fuente bloquea la nueva entrada, pero no la gestión de posiciones.
- Endpoint de diagnóstico: GET /api/markets/quality.

## Autoentrenamiento

El planificador se inicia con el backend, espera 20 segundos y revisa pendientes cada
cinco minutos. Detecta instrumentos nuevos, validaciones antiguas y parámetros de otra
versión del motor. Procesa lotes de tres instrumentos, con máximo dos entrenamientos
simultáneos. Los intentos se guardan en SQLite; un fallo de fuente introduce al menos
una hora de espera. El entrenamiento manual desde el panel sigue disponible.

La cadencia objetivo es cuatro horas para Scalper, seis para ORB/Squeeze/Flujo y doce
para Swing/Tendencia/Reversión. La cola puede retrasar estas cadencias. El aprendizaje
por operaciones cerradas sigue activo. El panel muestra cuántos mercados están pendientes.
Los parámetros nuevos solo se aplican cuando superan los filtros de validación existentes.

Se comparten peticiones de velas durante diez segundos entre bots y consultas simultáneas
de sentimiento por instrumento. Cada fuente de opinión dispone de un máximo de 25 segundos.
La Reserva Federal y los datos del libro tienen sus propias cachés compartidas.

## Comprobaciones

- 26 pruebas de regresión superadas y frontend compilado.
- Consulta real de los diez pares, spreads y volúmenes: expansion-sources.json.
  Nueve pasaron el filtro genérico de volumen/spread en esa instantánea; BCH no.
  DOT además excedía el spread del simulador y no habría pasado la entrada del bot.
- Feed de la Reserva Federal: dos comunicados recientes recibidos en la comprobación.
- Flujo/BTC y Flujo/LINK: históricos de 8.639 velas cerradas de 15m por instrumento.
  Ninguna configuración cumplió rentabilidad y muestra mínima en entrenamiento.
  No se validaron ni aplicaron parámetros en estas evaluaciones (JSON flow-*-validation).
  No se reajustó la estrategia a posteriori para hacer que estas muestras parezcan rentables.

El sistema sigue siendo paper trading. Ampliar mercados y automatizar la evaluación no
acredita beneficios más rápidos. La simulación mantiene las limitaciones descritas en
AUDITORIA.md, especialmente costes aproximados, filtros en vivo adicionales y falta de
réplica completa de la cartera. El nuevo bot esperará validación por mercado.

## Fuentes

- Binance, endpoints públicos sin claves: https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md
- Reserva Federal, catálogo oficial de RSS: https://www.federalreserve.gov/feeds/feeds.htm
- Feed utilizado: https://www.federalreserve.gov/feeds/press_monetary.xml

## Activación comprobada

Backend reiniciado en el puerto 8000. GET /api/bots devuelve siete bots activos:
Scalper 15 mercados, Swing 29, Tendencia 24, Reversión 12, ORB 10, Squeeze 20 y
Flujo 14. Se comprobó que el autoentrenamiento de Flujo se inició sin pulsar Entrenar
(progreso 2/3, procesando AVAX). GET /api/markets/quality respondió con 16 mercados.
El frontend existente permanece en el puerto 5173.

Antes del reinicio se guardó una copia coherente de SQLite en
.runtime/before-expansion-20260909-094431.db. La carpeta .runtime está excluida de Git.
No se borró ni reinició el historial de los bots existentes. El proceso actualizado
continúa gestionando sus operaciones simuladas y guardando nuevos entrenamientos.
