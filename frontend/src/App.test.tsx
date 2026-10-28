import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import { ExperimentsPage } from './pages/ExperimentsPage'
import { ExperimentDetailPage } from './pages/ExperimentDetailPage'
import { InvestigationPage } from './pages/InvestigationPage'
import { IncidentConsole } from './components/IncidentConsole'
import type { EvidenceResponse, Experiment, InvestigationResult } from './api/types'

const { apiMock } = vi.hoisted(() => ({
  apiMock: {
    health: vi.fn(), registry: vi.fn(), services: vi.fn(), experiments: vi.fn(), investigations: vi.fn(),
    experiment: vi.fn(), evidence: vi.fn(), investigation: vi.fn(), remediation: vi.fn(),
    evaluate: vi.fn(), createExperiment: vi.fn(), startExperiment: vi.fn(), scenarios: vi.fn(), dependencies: vi.fn(),
    stopExperiment: vi.fn(), investigate: vi.fn(), verify: vi.fn(), createOrder: vi.fn(),
  },
}))
vi.mock('./api/client', () => ({ api: apiMock, ApiError: class ApiError extends Error {} }))

const experiment: Experiment = {
  id: '11111111-2222-3333-4444-555555555555', mode: 'failure',
  target_service: 'payment-service', failure_type: 'unavailable', duration_seconds: 60,
  status: 'CREATED', created_at: '2026-10-09T10:00:00Z', started_at: null, ended_at: null,
}

const evidence: EvidenceResponse = {
  experiment_id: experiment.id, status: 'RUNNING', target_service: 'payment-service', failure_type: 'unavailable',
  timeline: [], service_observations: [],
  metrics: { available: true, request_rate: null, http_5xx_rate: null, p95_latency_seconds: null, message: null, observed_at: null, window_start: null, window_end: null, window_seconds: 300 },
  traces: { status: 'unavailable', available: false, trace_ids: [], message: 'Tempo could not be queried', failed_sources: ['trace-id'], spans: [] },
  logs: { status: 'not_found', logs: [], message: 'No structured logs were found in this experiment window.', failed_sources: [] },
}

const investigation: InvestigationResult = {
  investigation_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', experiment_id: experiment.id,
  created_at: '2026-10-09T10:02:00Z', provider: 'mock', outcome: 'root_cause_identified',
  root_cause: 'unavailable failure on payment-service caused request failures', root_cause_evidence_ids: ['ev_123'],
  evidence: [{ evidence_id: 'ev_123', claim: 'Payment returned 503', source: 'log', service: 'payment-service', description: '/pay returned HTTP 503', trace_id: null }],
  evidence_records: [{ evidence_id: 'ev_123', source: 'log', service: 'payment-service', observed_at: '2026-10-09T10:01:00Z', event: 'request', description: '/pay returned HTTP 503', trace_id: null, details: { status_code: 503 } }],
  affected_services: [{ service: 'payment-service', impact: 'direct', evidence_ids: ['ev_123'] }],
  timeline: [{ timestamp: '2026-10-09T10:01:00Z', source: 'log', service: 'payment-service', event: 'request', description: '/pay returned HTTP 503', evidence_id: 'ev_123' }],
  source_status: { logs: { status: 'available', message: null }, traces: { status: 'not_found', message: 'No trace found' }, metrics: { status: 'available', message: null } },
  confidence: 0.81, confidence_method: 'deterministic_heuristic_v1', recommended_action: 'Stop and verify recovery', recommended_action_evidence_ids: ['ev_123'], missing_evidence: [], degraded: false, limitations: [],
}

