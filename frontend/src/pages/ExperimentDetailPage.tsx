import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, ApiError } from '../api/client'
import type {
  EvidenceResponse,
  Experiment,
  ExperimentVerification,
  InvestigationEvaluation,
  IncidentScenario,
  InvestigationResult,
  RemediationPlan,
} from '../api/types'
import { IncidentConsole } from '../components/IncidentConsole'
import { InvestigationView } from '../components/InvestigationView'
import { CopyValue, EmptyState, ErrorMessage, Lifecycle, Loading, Section, StatusBadge, formatDate, shortId } from '../components/common'

export function ExperimentDetailPage({ id, onBack, onSelect, onOpenInvestigation }: {
  id: string
  onBack: () => void
  onSelect: (id: string) => void
  onOpenInvestigation?: (investigationId?: string) => void
}) {
  const [experiment, setExperiment] = useState<Experiment | null>(null)
  const [evidence, setEvidence] = useState<EvidenceResponse | null>(null)
  const [investigation, setInvestigation] = useState<InvestigationResult | null>(null)
  const [remediation, setRemediation] = useState<RemediationPlan | null>(null)
  const [evaluation, setEvaluation] = useState<InvestigationEvaluation | null>(null)
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [verification, setVerification] = useState<ExperimentVerification | null>(null)
  const [verificationId, setVerificationId] = useState('')
  const [loading, setLoading] = useState(true)
  const [evidenceError, setEvidenceError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [action, setAction] = useState<'start' | 'stop' | 'investigate' | 'order' | 'verify' | 'create-verification' | null>(null)
  const [requestMessage, setRequestMessage] = useState<string | null>(null)
  const [scenarios, setScenarios] = useState<IncidentScenario[]>([])

  const refresh = useCallback(async (initial = false) => {
    if (initial) setLoading(true)
    try {
      const item = await api.experiment(id)
      setExperiment(item)
      setError(null)
      try { setEvidence(await api.evidence(id)); setEvidenceError(null) }
      catch (reason) { setEvidence(null); setEvidenceError(reason instanceof Error ? reason.message : 'Evidence is unavailable.') }
      const saved = await api.investigations(100, id).catch(() => [])
      if (saved.length > 0) {
        const latest = saved[0]
        setInvestigation(latest)
        const plan = await api.remediation(latest.investigation_id).catch(() => null)
        setRemediation(plan)
        const scored = await api.evaluate(latest.investigation_id).catch(() => null)
        setEvaluation(scored)
      }
      const all = await api.experiments().catch(() => [])
      setExperiments(all)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Experiment could not be loaded.')
    } finally { if (initial) setLoading(false) }
  }, [id])

  useEffect(() => { void refresh(true) }, [refresh])
  useEffect(() => { void api.scenarios().then(setScenarios).catch(() => setScenarios([])) }, [])
  useEffect(() => {
    if (experiment?.status !== 'RUNNING') return
    const timer = window.setInterval(() => { void refresh() }, 8000)
    return () => window.clearInterval(timer)
  }, [experiment?.status, refresh])

  const sortedTimeline = useMemo(() => {
    if (!evidence) return []
    const events = [
      ...evidence.timeline.map((event) => ({ at: event.at, source: 'experiment', service: evidence.target_service, description: `${event.event}: ${event.status}` })),
      ...evidence.logs.logs.map((log) => ({ at: log.timestamp, source: 'log', service: log.service, description: `${log.route ?? 'request'} returned ${log.status_code ?? 'unknown status'}` })),
      ...evidence.traces.spans.map((span) => ({ at: span.start_time, source: 'trace', service: span.service_name, description: `${span.operation_name} · ${span.status} · ${span.duration_ms.toFixed(1)} ms` })),
      ...evidence.service_observations.map((item) => ({ at: item.observed_at, source: 'health', service: item.service, description: `${item.status}${item.status_code ? ` · HTTP ${item.status_code}` : ''}` })),
      ...(evidence.metrics.observed_at ? [{ at: evidence.metrics.observed_at, source: 'metric', service: evidence.target_service, description: `Prometheus query snapshot · ${evidence.metrics.window_seconds ?? 'unknown'}s window` }] : []),
    ]
    return events.sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime())
  }, [evidence])

  async function doAction(kind: NonNullable<typeof action>) {
    if (action) return
    setAction(kind)
    setError(null)
    setRequestMessage(null)
    try {
      if (kind === 'start') await api.startExperiment(id)
      if (kind === 'stop') await api.stopExperiment(id)
      if (kind === 'order') {
        const response = await api.createOrder()
        setRequestMessage(`Order request returned ${response.status}.`)
      }
      if (kind === 'investigate') {
        const result = await api.investigate(id)
        setInvestigation(result)
        const [plan, scored] = await Promise.all([
          api.remediation(result.investigation_id).catch((reason: unknown) => {
            if (import.meta.env.DEV) console.error('Remediation plan request failed', reason)
            return null
          }),
          api.evaluate(result.investigation_id).catch((reason: unknown) => {
            if (import.meta.env.DEV) console.error('Investigation evaluation request failed', reason)
            return null
          }),
        ])
        setRemediation(plan)
        setEvaluation(scored)
        if (onOpenInvestigation) {
          onOpenInvestigation(result.investigation_id)
          return
        }
      }
      if (kind === 'create-verification') {
        const created = await api.createExperiment({ target_service: 'payment-service', mode: 'verification', duration_seconds: 60 })
        onSelect(created.id)
        return
      }
      if (kind === 'verify' && verificationId) setVerification(await api.verify(id, verificationId))
      await refresh()
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : reason instanceof Error ? reason.message : 'Request failed.')
    } finally { setAction(null) }
  }

  if (loading) return <Loading label="Loading experiment..." />
  if (error && !experiment) return <div className="page-stack"><button className="text-button" onClick={onBack}>← Experiments</button><ErrorMessage message={error} /></div>
  if (!experiment) return <EmptyState title="Experiment not found" />

  const verifyOptions = experiments.filter((item) => item.mode === 'verification' && item.status === 'STOPPED')
  return (
    <div className="page-stack">
      <button className="text-button back-link" onClick={onBack}>← Experiments</button>
      <header className="detail-heading">
        <div><p className="eyebrow">{experiment.mode === 'verification' ? 'VERIFICATION WINDOW' : 'CONTROLLED FAILURE'}</p><h1>{experiment.target_service}</h1><p className="muted mono">{experiment.id}</p></div>
        <div className="detail-actions">
          {experiment.status === 'CREATED' && <button className="primary-button" disabled={Boolean(action)} onClick={() => void doAction('start')}>{action === 'start' ? 'Starting…' : 'Start incident'}</button>}
          {experiment.status === 'RUNNING' && <button className="danger-button" disabled={Boolean(action)} onClick={() => void doAction('stop')}>{action === 'stop' ? 'Stopping…' : 'Stop experiment'}</button>}
        </div>
      </header>
      {error && <ErrorMessage message={error} />}
      <Lifecycle status={experiment.status} investigated={Boolean(investigation)} />
      <section className="metadata-strip">
        <div><small>Status</small><StatusBadge value={experiment.status} /></div>
        <div><small>Mode</small><strong>{experiment.mode === 'failure' ? experiment.failure_type : 'Verification observation'}</strong></div>
        <div><small>Duration</small><strong>{experiment.duration_seconds}s</strong></div>
        <div><small>Created</small><strong>{formatDate(experiment.created_at)}</strong></div>
        <div><small>Started</small><strong>{formatDate(experiment.started_at)}</strong></div>
        <div><small>Ended</small><strong>{formatDate(experiment.ended_at)}</strong></div>
      </section>
      {experiment.mode === 'failure' && experiment.status === 'CREATED' && (
        <section className="failure-brief">
          <p className="eyebrow">SCENARIO</p>
          <strong>{scenarios.find((item) => item.id === experiment.scenario)?.name ?? `${experiment.target_service} · ${experiment.failure_type}`}</strong>
          <p>{scenarios.find((item) => item.id === experiment.scenario)?.expected_behavior ?? `${experiment.failure_type} on ${experiment.target_service} for ${experiment.duration_seconds}s. Start the incident to begin collecting the experiment window.`}</p>
        </section>
      )}
      {experiment.mode === 'failure' && experiment.started_at && (
        <IncidentConsole
          experiment={experiment}
          evidence={evidence}
          scenario={scenarios.find((item) => item.id === experiment.scenario) ?? scenarios.find((item) => item.target_service === experiment.target_service && item.failure_type === experiment.failure_type) ?? null}
        />
      )}

      {experiment.mode === 'failure' && experiment.status === 'RUNNING' && (
        <section className="active-banner">
          <div>
            <p className="eyebrow">TELEMETRY IS BEING COLLECTED</p>
            <strong>{experiment.target_service} · {experiment.failure_type} · {experiment.duration_seconds}s · {experiment.status}</strong>
            <small>The controlled failure is active. Logs, metrics, traces, and service health refresh from the control API.</small>
          </div>
          <button className="secondary-button" disabled={Boolean(action)} onClick={() => void doAction('order')}>{action === 'order' ? 'Sending request…' : 'Generate order request'}</button>
        </section>
      )}
      {experiment.mode === 'failure' && experiment.status === 'STOPPED' && (
        <section className="active-banner">
          <div>
            <p className="eyebrow">CONTROLLED FAILURE ENDED</p>
            <strong>{investigation ? 'The failure window is closed. Review the investigation next.' : 'The failure window is closed. Investigate is the next step.'}</strong>
            <small>{experiment.target_service} · {experiment.failure_type} · ran for {experiment.duration_seconds}s</small>
          </div>
        </section>
      )}
      {requestMessage && <p className="success-message" role="status">{requestMessage}</p>}

      <Section title="Incident investigation" aside={investigation ? <span className="muted">Latest saved result</span> : <span className="muted">Next step after the failure window</span>}>
        <div className="investigation-action-row">
          <p className="muted">{experiment.status === 'RUNNING' ? 'Telemetry is still being collected. You can investigate now, or wait until the window stops.' : 'The investigator uses saved experiment evidence and abstains when that evidence is not sufficient.'}</p>
          <div className="detail-actions">
            {investigation && onOpenInvestigation && <button className="secondary-button" onClick={() => onOpenInvestigation(investigation.investigation_id)}>Open investigation</button>}
            <button className="primary-button" disabled={Boolean(action) || !experiment.started_at} onClick={() => void doAction('investigate')}>{action === 'investigate' ? 'Investigating…' : investigation ? 'Run investigation again' : 'Investigate incident'}</button>
          </div>
        </div>
        {!experiment.started_at && <p className="muted">Start the experiment before running an investigation. Created experiments have not collected a failure window yet.</p>}
        {!investigation && <EmptyState title="No investigation has been run" detail="After you investigate, the result opens with the cited evidence." />}
        {investigation && <InvestigationView result={investigation} remediation={remediation} evaluation={evaluation} />}
      </Section>

      <Section title="Evidence" aside={experiment.status === 'RUNNING' ? <span className="muted">Auto-refreshes every 8 seconds while running</span> : undefined}>
        {evidenceError && <ErrorMessage message={evidenceError} />}
        {!evidence && !evidenceError && <Loading label="Collecting evidence…" />}
        {evidence && <>
          <div className="evidence-state-row">
            <EvidenceState label="Structured logs" status={evidence.logs.status} detail={evidence.logs.message} failures={evidence.logs.failed_sources} />
            <EvidenceState label="Prometheus metrics" status={evidence.metrics.available ? 'available' : 'unavailable'} detail={evidence.metrics.message} />
            <EvidenceState label="Tempo traces" status={evidence.traces.status} detail={evidence.traces.message} failures={evidence.traces.failed_sources} />
          </div>
          <div className="evidence-grid">
            <Section title={`Service observations (${evidence.service_observations.length})`}>
              {evidence.service_observations.length ? <div className="compact-list">{evidence.service_observations.map((item) => <div className="list-row static-row" key={item.service}><span><strong>{item.service}</strong><small>{formatDate(item.observed_at)}{item.status_code ? ` · HTTP ${item.status_code}` : ''}</small></span><StatusBadge value={item.status} /></div>)}</div> : <EmptyState title="No service observations returned" />}
            </Section>
            <Section title="Metrics">
              {!evidence.metrics.available ? <EmptyState title="Metrics unavailable" detail={evidence.metrics.message ?? 'Prometheus did not return observations.'} /> : <>
                <div className="metric-grid"><Metric label="Request rate" value={evidence.metrics.request_rate} unit="req/s" /><Metric label="HTTP 5xx rate" value={evidence.metrics.http_5xx_rate} unit="req/s" /><Metric label="p95 latency" value={evidence.metrics.p95_latency_seconds} unit="s" /></div>
                <p className="metric-window">Observed {formatDate(evidence.metrics.observed_at)} · query window {formatDate(evidence.metrics.window_start)} — {formatDate(evidence.metrics.window_end)} ({evidence.metrics.window_seconds ?? 'unknown'}s). Window proximity alone does not establish causality.</p>
              </>}
            </Section>
            <Section title={`Structured logs (${evidence.logs.logs.length})`}>
              {evidence.logs.logs.length === 0 ? <EmptyState title={evidence.logs.status === 'not_found' ? 'No logs in this experiment window' : 'No logs returned'} detail={evidence.logs.message ?? undefined} /> : <div className="log-list">{evidence.logs.logs.map((log, index) => <article className="log-row" key={`${log.timestamp}-${log.trace_id ?? index}`}><time>{formatDate(log.timestamp)}</time><StatusBadge value={log.level} /><strong>{log.service}</strong><span>{log.route ?? 'request'}{log.status_code ? ` · HTTP ${log.status_code}` : ''}{log.duration_ms !== null ? ` · ${log.duration_ms.toFixed(1)} ms` : ''}</span><p>{log.message}</p>{log.trace_id && <CopyValue label="Trace ID" value={log.trace_id} />}</article>)}</div>}
            </Section>
            <Section title={`Tempo traces (${evidence.traces.trace_ids.length})`}>
              {evidence.traces.spans.length === 0 ? <EmptyState title={traceEmptyTitle(evidence.traces.status)} detail={evidence.traces.message ?? undefined} /> : <TraceList spans={evidence.traces.spans} />}
            </Section>
          </div>
          <Section title="Chronological evidence timeline">
            {sortedTimeline.length === 0 ? <EmptyState title="No timeline events returned" /> : <ol className="timeline">{sortedTimeline.map((event, index) => <li key={`${event.at}-${event.source}-${index}`}><time>{formatDate(event.at)}</time><span className="timeline-source">{event.source}</span><span><strong>{event.service}</strong><small>{event.description}</small></span></li>)}</ol>}
          </Section>
        </>}
      </Section>

      {experiment.mode === 'failure' && <Section title="Before / after verification">
        <p className="muted">Create a verification window after stopping the failure experiment, run the same request, then compare its saved evidence.</p>
        <div className="inline-controls"><button className="secondary-button" disabled={Boolean(action) || experiment.status === 'RUNNING'} onClick={() => void doAction('create-verification')}>{action === 'create-verification' ? 'Creating…' : 'Create verification window'}</button>
          <select aria-label="Verification experiment" value={verificationId} onChange={(event) => setVerificationId(event.target.value)}><option value="">Select a stopped verification window</option>{verifyOptions.map((item) => <option key={item.id} value={item.id}>{shortId(item.id)} · {formatDate(item.created_at)}</option>)}</select>
          <button className="secondary-button" disabled={!verificationId || Boolean(action) || !investigation} onClick={() => void doAction('verify')}>{action === 'verify' ? 'Comparing…' : 'Compare observations'}</button>
        </div>
        {verification && <VerificationPanel result={verification} />}
      </Section>}
    </div>
  )
}

