# Gemini en observación — 14 de octubre de 2026

El observador está conectado y funcionando con **Gemini 3.5 Flash-Lite**.
Se comprobó el acceso a los modelos con la clave proporcionada y se ejecutó
una clasificación real de doce textos de oro. El resultado y los contadores
persisten tras reiniciar el backend y aparecen en el panel.

## Modelo y alcance

Google recomienda [3.5 Flash-Lite para proyectos nuevos de procesamiento de
textos](https://ai.google.dev/gemini-api/docs/models). Se usa el identificador
fijo `gemini-3.5-flash-lite`, no un alias que cambie de modelo automáticamente.
La respuesta debe cumplir un esquema JSON y también se valida localmente.

El piloto observa **XAUUSD, BTC y ETH**. Analiza hasta doce textos recientes
por lote de las fuentes públicas ya integradas: StockTwits, Reddit,
TradingView y comunicados oficiales. Deduplica publicaciones, rechaza las
que carecen de fecha o tienen fecha futura y recorta el tamaño de entrada.
La fecha de la fuente no se confunde con la primera recepción local.

Devuelve relevancia, ruido, sesgo textual, confianza, tipo de catalizador
y explicación. El resumen pide español, aunque el proveedor puede responder
en otro idioma. No busca en Google ni en X por su cuenta y no recibe claves,
datos de cuenta, posiciones o instrucciones para enviar órdenes.

Está en **observación**: no cambia señales, tamaño, meta-modelo ni la mezcla
de probabilidad del panel. La confianza del clasificador no es probabilidad
de éxito de una operación. Los textos pueden contener instrucciones engañosas;
se tratan como datos y el modelo no tiene herramientas de ejecución.

## Uso y límites

La Mesa Siete comparte estos límites, reservas y contadores con el observador.
Su selector tiene prioridad cuando hay una elección pendiente. Gemini elige
un mercado elegible o efectivo; los otros seis roles funcionan con reglas
locales. La selección se renueva a las seis horas si la mesa está activa y
no hay posición abierta. El observador textual sigue sin alterar el motor
anterior; el selector sí participa en la nueva mesa experimental simulada.

- Máximo 48 intentos por 24 horas móviles y seis por hora.
- Como mínimo treinta minutos entre llamadas del piloto completo.
- Los tres mercados se turnan, priorizando los más pendientes. No significa
  una llamada por mercado cada treinta minutos.
- Hasta 2.048 tokens de salida; sin búsquedas externas ni herramientas de pago.
- Los mismos textos usan el resultado guardado, incluso tras reiniciar.
- Reserva y contadores en SQLite antes de la petición: los timeouts también
  cuentan. Una segunda instancia no puede eludir el intervalo global.
- Ante cuota, autenticación o fallo, conserva la observación anterior y espera
  al menos una hora. El panel indica cuando el resultado es antiguo.

Hay un límite local de **10 USD estimados por mes UTC**. Se reserva un coste
conservador antes de llamar y se ajusta cuando llegan los contadores de tokens.
Si la factura de una petición no se puede estimar, se conserva la reserva.
La estimación utiliza la [tarifa publicada](https://ai.google.dev/gemini-api/docs/pricing)
de 3.5 Flash-Lite: 0,30 USD/millón de tokens de entrada y
2,50 USD/millón de salida, incluyendo pensamiento en la estimación.

No se consultó el saldo, tipo de facturación ni los créditos de tu cuenta.
El límite local es una estimación y no sustituye el control de gasto de Google.
Cambiar el modelo requiere revisar también sus tarifas y este control.
No se contrató otro plan ni se activó facturación de Google.

## Configuración y arranque

La configuración real está en `.runtime/gemini.env`, fuera de Git y con permisos
de archivo limitados al usuario actual y SYSTEM. La clave se envía únicamente
en el encabezado de autenticación hacia la API oficial, no en una URL.
No aparece en el frontend, los endpoints ni los mensajes de error.
Las variables de entorno tienen prioridad sobre el archivo local.

El archivo `.env.example` documenta las opciones sin una clave real. Para
sustituir la credencial, cambia `GEMINI_API_KEY` en el archivo local y reinicia
el backend. No hace falta cambiar código. Como la clave se compartió en el
chat, es recomendable generar una nueva y sustituirla localmente.

`start-background.ps1` inicia backend y frontend sin ventanas y reutiliza
los procesos del proyecto ya activos. Se comprobó que ejecutarlo dos veces
mantiene el mismo proceso del backend. `start.ps1` además abre el navegador.

Se creó el acceso **Trading-Gemini-observer.lnk** en Inicio del usuario de
Windows. Ejecuta el inicio en segundo plano al iniciar sesión; no se ha probado
un reinicio de Windows. Para funcionar continuamente el PC debe seguir
encendido, conectado a internet y sin suspensión. El lector de datos MT5
requiere además el terminal conectado a la cuenta demo autorizada.

## Panel, historial y comprobaciones

La tarjeta «Gemini · análisis de textos» muestra sesgo, resumen, fecha,
publicaciones originales y motivos. Leer o refrescar el panel no hace llamadas
de generación: el trabajo lo realiza el observador del backend.

- `GET /api/agents/gemini`: estado, límites, uso estimado y observaciones.
- `GET /api/agents/gemini/XAUUSD`: resultado persistido del mercado.
- `.runtime/gemini.sqlite`: documentos, primeras recepciones, intentos y
  observaciones con versión del prompt, modelo y uso de tokens.

84 pruebas automáticas pasan y el frontend compila. Se comprueban deduplicación,
fechas, cuota global, recuperación tras reiniciar, coste estimado, rechazo de
respuestas inválidas, protección de la clave y ausencia de influencia en la
probabilidad actual. La prueba real y el panel confirman conexión funcional;
no demuestran mejora de rentabilidad ni calibración financiera del modelo.
