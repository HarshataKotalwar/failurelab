import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { EvidenceResponse, Experiment, IncidentScenario, ServiceWindowMetric } from '../api/types'
import { StatusBadge } from './common'

export function IncidentConsole({
  experiment,
  evidence,
  scenario,
}: {
  experiment: Experiment
  evidence: EvidenceResponse | null
  scenario: IncidentScenario | null
}) {
  const [now, setNow] = useState(() => Date.now())
  const [dependencies, setDependencies] = useState<Record<string, string[]> | null>(null)
  const [dependencyError, setDependencyError] = useState<string | null>(null)

  useEffect(() => {
    if (experiment.status !== 'RUNNING') return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [experiment.status])

  useEffect(() => {
    let active = true
    api.dependencies()
      .then((value) => { if (active) setDependencies(value) })
      .catch(() => { if (active) setDependencyError('Dependency map could not be loaded.') })
    return () => { active = false }
  }, [])

  const elapsed = elapsedSeconds(experiment, now)
  const failing = new Set(
    (evidence?.service_observations ?? [])
      .filter((item) => item.status !== 'healthy' && item.status !== 'ok')
      .map((item) => item.service),
  )
  const logs = evidence?.logs.logs ?? []
  const targetLogs = logs.filter((log) => log.service === experiment.target_service)
  const errorEvents = logs.filter((log) => log.status_code !== null && log.status_code >= 400)
  const slowEvents = targetLogs.filter((log) => (log.duration_ms ?? 0) >= 1400 && (log.status_code === null || log.status_code < 500))
  const timeoutEvents = targetLogs.filter((log) => log.status_code === 504)
  const events = [...logs]
    .filter((log) => log.status_code !== null && log.status_code >= 400 || log.service === experiment.target_service && (log.duration_ms ?? 0) >= 1400)
    .slice(-8)
    .reverse()

  return (
    <section className="incident-console" aria-label="Live incident console">
      <header>
        <div>
          <p className="eyebrow">{experiment.status === 'RUNNING' ? 'LIVE INCIDENT' : 'INCIDENT WINDOW'}</p>
          <h2>{scenario?.name ?? `${experiment.target_service} ${experiment.failure_type}`}</h2>
          <p className="muted">{scenario?.expected_behavior ?? 'Controlled failure from the experiment record.'}</p>
        </div>
        <StatusBadge value={experiment.status} />
      </header>
      <dl className="console-facts">
        <div><dt>Target</dt><dd>{experiment.target_service}</dd></div>
        <div><dt>Failure</dt><dd>{experiment.failure_type}</dd></div>
        <div><dt>Elapsed</dt><dd>{elapsed === null ? '—' : `${elapsed}s / ${experiment.duration_seconds}s`}</dd></div>
      </dl>
      <div className="console-grid">
        <div>
          <h3>Service health</h3>
          {!evidence ? <p className="muted">Health observations have not loaded.</p> : evidence.service_observations.length === 0 ? <p className="muted">No service observations returned.</p> : (
            <ul className="console-list">
              {evidence.service_observations.map((item) => (
                <li key={item.service}><span>{item.service}</span><StatusBadge value={item.status} /></li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <h3>Incident-window telemetry</h3>
          <WindowMetrics metrics={evidence?.window_metrics} />
        </div>
      </div>
      <div>
        <h3>Observed incident signals</h3>
        <ul className="console-list" aria-label="Incident signal counts">
          {experiment.failure_type === 'latency' && <li><span>Slow successful target requests</span><strong>{slowEvents.length}</strong></li>}
          {experiment.failure_type === 'timeout' && <li><span>Target HTTP 504 timeout responses</span><strong>{timeoutEvents.length}</strong></li>}
          {(experiment.failure_type === 'http_500' || experiment.failure_type === 'unavailable') && <li><span>Target HTTP errors</span><strong>{errorEvents.filter((log) => log.service === experiment.target_service).length}</strong></li>}
          <li><span>Target requests observed in logs</span><strong>{targetLogs.length}</strong></li>
        </ul>
        {events.length === 0 ? <p className="muted">No matching request signals were found in the experiment window logs.</p> : (
          <ul className="console-list">
            {events.map((log) => (
              <li key={`${log.timestamp}-${log.trace_id ?? log.message}`}>
                <time>{new Date(log.timestamp).toLocaleTimeString()}</time>
                <span>{log.service} {log.route ?? 'request'} {log.status_code === 504 ? `timeout · HTTP 504 · ${Math.round(log.duration_ms ?? 0)} ms` : log.status_code !== null && log.status_code >= 400 ? `HTTP ${log.status_code} · ${Math.round(log.duration_ms ?? 0)} ms` : `slow request · ${Math.round(log.duration_ms ?? 0)} ms · HTTP ${log.status_code ?? '—'}`}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <h3>Dependency impact</h3>
        {dependencyError ? <p className="muted">{dependencyError}</p> : !dependencies ? <p className="muted">Loading dependency map...</p> : (
          <ul className="dependency-map">
            {Object.entries(dependencies).map(([service, downstreams]) => (
              <li key={service} className={failing.has(service) ? 'failing' : ''}>
                <strong>{service}{service === experiment.target_service ? ' · target' : ''}</strong>
                <span>{downstreams.length ? `calls ${downstreams.join(' and ')}` : 'no downstream calls'}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

function WindowMetrics({ metrics }: { metrics: ServiceWindowMetric[] | undefined }) {
  if (!metrics) return <p className="muted">Windowed Prometheus metrics were not returned.</p>
  return (
    <ul className="console-list">
      {metrics.map((item) => (
        <li key={item.service}>
          <strong>{item.service}</strong>
          {item.available ? (
            <span>
              requests {formatMetric(item.request_count)} · errors {formatMetric(item.error_count)} · error rate {item.error_rate === null ? '—' : `${Math.round(item.error_rate * 100)}%`} · p95 {item.p95_latency_seconds === null ? '—' : `${item.p95_latency_seconds.toFixed(3)}s`}
            </span>
          ) : <span>{item.message ?? 'Unavailable'}</span>}
          {item.available && item.message && <small>{item.message}</small>}
        </li>
      ))}
    </ul>
  )
}

function formatMetric(value: number | null) {
  if (value === null) return '—'
  return Number(value.toPrecision(3)).toString()
}

function elapsedSeconds(experiment: Experiment, now: number) {
  if (!experiment.started_at) return null
  const start = new Date(experiment.started_at).getTime()
  const end = experiment.ended_at ? new Date(experiment.ended_at).getTime() : now
  return Math.max(0, Math.min(experiment.duration_seconds, Math.floor((end - start) / 1000)))
}
