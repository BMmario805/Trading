# Motor v3 — 14 de octubre de 2026

Los cambios están aplicados y el servicio del panel se ha reiniciado. Sigue
siendo paper trading con un motor MT5 exclusivamente DEMO separado.

## Entradas y aprendizaje

- Los siete bots exigen parámetros validados de la versión actual.
- Un par rechazado, sin entrenar o vetado por rendimiento no abre posiciones
  nuevas en la cuenta principal, aunque el meta-modelo tenga pocas muestras.
- Se desactiva la exploración «a prueba». No se ha creado un libro shadow.
- Las posiciones anteriores se recuperan y siguen gestionándose con sus stops,
  trailing y límites. El reinicio no resetea balances ni borra operaciones.
- Motor de validación v3: los resultados v2 quedan visibles como historial,
  pero no autorizan entradas. MT5 aplica la misma comprobación de versión.
- El planificador considera pendientes los parámetros antiguos incluso si
  se habían entrenado recientemente. Mantiene el backoff por fallos de fuente.
- El meta-modelo empieza una generación v3 y aprende solo snapshots de esta
  versión; no mezcla automáticamente contextos de las estrategias antiguas.
  El historial de operaciones se conserva y hay copia de la base anterior.

## Señales y simulación

Scalper y swing sustituyen el promedio de numerosos indicadores por tres bloques:

| Bloque | Peso | Datos |
|---|---:|---|
| Régimen | 40% | ADX/Choppiness, EMA200 y marco superior cerrado |
| Disparador | 40% | Ruptura/pullback en tendencia; barrido/z-score en rango |
| Flujo | 20% | CMF; neutro cuando no hay volumen utilizable |

Sin disparador la señal es cero. Una confirmación no invierte su dirección.
Son familias de datos distintas, no factores cuya independencia estadística
esté demostrada. MT5 puede proporcionar volumen de ticks, no volumen negociado.
El detalle de la operación muestra los tres bloques realmente usados.

La distancia a EMA20/ATR se calcula causalmente y se filtra también en el
entrenamiento, la validación, ambos segmentos y la prueba con spread doble.
Los otros cinco bots mantienen su lógica específica, con la nueva validación.

Comparación BTC/swing sobre las mismas 8.759 velas de 1h:

| Versión | Resultado |
|---|---|
| V2 | PF de validación 1,09; PnL simulado +292,67; con spread doble +20,30 |
| V3 | Ninguna configuración rentable y con muestra suficiente en entrenamiento |

Por tanto, BTC/swing con v3 no se habilita. La comparación no demuestra una
mejora de rentabilidad. No se cambiaron pesos o umbrales después para forzar
un resultado positivo. Fechas y huella de los datos: [comparación](btc-swing-v3-comparison.json).
Esta evaluación independiente no modifica cuentas ni parámetros guardados.

## Oro, SQLite y repositorio

El panel y el entrenamiento priorizan las velas reales de XAUUSD del terminal
MT5 autorizado. Las lecturas se serializan con el motor para evitar llamadas
concurrentes al terminal. Se registra proveedor, instrumento y cuenta de origen.
La cuenta debe seguir siendo la DEMO configurada.

Sin terminal se conserva el proxy de GC/PAXG, que no valida el spot. Un par
validado con MT5 no entra con velas o cotizaciones de otra fuente. Se comprobó
en el servicio activo que el endpoint de XAUUSD devuelve velas de MT5.

Cada conexión SQLite configura WAL y busy_timeout de cinco segundos y conserva
el cierre transaccional. Se comprobó WAL en la base activa. Las copias están en
`.runtime/before-20261014-v3-data.db` y
`.runtime/before-20261014-v3-mt5-demo.sqlite`.

Se retiraron del índice Git `backend/data.db` y `bodycam-mods/`, sin borrar la
base local. Se ignoran bases, archivos auxiliares, esa carpeta y secretos `.env`.
Las eliminaciones del índice están preparadas; no se ha creado un commit.

## Comprobaciones y límites

70 pruebas automáticas pasan. Incluyen causalidad de señales, ejecución y gaps,
bloqueo sin validación, vetos, sobreextensión direccional, convivencia de lector
y escritor en WAL, preferencia/fallback de MT5 y bloqueo de mezcla de fuentes.
Se verificó el servicio activo, sus diagnósticos, conexión DEMO y autoentrenamiento.

El simulador todavía no reproduce todos los filtros de cartera del vivo ni
tasas históricas de financiación/swaps. No se inventaron tasas para rellenarlos.
Los resultados multidiarios requieren esa ampliación antes de interpretarlos
como rentabilidad neta completa. Los datos de sentimiento permanecen como
contexto; la nueva integración con agentes está pendiente y no hace llamadas
de pago. Los objetivos de «eliminar el 80% de pérdidas» o «validar más del 25%»
del texto adjunto no se han demostrado.