function setupDetail(item = experiment) {
  apiMock.experiment.mockResolvedValue(item)
  apiMock.evidence.mockResolvedValue(evidence)
  apiMock.investigations.mockResolvedValue([])
  apiMock.experiments.mockResolvedValue([item])
  apiMock.remediation.mockResolvedValue(null)
  apiMock.evaluate.mockResolvedValue(null)
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMock.health.mockResolvedValue({ status: 'ok', service: 'control-api' })
  apiMock.services.mockResolvedValue([
    { service: 'payment-service', url: '', status: 'unavailable', status_code: 503, observed_at: '2026-10-09T10:00:00Z' },
    { service: 'order-service', url: '', status: 'healthy', status_code: 200, observed_at: '2026-10-09T10:00:00Z' },
    { service: 'inventory-service', url: '', status: 'healthy', status_code: 200, observed_at: '2026-10-09T10:00:00Z' },
  ])
  apiMock.experiments.mockResolvedValue([experiment])
  apiMock.investigations.mockResolvedValue([])
  apiMock.scenarios.mockResolvedValue([
    { id: 'payment-unavailable', name: 'Payment unavailable', summary: 'Payment returns HTTP 503.', target_service: 'payment-service', failure_type: 'unavailable', duration_seconds: 30, expected_behavior: 'payment-service /health and /pay return HTTP 503.', direct_services: ['payment-service'], indirect_services: ['order-service'] },
    { id: 'inventory-latency', name: 'Inventory latency', summary: 'Inventory reservations are delayed.', target_service: 'inventory-service', failure_type: 'latency', duration_seconds: 30, expected_behavior: 'inventory-service /reserve waits 1.5 seconds.', direct_services: ['inventory-service'], indirect_services: ['order-service'] },
  ])
  apiMock.dependencies.mockResolvedValue({ 'order-service': ['inventory-service', 'payment-service'], 'inventory-service': [], 'payment-service': [] })
  apiMock.registry.mockResolvedValue([
    { id: 'order-service', name: 'order-service', url: 'http://order-service:8001', status: 'configured' },
    { id: 'inventory-service', name: 'inventory-service', url: 'http://inventory-service:8002', status: 'configured' },
    { id: 'payment-service', name: 'payment-service', url: 'http://payment-service:8003', status: 'configured' },
  ])
})
afterEach(cleanup)

