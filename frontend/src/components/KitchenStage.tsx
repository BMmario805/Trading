import { useEffect, useId, useState } from 'react'
import type { CSSProperties } from 'react'
import './KitchenStage.css'

export interface KitchenRole {
  name: string
  status: string
  detail: string
  provider: string
  at?: number
}

interface KitchenStageProps {
  bubbles?: Record<string, { text: string; at: number }>
  roles: KitchenRole[]
  selectedSymbol: string | null
  hasPosition: boolean
  enabled: boolean
  openRole: string
  onSelectRole: (name: string) => void
  motionEnabled: boolean
}

const STATIONS = [
  { name: 'Selector', label: 'La despensa', task: 'Escoge el mercado', color: '#dea64d', accent: '#f7df96', index: '01' },
  { name: 'Tape', label: 'El corte', task: 'Lee la tendencia', color: '#8aaa8a', accent: '#d8e6b7', index: '02' },
  { name: 'Price', label: 'La receta', task: 'Busca la entrada', color: '#e38a68', accent: '#f3bea0', index: '03' },
  { name: 'Wire', label: 'El ingrediente', task: 'Verifica la fuente', color: '#a0a0c0', accent: '#d9cfee', index: '04' },
  { name: 'Size', label: 'La balanza', task: 'Calcula el tamaño', color: '#6caaa2', accent: '#bfe3d4', index: '05' },
  { name: 'Exec', label: 'El pase', task: 'Ejecuta la orden', color: '#e58264', accent: '#f6d88e', index: '06' },
  { name: 'Risk', label: 'El cuidado', task: 'Controla el riesgo', color: '#809cba', accent: '#c8dfeb', index: '07' },
] as const

const STATUS_LABELS: Record<string, string> = {
  waiting: 'En espera', selected: 'Seleccionado', ready: 'Preparado', blocked: 'Bloqueado',
  corroborated: 'Verificado', managing: 'Gestionando', executed: 'Ejecutado', closed: 'Cerrado',
  error: 'Revisar', evaluating: 'Evaluando', scanning: 'Explorando',
  pending: 'En cola', partial: 'Salida parcial',
}
const WORKING_STATES = new Set(['selected', 'ready', 'corroborated', 'managing', 'executed', 'evaluating', 'scanning'])

function Chef({ color, index }: { color: string; index: number }) {
  const skins = ['#e8b48d', '#d89a72', '#f3c4a4', '#bd825e', '#edb89a', '#d99f79', '#e8b48d']
  return <g className="kitchen-chef">
    <ellipse cx="122" cy="169" rx="41" ry="8" fill="#644d36" opacity=".1" />
    <path d="M99 123 Q91 128 94 160 L150 160 Q151 125 138 123Z" fill={color} stroke="#493b31" strokeWidth="2.5" />
    <path d="M111 124 L111 151 Q123 157 135 151 L135 124" fill="#fff9ed" stroke="#493b31" strokeWidth="2" />
    <path d="M115 127 Q123 133 132 127" fill="none" stroke="#d5c8ae" strokeWidth="2" />
    <circle cx="123" cy="141" r="2" fill={color} />
    <g className="kitchen-head">
      <ellipse cx="95" cy="99" rx="6" ry="8" fill={skins[index]} stroke="#493b31" strokeWidth="2" />
      <ellipse cx="151" cy="99" rx="6" ry="8" fill={skins[index]} stroke="#493b31" strokeWidth="2" />
      <path d="M97 85 Q96 72 123 73 Q149 74 150 87 L148 108 Q146 124 123 126 Q100 123 98 108Z" fill={skins[index]} stroke="#493b31" strokeWidth="2.5" />
      <path d="M98 91 Q104 85 108 83 M138 83 Q146 86 149 91" fill="none" stroke="#493b31" strokeWidth="5" strokeLinecap="round" />
      <g className="kitchen-chef-eyes"><path d="M111 100v3 M136 100v3" stroke="#493b31" strokeWidth="3.5" strokeLinecap="round" /></g>
      <ellipse cx="106" cy="110" rx="5" ry="2.5" fill="#db806e" opacity=".65" />
      <ellipse cx="141" cy="110" rx="5" ry="2.5" fill="#db806e" opacity=".65" />
      <path d="M119 112 Q124 117 130 111" stroke="#493b31" strokeWidth="2" strokeLinecap="round" fill="none" />
      <path d="M97 81 L97 64 Q85 62 87 50 Q88 39 102 40 Q104 27 117 30 Q125 20 135 30 Q148 28 151 41 Q164 43 161 54 Q159 64 150 64 L149 81Z" fill="#fffdf6" stroke="#493b31" strokeWidth="2.5" strokeLinejoin="round" />
      <path d="M98 73 Q124 67 149 73 M108 49v11 M138 45v15" stroke="#dfd7c5" fill="none" strokeWidth="2.5" strokeLinecap="round" />
      <path d="M98 80 Q123 75 149 80" stroke="#493b31" strokeWidth="2" fill="none" />
    </g>
    <g className="kitchen-arm kitchen-arm-left"><path d="M100 130 Q87 132 88 145 L105 155" fill="none" stroke="#493b31" strokeWidth="15" strokeLinecap="round" /><path d="M100 130 Q87 132 88 145 L105 155" fill="none" stroke={color} strokeWidth="11" strokeLinecap="round" /><ellipse cx="107" cy="156" rx="8" ry="6" fill={skins[index]} stroke="#493b31" strokeWidth="2" /></g>
    <g className="kitchen-arm kitchen-arm-right"><path d="M145 130 Q159 134 155 146 L139 154" fill="none" stroke="#493b31" strokeWidth="15" strokeLinecap="round" /><path d="M145 130 Q159 134 155 146 L139 154" fill="none" stroke={color} strokeWidth="11" strokeLinecap="round" /><ellipse cx="137" cy="155" rx="8" ry="6" fill={skins[index]} stroke="#493b31" strokeWidth="2" /></g>
  </g>
}

