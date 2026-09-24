import {
  Activity,
  Artifact,
  AuditLogItem,
  PaginatedResponse,
  Project,
  Relationship,
  ReviewQueueItem,
  TimeAgentActionCard,
  TimeAgentBulkConfirmResponse,
  TimeAgentConfirmResponse,
  TimeAgentConversation,
  TimeAgentConversationSummary,
  TimeAgentMessage,
  TimeAgentMessageResponse,
  TimeAgentVoiceMessageResponse,
  ValidationErrorDetail,
  WBSNode,
  WBSTreeNode,
  DurationSummary,
  HistoricalLedgerPage,
  HistoricalQueryRequest,
  HistoricalQueryResponse,
  InstitutionalMemorySummary,
  PlanningBenchmark,
  ProductivityMetric,
  CPMResult,
  TTSRequest,
  TTSResponse,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

export class ApiError extends Error {
  errors: ValidationErrorDetail[];
  status: number;
  validation?: { error: string; errors: ValidationErrorDetail[] };

  constructor(message: string, status: number, errors: ValidationErrorDetail[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.errors = errors;
    this.validation = { error: message, errors };
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let errDetail: any = {};
    try {
      errDetail = await res.json();
    } catch {
      errDetail = { detail: res.statusText };
    }

    const message = errDetail.error || errDetail.detail || "API request failed";
    const errors = errDetail.errors || [];
    throw new ApiError(message, res.status, errors);
  }
  return res.json() as Promise<T>;
}

// -------------------------------------------------------------
// Schedule Project & Activity APIs
// -------------------------------------------------------------
export async function fetchProjects(): Promise<Project[]> {
  const res = await fetch(`${API_BASE}/projects`);
  return handleResponse<Project[]>(res);
}

export async function fetchProject(id: string): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects/${id}`);
  return handleResponse<Project>(res);
}

export async function updateProject(id: string, updates: Partial<Project>): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  return handleResponse<Project>(res);
}

export async function updateProjectDataDate(id: string, dataDate: string | null): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects/${id}/data-date`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data_date: dataDate ? new Date(dataDate).toISOString() : null }),
  });
  return handleResponse<Project>(res);
}

export async function deleteProject(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/projects/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete project");
}

export async function uploadScheduleFile(file: File, projectCode?: string, name?: string): Promise<Project> {
  const formData = new FormData();
  formData.append("file", file);
  if (projectCode) formData.append("project_code", projectCode);
  if (name) formData.append("name", name);

  const res = await fetch(`${API_BASE}/projects/import`, {
    method: "POST",
    body: formData,
  });
  return handleResponse<Project>(res);
}

export async function fetchActivities(
  projectId: string,
  params?: {
    page?: number;
    page_size?: number;
    search?: string;
    activity_code?: string;
    name?: string;
    status?: string;
    wbs_id?: string;
    sort_by?: string;
    sort_desc?: boolean;
    sort_dir?: string;
  }
): Promise<PaginatedResponse<Activity>> {
  const query = new URLSearchParams();
  if (params?.page) query.set("page", params.page.toString());
  if (params?.page_size) query.set("page_size", params.page_size.toString());
  if (params?.search) query.set("search", params.search);
  if (params?.activity_code) query.set("activity_code", params.activity_code);
  if (params?.name) query.set("name", params.name);
  if (params?.status) query.set("status", params.status);
  if (params?.wbs_id) query.set("wbs_id", params.wbs_id);
  if (params?.sort_by) query.set("sort_by", params.sort_by);
  if (params?.sort_desc !== undefined) query.set("sort_desc", params.sort_desc.toString());
  if (params?.sort_dir) query.set("sort_dir", params.sort_dir);

  const res = await fetch(`${API_BASE}/projects/${projectId}/activities?${query.toString()}`);
  return handleResponse<PaginatedResponse<Activity>>(res);
}

export async function fetchProjectCPM(projectId: string): Promise<CPMResult> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/cpm`);
  return handleResponse<CPMResult>(res);
}


export async function createActivity(projectId: string, data: Partial<Activity>): Promise<Activity> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/activities`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<Activity>(res);
}

export async function updateActivity(id: string, updates: Partial<Activity>): Promise<Activity> {
  const res = await fetch(`${API_BASE}/activities/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  return handleResponse<Activity>(res);
}

export async function deleteActivity(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/activities/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete activity");
}

export async function fetchProjectWbs(projectId: string): Promise<WBSNode[]> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/wbs`);
  return handleResponse<WBSNode[]>(res);
}

