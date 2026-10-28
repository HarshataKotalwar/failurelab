import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { EvidenceRecord, InvestigationEvaluation, InvestigationResult, RemediationPlan } from '../api/types'
import { CopyValue, EmptyState, Section, StatusBadge, formatDate } from './common'

function EvidenceChain({ records }: { records: EvidenceRecord[] }) {
  const groups = [
    ['Experiment lifecycle', records.filter((record) => record.source === 'experiment')],
    ['Target signals', records.filter((record) => record.source !== 'experiment' && record.source !== 'dependency')],
    ['Dependency records', records.filter((record) => record.source === 'dependency')],
  ] as const
  if (records.length === 0) return <p className="muted">No cited evidence records are available to order into a chain.</p>
  return (
    <div className="story-chain">
      {groups.filter(([, items]) => items.length > 0).map(([title, items]) => (
        <div key={title}>
          <h4>{title}</h4>
          <ol>
            {items.map((record) => <li key={record.evidence_id}><code>{record.evidence_id}</code><span>{record.description}</span></li>)}
          </ol>
        </div>
      ))}
    </div>
  )
}

function DependencyTree({ dependencies, affected }: { dependencies: Record<string, string[]>; affected: InvestigationResult['affected_services'] }) {
  return (
    <ul className="dependency-tree">
      {Object.entries(dependencies).map(([service, downstreams]) => {
        const impact = affected.find((item) => item.service === service)
        return (
          <li key={service} className={impact ? 'affected' : ''}>
            <strong>{service}</strong>
            <span>{impact ? impact.impact : 'not established as affected'}</span>
            {downstreams.length > 0 && (
              <ul>
                {downstreams.map((downstream) => {
                  const child = affected.find((item) => item.service === downstream)
                  return <li key={downstream} className={child ? 'affected' : ''}><strong>{downstream}</strong><span>{child ? child.impact : 'not established as affected'}</span></li>
                })}
              </ul>
            )}
          </li>
        )
      })}
    </ul>
  )
}

export function providerLabel(provider: string) {
  return provider === 'mock' || provider === 'local'
    ? 'Local / Deterministic Investigator'
    : provider === 'gemini'
      ? 'Gemini Investigator'
      : provider === 'local_fallback'
        ? 'Local fallback (Gemini unavailable)'
      : provider
}

