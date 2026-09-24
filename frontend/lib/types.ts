export type ActivityStatus = "NOT_STARTED" | "IN_PROGRESS" | "COMPLETED";
export type RelationshipType = "FS" | "SS" | "FF" | "SF";

export interface Project {
  id: string;
  project_code: string;
  name: string;
  planned_start: string | null;
  planned_finish: string | null;
  data_date: string | null;
  created_at: string;
  updated_at: string;
  activity_count?: number;
  wbs_count?: number;
  relationship_count?: number;
}

export interface WBSNode {
  id: string;
  project_id: string;
  parent_id: string | null;
  code: string;
  name: string;
  created_at: string;
  children?: WBSNode[];
}

export interface WBSTreeNode {
  id: string;
  code: string;
  name: string;
  parent_id: string | null;
  activity_count: number;
  children: WBSTreeNode[];
}

export interface Activity {
  id: string;
  project_id: string;
  wbs_id: string | null;
  wbs_code?: string | null;
  wbs_name?: string | null;
  activity_code: string;
  name: string;
  activity_type: string;
  status: ActivityStatus;
  planned_start: string | null;
  planned_finish: string | null;
  actual_start: string | null;
  actual_finish: string | null;
  original_duration: number | null;
  remaining_duration: number | null;
  percent_complete: number | null;
  calendar: string | null;
  location_code?: string | null;
  discipline?: string | null;
  contractor_name?: string | null;
  planned_quantity?: number | null;
  quantity_unit?: string | null;
  created_at: string;
  updated_at: string;
  wbs_node?: WBSNode | null;
}

export interface Relationship {
  id: string;
  project_id: string;
  predecessor_id: string;
  successor_id: string;
  predecessor_code?: string;
  predecessor_name?: string;
  successor_code?: string;
  successor_name?: string;
  relationship_type: RelationshipType;
  lag: number;
  created_at: string;
  predecessor?: Activity;
  successor?: Activity;
}