export async function fetchProjectWbsTree(projectId: string): Promise<WBSTreeNode[]> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/wbs/tree`);
  return handleResponse<WBSTreeNode[]>(res);
}

export async function fetchProjectRelationships(projectId: string): Promise<Relationship[]> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/relationships`);
  return handleResponse<Relationship[]>(res);
}

export async function createRelationship(projectId: string, data: Partial<Relationship>): Promise<Relationship> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/relationships`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<Relationship>(res);
}

export async function deleteRelationship(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/relationships/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete relationship");
}

// -------------------------------------------------------------
// Field Ingestion, MinIO Artifacts & Review Pipeline APIs
// -------------------------------------------------------------
export async function uploadFieldArtifact(
  projectId: string,
  file: File,
  reportId?: string,
  uploadedBy: string = "site-user"
): Promise<{ artifact: Artifact; is_duplicate: boolean; message: string }> {
  const formData = new FormData();
  formData.append("file", file);
  if (reportId) formData.append("report_id", reportId);
  formData.append("uploaded_by", uploadedBy);

  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/artifacts/upload`, {
    method: "POST",
    body: formData,
  });
  return handleResponse<{ artifact: Artifact; is_duplicate: boolean; message: string }>(res);
}

export async function fetchProjectArtifacts(projectId: string): Promise<Artifact[]> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/artifacts`);
  return handleResponse<Artifact[]>(res);
}

export async function extractArtifact(
  artifactId: string,
  forceReextract: boolean = false
): Promise<{ artifact_id: string; status: string; events_extracted: number; events: any[] }> {
  const url = forceReextract
    ? `${API_BASE}/api/v1/artifacts/${artifactId}/extract?force_reextract=true`
    : `${API_BASE}/api/v1/artifacts/${artifactId}/extract`;
  const res = await fetch(url, {
    method: "POST",
  });
  return handleResponse<{ artifact_id: string; status: string; events_extracted: number; events: any[] }>(res);
}

export async function evaluateMatching(projectId: string, eventIds?: string[]): Promise<any> {
  const res = await fetch(`${API_BASE}/api/v1/matching/evaluate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId, event_ids: eventIds }),
  });
  return handleResponse<any>(res);
}

export async function fetchReviewQueue(projectId: string): Promise<{ project_id: string; pending_count: number; items: ReviewQueueItem[] }> {
  const res = await fetch(`${API_BASE}/api/v1/review/queue?project_id=${projectId}`);
  return handleResponse<{ project_id: string; pending_count: number; items: ReviewQueueItem[] }>(res);
}

export async function submitReviewDecision(decision: {
  event_id: string;
  decision: "APPROVED" | "REJECTED" | "REASSIGNED";
  activity_id?: string;
  adjustment_percent?: number;
  reviewer_id?: string;
  notes?: string;
}): Promise<any> {
  const res = await fetch(`${API_BASE}/api/v1/review/decisions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(decision),
  });
  return handleResponse<any>(res);
}

export async function fetchAuditTrail(projectId: string): Promise<{ project_id: string; total_records: number; audit_trail: AuditLogItem[] }> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/audit-trail`);
  return handleResponse<{ project_id: string; total_records: number; audit_trail: AuditLogItem[] }>(res);
}

export function getExportXerUrl(projectId: string): string {
  return `${API_BASE}/api/v1/projects/${projectId}/export/xer`;
}

// -------------------------------------------------------------
// Time Agent APIs
// -------------------------------------------------------------
export async function fetchAgentConversations(
  projectId: string,
  searchQuery?: string,
  userId: string = "site-supervisor"
): Promise<TimeAgentConversationSummary[]> {
  const url = new URL(`${API_BASE}/api/v1/projects/${projectId}/agent/conversations`);
  if (searchQuery && searchQuery.trim()) {
    url.searchParams.set("q", searchQuery.trim());
  }
  const res = await fetch(url.toString(), {
    headers: {
      "X-User-ID": userId,
    },
  });
  return handleResponse<TimeAgentConversationSummary[]>(res);
}