function CounterProps({ name }: { name: string }) {
  if (name === 'Selector') return <g>
    <path d="M56 153L76 147 96 153 91 171 61 172Z" fill="#b68552" stroke="#65492e" strokeWidth="2" />
    <path d="M60 158h32 M61 165h30" stroke="#e4bb81" strokeWidth="2" />
    <circle className="kitchen-ingredient" cx="67" cy="149" r="8" fill="#e68d56" stroke="#81583d" strokeWidth="1.5" />
    <circle cx="81" cy="146" r="9" fill="#adc675" stroke="#6b8052" strokeWidth="1.5" />
    <path d="M80 135l2 5 6-3" fill="none" stroke="#6b8052" strokeWidth="2.5" />
    <g className="kitchen-utensil"><path d="M143 147l8-26 21 7-9 28Z" fill="#fff9df" stroke="#695848" strokeWidth="2" /><path d="M153 133l11 3 M151 139l10 3 M149 145l7 2" stroke="#c39b61" strokeWidth="2" /></g>
    <path d="M178 156l13-4 8 5-13 5Z" fill="#b4cb8e" stroke="#6b8052" strokeWidth="1.5" />
  </g>
  if (name === 'Tape') return <g>
    <path d="M77 153l68-5 16 18-70 5Z" fill="#d9ad70" stroke="#8d6945" strokeWidth="2" />
    <g className="kitchen-chopped"><path d="M97 157l5-9 5 10Z M110 156l5-9 5 9Z M123 156l5-9 5 9Z" fill="#90b763" stroke="#5e8348" strokeWidth="1.5" /></g>
    <g className="kitchen-utensil"><path d="M129 149l26-21 5 7-22 20Z" fill="#e4e9df" stroke="#49594d" strokeWidth="2" /><path d="M155 128l10-8" stroke="#7d5940" strokeWidth="7" strokeLinecap="round" /></g>
    <path d="M176 152l-4-18 13-3 6 18" fill="#a1bd7e" stroke="#6b8052" strokeWidth="2" />
    <path d="M176 136l-2-15 M180 134l2-15 M185 135l7-12" stroke="#789659" strokeWidth="3" strokeLinecap="round" />
  </g>
  if (name === 'Price') return <g>
    <g className="kitchen-steam"><path d="M106 136q-7-7 0-13t0-12 M121 135q8-8 0-14t0-12 M135 135q-7-6 0-12" stroke="#fffdf7" strokeWidth="3.5" fill="none" strokeLinecap="round" /></g>
    <path d="M90 149l5 18q24 10 49-2l5-18Z" fill="#e68d69" stroke="#795546" strokeWidth="2.5" />
    <ellipse cx="119" cy="148" rx="29" ry="8" fill="#f9d696" stroke="#795546" strokeWidth="2.5" />
    <path d="M91 155l-10-3 M148 155l10-4" stroke="#795546" strokeWidth="4" strokeLinecap="round" />
    <path className="kitchen-utensil" d="M126 149l21-30" stroke="#99633d" strokeWidth="5" strokeLinecap="round" />
    <path d="M178 154l-1-20h15l-2 20Z" fill="#fff6db" stroke="#9b875f" strokeWidth="2" /><path d="M181 131l3-6 5 6" fill="#d9aa69" />
  </g>
  if (name === 'Wire') return <g>
    <path d="M83 152l1-21 32-5 2 26Z" fill="#faf4de" stroke="#938165" strokeWidth="2" /><path d="M118 152l1-26 29 6 4 23Z" fill="#fff9e8" stroke="#938165" strokeWidth="2" />
    <path d="M90 135l19-3 M90 142l19-3 M125 135l15 3 M125 141l17 4" stroke="#b4a58c" strokeWidth="2" strokeLinecap="round" />
    <g className="kitchen-utensil"><circle cx="153" cy="143" r="13" fill="#cce4dc" fillOpacity=".8" stroke="#607b79" strokeWidth="4" /><path d="M144 154l-11 12" stroke="#607b79" strokeWidth="6" strokeLinecap="round" /><path d="M149 142l3 4 6-7" fill="none" stroke="#6d9a79" strokeWidth="2.5" /></g>
    <path d="M183 151v-18 M177 137l6-7 6 7" stroke="#9b8aa8" strokeWidth="2" fill="none" />
  </g>
  if (name === 'Size') return <g>
    <path d="M96 159l7-15h37l9 15v11H96Z" fill="#a8d0be" stroke="#5c877d" strokeWidth="2.5" /><path d="M104 145h36l-4-12h-28Z" fill="#fff9e5" stroke="#5c877d" strokeWidth="2" />
    <rect x="111" y="155" width="22" height="10" rx="3" fill="#f7f3de" /><path className="kitchen-scale-needle" d="M122 163l5-5" stroke="#5c877d" strokeWidth="2" strokeLinecap="round" />
    <circle className="kitchen-ingredient" cx="122" cy="131" r="9" fill="#e6b55b" stroke="#987646" strokeWidth="1.5" /><path d="M122 121l5-3" stroke="#7b985c" strokeWidth="2.5" strokeLinecap="round" />
    <path d="M174 158l4-19h13l4 19Z" fill="#eee7d0" stroke="#a69a80" strokeWidth="2" /><path d="M178 139q7-8 13 0" fill="none" stroke="#a69a80" strokeWidth="2" />
  </g>
  if (name === 'Exec') return <g>
    <ellipse cx="124" cy="168" rx="42" ry="9" fill="#fdf9e9" stroke="#b39e73" strokeWidth="2" />
    <g className="kitchen-cloche"><path d="M91 159q3-32 33-32t34 32Z" fill="#c5d7c7" stroke="#668d80" strokeWidth="2.5" /><path d="M92 159h65 M100 148q4-14 15-16" stroke="#eff4e6" strokeWidth="3" fill="none" strokeLinecap="round" /><path d="M121 126v-4h7v4" fill="none" stroke="#668d80" strokeWidth="3" /><path d="M88 161h73" stroke="#668d80" strokeWidth="4" strokeLinecap="round" /></g>
    <path d="M178 151v-13q0-8 8-8t8 8v13" fill="#efcb79" stroke="#9b7d4e" strokeWidth="2" /><path d="M175 153h22 M184 125h5" stroke="#9b7d4e" strokeWidth="3" strokeLinecap="round" />
  </g>
  return <g>
    <g className="kitchen-steam"><path d="M109 133q-7-8 0-15t0-9 M126 133q7-7 0-15t0-9" stroke="#fffdf7" strokeWidth="3" fill="none" strokeLinecap="round" /></g>
    <path d="M94 145h53l-3 22q-23 9-47 0Z" fill="#93adbc" stroke="#526d7d" strokeWidth="2.5" /><ellipse cx="120" cy="145" rx="27" ry="6" fill="#e6c17d" stroke="#526d7d" strokeWidth="2.5" /><path d="M94 151h-8 M147 151h8" stroke="#526d7d" strokeWidth="4" strokeLinecap="round" />
    <g className="kitchen-flame"><path d="M110 177q-6-6 1-12 0 6 6 4 7 8-1 10Z M126 176q-5-7 2-11 0 5 5 6 4 6-3 8Z" fill="#e9a34b" /><path d="M115 177l1-5 4 5 M128 177l2-5 3 5" fill="#f6d771" /></g>
    <path d="M177 155v-24q0-5 4-5t4 5v24a7 7 0 1 1-8 0Z" fill="#fff7e3" stroke="#7e8c8e" strokeWidth="2" /><path d="M181 138v21" stroke="#df896b" strokeWidth="3" strokeLinecap="round" /><circle cx="181" cy="161" r="3" fill="#df896b" />
  </g>
}

