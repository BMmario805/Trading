import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchAnalysis, fetchSymbols, PriceSocket } from './api'
import Watchlist, { formatPrice } from './components/Watchlist'
import ChartPanel from './components/ChartPanel'
import AnalysisPanel from './components/AnalysisPanel'
import GeminiPanel from './components/GeminiPanel'
import DeskPanel from './components/DeskPanel'
import SentimentHistory from './components/SentimentHistory'
import OpinionsModal from './components/OpinionsModal'
import BotsPanel from './components/BotsPanel'
import MetaTraderPanel from './components/MetaTraderPanel'
import {
  INTERVALS,
  type Analysis,
  type Interval,
  type SymbolInfo,
  type Tick,
} from './types'

const ANALYSIS_REFRESH_MS = 60_000

export default function App() {
  const [view, setView] = useState<'desk' | 'markets'>('desk')
  const [symbols, setSymbols] = useState<SymbolInfo[]>([])
  const [selected, setSelected] = useState('XAUUSD')
  const [interval, setInterval_] = useState<Interval>('1h')
  const [prices, setPrices] = useState<Record<string, Tick>>({})
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [opinionsSource, setOpinionsSource] = useState<string | null>(null)
  const [loadingAnalysis, setLoadingAnalysis] = useState(false)
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const socketRef = useRef<PriceSocket | null>(null)
  if (!socketRef.current) socketRef.current = new PriceSocket()
  const socket = socketRef.current

  useEffect(() => {
    socket.open()
    return () => socket.close()
  }, [socket])

  useEffect(() => {
    fetchSymbols()
      .then((list) => {
        setSymbols(list)
        for (const s of list) socket.subscribe(s.id)
      })
      .catch(() =>
        setError(
          'No se puede conectar con el backend. Arranca la API en el puerto 8000.',
        ),
      )
  }, [socket])

  useEffect(
    () =>
      socket.onTick((t) => {
        setPrices((prev) => ({ ...prev, [t.symbol]: t }))
      }),
    [socket],
  )

  useEffect(() => {
    let cancelled = false
    const load = () => {
      setLoadingAnalysis(true)
      fetchAnalysis(selected, interval)
        .then((a) => {
          if (!cancelled) { setAnalysis(a); setAnalysisError(null) }
        })
        .catch(() => {
          if (!cancelled) setAnalysisError('No se ha podido actualizar el análisis. Se reintentará automáticamente.')
        })
        .finally(() => {
          if (!cancelled) setLoadingAnalysis(false)
        })
    }
    setAnalysis(null)
    setAnalysisError(null)
    load()
    const id = window.setInterval(load, ANALYSIS_REFRESH_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [selected, interval])

  useEffect(() => setOpinionsSource(null), [selected])

  useEffect(() => {
    document.title = view === 'desk' ? 'Mesa Siete · La cocina del mercado' : 'Mercados y laboratorio · Mesa Siete'
  }, [view])

  const current = useMemo(
    () => symbols.find((s) => s.id === selected),
    [symbols, selected],
  )
  const tick = prices[selected]

  return (
    <div className={`layout app-${view}`}>
      <nav className="workspace-switch" aria-label="Vista del proyecto">
        <button className={view === 'desk' ? 'active' : ''} aria-pressed={view === 'desk'} onClick={() => setView('desk')}><span aria-hidden="true">♨</span> Mesa Siete</button>
        <button className={view === 'markets' ? 'active' : ''} aria-pressed={view === 'markets'} onClick={() => setView('markets')}>Mercados y laboratorio</button>
        {view === 'desk' && <span className="workspace-kitchen-tag">LA COCINA DEL MERCADO <i aria-hidden="true" /></span>}
      </nav>
      {view === 'desk' ? <DeskPanel /> : <>
      <Watchlist
        symbols={symbols}
        prices={prices}
        selected={selected}
        onSelect={setSelected}
      />
      <main className="main">
        {error && <div className="error">{error}</div>}
        <header className="header">
          <div className="title">
            <h2>
              {current?.label ?? selected}
              <span className="muted"> · {selected}</span>
            </h2>
            {current?.note && <p className="note muted">{current.note}</p>}
            {tick && (
              <div className={`big-price ${tick.changePct >= 0 ? 'up' : 'down'}`}>
                {formatPrice(tick.price)}
                <small>
                  {tick.changePct >= 0 ? '▲' : '▼'}{' '}
                  {Math.abs(tick.changePct).toFixed(2)}% hoy
                </small>
              </div>
            )}
          </div>
          <nav className="intervals">
            {INTERVALS.map((iv) => (
              <button
                key={iv}
                className={iv === interval ? 'active' : ''}
                onClick={() => setInterval_(iv)}
              >
                {iv}
              </button>
            ))}
          </nav>
        </header>
        <ChartPanel symbol={selected} interval={interval} socket={socket} />
        <AnalysisPanel
          analysis={analysis}
          loading={loadingAnalysis}
          error={analysisError}
          onSourceClick={setOpinionsSource}
        />
        <SentimentHistory symbol={selected} />
        <GeminiPanel symbol={selected} />
        <details className="panel" id="laboratorio-paper" open>
          <summary>Laboratorio de paper trading · simulación independiente de MT5</summary>
          <BotsPanel />
        </details>
        <MetaTraderPanel />
        {opinionsSource && (
          <OpinionsModal
            symbol={selected}
            initialSource={opinionsSource}
            onClose={() => setOpinionsSource(null)}
          />
        )}
      </main>
      </>}
    </div>
  )
}
