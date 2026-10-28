import { FormEvent, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { CreateExperiment, Experiment, ExperimentMode, IncidentScenario } from '../api/types'
import { EmptyState, ErrorMessage, Loading, Section, StatusBadge, formatDate, shortId } from '../components/common'

export function ExperimentsPage({ onSelect }: { onSelect: (id: string) => void }) {
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [scenarios, setScenarios] = useState<IncidentScenario[]>([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [scenarioError, setScenarioError] = useState<string | null>(null)
  const [mode, setMode] = useState<ExperimentMode>('failure')
  const [scenarioId, setScenarioId] = useState('payment-unavailable')
  const [duration, setDuration] = useState('30')

  const refresh = async () => {
    setLoading(true)
    try { setExperiments(await api.experiments()); setError(null) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load experiments.') }
    finally { setLoading(false) }
  }

  useEffect(() => {
    void refresh()
    void api.scenarios()
      .then((items) => {
        setScenarios(items)
        setScenarioId((current) => items.some((item) => item.id === current) ? current : items[0]?.id ?? current)
        setScenarioError(null)
      })
      .catch((reason: unknown) => {
        if (import.meta.env.DEV) console.error('Scenario request failed', reason)
        setScenarioError('Unable to load incident scenarios. Check that the control API is running.')
      })
  }, [])

  async function create(event: FormEvent) {
    event.preventDefault()
    const seconds = Number(duration)
    if (!Number.isInteger(seconds) || seconds < 1 || seconds > 3600) {
      setError('Duration must be a whole number from 1 to 3600 seconds.')
      return
    }
    setSubmitting(true)
    setError(null)
    const selected = scenarios.find((item) => item.id === scenarioId)
    if (mode === 'failure' && !selected) {
      setError('Select a supported incident scenario.')
      setSubmitting(false)
      return
    }
    const payload: CreateExperiment = mode === 'failure' && selected
      ? { scenario_id: selected.id, target_service: selected.target_service, mode, failure_type: selected.failure_type, duration_seconds: seconds }
      : { target_service: 'payment-service', mode, duration_seconds: seconds }
    try {
      const experiment = await api.createExperiment(payload)
      await refresh()
      onSelect(experiment.id)
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Experiment could not be created.')
    } finally { setSubmitting(false) }
  }

  const selected = scenarios.find((item) => item.id === scenarioId) ?? null
  const canCreate = mode === 'verification' || Boolean(selected)

  return (
    <div className="page-stack">
      <header className="page-title-row"><div><p className="eyebrow">CONTROLLED EXPERIMENTS</p><h1>Experiments</h1><p className="lede">Create a failure experiment or collect a post-remediation verification window.</p></div></header>
      <Section title="New experiment">
        {scenarioError && <ErrorMessage message={scenarioError} />}
        <form className="experiment-form" onSubmit={create}>
          <label>Mode<select aria-label="Experiment mode" value={mode} onChange={(event) => setMode(event.target.value as ExperimentMode)}><option value="failure">Incident scenario</option><option value="verification">Verification observation</option></select></label>
          {mode === 'failure' && (
            <label>Scenario
              <select aria-label="Incident scenario" value={scenarioId} onChange={(event) => { setScenarioId(event.target.value); const next = scenarios.find((item) => item.id === event.target.value); if (next) setDuration(String(next.duration_seconds)) }}>
                {scenarios.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </label>
          )}
          <label>Duration (seconds)<input aria-label="Duration in seconds" type="number" min="1" max="3600" step="1" value={duration} onChange={(event) => setDuration(event.target.value)} required /></label>
          <button className="primary-button" type="submit" disabled={submitting || !canCreate}>{submitting ? 'Creating…' : mode === 'failure' ? 'Create failure experiment' : 'Create verification'}</button>
        </form>
        <div className="failure-brief">
          <p className="eyebrow">{mode === 'failure' ? 'EXPECTED BEHAVIOR' : 'WHAT VERIFICATION DOES'}</p>
          {mode === 'failure' && selected ? (
            <>
              <strong>{selected.target_service} · {selected.failure_type} · {selected.duration_seconds}s</strong>
              <p>{selected.expected_behavior}</p>
            </>
          ) : mode === 'failure' ? (
            <>
              <strong>No scenario loaded</strong>
              <p>Incident scenarios come from the control API. Unsupported combinations are not offered.</p>
            </>
          ) : (
            <>
              <strong>payment-service · no injection</strong>
              <p>A verification experiment does not inject a failure. Use it after the failure window to record a comparison observation.</p>
            </>
          )}
        </div>
        {error && <ErrorMessage message={error} />}
      </Section>
      <Section title="Experiment history">
        {loading ? <Loading label="Loading experiments..." /> : experiments.length === 0 ? <EmptyState title="No experiments yet" /> : (
          <div className="table-wrap"><table><thead><tr><th>Experiment</th><th>Target</th><th>Mode / failure</th><th>Duration</th><th>Status</th><th>Created</th><th>Started</th><th>Ended</th></tr></thead><tbody>{experiments.map((item) => (
            <tr key={item.id} className="selectable-row" onClick={() => onSelect(item.id)} tabIndex={0} onKeyDown={(event) => event.key === 'Enter' && onSelect(item.id)}>
              <td><button className="inline-link" onClick={(event) => { event.stopPropagation(); onSelect(item.id) }}>{shortId(item.id)}</button></td><td>{item.target_service}</td><td>{item.mode === 'verification' ? 'Verification' : item.failure_type}</td><td>{item.duration_seconds}s</td><td><StatusBadge value={item.status} /></td><td>{formatDate(item.created_at)}</td><td>{formatDate(item.started_at)}</td><td>{formatDate(item.ended_at)}</td>
            </tr>
          ))}</tbody></table></div>
        )}
      </Section>
    </div>
  )
}