function KitchenBackdrop({ id }: { id: string }) {
  return <svg className="kitchen-backdrop" viewBox="0 0 1000 530" preserveAspectRatio="none" aria-hidden="true">
    <defs>
      <pattern id={`${id}-tiles`} width="66" height="37" patternUnits="userSpaceOnUse"><rect width="66" height="37" fill="#f5eddd" /><path d="M0 0H66V37H0Z" fill="none" stroke="#e5dac4" strokeWidth="1" /><path d="M3 3h60v31" fill="none" stroke="#fff9ee" strokeWidth="1" /></pattern>
      <pattern id={`${id}-floor`} width="100" height="64" patternUnits="userSpaceOnUse" patternTransform="skewX(-18)"><rect width="100" height="64" fill="#e7d5b5" /><rect width="50" height="32" fill="#f4e5c8" /><rect x="50" y="32" width="50" height="32" fill="#f4e5c8" /><path d="M0 0H100V64H0Z M50 0v64 M0 32h100" fill="none" stroke="#d3bd96" strokeWidth="1" opacity=".4" /></pattern>
      <linearGradient id={`${id}-wallshade`} x1="0" x2="0" y1="0" y2="1"><stop stopColor="#ddcba9" stopOpacity="0" /><stop offset="1" stopColor="#b19c77" stopOpacity=".18" /></linearGradient>
    </defs>
    <rect width="1000" height="530" rx="22" fill={`url(#${id}-floor)`} />
    <path d="M0 0H1000V205H0Z" fill={`url(#${id}-tiles)`} />
    <path d="M0 0H1000V205H0Z" fill={`url(#${id}-wallshade)`} />
    <path d="M0 202h1000" stroke="#c8ae84" strokeWidth="7" />
    <path d="M0 210h1000" stroke="#fff4d7" strokeWidth="4" />
    <g fill="#7d6b51"><path d="M122 0v28 M377 0v28 M623 0v28 M878 0v28" stroke="#7d6b51" strokeWidth="2" /><path d="M103 36q3-17 19-17t19 17Z M358 36q3-17 19-17t19 17Z M604 36q3-17 19-17t19 17Z M859 36q3-17 19-17t19 17Z" /></g>
    <g fill="#ffe6a5"><ellipse cx="122" cy="37" rx="19" ry="3" /><ellipse cx="377" cy="37" rx="19" ry="3" /><ellipse cx="623" cy="37" rx="19" ry="3" /><ellipse cx="878" cy="37" rx="19" ry="3" /></g>
    <path d="M0 77h1000" stroke="#e5d5b6" strokeWidth="6" />
    <path d="M0 74h1000" stroke="#fef7e7" strokeWidth="3" />
    <g className="kitchen-wall-herbs" stroke="#79895e" strokeWidth="3" strokeLinecap="round"><path d="M30 45v23 M30 53l-8-7 M30 58l8-9 M960 48v21 M960 56l-8-8 M960 60l8-9" /></g>
    <g fill="#d19a74" stroke="#a37552" strokeWidth="1.5"><path d="M19 62h23l-4 13H23Z M949 63h23l-4 12h-15Z" /></g>
    <g className="kitchen-floor-mark" fill="none" stroke="#c4ad85" strokeWidth="2" strokeDasharray="4 7" opacity=".55"><path d="M28 292q0-10 12-10h920q12 0 12 12" /><path d="M85 492h830" /></g>
    <g opacity=".42" fill="#fff9e7"><path d="M65 246l4 8 9 2-9 3-4 9-3-9-9-3 9-2Z M934 446l4 8 9 2-9 3-4 9-3-9-9-3 9-2Z" /></g>
  </svg>
}