export async function getAgentConversation(
  projectId: string,
  conversationId: string
): Promise<TimeAgentConversation> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/agent/conversations/${conversationId}`);
  return handleResponse<TimeAgentConversation>(res);
}

export async function pinAgentConversation(
  projectId: string,
  conversationId: string,
  isPinned: boolean
): Promise<TimeAgentConversationSummary> {
  const res = await fetch(
    `${API_BASE}/api/v1/projects/${projectId}/agent/conversations/${conversationId}/pin`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ is_pinned: isPinned }),
    }
  );
  return handleResponse<TimeAgentConversationSummary>(res);
}

export async function deleteAgentConversation(
  projectId: string,
  conversationId: string
): Promise<{ success: boolean; message: string }> {
  const res = await fetch(
    `${API_BASE}/api/v1/projects/${projectId}/agent/conversations/${conversationId}`,
    {
      method: "DELETE",
    }
  );
  return handleResponse<{ success: boolean; message: string }>(res);
}

export async function startOrGetConversation(
  projectId: string,
  activeActivityId?: string,
  forceNew: boolean = false,
  title?: string,
  userId: string = "site-supervisor"
): Promise<TimeAgentConversation> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/agent/conversations`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-ID": userId,
    },
    body: JSON.stringify({
      active_activity_id: activeActivityId || null,
      force_new: forceNew,
      title: title || null,
    }),
  });
  return handleResponse<TimeAgentConversation>(res);
}

export async function sendAgentMessage(
  projectId: string,
  conversationId: string,
  content: string,
  userId: string = "site-supervisor"
): Promise<TimeAgentMessageResponse> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/agent/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-ID": userId,
    },
    body: JSON.stringify({ content }),
  });
  return handleResponse<TimeAgentMessageResponse>(res);
}

export async function sendAgentVoiceMessage(
  projectId: string,
  conversationId: string,
  audioBlob: Blob,
  fileName: string = "recording.webm",
  userId: string = "site-supervisor",
  signal?: AbortSignal
): Promise<TimeAgentVoiceMessageResponse> {
  const formData = new FormData();
  formData.append("file", audioBlob, fileName);

  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/agent/conversations/${conversationId}/voice`, {
    method: "POST",
    headers: {
      "X-User-ID": userId,
    },
    body: formData,
    signal,
  });
  return handleResponse<TimeAgentVoiceMessageResponse>(res);
}

export async function generateTTS(
  payload: TTSRequest,
  signal?: AbortSignal
): Promise<TTSResponse> {
  const res = await fetch(`${API_BASE}/api/tts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal,
  });
  return handleResponse<TTSResponse>(res);
}


export async function uploadAgentAttachment(
  projectId: string,
  conversationId: string,
  file: File,
  userId: string = "site-supervisor"
): Promise<{ artifact_id: string; filename: string; extracted_events_count: number; agent_message: string; action_card?: TimeAgentActionCard }> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/agent/conversations/${conversationId}/attachments`, {
    method: "POST",
    headers: {
      "X-User-ID": userId,
    },
    body: formData,
  });
  return handleResponse<{ artifact_id: string; filename: string; extracted_events_count: number; agent_message: string; action_card?: TimeAgentActionCard }>(res);
}

export async function confirmUpdateProposal(
  projectId: string,
  conversationId: string,
  proposalId: string,
  action: "CONFIRM" | "REJECT" = "CONFIRM",
  userId: string = "site-supervisor"
): Promise<TimeAgentConfirmResponse> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/agent/conversations/${conversationId}/confirm`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-ID": userId,
    },
    body: JSON.stringify({ proposal_id: proposalId, action }),
  });
  return handleResponse<TimeAgentConfirmResponse>(res);
}

