import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { InvestigationEvaluation, InvestigationResult, RemediationPlan } from '../api/types'
import { InvestigationView, providerLabel } from '../components/InvestigationView'
import { EmptyState, ErrorMessage, Loading, Section, StatusBadge, formatDate, shortId } from '../components/common'

export function InvestigationPage({
  experimentId,
  investigationId,
  onBack,
  onOpenExperiment,
  onSelectInvestigation,
}: {
  experimentId: string | null
  investigationId: string | null
  onBack: () => void
  onOpenExperiment: (id: string) => void
  onSelectInvestigation: (investigationId: string, experimentId: string) => void
}) {
  const [investigation, setInvestigation] = useState<InvestigationResult | null>(null)
  const [recent, setRecent] = useState<InvestigationResult[]>([])
  const [remediation, setRemediation] = useState<RemediationPlan | null>(null)
  const [evaluation, setEvaluation] = useState<InvestigationEvaluation | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const requestGeneration = useRef(0)

  const loadRelated = useCallback(async (result: InvestigationResult) => {
    let planFailed = false
    let evaluationFailed = false
    const [plan, scored] = await Promise.all([
      api.remediation(result.investigation_id).catch((reason: unknown) => {
        planFailed = true
        if (import.meta.env.DEV) console.error('Remediation plan request failed', reason)
        return null
      }),
      api.evaluate(result.investigation_id).catch((reason: unknown) => {
        evaluationFailed = true
        if (import.meta.env.DEV) console.error('Investigation evaluation request failed', reason)
        return null
      }),
    ])
    setRemediation(plan)
    setEvaluation(scored)
    const failed = [planFailed ? 'remediation plan' : null, evaluationFailed ? 'evaluation' : null].filter(Boolean)
    setNotice(failed.length > 0 ? `The investigation result loaded. The ${failed.join(' and ')} could not be loaded.` : null)
  }, [])

  const load = useCallback(async () => {
    const generation = ++requestGeneration.current
    setLoading(true)
    setError(null)
    try {
      const saved = await api.investigations(20)
      const forExperiment = experimentId ? await api.investigations(20, experimentId) : []
      const selected = investigationId
        ? await api.investigation(investigationId)
        : experimentId
          ? forExperiment[0] ?? null
          : saved[0] ?? null
      if (generation !== requestGeneration.current) return
      setRecent(saved)
      setInvestigation(selected)
      if (selected) await loadRelated(selected)
      else {
        setRemediation(null)
        setEvaluation(null)
        setNotice(null)
      }
    } catch (reason) {
      if (generation !== requestGeneration.current) return
      if (import.meta.env.DEV) console.error('Investigation load failed', reason)
      setInvestigation(null)
      setError(reason instanceof ApiError ? reason.message : 'Unable to load investigations. Check that the control API is running.')
    } finally {
      if (generation === requestGeneration.current) setLoading(false)
    }
  }, [experimentId, investigationId, loadRelated])

  useEffect(() => { void load() }, [load])

  const activeExperimentId = experimentId ?? investigation?.experiment_id ?? null

  async function investigate() {
    if (!activeExperimentId) return
    setRunning(true)
    setError(null)
    try {
      const result = await api.investigate(activeExperimentId)
      setInvestigation(result)
      setRecent((current) => [result, ...current.filter((item) => item.investigation_id !== result.investigation_id)])
      await loadRelated(result)
      onSelectInvestigation(result.investigation_id, result.experiment_id)
    } catch (reason) {
      if (import.meta.env.DEV) console.error('Investigation request failed', reason)
      setError(reason instanceof ApiError ? reason.message : 'Investigation failed.')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="page-stack">
      <button className="text-button back-link" onClick={onBack}>{activeExperimentId ? '← Experiment' : '← Overview'}</button>
      <header className="page-title-row">
        <div>
          <p className="eyebrow">EVIDENCE-GROUNDED INVESTIGATION</p>
          <h1>Investigation</h1>
          <p className="lede">Saved investigations come from the control API. Run one against an experiment that has already started.</p>
        </div>
        {activeExperimentId && (
          <div className="detail-actions">
            <button className="secondary-button" onClick={() => onOpenExperiment(activeExperimentId)}>Experiment {shortId(activeExperimentId)}</button>
            <button className="primary-button" disabled={running} onClick={() => void investigate()}>
              {running ? 'Investigating…' : investigation ? 'Run investigation again' : 'Investigate incident'}
            </button>
          </div>
        )}
      </header>
      {error && <ErrorMessage message={error} />}
      {notice && <p className="muted" role="status">{notice}</p>}
      {loading ? <Loading label="Loading investigation..." /> : (
        <>
          {investigation ? (
            <InvestigationView result={investigation} remediation={remediation} evaluation={evaluation} />
          ) : (
            <EmptyState
              title={experimentId ? 'No investigation has been run for this experiment' : recent.length === 0 ? 'No investigations yet' : 'Select an investigation'}
              detail={experimentId ? 'Start the experiment, then run an investigation. The result is rendered from the control API response.' : recent.length === 0 ? 'Investigations appear here after an experiment has been analyzed.' : 'Choose a saved investigation below.'}
            />
          )}
          <Section title="Recent investigations">
            {recent.length === 0 ? <EmptyState title="No saved investigations" /> : (
              <div className="compact-list">
                {recent.map((item) => (
                  <button className="list-row" key={item.investigation_id} onClick={() => onSelectInvestigation(item.investigation_id, item.experiment_id)}>
                    <span>
                      <strong>{item.outcome === 'insufficient_evidence' ? 'Insufficient evidence' : 'Root cause identified'}</strong>
                      <small>{shortId(item.experiment_id)} · {providerLabel(item.provider)} · {formatDate(item.created_at)}</small>
                    </span>
                    <StatusBadge value={item.outcome} />
                  </button>
                ))}
              </div>
            )}
          </Section>
        </>
      )}
    </div>
  )
}
