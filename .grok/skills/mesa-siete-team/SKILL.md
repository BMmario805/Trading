---
name: mesa-siete-team
description: >
  Equipo Mesa Siete: verificador de noticias, investigador cuantitativo y
  revisor técnico, encima del radar grok-x-pulse y del commander. Úsalo con
  /mesa-siete-team, al dudar de un titular, al plantear una hipótesis
  falsable o al revisar un fallo demostrable. No activa subagentes en el
  puente de ejecución.
---

# Equipo Mesa Siete

El radar (`grok-x-pulse`, lote de hasta 5 símbolos) y el commander (`grok-desk-commander`) siguen siendo el ciclo de mercado. Estos tres perfiles no los sustituyen.

Los perfiles no son bots desktop conectados automáticamente. El contrato del
flujo, los recibos, la correlación y el health están en
`.grok/WORKFLOW_CONTRACT.md`. Completar un validador local no acredita recepción
por Grok Desktop, ejecución de un experimento ni beneficio. Usa un challenge
por rol y un recibo con nonce/hash para acreditar cada recepción de escritorio.

## Cuándo activar

- Verificador (`.grok/agents/news-verifier.md`): hay incertidumbre en el radar o el commander discrepa del titular. Si `pulse_cache` tiene menos de 45 minutos, no busques otra vez.
- Investigador (`.grok/agents/quant-researcher.md`): hay una hipótesis con métrica, universo, horizonte y dirección. Una idea vaga no lo activa.
- Revisor (`.grok/agents/technical-reviewer.md`): una prueba ya falla y el mensaje está a la vista. Sin eso, no edites.

## Cupo

El proceso local admite 4 verificaciones o hipótesis nuevas por día UTC en `specialist_cache`. Un acierto de caché cuenta 0 búsquedas. El revisor no gasta ese cupo. No hay fallback a una API de pago.

## Aislamiento

No invoques estos perfiles desde `subscription_bridge.py`. Ese puente permanece con `--no-subagents`, `--permission-mode dontAsk` y sin variables `*_API_KEY`. El modo por defecto sigue en sombra y desarmado. Ningún perfil autoriza una orden ni mueve un stop.