export async function confirmTimeAgentBulkProposal(
  projectId: string,
  conversationId: string,
  activityIds: string[],
  action: "CONFIRM" | "CANCEL" = "CONFIRM",
  targetPercent: number = 100.0,
  userId: string = "site-supervisor"
): Promise<TimeAgentBulkConfirmResponse> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/agent/conversations/${conversationId}/bulk-confirm`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-ID": userId,
    },
    body: JSON.stringify({
      activity_ids: activityIds,
      action,
      target_percent: targetPercent,
    }),
  });
  return handleResponse<TimeAgentBulkConfirmResponse>(res);
}

// -------------------------------------------------------------
// Institutional Memory V1 APIs
// -------------------------------------------------------------

export async function fetchInstitutionalMemorySummary(
  projectId: string
): Promise<InstitutionalMemorySummary> {
  const res = await fetch(
    `${API_BASE}/api/v1/projects/${projectId}/institutional-memory/summary`
  );
  return handleResponse<InstitutionalMemorySummary>(res);
}

export async function fetchInstitutionalMemoryLedger(
  projectId: string,
  params: {
    activity_code?: string;
    discipline?: string;
    contractor?: string;
    location?: string;
    unit?: string;
    from_date?: string;
    to_date?: string;
    source_type?: string;
    status?: string;
    page?: number;
    page_size?: number;
  } = {}
): Promise<HistoricalLedgerPage> {
  const query = new URLSearchParams();
  if (params.activity_code) query.append("activity_code", params.activity_code);
  if (params.discipline) query.append("discipline", params.discipline);
  if (params.contractor) query.append("contractor", params.contractor);
  if (params.location) query.append("location", params.location);
  if (params.unit) query.append("unit", params.unit);
  if (params.from_date) query.append("from_date", params.from_date);
  if (params.to_date) query.append("to_date", params.to_date);
  if (params.source_type) query.append("source_type", params.source_type);
  if (params.status) query.append("status", params.status);
  if (params.page) query.append("page", params.page.toString());
  if (params.page_size) query.append("page_size", params.page_size.toString());

  const qs = query.toString();
  const url = `${API_BASE}/api/v1/projects/${projectId}/institutional-memory/ledger${qs ? `?${qs}` : ""}`;
  const res = await fetch(url);
  return handleResponse<HistoricalLedgerPage>(res);
}

export async function fetchInstitutionalMemoryProductivity(
  projectId: string,
  params: {
    discipline?: string;
    contractor?: string;
    activity_code?: string;
    unit?: string;
    from_date?: string;
    to_date?: string;
  } = {}
): Promise<ProductivityMetric[]> {
  const query = new URLSearchParams();
  if (params.discipline) query.append("discipline", params.discipline);
  if (params.contractor) query.append("contractor", params.contractor);
  if (params.activity_code) query.append("activity_code", params.activity_code);
  if (params.unit) query.append("unit", params.unit);
  if (params.from_date) query.append("from_date", params.from_date);
  if (params.to_date) query.append("to_date", params.to_date);

  const qs = query.toString();
  const url = `${API_BASE}/api/v1/projects/${projectId}/institutional-memory/productivity${qs ? `?${qs}` : ""}`;
  const res = await fetch(url);
  return handleResponse<ProductivityMetric[]>(res);
}

export async function fetchInstitutionalMemoryDurations(
  projectId: string,
  params: {
    discipline?: string;
    activity_code?: string;
  } = {}
): Promise<DurationSummary> {
  const query = new URLSearchParams();
  if (params.discipline) query.append("discipline", params.discipline);
  if (params.activity_code) query.append("activity_code", params.activity_code);

  const qs = query.toString();
  const url = `${API_BASE}/api/v1/projects/${projectId}/institutional-memory/durations${qs ? `?${qs}` : ""}`;
  const res = await fetch(url);
  return handleResponse<DurationSummary>(res);
}

export async function fetchInstitutionalMemoryBenchmarks(
  projectId: string,
  params: {
    activity_code?: string;
    discipline?: string;
    unit?: string;
  } = {}
): Promise<PlanningBenchmark> {
  const query = new URLSearchParams();
  if (params.activity_code) query.append("activity_code", params.activity_code);
  if (params.discipline) query.append("discipline", params.discipline);
  if (params.unit) query.append("unit", params.unit);

  const qs = query.toString();
  const url = `${API_BASE}/api/v1/projects/${projectId}/institutional-memory/benchmarks${qs ? `?${qs}` : ""}`;
  const res = await fetch(url);
  return handleResponse<PlanningBenchmark>(res);
}

export async function queryInstitutionalMemory(
  projectId: string,
  req: HistoricalQueryRequest
): Promise<HistoricalQueryResponse> {
  const res = await fetch(
    `${API_BASE}/api/v1/projects/${projectId}/institutional-memory/query`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(req),
    }
  );
  return handleResponse<HistoricalQueryResponse>(res);
}

export function exportInstitutionalMemoryLedgerUrl(
  projectId: string,
  params: {
    activity_code?: string;
    discipline?: string;
    contractor?: string;
  } = {}
): string {
  const query = new URLSearchParams();
  if (params.activity_code) query.append("activity_code", params.activity_code);
  if (params.discipline) query.append("discipline", params.discipline);
  if (params.contractor) query.append("contractor", params.contractor);
  const qs = query.toString();
  return `${API_BASE}/api/v1/projects/${projectId}/institutional-memory/ledger/export${qs ? `?${qs}` : ""}`;
}
