# Agentes: qué contratar y cómo mantenerlos — 14 de octubre de 2026

> **Actualización:** Gemini 3.5 Flash-Lite ya está integrado y activo como
> clasificador en observación. [Configuración y límites](GEMINI.md).
> La recomendación vigente es validar primero el motor; Jev y Grok quedan
> como alternativas pendientes, sin contratar ni activar.

Mi recomendación es un piloto de análisis de textos: Jev como clasificador y
Grok como fuente puntual de noticias/publicaciones en X. El código mantiene
el control de entradas, tamaño, stop y objetivo. Ninguno ha demostrado una
ventaja rentable en este proyecto. Todavía no están integrados ni activos.

## Alta

1. **Jev:** crea una cuenta en [la consola oficial de TypeSafe](https://console.typesafe.ai),
   configura la facturación que te ofrezca y genera una clave de API.
   La [guía oficial](https://docs.typesafe.ai/introduction/quickstart) explica el alta
   y la variable `TYPESAFE_API_KEY`. Para reproducibilidad, fijaría una versión
   evaluada en lugar de cambiar automáticamente a la última.
2. **Grok:** crea una cuenta en [la consola de xAI](https://console.x.ai),
   carga créditos de API y genera una clave `XAI_API_KEY`, conforme a su
   [guía de inicio](https://docs.x.ai/developers/quickstart).
3. Las claves se configuran localmente en el servidor como secretos o variables
   de entorno. No es necesario pegarlas en el chat ni guardarlas en Git.
   Comprar acceso no activa por sí solo los agentes: falta conectar y probar
   sus APIs en este proyecto.

## Costes y capacidades comprobados

- [Jev 1.13](https://docs.typesafe.ai/models): 0,042 USD por millón de tokens
  de entrada y salida sin cargo. Diez millones de tokens equivalen a 0,42 USD
  de procesamiento a esa tarifa; no representa toda la operación.
- [Grok X Search](https://docs.x.ai/developers/tools/x-search): 5 USD por mil
  publicaciones recuperadas y 10 USD por mil perfiles, más tokens del modelo.
  No equivale a disponer de todo el flujo de X en tiempo real.
- Ejemplo calculado: dos consultas al día, cada una con veinte publicaciones,
  durante treinta días suman 1.200 publicaciones: 6 USD solo de búsqueda,
  más tokens, perfiles y otras herramientas si se usan. Las consultas pueden
  recuperar más publicaciones de las previstas, incluidas respuestas y citas.

No doy por válida la previsión de 5–15 USD/mes del texto adjunto. El coste debe
medirse y limitarse en la integración. Recomiendo empezar con un presupuesto
de prueba elegido por ti, caché, consultas por eventos y un límite que deje
de hacer llamadas al agotarlo. La documentación consultada está enlazada;
los proveedores pueden cambiar tarifas y condiciones.

## Funcionamiento continuo

La integración pendiente debe recopilar textos, deduplicarlos, clasificarlos,
guardar fecha de publicación y de recepción, versión de modelo, respuesta,
fuentes y coste; después se podrá comprobar causalmente si esas variables
mejoran el meta-modelo. Primero funcionaría en observación, sin influir en las
órdenes. La confianza del modelo no demuestra probabilidad calibrada de ganar
una operación en este mercado.

Para tenerlo 24/7 necesitas internet, el servidor ejecutándose y Windows
encendido sin suspensión. Para usar las velas del bróker, MT5 debe permanecer
abierto y conectado a la cuenta DEMO autorizada. Deben añadirse arranque
automático y recuperación tras fallos cuando se active el piloto. El backend
actual está funcionando, pero no se ha instalado un servicio de arranque
automático tras reiniciar Windows.

Un equipo Windows remoto con MT5 es una alternativa si no quieres mantener
este PC encendido; no se ha contratado alojamiento. Usar las APIs alojadas
de Jev/Grok no requiere una GPU local. No recomiendo contratar un paquete de
«bot rentable» ni asumir que `imajev-4b` tiene las mismas capacidades que el
servicio oficial sin verificar su origen, licencia y resultados por separado.