describe('FailureLab incident dashboard', () => {
  it('renders the dashboard and real service states', async () => {
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Incident overview' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create Experiment' })).toBeInTheDocument()
    expect(screen.getByText('Break it')).toBeInTheDocument()
    expect(screen.getAllByText('payment-service').length).toBeGreaterThan(0)
    expect(screen.getByText('unavailable')).toBeInTheDocument()
  })

  it('shows experiment status in the experiment table', async () => {
    const onSelect = vi.fn()
    render(<ExperimentsPage onSelect={onSelect} />)
    expect(await screen.findByText('CREATED')).toBeInTheDocument()
    expect(screen.getAllByText('payment-service').length).toBeGreaterThan(0)
    await userEvent.click(screen.getByRole('button', { name: /11111111/ }))
    expect(onSelect).toHaveBeenCalledWith(experiment.id)
  })

  it('offers supported scenarios and does not offer an unsupported order unavailable failure', async () => {
    render(<ExperimentsPage onSelect={vi.fn()} />)
    expect(await screen.findByRole('option', { name: 'Payment unavailable' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Inventory latency' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /order unavailable/i })).not.toBeInTheDocument()
    expect(screen.getByText(/payment-service \/health and \/pay return HTTP 503/)).toBeInTheDocument()
  })

  it('submits an unavailable failure experiment with the supported payload', async () => {
    apiMock.createExperiment.mockResolvedValue(experiment)
    apiMock.experiments.mockResolvedValue([])
    const onSelect = vi.fn()
    render(<ExperimentsPage onSelect={onSelect} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Create failure experiment' }))
    await waitFor(() => expect(apiMock.createExperiment).toHaveBeenCalledWith({ scenario_id: 'payment-unavailable', target_service: 'payment-service', mode: 'failure', failure_type: 'unavailable', duration_seconds: 30 }))
    expect(onSelect).toHaveBeenCalledWith(experiment.id)
  })

  it('submits a non-injecting verification experiment', async () => {
    apiMock.createExperiment.mockResolvedValue({ ...experiment, mode: 'verification', failure_type: 'none' })
    render(<ExperimentsPage onSelect={vi.fn()} />)
    await userEvent.selectOptions(await screen.findByLabelText('Experiment mode'), 'verification')
    await userEvent.click(screen.getByRole('button', { name: 'Create verification' }))
    await waitFor(() => expect(apiMock.createExperiment).toHaveBeenCalledWith({ target_service: 'payment-service', mode: 'verification', duration_seconds: 30 }))
  })

  it('starts and stops through the matching lifecycle endpoints', async () => {
    setupDetail()
    const user = userEvent.setup()
    const { unmount } = render(<ExperimentDetailPage id={experiment.id} onBack={vi.fn()} onSelect={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Start incident' }))
    expect(apiMock.startExperiment).toHaveBeenCalledWith(experiment.id)
    unmount()
    apiMock.experiment.mockResolvedValue({ ...experiment, status: 'RUNNING', started_at: '2026-10-09T10:01:00Z' })
    render(<ExperimentDetailPage id={experiment.id} onBack={vi.fn()} onSelect={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Stop experiment' }))
    expect(apiMock.stopExperiment).toHaveBeenCalledWith(experiment.id)
  })

  it('keeps Tempo unavailable distinct from not-found logs', async () => {
    setupDetail({ ...experiment, status: 'RUNNING', started_at: '2026-10-09T10:01:00Z' })
    render(<ExperimentDetailPage id={experiment.id} onBack={vi.fn()} onSelect={vi.fn()} />)
    expect((await screen.findAllByText('unavailable', { selector: '.status-badge' })).length).toBeGreaterThan(0)
    expect(screen.getByText('not found', { selector: '.status-badge' })).toBeInTheDocument()
    expect(screen.getByText('Trace provider unavailable')).toBeInTheDocument()
  })

  it('shows latency and HTTP error signals together in the incident console', async () => {
    const latencyExperiment: Experiment = {
      ...experiment,
      target_service: 'inventory-service',
      failure_type: 'latency',
      status: 'RUNNING',
      started_at: '2026-10-09T10:01:00Z',
    }
    const telemetry: EvidenceResponse = {
      ...evidence,
      experiment_id: latencyExperiment.id,
      target_service: 'inventory-service',
      failure_type: 'latency',
      logs: {
        status: 'available', message: null, failed_sources: [], logs: [
          { timestamp: '2026-10-09T10:01:02Z', service: 'inventory-service', level: 'info', message: 'request completed', route: '/reserve', status_code: 200, duration_ms: 1510, trace_id: 'trace-a' },
          { timestamp: '2026-10-09T10:01:03Z', service: 'order-service', level: 'error', message: 'request completed', route: '/orders', status_code: 502, duration_ms: 8, trace_id: 'trace-b' },
        ],
      },
    }
    render(<IncidentConsole experiment={latencyExperiment} evidence={telemetry} scenario={null} />)
    expect(screen.getByText('Slow successful target requests')).toBeInTheDocument()
    expect(screen.getByText('inventory-service /reserve slow request · 1510 ms · HTTP 200')).toBeInTheDocument()
    expect(screen.getByText('order-service /orders HTTP 502 · 8 ms')).toBeInTheDocument()
  })

  it('shows an investigation loading state before the result arrives', async () => {
    setupDetail({ ...experiment, status: 'RUNNING', started_at: '2026-10-09T10:01:00Z' })
    apiMock.investigations.mockResolvedValue([investigation])
    apiMock.remediation.mockResolvedValue({ plan_id: 'plan', investigation_id: investigation.investigation_id, experiment_id: experiment.id, outcome: 'planned', actions: [], limitations: [] })
    apiMock.evaluate.mockResolvedValue(null)
    let resolveInvestigation!: (result: InvestigationResult) => void
    apiMock.investigate.mockReturnValue(new Promise((resolve) => { resolveInvestigation = resolve }))
    render(<ExperimentDetailPage id={experiment.id} onBack={vi.fn()} onSelect={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Run investigation again' }))
    expect(screen.getByRole('button', { name: 'Investigating…' })).toBeDisabled()
    resolveInvestigation(investigation)
    expect((await screen.findAllByText(/unavailable failure on payment-service/)).length).toBeGreaterThan(0)
  })

  it('renders the result, evidence chain, affected service, and heuristic confidence honestly', async () => {
    setupDetail({ ...experiment, status: 'STOPPED', started_at: '2026-10-09T10:01:00Z', ended_at: '2026-10-09T10:02:00Z' })
    apiMock.investigations.mockResolvedValue([investigation])
    apiMock.remediation.mockResolvedValue({ plan_id: 'plan', investigation_id: investigation.investigation_id, experiment_id: experiment.id, outcome: 'planned', actions: [], limitations: [] })
    render(<ExperimentDetailPage id={experiment.id} onBack={vi.fn()} onSelect={vi.fn()} />)
    expect(await screen.findByRole('heading', { name: investigation.root_cause! })).toBeInTheDocument()
    expect(screen.getByText('0.81 / 1')).toBeInTheDocument()
    expect(screen.getByText(/not a probability/)).toBeInTheDocument()
    expect(screen.getByText('Local / Deterministic Investigator')).toBeInTheDocument()
    expect(screen.getAllByText('payment-service').length).toBeGreaterThan(0)
    expect(screen.getAllByText('ev_123').length).toBeGreaterThan(0)
    expect(screen.getByText('Claim supported: Payment returned 503')).toBeInTheDocument()
    expect(screen.getByText(/Recommendation only/)).toBeInTheDocument()
    const lifecycle = screen.getByRole('list', { name: 'Experiment lifecycle' })
    expect(lifecycle).toHaveTextContent('CREATED')
    expect(lifecycle).toHaveTextContent('RUNNING')
    expect(lifecycle).toHaveTextContent('STOPPED')
    expect(screen.getAllByText(experiment.id).length).toBeGreaterThan(0)
  })

  it('clearly labels a degraded local fallback after Gemini is unavailable', async () => {
    const fallback = {
      ...investigation,
      provider: 'local_fallback',
      degraded: true,
      limitations: ['Gemini unavailable: HTTP 503'],
    }
    setupDetail({ ...experiment, status: 'STOPPED', started_at: '2026-10-09T10:01:00Z', ended_at: '2026-10-09T10:02:00Z' })
    apiMock.investigations.mockResolvedValue([fallback])
    apiMock.remediation.mockResolvedValue(null)
    apiMock.evaluate.mockResolvedValue(null)
    render(<ExperimentDetailPage id={experiment.id} onBack={vi.fn()} onSelect={vi.fn()} />)
    expect(await screen.findByText('Local fallback (Gemini unavailable)')).toBeInTheDocument()
    expect(screen.getByText('Gemini unavailable: HTTP 503')).toBeInTheDocument()
    expect(screen.getByText('degraded')).toBeInTheDocument()
  })

  it('presents insufficient evidence without inventing a root cause', async () => {
    setupDetail({ ...experiment, status: 'RUNNING', started_at: '2026-10-09T10:01:00Z' })
    const insufficient = { ...investigation, outcome: 'insufficient_evidence' as const, root_cause: null, root_cause_evidence_ids: [], evidence: [], affected_services: [], missing_evidence: ['Tempo trace was not found'] }
    apiMock.investigations.mockResolvedValue([insufficient])
    apiMock.remediation.mockResolvedValue({ plan_id: 'plan', investigation_id: investigation.investigation_id, experiment_id: experiment.id, outcome: 'insufficient_evidence', actions: [], limitations: ['No plan'] })
    render(<ExperimentDetailPage id={experiment.id} onBack={vi.fn()} onSelect={vi.fn()} />)
    expect(await screen.findByText('Insufficient evidence to determine a root cause.')).toBeInTheDocument()
    expect(screen.getByText('The available telemetry was not sufficient to determine a reliable root cause.')).toBeInTheDocument()
    expect(screen.getByText('Tempo trace was not found')).toBeInTheDocument()
    expect(screen.queryByText(/^Root cause:/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /caused request failures/ })).not.toBeInTheDocument()
  })

  it('shows the latest saved investigation when the investigation section opens', async () => {
    apiMock.investigations.mockResolvedValue([investigation])
    apiMock.remediation.mockResolvedValue(null)
    apiMock.evaluate.mockResolvedValue(null)
    render(<InvestigationPage experimentId={null} investigationId={null} onBack={vi.fn()} onOpenExperiment={vi.fn()} onSelectInvestigation={vi.fn()} />)
    expect(await screen.findByRole('heading', { name: investigation.root_cause! })).toBeInTheDocument()
    expect(screen.getByText('Claim supported: Payment returned 503')).toBeInTheDocument()
    expect(screen.getByText('0.81 / 1')).toBeInTheDocument()
    expect(screen.getAllByText('payment-service').length).toBeGreaterThan(0)
  })

  it('renders a saved investigation with evidence citations', async () => {
    apiMock.investigations.mockResolvedValue([investigation])
    apiMock.remediation.mockResolvedValue({ plan_id: 'plan', investigation_id: investigation.investigation_id, experiment_id: experiment.id, outcome: 'planned', actions: [], limitations: [] })
    apiMock.evaluate.mockResolvedValue(null)
    render(<InvestigationPage experimentId={experiment.id} investigationId={null} onBack={vi.fn()} onOpenExperiment={vi.fn()} onSelectInvestigation={vi.fn()} />)
    expect(await screen.findByRole('heading', { name: investigation.root_cause! })).toBeInTheDocument()
    expect(screen.getByText('Claim supported: Payment returned 503')).toBeInTheDocument()
    expect(screen.getByText(/Recommendation only/)).toBeInTheDocument()
  })

  it('keeps a supplied root cause hidden when evidence is insufficient', async () => {
    const hidden = 'A confident cause that must not render'
    apiMock.investigations.mockResolvedValue([{ ...investigation, outcome: 'insufficient_evidence' as const, root_cause: hidden, root_cause_evidence_ids: [], missing_evidence: ['metrics window'] }])
    apiMock.remediation.mockResolvedValue(null)
    apiMock.evaluate.mockResolvedValue(null)
    render(<InvestigationPage experimentId={experiment.id} investigationId={null} onBack={vi.fn()} onOpenExperiment={vi.fn()} onSelectInvestigation={vi.fn()} />)
    expect(await screen.findByText('Insufficient evidence to determine a root cause.')).toBeInTheDocument()
    expect(screen.queryByText(hidden)).not.toBeInTheDocument()
    expect(screen.getByText('metrics window')).toBeInTheDocument()
  })

  it('opens the investigation list from the sidebar without a selected experiment', async () => {
    apiMock.investigations.mockResolvedValue([investigation])
    apiMock.investigation.mockResolvedValue(investigation)
    apiMock.remediation.mockResolvedValue(null)
    apiMock.evaluate.mockResolvedValue(null)
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: /Investigation/ }))
    expect(await screen.findByRole('heading', { name: 'Investigation' })).toBeInTheDocument()
    await userEvent.click(await screen.findByRole('button', { name: /Root cause identified/ }))
    expect(await screen.findByRole('heading', { name: investigation.root_cause! })).toBeInTheDocument()
    expect(apiMock.investigation).toHaveBeenCalledWith(investigation.investigation_id)
  })

  it('shows a useful error when the investigation request fails', async () => {
    apiMock.investigations.mockRejectedValue(new Error('stack trace: secret internals'))
    render(<InvestigationPage experimentId={experiment.id} investigationId={null} onBack={vi.fn()} onOpenExperiment={vi.fn()} onSelectInvestigation={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load investigations. Check that the control API is running.')
    expect(screen.queryByText(/secret internals/)).not.toBeInTheDocument()
  })

  it('translates API 404 errors and does not fill evidence panels with invented records', async () => {
    setupDetail()
    apiMock.experiment.mockRejectedValue(new Error('Experiment not found.'))
    const { unmount } = render(<ExperimentDetailPage id={experiment.id} onBack={vi.fn()} onSelect={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Experiment not found.')
    unmount()
    setupDetail()
    apiMock.evidence.mockResolvedValue({ ...evidence, logs: { status: 'not_found', logs: [], message: 'No logs in the experiment window', failed_sources: [] } })
    render(<ExperimentDetailPage id={experiment.id} onBack={vi.fn()} onSelect={vi.fn()} />)
    expect(await screen.findByText('No logs in this experiment window')).toBeInTheDocument()
    expect(screen.queryByText('POST /pay returned HTTP 503')).not.toBeInTheDocument()
  })
})
