import { useEffect, useState } from 'react'

interface Item {
  id: string
  source: string
  text: string
  url: string
  direction: number
  confidence: number
  relevant: boolean
  is_noise: boolean
  explanation: string
}

interface Observation {
  configured: boolean
  tracked: boolean
  message: string
  model: string
  stale: boolean
  data: null | {
    summary: string
    label: string
    sampleSize: number
    totalItems: number
    analyzedAt: number
    items: Item[]
  }
}

export default function GeminiPanel({ symbol }: { symbol: string }) {
  const [observation, setObservation] = useState<Observation | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    let cancelled = false
    let pending = false
    setObservation(null)
    setError(false)
    const load = async () => {
      if (pending) return
      pending = true
      try {
        const response = await fetch(`/api/agents/gemini/${encodeURIComponent(symbol)}`)
        if (!response.ok) throw new Error('unavailable')
        const value: Observation = await response.json()
        if (!cancelled) { setObservation(value); setError(false) }
      } catch {
        if (!cancelled) setError(true)
      } finally { pending = false }
    }
    void load()
    const timer = window.setInterval(() => { void load() }, 30_000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [symbol])

  const data = observation?.data
  const relevant = data?.items.filter(item => item.relevant && !item.is_noise) ?? []
  return (
    <section className="panel gemini-panel" aria-label="Análisis de textos con Gemini">
      <h3>Gemini · análisis de textos</h3>
      <p className="muted small">Clasifica textos y no envía órdenes. Sus clasificaciones pueden activar un veto de entrada ante catalizadores recientes.</p>
      {error && <p role="status">No se pudo actualizar Gemini. Se reintentará automáticamente.</p>}
      {!observation && !error && <p className="muted">Cargando estado…</p>}
      {observation && !data && <p role="status">{
        !observation.configured ? 'Gemini no está configurado.' : !observation.tracked
          ? 'El piloto observa oro, BTC y ETH. Este mercado queda fuera por ahora.'
          : observation.message
      }</p>}
      {data && <>
        <p><strong className={data.label === 'alcista' ? 'up' : data.label === 'bajista' ? 'down' : 'flat'}>
          Sesgo textual {data.label}
        </strong> · {data.sampleSize} textos relevantes de {data.totalItems}</p>
        <p>{data.summary}</p>
        <p className="muted small">
          Actualizado {new Date(data.analyzedAt * 1000).toLocaleString('es-ES')}
          {observation?.stale && ' · se conserva el último análisis disponible'}
        </p>
        <details>
          <summary>Ver textos y motivos de la clasificación</summary>
          <ul className="gemini-items">
            {relevant.map(item => <li key={item.id}>
              <strong>{item.source}</strong>
              <p>{item.text}</p>
              <p className="muted small">{item.explanation} · confianza en la etiqueta: {Math.round(item.confidence * 100)}%</p>
              {/^https?:\/\//i.test(item.url) && <a href={item.url} target="_blank" rel="noreferrer">Ver publicación original ↗</a>}
            </li>)}
            {relevant.length === 0 && <li>Los textos disponibles se clasificaron como ruido o irrelevantes.</li>}
          </ul>
        </details>
      </>}
      <p className="muted small">La confianza expresa certeza sobre el texto; no es una probabilidad de beneficio.</p>
    </section>
  )
}