export function InvestigationView({
  result,
  remediation,
  evaluation,
}: {
  result: InvestigationResult
  remediation: RemediationPlan | null
  evaluation: InvestigationEvaluation | null
}) {
  const records = new Map(result.evidence_records.map((record) => [record.evidence_id, record]))
  const insufficient = result.outcome === 'insufficient_evidence'
  const telemetryIssues = Object.entries(result.source_status).filter(([, state]) => state.status !== 'available')
  const showLimits = result.degraded || result.limitations.length > 0 || result.missing_evidence.length > 0 || telemetryIssues.length > 0
  const timeline = [...result.timeline].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())
  const [dependencies, setDependencies] = useState<Record<string, string[]> | null>(null)
  useEffect(() => {
    let active = true
    api.dependencies().then((value) => { if (active) setDependencies(value) }).catch(() => { if (active) setDependencies({}) })
    return () => { active = false }
  }, [])
  const cited = new Set([...result.root_cause_evidence_ids, ...result.affected_services.flatMap((item) => item.evidence_ids), ...result.recommended_action_evidence_ids])
  const chain = result.evidence_records
    .filter((record) => cited.has(record.evidence_id))
    .sort((a, b) => new Date(a.observed_at).getTime() - new Date(b.observed_at).getTime())
  const why = result.evidence.filter((item) => result.root_cause_evidence_ids.includes(item.evidence_id))

  return (
    <div className="investigation-result">
      <header className="investigation-header">
        <div>
          <p className="eyebrow">INVESTIGATION RESULT</p>
          <div className="header-badges">
            <StatusBadge value={result.outcome} />
            {result.degraded && <StatusBadge value="degraded" />}
          </div>
        </div>
        <dl>
          <div>
            <dt>Experiment</dt>
            <dd className="mono">{result.experiment_id}</dd>
          </div>
          <div>
            <dt>Confidence</dt>
            <dd>{result.confidence.toFixed(2)} / 1</dd>
          </div>
          <div>
            <dt>Investigator</dt>
            <dd>{providerLabel(result.provider)}</dd>
          </div>
        </dl>
        <p className="muted">Deterministic, uncalibrated heuristic · not a probability · {result.confidence_method.replaceAll('_', ' ')}</p>
      </header>

      <section className="incident-story">
        <h2>Incident summary</h2>
        {insufficient ? (
          <p>FailureLab could not establish a sufficiently supported root cause from the available telemetry.</p>
        ) : (
          <p>{result.root_cause}</p>
        )}
        {!insufficient && why.length > 0 && (
          <>
            <h3>Why FailureLab believes this</h3>
            <ul>{why.map((item) => <li key={item.evidence_id}><code>{item.evidence_id}</code> {item.claim}</li>)}</ul>
          </>
        )}
        <h3>Evidence chain</h3>
        <EvidenceChain records={chain} />
        <h3>Confidence</h3>
        <p>
          The displayed score uses deterministic heuristic v1.
          {insufficient
            ? ' It stays at or below 0.25 because the investigator abstained.'
            : ' It rises when more evidence sources support the configured failure and the experiment has started. It falls when a telemetry source is partial, unavailable, or not found.'}
        </p>
        {dependencies && Object.keys(dependencies).length > 0 && (
          <>
            <h3>Service dependencies</h3>
            <DependencyTree dependencies={dependencies} affected={result.affected_services} />
          </>
        )}
      </section>

      {insufficient && (
        <div className="root-cause uncertain">
          <p className="eyebrow">INSUFFICIENT EVIDENCE</p>
          <h3>Insufficient evidence to determine a root cause.</h3>
          <p>FailureLab could not establish a sufficiently supported root cause from the available telemetry.</p>
          <p>The available telemetry was not sufficient to determine a reliable root cause.</p>
          <p>Confidence {result.confidence.toFixed(2)} / 1. Abstaining is an investigation outcome, not a failed request.</p>
        </div>
      )}

      {!insufficient && (
        <div className="root-cause">
          <p className="eyebrow">ROOT CAUSE</p>
          <h3>{result.root_cause}</h3>
          <p>
            Drawn from {result.root_cause_evidence_ids.length} cited evidence record{result.root_cause_evidence_ids.length === 1 ? '' : 's'}.
            Confidence {Math.round(result.confidence * 100)}%.
          </p>
        </div>
      )}

      <Section title="Evidence" aside={<span className="muted">Citations stay attached to the claims they support</span>}>
        {result.evidence.length > 0 ? (
          <ol className="evidence-chain">
            {result.evidence.map((citation) => {
              const record = records.get(citation.evidence_id)
              const supports = !insufficient && result.root_cause_evidence_ids.includes(citation.evidence_id)
              return (
                <li key={citation.evidence_id} className={supports ? 'supports-root-cause' : ''}>
                  <StatusBadge value={citation.source} />
                  <strong>{citation.service ?? 'system'}</strong>
                  <code>{citation.evidence_id}</code>
                  <span>{citation.description}</span>
                  <small>Claim supported: {citation.claim}</small>
                  <small>Observed {formatDate(record?.observed_at)}</small>
                  {supports && <small>Supports the root-cause conclusion</small>}
                  {citation.trace_id && <CopyValue label="Trace ID" value={citation.trace_id} />}
                </li>
              )
            })}
          </ol>
        ) : result.evidence_records.length > 0 ? (
          <div className="compact-list">
            {result.evidence_records.map((record) => (
              <div className="evidence-record-row" key={record.evidence_id}>
                <StatusBadge value={record.source} />
                <strong>{record.service ?? 'system'}</strong>
                <code>{record.evidence_id}</code>
                <span>{record.description}</span>
                <small>Observed {formatDate(record.observed_at)}{record.trace_id ? ` · trace ${record.trace_id}` : ''}</small>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState title="No evidence records returned" />
        )}
      </Section>

      <Section title="Timeline">
        {timeline.length === 0 ? <EmptyState title="No investigation events returned" /> : (
          <ol className="timeline">
            {timeline.map((item, index) => (
              <li key={`${item.timestamp}-${item.event}-${item.evidence_id ?? index}`}>
                <time>{formatDate(item.timestamp)}</time>
                <span className="timeline-source">{item.source}</span>
                <span>
                  <strong>{item.event}{item.service ? ` · ${item.service}` : ''}</strong>
                  <small>{item.description}</small>
                  {item.evidence_id && <code>{item.evidence_id}</code>}
                </span>
              </li>
            ))}
          </ol>
        )}
      </Section>

      <div className="result-columns">
        <Section title="Affected services">
          {result.affected_services.length === 0 ? <EmptyState title="No affected services established" /> : (
            <div className="affected-list">
              {result.affected_services.map((item) => (
                <article key={item.service}>
                  <div>
                    <strong>{item.service}</strong>
                    <StatusBadge value={item.impact} />
                  </div>
                  <small>Supporting evidence</small>
                  <ul>{item.evidence_ids.map((evidenceId) => <li key={evidenceId}><code>{evidenceId}</code></li>)}</ul>
                </article>
              ))}
            </div>
          )}
        </Section>
        <section className="recommendation">
          <p className="eyebrow">RECOMMENDED ACTION</p>
          <p>{result.recommended_action}</p>
          <small>Recommendation only. FailureLab does not execute this action automatically.</small>
          {result.recommended_action_evidence_ids.length > 0 && (
            <small>Cited evidence: {result.recommended_action_evidence_ids.join(', ')}</small>
          )}
        </section>
      </div>

      {showLimits && (
        <div className="limitation-banner">
          <strong>Limitations</strong>
          <p>{insufficient ? 'These gaps explain the abstention. Unavailable telemetry is not treated as healthy.' : 'These gaps stay visible beside the conclusion. Unavailable telemetry is not treated as healthy.'}</p>
          {result.degraded && <p>This investigation is marked degraded.</p>}
          {result.missing_evidence.length > 0 && (
            <div>
              <span>Missing evidence</span>
              <ul>{result.missing_evidence.map((item) => <li key={item}>{item}</li>)}</ul>
            </div>
          )}
          {result.limitations.length > 0 && (
            <div>
              <span>Limitations</span>
              <ul>{result.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
            </div>
          )}
          {telemetryIssues.length > 0 && (
            <div>
              <span>Unavailable or incomplete telemetry</span>
              <ul>
                {telemetryIssues.map(([source, state]) => (
                  <li key={source}>{source}: {state.status.replaceAll('_', ' ')}{state.message ? ` — ${state.message}` : ''}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      <Section title="Remediation recommendations" aside={<span className="muted">Not applied automatically</span>}>
        {!remediation ? <EmptyState title="No remediation plan loaded" /> : remediation.outcome === 'insufficient_evidence' ? (
          <EmptyState title="No plan generated from insufficient evidence" detail={remediation.limitations.join(' ') || 'The planner did not emit actions.'} />
        ) : remediation.actions.length === 0 ? (
          <EmptyState title="Plan returned no actions" />
        ) : (
          <div className="remediation-list">
            {remediation.actions.map((item) => (
              <article key={item.action_id}>
                <div><StatusBadge value={item.priority} /><strong>{item.description}</strong></div>
                <p><b>Rationale:</b> {item.rationale}</p>
                <p><b>Expected effect:</b> {item.expected_effect}</p>
                <small>Recommendation · {item.evidence_ids.filter((id) => records.has(id)).length} evidence links</small>
              </article>
            ))}
          </div>
        )}
      </Section>

      {evaluation && (
        <Section title="Investigator evaluation">
          <StatusBadge value={evaluation.overall} />
          <div className="evaluation-grid">
            {Object.entries({
              'Root cause': evaluation.root_cause,
              'Affected services': evaluation.affected_services,
              'Evidence citations': evaluation.evidence_citations,
              Abstention: evaluation.abstention,
            }).map(([name, dimension]) => (
              <div key={name}>
                <StatusBadge value={dimension.correct ? 'correct' : 'incorrect'} />
                <strong>{name}</strong>
                <small>{dimension.explanation}</small>
              </div>
            ))}
          </div>
        </Section>
      )}

      <Section title="Telemetry source status">
        <div className="evidence-state-row">
          {Object.entries(result.source_status).map(([source, state]) => (
            <div className="evidence-state" key={source}>
              <div><strong>{source}</strong><StatusBadge value={state.status} /></div>
              {state.message && <small>{state.message}</small>}
              {state.failed_sources && state.failed_sources.length > 0 && <small>Failed providers: {state.failed_sources.join(', ')}</small>}
            </div>
          ))}
        </div>
      </Section>
    </div>
  )
}
