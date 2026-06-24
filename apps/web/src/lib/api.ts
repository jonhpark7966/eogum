const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export async function apiFetch<T>(
  path: string,
  token: string | null | undefined,
  options?: RequestInit
): Promise<T> {
  const headers = new Headers(options?.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error: ${res.status} ${res.statusText}`);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

// ── Types ──
export interface SourceDerived {
  status?: "queued" | "processing" | "ready" | "failed" | string | null;
  media_info_r2_key?: string | null;
  audio_proxy_r2_key?: string | null;
  audio_codec?: string | null;
  sample_rate?: number | null;
  channels?: number | null;
  duration_ms?: number | null;
  duration_diff_ms?: number | null;
  error?: string | null;
}

export interface ExtraSource {
  r2_key: string;
  filename: string;
  size_bytes: number;
  offset_ms?: number | null;
  source_sha256?: string | null;
  derived?: SourceDerived | null;
}

export type MulticamSwitching = "none" | "follow_speaker" | "conservative_follow_speaker";
export type EditDecisionVersion = "legacy" | "boundary_aware_v1";
export type SegmentationBoundaryRule = "word_boundary" | "midpoint_gap" | "low_energy_gap_v1";

export interface MulticamSourceLabel {
  display_id?: string;
  display_name?: string;
}

export interface UpdateMulticamSettingsPayload {
  multicam_switching?: MulticamSwitching;
  multicam_source_labels?: Record<string, MulticamSourceLabel>;
  speaker_source_map?: Record<string, string>;
  audio_source_key?: string;
}

export interface Project {
  id: string;
  user_id: string;
  name: string;
  status: string;
  cut_type: string;
  language: string;
  source_filename: string | null;
  source_duration_seconds: number | null;
  source_sha256?: string | null;
  source_derived?: SourceDerived | null;
  settings?: Record<string, unknown>;
  extra_sources: ExtraSource[];
  multicam_state: MulticamState;
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

export interface PipelineStage {
  id: string;
  label: string;
  status: "pending" | "running" | "completed" | "failed" | "skipped" | string;
  progress: number;
  detail?: string;
  started_at?: string;
  completed_at?: string;
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
  pipeline_stages: PipelineStage[];
  external_task_ids: Record<string, string>;
  processing_metadata: Record<string, unknown>;
  result_r2_keys: Record<string, string> | null;
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

export interface MultipartPartUrl {
  part_number: number;
  upload_url: string;
}

export interface MultipartInitiateResponse {
  upload_id: string;
  r2_key: string;
  part_size: number;
  part_urls: MultipartPartUrl[];
}

export interface MultipartCompleteResponse {
  r2_key: string;
}

export interface MultipartAbortResponse {
  r2_key: string;
  aborted: boolean;
}

export interface DownloadResponse {
  download_url: string;
  filename: string;
}

export interface SourceLookupResponse {
  hit: boolean;
  r2_key: string | null;
  source_asset_id: string | null;
}

// ── Evaluation ──
export interface AiDecision {
  [key: string]: unknown;
  action: string;
  reason: string;
  confidence: number;
  note: string | null;
  edit_type?: string | null;
  origin_kind?: string | null;
  source_segment_index?: number | null;
}

export interface HumanDecision {
  [key: string]: unknown;
  action: string;
  reason: string;
  note: string;
}

export interface SegmentWithDecision {
  [key: string]: unknown;
  index: number;
  start_ms: number;
  end_ms: number;
  raw_start_ms?: number | null;
  raw_end_ms?: number | null;
  text: string;
  speaker?: string | null;
  ai: AiDecision | null;
  human?: HumanDecision | null;
}

export interface EvalSegment {
  [key: string]: unknown;
  index: number;
  start_ms: number;
  end_ms: number;
  raw_start_ms?: number | null;
  raw_end_ms?: number | null;
  text: string;
  speaker?: string | null;
  ai: AiDecision | null;
  human: HumanDecision | null;
}

export interface SegmentsResponse {
  [key: string]: unknown;
  schema_version: string | null;
  review_scope: string | null;
  join_strategy: string | null;
  command?: string | null;
  status?: string | null;
  avid_version?: string | null;
  package_version?: string | null;
  git_revision?: string | null;
  stats?: Record<string, unknown> | null;
  project_json?: string | null;
  segments: SegmentWithDecision[];
  source_duration_ms: number;
}

export interface EvaluationResponse {
  [key: string]: unknown;
  id: string;
  project_id: string;
  evaluator_id: string;
  version: string;
  avid_version: string | null;
  eogum_version: string | null;
  schema_version: string | null;
  review_scope: string | null;
  join_strategy: string | null;
  command?: string | null;
  status?: string | null;
  package_version?: string | null;
  git_revision?: string | null;
  stats?: Record<string, unknown> | null;
  project_json?: string | null;
  source_duration_ms?: number | null;
  segments: EvalSegment[];
  created_at: string;
  updated_at: string;
}

export interface EvaluationSavePayload {
  [key: string]: unknown;
  schema_version?: string | null;
  review_scope?: string | null;
  join_strategy?: string | null;
  command?: string | null;
  status?: string | null;
  package_version?: string | null;
  git_revision?: string | null;
  stats?: Record<string, unknown> | null;
  project_json?: string | null;
  source_duration_ms?: number | null;
  segments: EvalSegment[];
}

export interface VideoUrlResponse {
  video_url: string;
  duration_ms: number;
}

export interface FinalPreviewJobResponse {
  job_id: string;
  status: string;
  progress: number;
  error_message: string | null;
  video_url: string | null;
  captions_url: string | null;
  timeline_map_url: string | null;
  duration_ms: number | null;
}

export interface FinalPreviewTimelineInterval {
  source_start_ms: number;
  source_end_ms: number;
  requested_duration_ms: number;
  actual_duration_ms: number;
  preview_start_ms: number;
  preview_end_ms: number;
}

export interface FinalPreviewTimelineMap {
  version: number;
  intervals: FinalPreviewTimelineInterval[];
}

export interface MulticamState {
  status?: "not_applied" | "pending_apply" | "queued" | "running" | "applied" | "failed" | "canceling" | "canceled" | string;
  desired_sources_hash?: string | null;
  applied_sources_hash?: string | null;
  source_count?: number;
  job_id?: string | null;
  applied_at?: string | null;
  error?: string | null;
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

// ── YouTube ──
export interface YouTubeInfoResponse {
  title: string;
  duration_seconds: number;
  filesize_approx_bytes: number;
  thumbnail: string;
  uploader: string;
  upload_date: string;
}

export interface YouTubeDownloadResponse {
  task_id: string;
  title: string;
  duration_seconds: number;
  filesize_approx_bytes: number;
}

export interface YouTubeTaskResponse {
  task_id: string;
  status: string;
  progress: number;
  error: string | null;
  r2_key: string | null;
  filename: string | null;
  duration_seconds: number;
  filesize_bytes: number;
}

// ── API Functions ──
export const api = {
  // Upload
  presign: (token: string, data: { filename: string; content_type: string; size_bytes: number }) =>
    apiFetch<PresignResponse>("/upload/presign", token, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  initiateMultipart: (
    token: string,
    data: { filename: string; content_type: string; size_bytes: number }
  ) =>
    apiFetch<MultipartInitiateResponse>("/upload/multipart/initiate", token, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  completeMultipart: (
    token: string,
    data: { r2_key: string; upload_id: string; parts: { part_number: number; etag: string }[] }
  ) =>
    apiFetch<MultipartCompleteResponse>("/upload/multipart/complete", token, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  abortMultipart: (
    token: string,
    data: { r2_key: string; upload_id: string }
  ) =>
    apiFetch<MultipartAbortResponse>("/upload/multipart/abort", token, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // Projects
  lookupSource: (token: string, data: { sha256: string; size_bytes: number }) =>
    apiFetch<SourceLookupResponse>("/sources/lookup", token, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  listProjects: (token: string) => apiFetch<Project[]>("/projects", token),

  getProject: (token: string | null | undefined, id: string) =>
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
      source_sha256?: string | null;
      settings?: Record<string, unknown>;
    }
  ) =>
    apiFetch<Project>("/projects", token, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  createProjectVariant: (
    token: string,
    id: string,
    data: {
      edit_intensity: "light" | "normal" | "heavy";
      edit_decision_version?: EditDecisionVersion;
      segmentation_boundary_rule?: SegmentationBoundaryRule;
      overlap_protection_enabled?: boolean;
      name?: string;
    }
  ) =>
    apiFetch<Project>("/projects/" + id + "/variants", token, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  deleteProject: (token: string, id: string) =>
    apiFetch<void>("/projects/" + id, token, { method: "DELETE" }),

  retryProject: (token: string, id: string) =>
    apiFetch<Project>(`/projects/${id}/retry`, token, { method: "POST" }),

  rerunCutDecision: (token: string, id: string) =>
    apiFetch<Project>(`/projects/${id}/cut-decision`, token, { method: "POST" }),

  regenerateExports: (token: string, id: string) =>
    apiFetch<Project>(`/projects/${id}/exports/regenerate`, token, { method: "POST" }),

  updateExtraSources: (token: string, id: string, extra_sources: ExtraSource[]) =>
    apiFetch<Project>(`/projects/${id}/extra-sources`, token, {
      method: "PUT",
      body: JSON.stringify({ extra_sources }),
    }),

  retryExtraSourceDerivatives: (token: string, id: string, force = false) =>
    apiFetch<Project>(`/projects/${id}/extra-sources/derive`, token, {
      method: "POST",
      body: JSON.stringify({ force }),
    }),

  updateMulticamSettings: (token: string, id: string, data: UpdateMulticamSettingsPayload) =>
    apiFetch<Project>(`/projects/${id}/multicam-settings`, token, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  multicamReprocess: (token: string, id: string) =>
    apiFetch<Project>(`/projects/${id}/multicam`, token, { method: "POST" }),

  cancelMulticam: (token: string, id: string) =>
    apiFetch<Project>(`/projects/${id}/multicam/cancel`, token, { method: "POST" }),

  // Credits
  getCredits: (token: string) => apiFetch<CreditBalance>("/credits", token),

  // Downloads
  getDownload: (token: string | null | undefined, projectId: string, fileType: string) =>
    apiFetch<DownloadResponse>(
      `/projects/${projectId}/download/${fileType}`,
      token
    ),

  downloadExtraSource: (token: string | null | undefined, projectId: string, index: number) =>
    apiFetch<DownloadResponse>(
      `/projects/${projectId}/download/extra-source/${index}`,
      token
    ),

  // Evaluations
  getSegments: (token: string | null | undefined, projectId: string) =>
    apiFetch<SegmentsResponse>(`/projects/${projectId}/segments`, token),

  getVideoUrl: (token: string | null | undefined, projectId: string) =>
    apiFetch<VideoUrlResponse>(`/projects/${projectId}/video-url`, token),

  getEvaluation: async (token: string | null | undefined, projectId: string): Promise<EvaluationResponse | null> => {
    try {
      return await apiFetch<EvaluationResponse>(`/projects/${projectId}/evaluation`, token);
    } catch (err) {
      // 404 = no evaluation yet → expected
      if (err instanceof Error && (err.message.includes("404") || err.message.includes("평가 데이터가 없습니다"))) return null;
      throw err;
    }
  },

  saveEvaluation: (token: string, projectId: string, payload: EvaluationSavePayload) =>
    apiFetch<EvaluationResponse>(`/projects/${projectId}/evaluation`, token, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  startFinalPreview: (token: string, projectId: string, payload: EvaluationSavePayload) =>
    apiFetch<FinalPreviewJobResponse>(`/projects/${projectId}/final-preview`, token, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  startJunctionPreview: (token: string, projectId: string, payload: EvaluationSavePayload) =>
    apiFetch<FinalPreviewJobResponse>(`/projects/${projectId}/junction-preview`, token, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getFinalPreview: (token: string | null | undefined, projectId: string, jobId: string) =>
    apiFetch<FinalPreviewJobResponse>(`/projects/${projectId}/final-preview/${jobId}`, token),

  getEvalReport: (token: string | null | undefined, projectId: string) =>
    apiFetch<EvalReportResponse>(`/projects/${projectId}/eval-report`, token),

  // YouTube
  getYouTubeInfo: (token: string, url: string) =>
    apiFetch<YouTubeInfoResponse>("/youtube/info", token, {
      method: "POST",
      body: JSON.stringify({ url }),
    }),

  startYouTubeDownload: (token: string, url: string) =>
    apiFetch<YouTubeDownloadResponse>("/youtube/download", token, {
      method: "POST",
      body: JSON.stringify({ url }),
    }),

  getYouTubeDownloadStatus: (token: string, taskId: string) =>
    apiFetch<YouTubeTaskResponse>(`/youtube/download/${taskId}`, token),
};

// ── Multipart Upload Utility ──
const UPLOAD_CONCURRENCY = 3;
const RETRYABLE_UPLOAD_STATUSES = new Set([408, 429, 500, 502, 503, 504]);
const R2_NETWORK_ERROR_MESSAGE = "R2 업로드 네트워크가 끊겼습니다. 네트워크/VPN/Wi-Fi 확인 후 다시 시도해 주세요.";

export interface UploadFileOptions {
  onProgress?: (loaded: number, total: number) => void;
  signal?: AbortSignal;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timeout = window.setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timeout);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true }
    );
  });
}

function isRetryableUploadError(error: unknown): boolean {
  if (error instanceof TypeError && error.message.includes("Failed to fetch")) return true;
  if (error instanceof Error && error.message.includes("Failed to fetch")) return true;
  return false;
}

function normalizeUploadError(error: unknown): Error {
  if (error instanceof DOMException && error.name === "AbortError") {
    return new Error("업로드가 취소되었습니다");
  }
  if (isRetryableUploadError(error)) return new Error(R2_NETWORK_ERROR_MESSAGE);
  return error instanceof Error ? error : new Error("업로드에 실패했습니다");
}

async function fetchPartWithRetry(
  url: string,
  chunk: Blob,
  signal: AbortSignal,
  attempts = 3
): Promise<Response> {
  let lastError: unknown = null;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const resp = await fetch(url, {
        method: "PUT",
        body: chunk,
        signal,
      });
      if (resp.ok) return resp;
      if (!RETRYABLE_UPLOAD_STATUSES.has(resp.status) || attempt === attempts - 1) {
        throw new Error(`Part 업로드 실패: ${resp.status}`);
      }
      lastError = new Error(`Retryable upload status: ${resp.status}`);
    } catch (error) {
      if (signal.aborted) throw error;
      if (!isRetryableUploadError(error) || attempt === attempts - 1) throw error;
      lastError = error;
    }
    await sleep(500 * 2 ** attempt, signal);
  }
  throw lastError instanceof Error ? lastError : new Error(R2_NETWORK_ERROR_MESSAGE);
}

export async function uploadFile(
  token: string,
  file: File,
  options?: ((loaded: number, total: number) => void) | UploadFileOptions
): Promise<string> {
  const onProgress = typeof options === "function" ? options : options?.onProgress;
  const externalSignal = typeof options === "function" ? undefined : options?.signal;
  const contentType = file.type || "video/mp4";

  const initResp = await api.initiateMultipart(token, {
    filename: file.name,
    content_type: contentType,
    size_bytes: file.size,
  });

  const { part_urls, part_size, upload_id, r2_key } = initResp;
  const completedParts: { part_number: number; etag: string }[] = [];
  let totalUploaded = 0;
  let completeStarted = false;
  const batchControllers = new Set<AbortController>();

  const abortAllInFlight = () => {
    for (const controller of batchControllers) controller.abort();
  };
  if (externalSignal) {
    if (externalSignal.aborted) abortAllInFlight();
    externalSignal.addEventListener("abort", abortAllInFlight, { once: true });
  }

  try {
    for (let i = 0; i < part_urls.length; i += UPLOAD_CONCURRENCY) {
      if (externalSignal?.aborted) throw new DOMException("Aborted", "AbortError");
      const batch = part_urls.slice(i, i + UPLOAD_CONCURRENCY);
      const results = await Promise.all(
        batch.map(async (part) => {
          const start = (part.part_number - 1) * part_size;
          const end = Math.min(start + part_size, file.size);
          const chunk = file.slice(start, end);
          const controller = new AbortController();
          batchControllers.add(controller);

          const abortPart = () => controller.abort();
          externalSignal?.addEventListener("abort", abortPart, { once: true });
          try {
            const resp = await fetchPartWithRetry(part.upload_url, chunk, controller.signal);
            const etag = resp.headers.get("ETag") || "";
            totalUploaded += end - start;
            onProgress?.(totalUploaded, file.size);
            return { part_number: part.part_number, etag };
          } finally {
            externalSignal?.removeEventListener("abort", abortPart);
            batchControllers.delete(controller);
          }
        })
      );
      completedParts.push(...results);
    }

    completedParts.sort((a, b) => a.part_number - b.part_number);
    completeStarted = true;
    await api.completeMultipart(token, { r2_key, upload_id, parts: completedParts });
    return r2_key;
  } catch (error) {
    abortAllInFlight();
    if (!completeStarted) {
      await api.abortMultipart(token, { r2_key, upload_id }).catch(() => undefined);
      throw normalizeUploadError(error);
    }
    throw new Error("업로드 완료 확인에 실패했습니다. 프로젝트 상태를 새로고침한 뒤 다시 확인해 주세요.");
  } finally {
    externalSignal?.removeEventListener("abort", abortAllInFlight);
  }
}
