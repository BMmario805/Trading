# Perfil agresivo ampliado — 21/10/2026

**Activado en las mesas simuladas de Cripto, Forex y Acciones y en los siete bots del laboratorio.** Identificador: `aggressive-capacity-v4`. Es una ampliación efectiva de capacidad y tamaño; no una validación de rentabilidad.

| Ajuste | Anterior | Activo |
|---|---:|---:|
| Posiciones máximas en cada mesa | 4 | 6 |
| Capital máximo por entrada en cada mesa | 25% | 35% |
| Riesgo objetivo por entrada en cada mesa | 0,75% | 1,5% |
| Riesgo abierto máximo por mesa | 3% | 6% |
| Ciclo de búsqueda de las mesas | 20 s | 15 s |
| Tamaño a prueba del laboratorio | 25% del riesgo habitual | 50% del riesgo habitual |
| Posiciones a prueba por bot del laboratorio | 2 | 4 |

El laboratorio puede asignar el doble del presupuesto anterior a una nueva entrada a prueba. Con sus riesgos base actuales, eso equivale a un máximo de 0,5% del patrimonio en Scalper y ORB; 1% en Swing y Tendencia; 0,75% en Reversión y Squeeze; y 0,25% en Flujo. Las reducciones por racha, aprendizaje, riesgo abierto y margen pueden reducir esas cantidades.

En las mesas, 35% es el máximo de una entrada, no la asignación simultánea a cada una de las seis posiciones: todas comparten efectivo. No se permite saldo negativo ni apalancamiento en esas mesas. La volatilidad, los lotes y los mínimos pueden hacer que el riesgo real sea menor que el objetivo del 1,5%.

Los límites globales siguen aplicándose además de los locales: riesgo conjunto 3% del patrimonio agregado de las cuentas, riesgo por factor 1,5%, exposición bruta 2× y concentración por factor 0,75×. También siguen activos el límite diario de las mesas del 5%, los límites diarios de cada bot, las cotizaciones frescas, las comisiones y los vetos por pérdidas. Por tanto, aumentar un límite local no garantiza que todas las órdenes propuestas sean admitidas.

Se conservan las señales, stops, objetivos y reglas de salida del perfil anterior. Las posiciones anteriores mantienen sus condiciones guardadas. Las entradas nuevas registran el identificador de capacidad; las mesas separan su seguimiento mediante `desk-aggressive-1h-v7` y `desk-cash-aggressive-v2`.

MT5 conserva su configuración DEMO anterior, separada del laboratorio: riesgo base 0,5%, riesgo a prueba 0,125% y dos posiciones a prueba por bot. Este ajuste solicitado a las mesas y el laboratorio no cambia esa cuenta.

## Comprobación en funcionamiento

- 383 pruebas superadas, incluidas las comprobaciones de aumento real del tamaño, efectivo, riesgo restante, cuatro cupos a prueba y conservación tras reinicio.
- Interfaz compilada; lint sin errores, con tres avisos anteriores.
- Las API de las tres mesas confirman 6 posiciones, 35%, 1,5% por entrada y 6% agregado; las de los siete bots confirman 50% y cuatro posiciones a prueba.
- Cripto abrió una posición con el perfil nuevo y pasó de cuatro a cinco posiciones en la primera comprobación. Los bots Scalper y Swing ya registran entradas con la nueva capacidad. No se fuerzan operaciones en mercados sin señal.
- Todos los identificadores de operaciones previos siguen presentes. Se conservan capital inicial y fecha de sesión; los modelos siguen aprendiendo de cierres nuevos, sin reiniciarse.
- Copia previa: `.runtime/capacity-v4-backup-20261021T164825Z/`. Estados antes y después: `.runtime/capacity-v4-before.json` y `.runtime/capacity-v4-after.json`.
- Captura de la interfaz: `.runtime/perfil-agresivo-ampliado.png`.

Este cambio aumenta la exposición posible y el riesgo de pérdida. No demuestra más beneficio futuro ni convierte el objetivo de duplicar o triplicar diariamente en un resultado esperable. La prueba extrema anterior conserva sus resultados y no se ha utilizado para validar este perfil.