export interface ValidationErrorDetail {
  field: string;
  message: string;
  code?: string;
  value?: any;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

// Integration pipeline types
export interface Artifact {
  artifact_id: string;
  project_id: string;
  report_id: string;
  artifact_type: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  storage_bucket: string;
  storage_key: string;
  uploaded_by: string;
  uploaded_at: string;
  extraction_status: string;
  error_message?: string | null;
}

export interface ExecutionEvent {
  event_id: string;
  artifact_id: string;
  source_report_id: string;
  source_document_name: string;
  storage_key: string;
  file_sha256: string;
  page_number: number;
  bounding_box?: number[] | null;
  verbatim_excerpt: string;
  activity_reference?: string | null;
  reported_activity_code?: string | null;
  description: string;
  execution_date: string;
  start_time?: string | null;
  end_time?: string | null;
  status_reported: string;
  quantity?: number | null;
  unit?: string | null;
  location?: string | null;
  discipline?: string | null;
  contractor?: string | null;
  asset?: string | null;
  wbs_hint?: string | null;
  extraction_confidence: number;
  extraction_notes?: string | null;
  status: string;
  matched_activity_id?: string | null;
  match_score?: number | null;
}

export interface MatchCandidate {
  activity_id: string;
  activity_code: string;
  activity_name: string;
  wbs_code?: string | null;
  match_score: number;
  margin_delta: number;
  score_breakdown: Record<string, any>;
}

export interface ReviewQueueItem {
  event: ExecutionEvent;
  candidates: MatchCandidate[];
  artifact_view_url?: string | null;
}

export interface AuditLogItem {
  id: string;
  project_id: string;
  activity_id: string;
  activity_code?: string | null;
  activity_name?: string | null;
  action: string;
  user_id: string;
  timestamp: string;
  previous_state?: string | null;
  new_state?: string | null;
  execution_event?: {
    id: string;
    verbatim_excerpt: string;
    execution_date: string;
  } | null;
  artifact?: {
    id: string;
    original_filename: string;
    sha256: string;
    storage_key: string;
    storage_bucket: string;
  } | null;
}

// -------------------------------------------------------------
// Time Agent Types
// -------------------------------------------------------------
export interface TimeAgentActionCard {
  type: "PROPOSAL_CONFIRMATION" | "CLARIFICATION_CHOICE" | "INFORMATIONAL" | "BULK_SCOPE_PROPOSAL";
  proposal_id?: string | null;
  proposal_status?: "PENDING" | "CONSUMED" | "APPLIED" | "REJECTED" | "EXPIRED" | "CANCELLED" | null;
  event_id?: string | null;
  activity_id?: string | null;
  activity_code?: string | null;
  activity_name?: string | null;
  current_percent?: number | null;
  proposed_percent?: number | null;
  incremental_quantity?: number | null;
  unit?: string | null;
  execution_date?: string | null;
  question?: string | null;
  options?: Array<{
    label: string;
    value: string;
    activity_code?: string;
    activity_name?: string;
    confidence_score?: number;
  }> | null;
  bulk_proposal_id?: string | null;
  bulk_activities?: Array<{
    activity_id: string;
    activity_code: string;
    activity_name: string;
    current_percent: number;
    proposed_percent: number;
  }> | null;
  bulk_count?: number | null;
  scope_label?: string | null;
  proposed_status?: string | null;
  target_percent?: number | null;
  confirm_label?: string | null;
  reject_label?: string | null;
  review_label?: string | null;
  language?: string | null;
}

export interface TimeAgentMessage {
  id: string;
  sender: "USER" | "AGENT" | "SYSTEM";
  content: string;
  message_metadata?: TimeAgentActionCard | null;
  created_at: string;
}

export interface TimeAgentConversationSummary {
  id: string;
  project_id: string;
  title: string;
  status: string;
  is_pinned?: boolean;
  language?: string | null;
  conversation_language?: string | null;
  conversation_style?: string | null;
  language_locked?: boolean;
  created_at: string;
  updated_at: string;
  message_count: number;
  active_activity_id?: string | null;
  active_event_id?: string | null;
}

export interface PendingAction {
  type: "BULK_UPDATE_CONFIRMATION" | "PROPOSAL_CONFIRMATION";
  proposalId?: string | null;
  proposal_id?: string | null;
  status: "PENDING" | "RESOLVING" | "RESOLVED" | "APPLIED" | "CANCELLED";
  activityCount?: number | null;
  activity_count?: number | null;
  targetPercent?: number | null;
  target_percent?: number | null;
  bulkActivities?: Array<{
    activity_id: string;
    activity_code: string;
    activity_name: string;
    current_percent: number;
    proposed_percent: number;
  }> | null;
  bulk_activities?: Array<{
    activity_id: string;
    activity_code: string;
    activity_name: string;
    current_percent: number;
    proposed_percent: number;
  }> | null;
}

export interface TimeAgentConversation {
  conversation_id: string;
  project_id: string;
  title?: string | null;
  status: string;
  is_pinned?: boolean;
  language?: string | null;
  conversation_language?: string | null;
  conversation_style?: string | null;
  language_locked?: boolean;
  active_activity?: {
    activity_id: string;
    activity_code: string;
    name: string;
    percent_complete: number;
    status: string;
  } | null;
  active_event_id?: string | null;
  clarification_turns: number;
  created_at?: string | null;
  updated_at?: string | null;
  pending_action?: PendingAction | null;
  history: TimeAgentMessage[];
}

export interface TimeAgentMessageResponse {
  message_id: string;
  sender: string;
  reply_text: string;
  action_card?: TimeAgentActionCard;
  created_at?: string;
  transcript?: string | null;
  detected_language?: string | null;
  conversation_language?: string | null;
  conversation_style?: string | null;
  language_locked?: boolean;
}

export interface TimeAgentVoiceMessageResponse extends TimeAgentMessageResponse {
  transcript: string;
  transcription?: string;
  detected_language?: string | null;
  detected_languages?: string[];
  is_code_mixed?: boolean;
  confidence?: number;
}

export interface TimeAgentConfirmResponse {
  status: "APPLIED" | "REJECTED";
  activity_id: string;
  activity_code: string;
  previous_percent: number;
  new_percent: number;
  audit_log_id: string;
  message: string;
}

export interface TimeAgentBulkConfirmResponse {
  status: "APPLIED" | "CANCELLED";
  updated_count: number;
  updated_activities: Array<{
    activity_id: string;
    activity_code: string;
    previous_percent: number;
    new_percent: number;
    status: string;
  }>;
  message: string;
}

export interface TTSRequest {
  text: string;
  language?: string;
  speaker?: string;
  model?: string;
}

export interface TTSResponse {
  audio_base64: string;
  content_type: string;
  language: string;
  speaker: string;
}


// -------------------------------------------------------------
// Institutional Memory V1 Types
// -------------------------------------------------------------

export interface EvidenceReference {
  execution_event_id: string;
  ledger_id?: string | null;
  activity_code: string;
  activity_name?: string | null;
  reporting_date: string;
  quantity?: number | null;
  unit?: string | null;
  source_type?: string | null;
  source_document_name?: string | null;
  verbatim_excerpt?: string | null;
  confidence?: number | null;
}

export interface HistoricalLedgerEntry {
  ledger_id: string;
  execution_event_id: string;
  project_id: string;
  project_code?: string | null;
  activity_id: string;
  activity_code: string;
  activity_name: string;
  reporting_date: string;
  installed_quantity?: number | null;
  unit_of_measure?: string | null;
  incremental_percent?: number | null;
  cumulative_percent: number;
  status_reported?: string | null;
  source_type?: string | null;
  discipline?: string | null;
  contractor?: string | null;
  location?: string | null;
  source_document_name?: string | null;
  artifact_id?: string | null;
  created_at: string;
}

export interface HistoricalLedgerPage {
  items: HistoricalLedgerEntry[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface ProductivityMetric {
  discipline?: string | null;
  contractor?: string | null;
  activity_code?: string | null;
  unit: string;
  total_quantity: number;
  reporting_days: number;
  rate: number;
  sample_count: number;
  formula: string;
  evidence: EvidenceReference[];
}

export interface DurationMetric {
  activity_id: string;
  activity_code: string;
  activity_name: string;
  discipline?: string | null;
  contractor?: string | null;
  planned_duration_days: number;
  actual_duration_days: number;
  variance_days: number;
  variance_percent: number;
  is_on_time: boolean;
  actual_start: string;
  actual_finish: string;
}

export interface DurationSummary {
  activities_completed: number;
  average_planned_duration: number;
  average_actual_duration: number;
  average_variance_days: number;
  on_time_count: number;
  delayed_count: number;
  p50_duration?: number | null;
  p80_duration?: number | null;
  items: DurationMetric[];
}

export interface HistoricalInsight {
  insight_type: string;
  title: string;
  summary: string;
  metric_value?: number | null;
  unit?: string | null;
  sample_size: number;
  reporting_days?: number | null;
  evidence: EvidenceReference[];
  limitations: string[];
}

export interface PlanningBenchmark {
  activity_code?: string | null;
  discipline?: string | null;
  sample_size: number;
  median_actual_duration?: number | null;
  average_actual_duration?: number | null;
  p50_duration?: number | null;
  p80_duration?: number | null;
  observed_rate?: number | null;
  rate_unit?: string | null;
  status: "SUFFICIENT_SAMPLE" | "INSUFFICIENT_SAMPLE" | "NO_HISTORICAL_BENCHMARK";
  advisory_message: string;
  evidence: EvidenceReference[];
}

export interface InstitutionalMemorySummary {
  verified_event_count: number;
  ledger_entry_count: number;
  completed_activity_count: number;
  total_quantity_by_unit: Record<string, number>;
  average_actual_duration?: number | null;
  average_duration_variance?: number | null;
  top_productivities: ProductivityMetric[];
  latest_reporting_date?: string | null;
  data_quality: {
    verified_events?: number;
    usable_ledger_entries?: number;
    records_with_quantity?: number;
    records_with_valid_units?: number;
    completed_activities_with_dates?: number;
  };
}

export interface HistoricalQueryRequest {
  query_type: "PRODUCTIVITY" | "DURATION" | "VARIANCE" | "EXECUTION_HISTORY" | "SUMMARY";
  activity_code?: string | null;
  discipline?: string | null;
  contractor?: string | null;
  location?: string | null;
  unit?: string | null;
  from_date?: string | null;
  to_date?: string | null;
  natural_query?: string | null;
}

export interface HistoricalQueryResponse {
  query_type: string;
  summary: string;
  metrics: any[];
  insights: HistoricalInsight[];
  evidence: EvidenceReference[];
  data_status: "CONFIRMED" | "INSUFFICIENT_DATA" | "NO_RECORDS";
}

export interface CPMActivityNode {
  id: string;
  activity_code: string;
  name: string;
  early_start: string | null;
  early_finish: string | null;
  late_start: string | null;
  late_finish: string | null;
  total_float: number | null;
  free_float: number | null;
  is_critical: boolean;
  is_near_critical: boolean;
  has_negative_float: boolean;
  driving_predecessor_id?: string | null;
  driving_predecessor_code?: string | null;
}

export interface CPMResult {
  project_id: string;
  project_start: string | null;
  project_finish: string | null;
  project_duration_days: number;
  critical_path: string[];
  critical_activities: string[];
  near_critical_activities: string[];
  negative_float_activities: string[];
  open_start_activities: string[];
  open_finish_activities: string[];
  isolated_activities: string[];
  logic_quality_percent: number;
  activities: Record<string, CPMActivityNode>;
  cycles_detected: boolean;
  error?: string | null;
}

