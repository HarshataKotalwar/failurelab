import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Experiment, InvestigationResult, ServiceObservation } from '../api/types'
import { EmptyState, ErrorMessage, Loading, Section, StatusBadge, formatDate, shortId } from '../components/common'

export function DashboardPage({ onSelect, onExperiments, onOpenInvestigation }: {
  onSelect: (id: string) => void
  onExperiments: () => void
  onOpenInvestigation?: (investigationId: string, experimentId: string) => void
}) {
  const [services, setServices] = useState<ServiceObservation[] | null>(null)
  const [control, setControl] = useState<'healthy' | 'unknown'>('unknown')
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [investigations, setInvestigations] = useState<InvestigationResult[]>([])
  const [error, setError] = useState<string | null>(null)
  const [investigationError, setInvestigationError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    Promise.allSettled([api.health(), api.services(), api.experiments(), api.investigations(20)])
      .then(([health, serviceResult, experimentResult, investigationResult]) => {
        if (!active) return
        setControl(health.status === 'fulfilled' ? 'healthy' : 'unknown')
        setServices(serviceResult.status === 'fulfilled' ? serviceResult.value : null)
        if (experimentResult.status === 'fulfilled') setExperiments(experimentResult.value)
        else setError('Unable to load experiments. Check that the control API is running.')
        if (investigationResult.status === 'fulfilled') setInvestigations(investigationResult.value)
        else setInvestigationError('Unable to load investigations. Check that the control API is running.')
      })
      .finally(() => active && setLoading(false))
    return () => { active = false }
  }, [])

  const activeExperiments = experiments.filter((item) => item.status === 'RUNNING')
  const identified = investigations.filter((item) => item.outcome === 'root_cause_identified').length
  const abstained = investigations.filter((item) => item.outcome === 'insufficient_evidence').length
  return (
    <div className="page-stack">
      <header className="page-title-row">
        <div>
          <p className="eyebrow">RELIABILITY WORKSPACE</p>
          <h1>Incident overview</h1>
          <p className="lede">FailureLab intentionally introduces a controlled failure, captures the resulting telemetry, and investigates the incident using evidence-backed conclusions.</p>
        </div>
        <button className="primary-button" onClick={onExperiments}>Create Experiment</button>
      </header>
      <ol className="flow-steps" aria-label="FailureLab investigation flow">
        <li><span>01</span><strong>Break it</strong><small>Inject a controlled failure into a service.</small></li>
        <li><span>02</span><strong>Observe it</strong><small>Capture logs, metrics, traces, and service health.</small></li>
        <li><span>03</span><strong>Investigate it</strong><small>Correlate the evidence and identify the likely root cause.</small></li>
      </ol>

      {error && <ErrorMessage message={error} />}
      {!loading && (
        <section className="outcome-strip" aria-label="Experiment and investigation counts">
          <div><small>Running</small><em>experiments in progress</em><strong>{activeExperiments.length}</strong></div>
          <div><small>Created</small><em>not started yet</em><strong>{experiments.filter((item) => item.status === 'CREATED').length}</strong></div>
          <div><small>Stopped</small><em>failure window ended</em><strong>{experiments.filter((item) => item.status === 'STOPPED').length}</strong></div>
          <div><small>Root causes</small><em>recent investigations</em><strong>{investigationError ? '—' : identified}</strong></div>
          <div><small>Insufficient evidence</small><em>recent investigations</em><strong>{investigationError ? '—' : abstained}</strong></div>
        </section>
      )}
      <Section title="System status" aside={<span className="muted">Live observations · refreshed on load</span>}>
        {loading ? <Loading label="Loading system status..." /> : (
          <div className="service-grid">
            <article className="service-card" aria-label="control-api service status">
              <div className="service-card-top"><span className="service-dot" /><StatusBadge value={control} /></div>
              <strong>control-api</strong><span className="muted">Investigation control plane</span>
            </article>
            {services === null ? (
              <article className="service-card"><strong>Application services</strong><span className="muted">Health observations could not be loaded.</span></article>
            ) : services.length === 0 ? (
              <article className="service-card"><strong>Application services</strong><span className="muted">No service observations returned.</span></article>
            ) : services.map((service) => (
              <article className="service-card" key={service.service} aria-label={`${service.service} service status`}>
                <div className="service-card-top"><span className="service-dot" /><StatusBadge value={service.status} /></div>
                <strong>{service.service}</strong>
                <span className="muted">{service.status_code ? `HTTP ${service.status_code}` : 'Health endpoint'}</span>
              </article>
            ))}
          </div>
        )}
      </Section>

      {activeExperiments.length > 0 && (
        <section className="active-banner">
          <div><p className="eyebrow">ACTIVE EXPERIMENT</p><strong>{activeExperiments.length} experiment{activeExperiments.length > 1 ? 's' : ''} currently running</strong></div>
          <button className="text-button" onClick={() => onSelect(activeExperiments[0].id)}>Inspect active incident →</button>
        </section>
      )}

      <div className="dashboard-columns">
        <Section title="Recent experiments" aside={<button className="text-button" onClick={onExperiments}>View all →</button>}>
          {loading ? <Loading label="Loading experiments..." /> : experiments.length === 0 ? (
            <EmptyState title="No experiments yet" detail="Create a controlled failure experiment to begin an investigation." />
          ) : <div className="compact-list">{experiments.slice(0, 5).map((experiment) => (
            <button className="list-row" key={experiment.id} onClick={() => onSelect(experiment.id)}>
              <span><strong>{experiment.target_service}</strong><small>{experiment.mode === 'verification' ? 'Verification' : experiment.failure_type} · {formatDate(experiment.created_at)}</small></span>
              <StatusBadge value={experiment.status} />
            </button>
          ))}</div>}
        </Section>
        <Section title="Recent investigations">
          {loading ? <Loading label="Loading investigations..." /> : investigationError ? <ErrorMessage message={investigationError} /> : investigations.length === 0 ? <EmptyState title="No investigations yet" detail="Investigation results appear here after an experiment has been analyzed." /> : (
            <div className="compact-list">{investigations.slice(0, 5).map((item) => (
              <button className="list-row" key={item.investigation_id} onClick={() => onOpenInvestigation ? onOpenInvestigation(item.investigation_id, item.experiment_id) : onSelect(item.experiment_id)}>
                <span><strong>{item.outcome === 'insufficient_evidence' ? 'Insufficient evidence' : 'Root cause identified'}</strong><small>{shortId(item.experiment_id)} · {formatDate(item.created_at)} · open result</small></span>
                <span className="muted">{Math.round(item.confidence * 100)}%</span>
              </button>
            ))}</div>
          )}
        </Section>
      </div>
    </div>
  )
}