function EvidenceState({ label, status, detail, failures = [] }: { label: string; status: string; detail?: string | null; failures?: string[] }) {
  return <div className="evidence-state"><div><strong>{label}</strong><StatusBadge value={status} /></div>{detail && <small>{detail}</small>}{failures.length > 0 && <small>Failed providers: {failures.join(', ')}</small>}</div>
}

function Metric({ label, value, unit }: { label: string; value: number | null; unit: string }) {
  return <div className="metric-cell"><small>{label}</small><strong>{value === null ? '—' : `${Number(value.toPrecision(4))} ${unit}`}</strong></div>
}

function TraceList({ spans }: { spans: EvidenceResponse['traces']['spans'] }) {
  const byId = new Map(spans.map((span) => [span.span_id, span]))
  const roots = spans.filter((span) => !span.parent_span_id || !byId.has(span.parent_span_id))
  function spanRow(span: (typeof spans)[number], depth = 0): ReactNode {
    const children = spans.filter((item) => item.parent_span_id === span.span_id)
    return <div className="trace-span" key={span.span_id} style={{ marginLeft: `${depth * 20}px` }}><div><StatusBadge value={span.status} /><strong>{span.service_name}</strong><span>{span.operation_name}</span><small>{span.duration_ms.toFixed(2)} ms</small></div><small>Span {span.span_id} · parent {span.parent_span_id ?? 'root'}</small>{children.map((child) => spanRow(child, depth + 1))}</div>
  }
  return <div className="trace-list">{roots.map((span) => <article className="trace-group" key={span.trace_id}><CopyValue label="Trace ID" value={span.trace_id} />{spanRow(span)}</article>)}</div>
}