export default function KitchenStage({ roles, bubbles = {}, selectedSymbol, hasPosition, enabled, openRole, onSelectRole, motionEnabled }: KitchenStageProps) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 500)
    return () => window.clearInterval(timer)
  }, [])
  const id = useId().replace(/:/g, '')
  return <div className={`kitchen-stage ${motionEnabled ? '' : 'kitchen-motion-off'} ${enabled ? 'kitchen-is-open' : 'kitchen-is-paused'}`} aria-label="Cocina de Mesa Siete: siete chefs representan los procesos del motor">
    <KitchenBackdrop id={id} />
    <div className="kitchen-scene-sign"><span className={`kitchen-sign-light ${enabled ? 'is-on' : ''}`} />{enabled ? 'COCINA ABIERTA' : 'ENTRADAS EN PAUSA'}<span className="kitchen-sign-star">✦</span></div>
    <div className="kitchen-pass" aria-hidden="true"><span className="kitchen-pass-line" /><span className="kitchen-pass-arrow">›</span><span className="kitchen-pass-caption">DE LA SEÑAL A LA ORDEN</span><span className="kitchen-pass-arrow">›</span><span className="kitchen-pass-line" /></div>
    <div className="kitchen-stations">
      {STATIONS.map((station, index) => {
        const role = roles.find(item => item.name === station.name)
        const status = role?.status ?? 'waiting'
        const working = (enabled || hasPosition) && WORKING_STATES.has(status)
        const chosen = openRole === station.name
        return <button
          key={station.name}
          type="button"
          className={`kitchen-station kitchen-station-${station.name.toLowerCase()} ${working ? 'is-working' : 'is-idle'} ${status === 'blocked' || status === 'error' ? 'is-blocked' : ''} ${chosen ? 'is-chosen' : ''}`}
          style={{ '--chef-color': station.color, '--chef-accent': station.accent, '--chef-delay': `${index * -.39}s` } as CSSProperties}
          onClick={() => onSelectRole(station.name)}
          aria-pressed={chosen}
          aria-label={`${station.name}, ${station.label}: ${STATUS_LABELS[status] ?? status}. ${role?.detail ?? station.task}`}
          title={role?.detail ?? station.task}
        >
          <span className="kitchen-station-state"><i />{STATUS_LABELS[status] ?? status}</span>
          {bubbles[station.name] && now - bubbles[station.name].at < 7 && now - bubbles[station.name].at >= -5 && <span className="kitchen-speech-bubble" key={bubbles[station.name].at}>{bubbles[station.name].text}</span>}
          <svg className="kitchen-station-art" viewBox="0 0 250 225" fill="none" aria-hidden="true">
            <ellipse className="kitchen-station-shadow" cx="123" cy="206" rx="100" ry="14" fill="#8c7050" opacity=".16" />
            <ellipse className="kitchen-selected-ring" cx="123" cy="205" rx="104" ry="16" stroke={station.color} strokeWidth="3" strokeDasharray="5 5" />
            <path d="M37 175l12 27 74 12 86-13 7-29Z" fill="#b98a59" stroke="#826343" strokeWidth="2" strokeLinejoin="round" />
            <path d="M123 179v35l86-13 7-29Z" fill="#bd9566" />
            <path d="M37 175l86 13 93-16-87-16Z" fill="#f9e3b5" stroke="#96744d" strokeWidth="2" strokeLinejoin="round" />
            <Chef color={station.color} index={index} />
            <path d="M37 166l86 13 93-16-87-16Z" fill="#fff0cf" stroke="#96744d" strokeWidth="2" strokeLinejoin="round" />
            <path d="M37 166v9l86 13v-9Z" fill="#dfbb7e" stroke="#96744d" strokeWidth="1.5" />
            <path d="M123 179v9l93-16v-9Z" fill="#e4c690" stroke="#96744d" strokeWidth="1.5" />
            <path d="M47 177l72 11v20l-66-10Z" fill={station.accent} stroke="#96744d" strokeWidth="1.5" />
            <path d="M133 188l69-11-3 19-66 10Z" fill={station.color} opacity=".62" />
            <path d="M146 190l12-2 M177 185l12-2" stroke="#806442" strokeWidth="2.5" strokeLinecap="round" />
            <text x="81" y="196" textAnchor="middle" fill="#765a3a" fontSize="11" fontWeight="800" fontFamily="inherit" transform="rotate(8 81 196)">{station.index}</text>
            <CounterProps name={station.name} />
            <g className="kitchen-work-spark" fill={station.color}><path d="M174 101l2 6 6 2-6 2-2 6-2-6-6-2 6-2Z" /><circle cx="184" cy="91" r="2.5" /><path d="M68 116l2 4 5 2-5 1-2 5-1-5-5-1 5-2Z" /></g>
          </svg>
          <span className="kitchen-station-label"><b>{station.name}</b><span>{station.label}</span></span>
        </button>
      })}
    </div>
    <div className={`kitchen-market-badge ${hasPosition ? 'has-position' : ''}`}><span className="kitchen-market-icon">{hasPosition ? '↗' : '◎'}</span><span><small>{hasPosition ? 'POSICIÓN EN MARCHA' : selectedSymbol ? 'MERCADO ELEGIDO' : 'ESPERANDO MERCADO'}</small><b>{selectedSymbol?.replace(/USDT$/, '') ?? 'La despensa está lista'}</b></span>{hasPosition && <i className="kitchen-market-pulse" />}</div>
  </div>
}
