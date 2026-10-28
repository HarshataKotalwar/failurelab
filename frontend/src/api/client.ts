import type {
  CreateExperiment,
  IncidentScenario,
  EvidenceResponse,
  Experiment,
  ExperimentVerification,
  InvestigationEvaluation,
  InvestigationResult,
  RemediationPlan,
  ServiceInfo,
  ServiceObservation,
} from './types'

const baseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch {
    throw new ApiError('Control API is unavailable. Check the local FailureLab stack.', 0)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const parsed = messageFromDetail(body?.detail, response.status)
    if (import.meta.env.DEV) {
      console.error(`Control API ${init?.method ?? 'GET'} ${path} failed`, response.status, body)
    }
    throw new ApiError(parsed.message, response.status, parsed.code)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function messageFromDetail(detail: unknown, status: number): { message: string; code?: string } {
  if (Array.isArray(detail)) {
    const text = detail
      .map((item) => {
        if (!item || typeof item !== 'object') return null
        const record = item as { msg?: unknown; loc?: unknown }
        const msg = typeof record.msg === 'string' ? record.msg : null
        const loc = Array.isArray(record.loc) ? record.loc.filter((part) => part !== 'body').join('.') : ''
        return msg ? (loc ? `${loc}: ${msg}` : msg) : null
      })
      .filter((item): item is string => Boolean(item))
      .join(' ')
    return { message: text || `Control API returned HTTP ${status}.` }
  }
  if (detail && typeof detail === 'object') {
    const record = detail as { message?: unknown; code?: unknown }
    const code = typeof record.code === 'string' ? record.code : undefined
    if (status === 404) return { message: 'Experiment or investigation not found.', code }
    if (status === 409) return { message: 'This item is not in a valid state for that operation.', code }
    if (status === 502) return { message: 'An evidence or control dependency is unavailable.', code }
    if (typeof record.message === 'string') return { message: record.message, code }
    return { message: `Control API returned HTTP ${status}.`, code }
  }
  if (typeof detail === 'string' && detail.trim()) return { message: detail }
  if (status === 404) return { message: 'Experiment or investigation not found.' }
  if (status === 409) return { message: 'This item is not in a valid state for that operation.' }
  if (status === 502) return { message: 'An evidence or control dependency is unavailable.' }
  return { message: `Control API returned HTTP ${status}.` }
}

export const api = {
  health: () => request<{ status: string; service: string }>('/health'),
  registry: () => request<ServiceInfo[]>('/services'),
  scenarios: () => request<IncidentScenario[]>('/scenarios'),
  dependencies: () => request<Record<string, string[]>>('/dependencies'),
  services: () => request<ServiceObservation[]>('/services/health'),
  experiments: () => request<Experiment[]>('/experiments'),
  investigations: (limit = 10, experimentId?: string) =>
    request<InvestigationResult[]>(`/investigations?limit=${limit}${experimentId ? `&experiment_id=${experimentId}` : ''}`),
  experiment: (id: string) => request<Experiment>(`/experiments/${id}`),
  evidence: (id: string) => request<EvidenceResponse>(`/experiments/${id}/evidence`),
  investigation: (id: string) => request<InvestigationResult>(`/investigations/${id}`),
  remediation: (id: string) =>
    request<RemediationPlan>(`/investigations/${id}/remediation`),
  evaluate: (id: string) =>
    request<InvestigationEvaluation>(`/investigations/${id}/evaluate`, { method: 'POST' }),
  createExperiment: (values: CreateExperiment) =>
    request<Experiment>('/experiments', { method: 'POST', body: JSON.stringify(values) }),
  startExperiment: (id: string) =>
    request<Experiment>(`/experiments/${id}/start`, { method: 'POST' }),
  stopExperiment: (id: string) =>
    request<Experiment>(`/experiments/${id}/stop`, { method: 'POST' }),
  investigate: (id: string) =>
    request<InvestigationResult>(`/experiments/${id}/investigate`, { method: 'POST' }),
  verify: (baselineId: string, verificationId: string) =>
    request<ExperimentVerification>(`/experiments/${baselineId}/verify`, {
      method: 'POST',
      body: JSON.stringify({ verification_experiment_id: verificationId }),
    }),
  createOrder: () =>
    request<{ order_id: string; status: string }>('/orders', {
      method: 'POST',
      body: JSON.stringify({ product_id: 'product-1', quantity: 1, amount: 12.5 }),
    }),
}
