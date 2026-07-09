import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchAnalysis, fetchSymbols, PriceSocket } from './api'
import Watchlist, { formatPrice } from './components/Watchlist'
import ChartPanel from './components/ChartPanel'
import AnalysisPanel from './components/AnalysisPanel'
import SentimentHistory from './components/SentimentHistory'
import OpinionsModal from './components/OpinionsModal'
import BotsPanel from './components/BotsPanel'
import {
  INTERVALS,
  type Analysis,
  type Interval,
  type SymbolInfo,
  type Tick,
} from './types'

const ANALYSIS_REFRESH_MS = 60_000

export default function App() {
  const [symbols, setSymbols] = useState<SymbolInfo[]>([])
  const [selected, setSelected] = useState('XAUUSD')
  const [interval, setInterval_] = useState<Interval>('1h')
  const [prices, setPrices] = useState<Record<string, Tick>>({})
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [opinionsSource, setOpinionsSource] = useState<string | null>(null)
  const [loadingAnalysis, setLoadingAnalysis] = useState(false)
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
          if (!cancelled) setAnalysis(a)
        })
        .catch((e) => console.error('analysis', e))
        .finally(() => {
          if (!cancelled) setLoadingAnalysis(false)
        })
    }
    setAnalysis(null)
    load()
    const id = window.setInterval(load, ANALYSIS_REFRESH_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [selected, interval])

  useEffect(() => setOpinionsSource(null), [selected])

  const current = useMemo(
    () => symbols.find((s) => s.id === selected),
    [symbols, selected],
  )
  const tick = prices[selected]

  return (
    <div className="layout">
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
          onSourceClick={setOpinionsSource}
        />
        <SentimentHistory symbol={selected} />
        <BotsPanel />
        {opinionsSource && (
          <OpinionsModal
            symbol={selected}
            initialSource={opinionsSource}
            onClose={() => setOpinionsSource(null)}
          />
        )}
      </main>
    </div>
  )
}