function VerificationPanel({ result }: { result: ExperimentVerification }) {
  return <div className="verification-result"><div className="verification-heading"><div><small>Verification outcome</small><StatusBadge value={result.status} /></div><small>Baseline {shortId(result.baseline_experiment_id)} · verification {shortId(result.verification_experiment_id)}</small></div><div className="table-wrap"><table><thead><tr><th>Observation</th><th>Baseline</th><th>Verification</th><th>Direction</th><th>Unit</th></tr></thead><tbody>{result.comparisons.map((item) => <tr key={item.metric}><td>{item.metric}</td><td>{item.baseline_value ?? '—'}</td><td>{item.verification_value ?? '—'}</td><td><StatusBadge value={item.direction} /></td><td>{item.unit ?? '—'}</td></tr>)}</tbody></table></div><p className="metric-window">Baseline window: {formatDate(result.baseline_window_start)} — {formatDate(result.baseline_window_end)} · Verification window: {formatDate(result.verification_window_start)} — {formatDate(result.verification_window_end)}</p>{result.limitations.map((item) => <small className="block-muted" key={item}>{item}</small>)}</div>
}

function traceEmptyTitle(status: string) {
  if (status === 'unavailable') return 'Trace provider unavailable'
  if (status === 'partial') return 'Partial trace evidence'
  return 'No traces found in this experiment window'
}
