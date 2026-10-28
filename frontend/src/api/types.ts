export type ExperimentMode = 'failure' | 'verification'
export type ExperimentStatus = 'CREATED' | 'RUNNING' | 'STOPPED'

export interface Experiment {
  id: string
  mode: ExperimentMode
  target_service: string
  failure_type: string
  duration_seconds: number
  status: ExperimentStatus
  scenario?: string | null
  created_at: string
  started_at: string | null
  ended_at: string | null
}

export interface ServiceObservation {
  service: string
  url: string
  status: 'healthy' | 'unavailable' | 'unreachable' | string
  status_code: number | null
  observed_at: string
}

export interface EvidenceLog {
  timestamp: string
  service: string
  level: string
  message: string
  route: string | null
  status_code: number | null
  duration_ms: number | null
  trace_id: string | null
}

export interface EvidenceSpan {
  trace_id: string
  span_id: string
  parent_span_id: string | null
  service_name: string
  operation_name: string
  start_time: string
  end_time: string
  duration_ms: number
  status: string
  attributes: Record<string, string | number | boolean>
}

export interface EvidenceResponse {
  experiment_id: string
  status: string
  target_service: string
  failure_type: string
  timeline: { event: string; status: string; at: string }[]
  service_observations: ServiceObservation[]
  metrics: {
    available: boolean
    request_rate: number | null
    http_5xx_rate: number | null
    p95_latency_seconds: number | null
    message: string | null
    observed_at: string | null
    window_start: string | null
    window_end: string | null
    window_seconds: number | null
  }
  window_metrics?: ServiceWindowMetric[]
  observation_window_start?: string | null
  observation_window_end?: string | null
  traces: {
    status: 'available' | 'partial' | 'not_found' | 'unavailable'
    available: boolean
    trace_ids: string[]
    message: string | null
    failed_sources: string[]
    spans: EvidenceSpan[]
  }
  logs: {
    status: 'available' | 'partial' | 'not_found' | 'unavailable'
    logs: EvidenceLog[]
    message: string | null
    failed_sources: string[]
  }
}

export interface ServiceWindowMetric {
  service: string
  available: boolean
  request_count: number | null
  error_count: number | null
  error_rate: number | null
  p95_latency_seconds: number | null
  message: string | null
}

export interface IncidentScenario {
  id: string
  name: string
  summary: string
  target_service: string
  failure_type: 'unavailable' | 'http_500' | 'latency' | 'timeout'
  duration_seconds: number
  expected_behavior: string
  direct_services: string[]
  indirect_services: string[]
}

export interface EvidenceRecord {
  evidence_id: string
  source: string
  service: string | null
  observed_at: string
  event: string
  description: string
  trace_id: string | null
  details: Record<string, string | number | boolean | null>
}

export interface InvestigationResult {
  investigation_id: string
  experiment_id: string
  created_at: string
  provider: string
  outcome: 'root_cause_identified' | 'insufficient_evidence'
  root_cause: string | null
  root_cause_evidence_ids: string[]
  evidence: {
    evidence_id: string
    claim: string
    source: string
    service: string | null
    description: string
    trace_id: string | null
  }[]
  evidence_records: EvidenceRecord[]
  affected_services: { service: string; impact: 'direct' | 'indirect'; evidence_ids: string[] }[]
  timeline: {
    timestamp: string
    source: string
    service: string | null
    event: string
    description: string
    evidence_id: string | null
  }[]
  source_status: Record<
    string,
    {
      status: 'available' | 'partial' | 'not_found' | 'unavailable'
      available?: boolean
      message: string | null
      failed_sources?: string[]
    }
  >
  confidence: number
  confidence_method: string
  recommended_action: string
  recommended_action_evidence_ids: string[]
  missing_evidence: string[]
  degraded: boolean
  limitations: string[]
}

export interface RemediationPlan {
  plan_id: string
  investigation_id: string
  experiment_id: string
  outcome: 'planned' | 'insufficient_evidence'
  actions: {
    action_id: string
    action: string
    description: string
    priority: 'high' | 'medium' | 'low'
    rationale: string
    expected_effect: string
    evidence_ids: string[]
  }[]
  limitations: string[]
}

export interface InvestigationEvaluation {
  investigation_id: string
  experiment_id: string
  root_cause: { correct: boolean; explanation: string }
  affected_services: { correct: boolean; explanation: string }
  evidence_citations: { correct: boolean; explanation: string }
  abstention: { correct: boolean; explanation: string }
  overall: 'correct_investigation' | 'unsupported_or_incorrect'
}

export interface ExperimentVerification {
  baseline_experiment_id: string
  verification_experiment_id: string
  baseline_investigation_id: string
  verification_investigation_id: string
  baseline_window_start: string | null
  baseline_window_end: string | null
  verification_window_start: string | null
  verification_window_end: string | null
  status: 'improved' | 'no_significant_change' | 'regressed' | 'insufficient_data'
  comparisons: {
    metric: string
    baseline_value: number | string | null
    verification_value: number | string | null
    direction: 'improved' | 'unchanged' | 'regressed' | 'not_comparable'
    unit: string | null
  }[]
  limitations: string[]
}

export interface ServiceInfo {
  id: string
  name: string
  url: string
  status: string
}

export interface CreateExperiment {
  target_service: string
  mode: ExperimentMode
  failure_type?: 'unavailable' | 'http_500' | 'latency' | 'timeout'
  scenario_id?: string
  duration_seconds: number
}
