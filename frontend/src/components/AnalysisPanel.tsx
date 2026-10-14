import type { Analysis } from '../types'

interface Props {
  analysis: Analysis | null
  loading: boolean
  error?: string | null
  onSourceClick: (source: string) => void
}

export default function AnalysisPanel({ analysis, loading, error, onSourceClick }: Props) {
  if (loading && !analysis) return <div className="panel">Cargando análisis…</div>
  if (!analysis) return <div className="panel" role="status">{error || "Sin datos de análisis."}</div>

  const { technical, sentiment, upProbability, verdict } = analysis
  const verdictClass =
    verdict === 'alcista' ? 'up' : verdict === 'bajista' ? 'down' : 'flat'

  return (
    <div className="panels">
      {error && <p role="status">{error} Se muestran los últimos datos recibidos.</p>}
      <div className="panel">
        <h3>Probabilidad combinada</h3>
        <div className={`verdict ${verdictClass}`}>
          {upProbability}% <span>de dirección alcista · {verdict}</span>
        </div>
        <div className="meter">
          <div className="meter-fill" style={{ width: `${upProbability}%` }} />
        </div>
        <p className="muted small">
          {sentiment.sampleSize > 0
            ? '60% análisis técnico + 40% opinión pública.'
            : 'Solo análisis técnico: opinión pública sin muestra disponible.'} No es asesoramiento financiero.
        </p>
      </div>

      <div className="panel">
        <h3>Análisis técnico</h3>
        <ul className="signals">
          {technical.signals.map((s) => (
            <li key={s.name}>
              <span className={s.vote > 0.15 ? 'up' : s.vote < -0.15 ? 'down' : 'flat'}>
                {s.vote > 0.15 ? '▲' : s.vote < -0.15 ? '▼' : '►'}
              </span>
              <strong>{s.name}</strong>
              <small className="muted">{s.value}</small>
            </li>
          ))}
        </ul>
      </div>

      <div className="panel">
        <h3>Opinión pública</h3>
        {sentiment.sampleSize > 0 && <>
        <div className="senti-bar">
          <div
            className="senti-bull"
            style={{ width: `${sentiment.bullishPct}%` }}
          />
        </div>
        <div className="senti-legend">
          <span className="up">▲ {sentiment.bullishPct}% alcista</span>
          <span className="down">{sentiment.bearishPct}% bajista ▼</span>
        </div>
        </>}
        <p>
          Sentimiento <strong>{sentiment.label}</strong>
          {sentiment.sampleSize > 0 && (
            <span className="muted"> · muestra: {sentiment.sampleSize}</span>
          )}
        </p>
        <ul className="sources">
          {sentiment.sources.map((s) =>
            s.hasItems ? (
              <li key={s.source}>
                <button
                  className="source-btn"
                  onClick={() => onSourceClick(s.source)}
                  title="Ver todas las opiniones de esta fuente"
                >
                  <strong>{s.source} ↗</strong>
                  <small className="muted">{s.detail}</small>
                </button>
              </li>
            ) : (
              <li key={s.source}>
                <strong>{s.source}</strong>
                <small className="muted">{s.detail}</small>
              </li>
            ),
          )}
          {sentiment.sources.length === 0 && (
            <li className="muted">Sin datos de opinión para este símbolo.</li>
          )}
        </ul>
        <p className="muted small">
          Haz clic en una fuente para ver cada opinión, su gráfico y sus votos.
        </p>
      </div>
    </div>
  )
}
