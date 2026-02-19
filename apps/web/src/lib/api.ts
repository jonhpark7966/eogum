const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export async function apiFetch<T>(
  path: string,
  token: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...options?.headers,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error: ${res.status}`);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

// ── Types ──
export interface Project {
  id: string;
  name: string;
  status: string;
  cut_type: string;
  language: string;
  source_filename: string | null;
  source_duration_seconds: number | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectDetail extends Project {
  source_r2_key: string | null;
  source_size_bytes: number | null;
  settings: Record<string, unknown>;
  jobs: Job[];
  report: EditReport | null;
}

export interface Job {
  id: string;
  type: string;
  status: string;
  progress: number;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface CreditBalance {
  balance_seconds: number;
  held_seconds: number;
  available_seconds: number;
}

export interface EditReport {
  total_duration_seconds: number;
  cut_duration_seconds: number;
  cut_percentage: number;
  edit_summary: Record<string, unknown>;
  report_markdown: string;
}

export interface PresignResponse {
  upload_url: string;
  r2_key: string;
}

export interface DownloadResponse {
  download_url: string;
  filename: string;
}

// ── Evaluation ──
export interface AiDecision {
  action: string;
  reason: string;
  confidence: number;
  note: string | null;
}

export interface HumanDecision {
  action: string;
  reason: string;
  note: string;
}

export interface SegmentWithDecision {
  index: number;
  start_ms: number;
  end_ms: number;
  text: string;
  ai: AiDecision | null;
}

export interface EvalSegment {
  index: number;
  start_ms: number;
  end_ms: number;
  text: string;
  ai: AiDecision | null;
  human: HumanDecision | null;
}

export interface SegmentsResponse {
  segments: SegmentWithDecision[];
  source_duration_ms: number;
}

export interface EvaluationResponse {
  id: string;
  project_id: string;
  evaluator_id: string;
  version: string;
  avid_version: string | null;
  eogum_version: string | null;
  segments: EvalSegment[];
  created_at: string;
  updated_at: string;
}

export interface VideoUrlResponse {
  video_url: string;
  duration_ms: number;
}

export interface ConfusionMatrix {
  tp: number;
  tn: number;
  fp: number;
  fn: number;
}

export interface EvalMetrics {
  accuracy: number;
  precision: number;
  recall: number;
  f1: number;
}

export interface ReasonBreakdown {
  reason: string;
  count: number;
  total_ms: number;
}

export interface DisagreementDetail {
  index: number;
  start_ms: number;
  end_ms: number;
  text: string;
  ai_action: string;
  ai_reason: string;
  human_action: string;
  human_reason: string;
  human_note: string;
}

export interface EvalReportResponse {
  project_id: string;
  avid_version: string | null;
  eogum_version: string | null;
  total_segments: number;
  human_reviewed: number;
  implicit_agree: number;
  agreement_rate: number;
  confusion: ConfusionMatrix;
  metrics: EvalMetrics;
  ai_cut_count: number;
  ai_cut_ms: number;
  truth_cut_count: number;
  truth_cut_ms: number;
  fp_reasons: ReasonBreakdown[];
  fn_reasons: ReasonBreakdown[];
  disagreements: DisagreementDetail[];
}

// ── API Functions ──
export const api = {
  // Upload
  presign: (token: string, data: { filename: string; content_type: string; size_bytes: number }) =>
    apiFetch<PresignResponse>("/upload/presign", token, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // Projects
  listProjects: (token: string) => apiFetch<Project[]>("/projects", token),

  getProject: (token: string, id: string) =>
    apiFetch<ProjectDetail>(`/projects/${id}`, token),

  createProject: (
    token: string,
    data: {
      name: string;
      cut_type: string;
      language: string;
      source_r2_key: string;
      source_filename: string;
      source_duration_seconds: number;
      source_size_bytes: number;
    }
  ) =>
    apiFetch<Project>("/projects", token, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  deleteProject: (token: string, id: string) =>
    apiFetch<void>(`/projects/${id}`, token, { method: "DELETE" }),

  retryProject: (token: string, id: string) =>
    apiFetch<Project>(`/projects/${id}/retry`, token, { method: "POST" }),

  // Credits
  getCredits: (token: string) => apiFetch<CreditBalance>("/credits", token),

  // Downloads
  getDownload: (token: string, projectId: string, fileType: string) =>
    apiFetch<DownloadResponse>(
      `/projects/${projectId}/download/${fileType}`,
      token
    ),

  // Evaluations
  getSegments: (token: string, projectId: string) =>
    apiFetch<SegmentsResponse>(`/projects/${projectId}/segments`, token),

  getVideoUrl: (token: string, projectId: string) =>
    apiFetch<VideoUrlResponse>(`/projects/${projectId}/video-url`, token),

  getEvaluation: async (token: string, projectId: string): Promise<EvaluationResponse | null> => {
    try {
      return await apiFetch<EvaluationResponse>(`/projects/${projectId}/evaluation`, token);
    } catch {
      return null;
    }
  },

  saveEvaluation: (token: string, projectId: string, segments: EvalSegment[]) =>
    apiFetch<EvaluationResponse>(`/projects/${projectId}/evaluation`, token, {
      method: "POST",
      body: JSON.stringify({ segments }),
    }),

  getEvalReport: (token: string, projectId: string) =>
    apiFetch<EvalReportResponse>(`/projects/${projectId}/eval-report`, token),
};
