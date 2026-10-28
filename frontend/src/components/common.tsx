import { useState, type ReactNode } from 'react'

export function StatusBadge({ value }: { value: string }) {
  const normalized = value.toLowerCase().replaceAll('_', ' ')
  const tone = GOOD_STATES.has(normalized)
    ? 'good'
    : WARN_STATES.has(normalized)
      ? 'warn'
      : BAD_STATES.has(normalized)
        ? 'bad'
        : 'quiet'
  return <span className={`status-badge ${tone}`}>{value.replaceAll('_', ' ')}</span>
}

const GOOD_STATES = new Set([
  'healthy',
  'stopped',
  'improved',
  'available',
  'correct',
  'root cause identified',
  'ok',
  'direct',
])

const WARN_STATES = new Set([
  'running',
  'partial',
  'degraded',
  'created',
  'medium',
  'insufficient evidence',
  'no significant change',
  'unchanged',
  'not found',
  'insufficient data',
  'indirect',
  'not comparable',
])

const BAD_STATES = new Set([
  'unavailable',
  'unreachable',
  'regressed',
  'incorrect',
  'high',
  'error',
  'failed',
])

const LIFECYCLE = ['CREATED', 'RUNNING', 'STOPPED', 'INVESTIGATE'] as const

export function Lifecycle({ status, investigated = false }: { status: string; investigated?: boolean }) {
  const statusRank = status === 'CREATED' ? 0 : status === 'RUNNING' ? 1 : status === 'STOPPED' ? 2 : -1
  return (
    <ol className="lifecycle" aria-label="Experiment lifecycle">
      {LIFECYCLE.map((step, index) => {
        const state = lifecycleState(statusRank, index, investigated)
        return (
          <li key={step} className={state} aria-current={state === 'current' ? 'step' : undefined}>
            <span>{index + 1}</span>
            {step}
          </li>
        )
      })}
    </ol>
  )
}

function lifecycleState(statusRank: number, index: number, investigated: boolean) {
  if (statusRank < 0) return ''
  if (index < statusRank) return 'done'
  if (index === statusRank) return statusRank === 2 ? 'done' : 'current'
  if (index === 3 && statusRank === 2) return investigated ? 'done' : 'current'
  return ''
}

export function Loading({ label }: { label: string }) {
  return (
    <div className="loading-state" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}
    </div>
  )
}

export function ErrorMessage({ message }: { message: string }) {
  return <div className="error-message" role="alert">{message}</div>
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      {detail && <p>{detail}</p>}
    </div>
  )
}

export function Section({
  title,
  aside,
  children,
}: {
  title: string
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="section">
      <div className="section-heading">
        <h2>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  )
}

export function formatDate(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

export function shortId(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-4)}`
}

export function CopyValue({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      className="copy-button"
      title={`Copy ${label}`}
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1200)
        }).catch(() => undefined)
      }}
    >
      {copied ? 'Copied' : `${label}: ${shortId(value)}`}
    </button>
  )
}
