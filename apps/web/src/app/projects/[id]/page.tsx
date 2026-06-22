"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import {
  api,
  type ProjectDetail,
  type PipelineStage,
  type MulticamState,
  type SegmentWithDecision,
  type MulticamSwitching,
  type MulticamSourceLabel,
} from "@/lib/api";
import { useUploads } from "@/lib/upload-provider";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Image from "next/image";

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}시간 ${m}분 ${s}초`;
  if (m > 0) return `${m}분 ${s}초`;
  return `${s}초`;
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: string; bg: string }> = {
  queued:     { label: "대기 중",   color: "text-amber-400",   icon: "◷", bg: "bg-amber-400/10" },
  processing: { label: "처리 중",   color: "text-cyan-400",    icon: "⟳", bg: "bg-cyan-400/10" },
  completed:  { label: "완료",      color: "text-emerald-400", icon: "✓", bg: "bg-emerald-400/10" },
  failed:     { label: "실패",      color: "text-red-400",     icon: "✕", bg: "bg-red-400/10" },
  reprocess_failed: { label: "재적용 실패", color: "text-red-400", icon: "✕", bg: "bg-red-400/10" },
};

const JOB_TYPE_LABELS: Record<string, string> = {
  subtitle_cut: "편집 처리",
  podcast_cut: "편집 처리",
  reprocess_multicam: "멀티캠 적용",
  cut_decision: "컷 결정 재실행",
  final_preview: "완성본 미리보기",
};


type EditIntensity = "light" | "normal" | "heavy";

const EDIT_INTENSITY_OPTIONS: { value: EditIntensity; label: string; description: string }[] = [
  { value: "light", label: "적게 편집", description: "꼭 필요한 컷만" },
  { value: "normal", label: "일반 편집", description: "균형 있게 정리" },
  { value: "heavy", label: "많이 편집", description: "적극적으로 압축" },
];

const EDIT_INTENSITY_LABELS: Record<EditIntensity, string> = {
  light: "적게 편집",
  normal: "일반 편집",
  heavy: "많이 편집",
};

function normalizeEditIntensity(value: unknown): EditIntensity {
  return value === "light" || value === "normal" || value === "heavy" ? value : "normal";
}

const STAGE_STATUS_CONFIG: Record<string, { label: string; dot: string; text: string }> = {
  pending: { label: "대기", dot: "bg-gray-600", text: "text-gray-500" },
  running: { label: "진행 중", dot: "bg-cyan-400 shadow-[0_0_12px_rgba(34,211,238,0.45)]", text: "text-cyan-300" },
  completed: { label: "완료", dot: "bg-emerald-400", text: "text-emerald-300" },
  failed: { label: "실패", dot: "bg-red-400", text: "text-red-300" },
  skipped: { label: "건너뜀", dot: "bg-gray-500", text: "text-gray-400" },
};

function getStageStatusConfig(status: string) {
  return STAGE_STATUS_CONFIG[status] ?? STAGE_STATUS_CONFIG.pending;
}

function PipelineStageList({ stages }: { stages: PipelineStage[] }) {
  if (!stages.length) return null;

  return (
    <div className="mt-4 grid gap-2">
      {stages.map((stage) => {
        const config = getStageStatusConfig(stage.status);
        const showProgress = stage.status === "running" || (stage.progress > 0 && stage.progress < 100);

        return (
          <div
            key={stage.id}
            className="flex items-start gap-3 rounded-xl border border-white/[0.05] bg-white/[0.025] px-3 py-2.5"
          >
            <span className={"mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full " + config.dot} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="min-w-0 truncate font-medium text-gray-300">{stage.label}</span>
                <span className={"shrink-0 " + config.text}>
                  {config.label}{showProgress ? " " + Math.round(stage.progress) + "%" : ""}
                </span>
              </div>
              {stage.detail && (
                <p className="mt-1 truncate text-[11px] leading-4 text-gray-500">{stage.detail}</p>
              )}
              {showProgress && (
                <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-cyan-400/80 to-violet-400/80 transition-all duration-700"
                    style={{ width: Math.min(100, Math.max(0, stage.progress)).toString() + "%" }}
                  />
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function hasScribeV2CacheHit(project: ProjectDetail): boolean {
  return project.jobs.some((job) => (job.pipeline_stages ?? []).some((stage) => {
    if (stage.id !== "scribe_v2_transcribe" || stage.status !== "completed") return false;
    return (stage.label ?? "").includes("cache") || (stage.detail ?? "").includes("캐시");
  }));
}

type ProjectJob = ProjectDetail["jobs"][number];

const ACTIVE_JOB_STATUSES = new Set(["pending", "queued", "running", "cancel_requested"]);
const ARTIFACT_JOB_TYPES = new Set(["subtitle_cut", "podcast_cut", "reprocess_multicam", "cut_decision"]);

function isActiveJob(job: ProjectJob): boolean {
  return ACTIVE_JOB_STATUSES.has(job.status);
}

function newestJobFirst(a: ProjectJob, b: ProjectJob): number {
  return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
}

function getVisibleProcessingJobs(project: ProjectDetail): ProjectJob[] {
  const multicamJobId = project.multicam_state?.job_id;
  if (multicamJobId) {
    const multicamJob = project.jobs.find((job) => job.id === multicamJobId);
    if (multicamJob && isActiveJob(multicamJob)) return [multicamJob];
  }

  const activeJobs = project.jobs.filter(isActiveJob).sort(newestJobFirst);
  if (activeJobs.length > 0) return activeJobs;

  if (project.status === "processing" || project.status === "queued") {
    const latestJob = [...project.jobs].sort(newestJobFirst)[0];
    return latestJob ? [latestJob] : [];
  }

  return [];
}

function multicamLabel(state: MulticamState | undefined, extraCount: number): string {
  const status = state?.status || (extraCount > 0 ? "pending_apply" : "not_applied");
  if (status === "not_applied") return "멀티캠 소스 없음";
  if (status === "pending_apply") return "소스 등록됨, 아직 적용 전";
  if (status === "queued") return "멀티캠 적용 대기 중";
  if (status === "running") return "멀티캠 적용 중";
  if (status === "applied") {
    const appliedAt = state?.applied_at ? new Date(state.applied_at).toLocaleString("ko-KR") : "";
    return `적용 완료: ${state?.source_count ?? extraCount}개 소스${appliedAt ? `, ${appliedAt}` : ""}`;
  }
  if (status === "failed") return "적용 실패";
  if (status === "canceling") return "취소 중";
  if (status === "canceled") return "취소됨";
  return status;
}


type MulticamSourceOption = {
  source_key: string;
  display_id: string;
  display_name: string;
  filename: string;
};

type SpeakerSummary = {
  speaker: string;
  samples: string[];
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function sourceLabelsFromSettings(settings: Record<string, unknown> | undefined): Record<string, MulticamSourceLabel> {
  const rawLabels = asRecord(settings?.multicam_source_labels);
  return Object.fromEntries(Object.entries(rawLabels).map(([sourceKey, rawLabel]) => {
    const label = asRecord(rawLabel);
    return [sourceKey, {
      display_id: typeof label.display_id === "string" ? label.display_id : undefined,
      display_name: typeof label.display_name === "string" ? label.display_name : undefined,
    }];
  }));
}

function speakerSourceMapFromSettings(settings: Record<string, unknown> | undefined): Record<string, string> {
  const rawMap = asRecord(settings?.speaker_source_map);
  return Object.fromEntries(Object.entries(rawMap).flatMap(([speaker, sourceKey]) => (
    typeof sourceKey === "string" ? [[speaker, sourceKey]] : []
  )));
}

function normalizeMulticamSwitching(value: unknown): MulticamSwitching {
  return value === "follow_speaker" || value === "conservative_follow_speaker" ? value : "none";
}

function multicamSourceOptions(project: ProjectDetail): MulticamSourceOption[] {
  const labels = sourceLabelsFromSettings(project.settings);
  const primaryLabel = labels.primary ?? {};
  return [
    {
      source_key: "primary",
      display_id: primaryLabel.display_id || "cam_1",
      display_name: primaryLabel.display_name || "Main / Wide",
      filename: project.source_filename || "source",
    },
    ...project.extra_sources.map((source, index) => {
      const sourceKey = `extra:${index}`;
      const label = labels[sourceKey] ?? {};
      return {
        source_key: sourceKey,
        display_id: label.display_id || `cam_${index + 2}`,
        display_name: label.display_name || `Camera ${index + 2}`,
        filename: source.filename,
      };
    }),
  ];
}

function speakerSummariesFromSegments(segments: SegmentWithDecision[]): SpeakerSummary[] {
  const grouped = new Map<string, string[]>();
  for (const segment of segments) {
    const speaker = typeof segment.speaker === "string" ? segment.speaker.trim() : "";
    if (!speaker) continue;
    const samples = grouped.get(speaker) ?? [];
    if (samples.length < 5 && segment.text.trim()) samples.push(segment.text.trim());
    grouped.set(speaker, samples);
  }
  return Array.from(grouped.entries())
    .map(([speaker, samples]) => ({ speaker, samples }))
    .sort((a, b) => a.speaker.localeCompare(b.speaker));
}

function truncateSample(text: string): string {
  return text.length > 44 ? `${text.slice(0, 44)}...` : text;
}

/* ── Section wrapper ── */
function Section({ title, icon, children, className = "" }: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`relative group/section ${className}`}>
      <div className="absolute -inset-px rounded-2xl bg-gradient-to-b from-white/[0.06] to-transparent opacity-0 group-hover/section:opacity-100 transition-opacity duration-500 pointer-events-none" />
      <div className="relative bg-white/[0.02] border border-white/[0.06] rounded-2xl p-6">
        <div className="flex items-center gap-2.5 mb-5">
          <span className="text-gray-400">{icon}</span>
          <h3 className="font-semibold text-[15px]">{title}</h3>
        </div>
        {children}
      </div>
    </div>
  );
}

export default function ProjectDetailPage() {
  const params = useParams();
  const router = useRouter();
  const supabase = createClient();
  const { tasks, startUpload, cancelUpload } = useUploads();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [rerunningCutDecision, setRerunningCutDecision] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [variantModalOpen, setVariantModalOpen] = useState(false);
  const [variantIntensity, setVariantIntensity] = useState<EditIntensity>("light");
  const [creatingVariant, setCreatingVariant] = useState(false);
  const [sourceCacheReused, setSourceCacheReused] = useState(false);

  // Multicam state
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [multicamProcessing, setMulticamProcessing] = useState(false);
  const [multicamSettingsSaving, setMulticamSettingsSaving] = useState(false);
  const [reviewSegments, setReviewSegments] = useState<SegmentWithDecision[]>([]);
  const [reviewSegmentsLoading, setReviewSegmentsLoading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const projectId = params.id as string;
  const uploadTask = tasks.find((task) => task.projectId === projectId && ["queued", "uploading", "registering"].includes(task.status));
  const latestUploadTaskStatus = tasks.find((task) => task.projectId === projectId)?.status;
  const multicamSources = useMemo(() => project ? multicamSourceOptions(project) : [], [project]);
  const speakerSummaries = useMemo(() => speakerSummariesFromSegments(reviewSegments), [reviewSegments]);

  const loadProject = useCallback(async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) { router.replace("/"); return; }
    try {
      const [data, projectList] = await Promise.all([
        api.getProject(session.access_token, projectId),
        api.listProjects(session.access_token).catch(() => []),
      ]);
      const sourceReused = Boolean(data.source_sha256) && projectList.some((item) =>
        item.id !== data.id &&
        item.source_sha256 === data.source_sha256 &&
        new Date(item.created_at).getTime() <= new Date(data.created_at).getTime()
      );
      setProject(data);
      setSourceCacheReused(sourceReused);
    } catch (err) {
      setError(err instanceof Error ? err.message : "오류가 발생했습니다");
    }
    setLoading(false);
  }, [projectId, router, supabase.auth]);

  useEffect(() => {
    loadProject();
    const interval = setInterval(() => {
      const multicamStatus = project?.multicam_state?.status;
      if (
        project?.status === "processing" ||
        project?.status === "queued" ||
        multicamStatus === "queued" ||
        multicamStatus === "running" ||
        multicamStatus === "canceling"
      ) {
        loadProject();
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [loadProject, project?.status, project?.multicam_state?.status]);

  useEffect(() => {
    if (latestUploadTaskStatus === "completed") {
      void loadProject();
    }
  }, [latestUploadTaskStatus, loadProject]);


  useEffect(() => {
    if (!project || project.status !== "completed" || project.extra_sources.length === 0) {
      setReviewSegments([]);
      return;
    }

    let canceled = false;
    async function loadSegments() {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session || canceled) return;
      setReviewSegmentsLoading(true);
      try {
        const payload = await api.getSegments(session.access_token, projectId);
        if (!canceled) setReviewSegments(payload.segments);
      } catch {
        if (!canceled) setReviewSegments([]);
      } finally {
        if (!canceled) setReviewSegmentsLoading(false);
      }
    }

    void loadSegments();
    return () => { canceled = true; };
  }, [project, projectId, supabase.auth]);

  const handleRetry = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) return;
    setRetrying(true);
    try {
      await api.retryProject(session.access_token, projectId);
      await loadProject();
    } catch (err) {
      setError(err instanceof Error ? err.message : "재시도에 실패했습니다");
    } finally { setRetrying(false); }
  };

  const handleRerunCutDecision = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) return;
    setRerunningCutDecision(true);
    setError("");
    try {
      await api.rerunCutDecision(session.access_token, projectId);
      await loadProject();
    } catch (err) {
      setError(err instanceof Error ? err.message : "컷 결정 재실행에 실패했습니다");
    } finally {
      setRerunningCutDecision(false);
    }
  };

  const handleDownload = async (fileType: string) => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) return;
    try {
      const result = await api.getDownload(session.access_token, projectId, fileType);
      window.open(result.download_url, "_blank");
    } catch (err) {
      setError(err instanceof Error ? err.message : "다운로드 링크 생성에 실패했습니다");
    }
  };

  const handleDownloadExtraSource = async (index: number) => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) return;
    try {
      const result = await api.downloadExtraSource(session.access_token, projectId, index);
      window.open(result.download_url, "_blank");
    } catch (err) {
      setError(err instanceof Error ? err.message : "다운로드 링크 생성에 실패했습니다");
    }
  };

  const handleRemoveExtraSource = async (r2Key: string) => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session || !project) return;
    const updated = project.extra_sources.filter((s) => s.r2_key !== r2Key);
    try {
      await api.updateExtraSources(session.access_token, projectId, updated);
      await loadProject();
    } catch (err) {
      setError(err instanceof Error ? err.message : "삭제에 실패했습니다");
    }
  };

  const handleUploadExtraSources = async () => {
    if (!project || pendingFiles.length === 0 || uploadTask) return;
    startUpload(projectId, pendingFiles);
    setPendingFiles([]);
  };

  const handleMulticamReprocess = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) return;
    setMulticamProcessing(true);
    try {
      await api.multicamReprocess(session.access_token, projectId);
      await loadProject();
    } catch (err) {
      setError(err instanceof Error ? err.message : "멀티캠 적용에 실패했습니다");
    } finally { setMulticamProcessing(false); }
  };

  const handleCancelMulticam = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) return;
    setMulticamProcessing(true);
    try {
      await api.cancelMulticam(session.access_token, projectId);
      await loadProject();
    } catch (err) {
      setError(err instanceof Error ? err.message : "멀티캠 취소에 실패했습니다");
    } finally { setMulticamProcessing(false); }
  };


  const updateMulticamSettings = async (payload: Parameters<typeof api.updateMulticamSettings>[2]) => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session || !project) return;
    setMulticamSettingsSaving(true);
    setError("");
    try {
      await api.updateMulticamSettings(session.access_token, projectId, payload);
      await loadProject();
    } catch (err) {
      setError(err instanceof Error ? err.message : "멀티캠 설정 저장에 실패했습니다");
    } finally {
      setMulticamSettingsSaving(false);
    }
  };

  const handleMulticamSwitchingChange = async (value: MulticamSwitching) => {
    if (!project || value === normalizeMulticamSwitching(project.settings?.multicam_switching)) return;
    await updateMulticamSettings({ multicam_switching: value });
  };

  const handleSpeakerSourceChange = async (speaker: string, sourceKey: string) => {
    if (!project) return;
    const speakerSourceMap = speakerSourceMapFromSettings(project.settings);
    if (speakerSourceMap[speaker] === sourceKey) return;
    await updateMulticamSettings({
      speaker_source_map: {
        ...speakerSourceMap,
        [speaker]: sourceKey,
      },
    });
  };

  const handleSourceDisplayNameBlur = async (sourceKey: string, displayName: string) => {
    if (!project) return;
    const trimmedName = displayName.trim();
    const labels = sourceLabelsFromSettings(project.settings);
    const option = multicamSourceOptions(project).find((item) => item.source_key === sourceKey);
    const currentName = labels[sourceKey]?.display_name || option?.display_name || "";
    if (trimmedName === currentName) return;
    await updateMulticamSettings({
      multicam_source_labels: {
        ...labels,
        [sourceKey]: {
          display_id: labels[sourceKey]?.display_id || option?.display_id || "",
          display_name: trimmedName,
        },
      },
    });
  };

  const handleDeleteProject = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session || !project) return;
    setDeleting(true);
    try {
      await api.deleteProject(session.access_token, project.id);
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "프로젝트 삭제에 실패했습니다");
      setConfirmDelete(false);
    } finally {
      setDeleting(false);
    }
  };


  const openVariantModal = () => {
    if (!project) return;
    const current = normalizeEditIntensity(project.settings?.edit_intensity);
    const next = EDIT_INTENSITY_OPTIONS.find((option) => option.value !== current)?.value ?? "normal";
    setVariantIntensity(next);
    setVariantModalOpen(true);
    setError("");
  };

  const handleCreateVariant = async () => {
    if (!project) return;
    const current = normalizeEditIntensity(project.settings?.edit_intensity);
    if (variantIntensity === current) {
      setError("이미 현재 프로젝트가 해당 편집 강도입니다");
      return;
    }

    const { data: { session } } = await supabase.auth.getSession();
    if (!session) return;
    setCreatingVariant(true);
    try {
      const variant = await api.createProjectVariant(session.access_token, projectId, {
        edit_intensity: variantIntensity,
      });
      router.push("/projects/" + variant.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "새 편집 강도 프로젝트 생성에 실패했습니다");
    } finally {
      setCreatingVariant(false);
    }
  };

  /* ── Loading ── */
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#030712]">
        <Image src="/logo.png" alt="어검" width={48} height={48} className="animate-pulse rounded" />
      </div>
    );
  }

  /* ── Error ── */
  if (error && !project) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-[#030712] gap-4">
        <p className="text-red-400">{error || "프로젝트를 찾을 수 없습니다"}</p>
        <button onClick={() => router.push("/dashboard")} className="text-sm text-gray-500 hover:text-gray-300 transition-colors">
          대시보드로 돌아가기
        </button>
      </div>
    );
  }

  if (!project) return null;

  const statusConfig = STATUS_CONFIG[project.status] ?? { label: project.status, color: "text-gray-400", icon: "○", bg: "bg-gray-400/10" };
  const isProcessing = project.status === "processing" || project.status === "queued";
  const isCompleted = project.status === "completed";
  const isFailed = project.status === "failed" || project.status === "reprocess_failed";
  const cutTypeLabel = project.cut_type === "subtitle_cut" ? "강의/설명" : "팟캐스트";
  const currentEditIntensity = normalizeEditIntensity(project.settings?.edit_intensity);
  const currentEditIntensityLabel = EDIT_INTENSITY_LABELS[currentEditIntensity];
  const isUploadingExtraSources = Boolean(uploadTask);
  const multicamStatus = project.multicam_state?.status || (project.extra_sources.length > 0 ? "pending_apply" : "not_applied");
  const currentMulticamSwitching = normalizeMulticamSwitching(project.settings?.multicam_switching);
  const currentSpeakerSourceMap = speakerSourceMapFromSettings(project.settings);
  const hasSpeakerMetadata = speakerSummaries.length > 0;
  const canApplyMulticam = project.extra_sources.length > 0 && !isUploadingExtraSources && !["queued", "running", "canceling"].includes(multicamStatus);
  const canCancelMulticam = ["queued", "running", "canceling"].includes(multicamStatus);
  const canDeleteProject = !isProcessing && !isUploadingExtraSources && !canCancelMulticam;
  const scribeV2CacheHit = hasScribeV2CacheHit(project);
  const showCacheReuseInfo = sourceCacheReused || scribeV2CacheHit;
  const visibleProcessingJobs = getVisibleProcessingJobs(project);
  const latestResultKeys = [...project.jobs]
    .filter((job) => job.result_r2_keys && ARTIFACT_JOB_TYPES.has(job.type))
    .sort(newestJobFirst)[0]?.result_r2_keys ?? {};
  const hasCutDecisionInputs = Boolean(latestResultKeys.project_json);
  const hasPendingMulticamChanges = project.extra_sources.length > 0 && multicamStatus === "pending_apply";
  const cutDecisionDisabledReason = isProcessing
    ? "진행 중인 작업이 끝난 뒤 다시 실행할 수 있습니다"
    : !hasCutDecisionInputs
    ? "기존 refined segment 정보가 필요합니다"
    : hasPendingMulticamChanges
      ? "등록된 멀티캠 변경사항을 먼저 적용하거나 제거해야 합니다"
      : canCancelMulticam
        ? "진행 중인 작업이 있습니다"
        : "같은 segment/refined segment로 cut decision만 다시 실행";
  const canRerunCutDecision = !isProcessing
    && hasCutDecisionInputs
    && !hasPendingMulticamChanges
    && !canCancelMulticam
    && !isUploadingExtraSources
    && !rerunningCutDecision;
  const downloadItems = [
    { key: "source", label: "원본 소스", icon: "📁", desc: "원본 영상 파일" },
    { key: "fcpxml", label: "FCPXML", icon: "🎬", desc: "Final Cut Pro 프로젝트" },
    { key: "srt", label: "SRT 자막", icon: "💬", desc: "자막 파일" },
    { key: "report", label: "편집 리포트", icon: "📄", desc: "편집 보고서 (.md)" },
    { key: "project_json", label: "프로젝트 JSON", icon: "📦", desc: "avid 프로젝트 파일" },
    { key: "storyline", label: "스토리라인", icon: "📋", desc: "구조 분석 JSON" },
    ...(latestResultKeys.llm_io_log
      ? [{ key: "llm_io_log", label: "LLM 로그", icon: "🧾", desc: "프롬프트/응답 JSONL" }]
      : []),
  ];

  return (
    <div className="min-h-screen bg-[#030712] text-white dot-grid">
      {/* ── Nav ── */}
      <nav className="sticky top-0 z-50 border-b border-white/[0.04] bg-[#030712]/80 backdrop-blur-xl">
        <div className="max-w-5xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => router.push("/dashboard")}
              className="flex items-center gap-2 text-gray-500 hover:text-gray-300 transition-colors"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="15 18 9 12 15 6" />
              </svg>
              <span className="text-sm">대시보드</span>
            </button>
            <div className="w-px h-5 bg-white/10" />
            <span className="text-sm text-gray-400 truncate max-w-[200px]">{project.name}</span>
          </div>
          <button onClick={() => router.push("/dashboard")} className="flex items-center gap-2">
            <Image src="/logo.png" alt="어검" width={24} height={24} className="rounded" />
            <span className="font-bold text-sm tracking-tight hidden sm:inline">어검</span>
          </button>
        </div>
      </nav>

      <main className="max-w-4xl mx-auto px-6 py-8 space-y-6">
        {/* ── Project Header ── */}
        <div className="relative">
          <div className="absolute -inset-px rounded-2xl bg-gradient-to-r from-cyan-500/20 via-transparent to-violet-500/20 opacity-60" />
          <div className="relative bg-[#0a0f1a] border border-white/[0.06] rounded-2xl p-6">
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1 min-w-0">
                <h1 className="text-2xl font-bold mb-3">{project.name}</h1>
                <div className="flex flex-wrap items-center gap-3 text-sm text-gray-500">
                  <span className="inline-flex items-center gap-1.5">
                    {project.cut_type === "subtitle_cut" ? (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2" y="2" width="20" height="20" rx="2" /><path d="M7 2v20" /><path d="M17 2v20" /><path d="M2 12h20" /></svg>
                    ) : (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" /></svg>
                    )}
                    {cutTypeLabel}
                  </span>
                  <span className="inline-flex items-center gap-1.5 text-cyan-300">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 21v-7" /><path d="M4 10V3" /><path d="M12 21v-9" /><path d="M12 8V3" /><path d="M20 21v-5" /><path d="M20 12V3" /><path d="M2 14h4" /><path d="M10 8h4" /><path d="M18 16h4" /></svg>
                    {currentEditIntensityLabel}
                  </span>
                  {project.source_duration_seconds && (
                    <span className="inline-flex items-center gap-1.5">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></svg>
                      {formatDuration(project.source_duration_seconds)}
                    </span>
                  )}
                  <span>{project.language === "ko" ? "한국어" : project.language === "en" ? "English" : project.language}</span>
                  <span>{new Date(project.created_at).toLocaleDateString("ko-KR")}</span>
                </div>
              </div>
              <div className="flex flex-wrap items-center justify-end gap-2">
                <button
                  onClick={handleRerunCutDecision}
                  disabled={!canRerunCutDecision}
                  title={cutDecisionDisabledReason}
                  className="rounded-lg border border-violet-500/20 bg-violet-500/10 px-3 py-1.5 text-xs font-medium text-violet-300 transition hover:bg-violet-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {rerunningCutDecision ? "재실행 중..." : "컷 결정만 재실행"}
                </button>
                {isCompleted && (
                  <button
                    onClick={openVariantModal}
                    className="rounded-lg border border-cyan-500/20 bg-cyan-500/10 px-3 py-1.5 text-xs font-medium text-cyan-300 transition hover:bg-cyan-500/20"
                  >
                    편집 강도 변경
                  </button>
                )}
                <button
                  onClick={() => setConfirmDelete(true)}
                  disabled={!canDeleteProject}
                  className="rounded-lg border border-red-500/20 px-3 py-1.5 text-xs font-medium text-red-300 transition hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-40"
                  title={canDeleteProject ? "프로젝트 삭제" : "진행 중인 작업이 있어 삭제할 수 없습니다"}
                >
                  삭제
                </button>
                <span className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium ${statusConfig.color} ${statusConfig.bg}`}>
                  <span className={isProcessing ? "animate-spin" : ""}>{statusConfig.icon}</span>
                  {statusConfig.label}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* ── Error banner ── */}
        {error && project && (
          <div className="p-4 bg-red-500/[0.06] border border-red-500/20 rounded-xl text-red-300 text-sm flex items-center gap-3">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="shrink-0">
              <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            {error}
          </div>
        )}

        {showCacheReuseInfo && (
          <Section
            title="캐시 재사용"
            icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M21 16V8" /><path d="M3 8v8" /><path d="M12 3 3 8l9 5 9-5-9-5Z" /><path d="m3 16 9 5 9-5" /><path d="m3 12 9 5 9-5" /></svg>}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              {sourceCacheReused && (
                <div className="rounded-xl border border-cyan-500/15 bg-cyan-500/[0.04] px-4 py-3">
                  <p className="text-sm font-medium text-cyan-200">원본 R2 캐시</p>
                  <p className="mt-1 text-xs leading-5 text-gray-500">같은 원본 파일을 재업로드하지 않고 기존 R2 소스를 재사용했습니다.</p>
                </div>
              )}
              {scribeV2CacheHit && (
                <div className="rounded-xl border border-emerald-500/15 bg-emerald-500/[0.04] px-4 py-3">
                  <p className="text-sm font-medium text-emerald-200">Scribe V2 캐시</p>
                  <p className="mt-1 text-xs leading-5 text-gray-500">동일 원본과 전사 옵션의 raw Scribe V2 결과를 재사용했습니다.</p>
                </div>
              )}
            </div>
          </Section>
        )}

        {/* ── Processing Status ── */}
        {isProcessing && visibleProcessingJobs.length > 0 && (
          <Section
            title="처리 상태"
            icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 2v4" /><path d="M12 18v4" /><path d="M4.93 4.93l2.83 2.83" /><path d="M16.24 16.24l2.83 2.83" /><path d="M2 12h4" /><path d="M18 12h4" /><path d="M4.93 19.07l2.83-2.83" /><path d="M16.24 7.76l2.83-2.83" /></svg>}
          >
            <div className="space-y-4">
              {visibleProcessingJobs.map((job) => (
                <div key={job.id}>
                  <div className="flex justify-between text-sm mb-2">
                    <span className="text-gray-300">{JOB_TYPE_LABELS[job.type] ?? job.type}</span>
                    <span className="text-gray-500">{job.progress}%</span>
                  </div>
                  <div className="relative h-2 bg-white/[0.05] rounded-full overflow-hidden">
                    {job.status === "failed" ? (
                      <div className="absolute inset-y-0 left-0 rounded-full bg-red-500/60" style={{ width: `${job.progress}%` }} />
                    ) : job.progress < 100 ? (
                      <>
                        <div className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-cyan-500/60 to-violet-500/60 transition-all duration-1000" style={{ width: `${job.progress}%` }} />
                        <div className="absolute inset-y-0 w-1/4 bg-gradient-to-r from-transparent via-white/20 to-transparent rounded-full animate-[shimmer_2s_ease-in-out_infinite]" />
                      </>
                    ) : (
                      <div className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-cyan-500 to-violet-500 w-full" />
                    )}
                  </div>
                  <PipelineStageList stages={job.pipeline_stages ?? []} />
                </div>
              ))}
            </div>
          </Section>
        )}

        {/* ── Failed ── */}
        {isFailed && (
          <div className="relative">
            <div className="absolute -inset-px rounded-2xl bg-gradient-to-r from-red-500/20 via-transparent to-red-500/20" />
            <div className="relative bg-red-500/[0.04] border border-red-500/10 rounded-2xl p-6">
              <div className="flex items-center gap-2.5 mb-4">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-red-400">
                  <circle cx="12" cy="12" r="10" /><line x1="15" y1="9" x2="9" y2="15" /><line x1="9" y1="9" x2="15" y2="15" />
                </svg>
                <h3 className="font-semibold text-[15px] text-red-300">처리 실패</h3>
              </div>
              {project.jobs.filter((j) => j.status === "failed").map((j) => (
                <p key={j.id} className="text-sm text-red-300/80 mb-2 font-mono bg-red-500/[0.06] rounded-lg px-3 py-2">
                  {j.error_message}
                </p>
              ))}
              <p className="text-xs text-gray-500 mt-3 mb-4">
                홀딩된 크레딧은 자동으로 복구되었습니다.
              </p>
              <button
                onClick={handleRetry}
                disabled={retrying}
                className="group relative px-5 py-2 text-sm font-medium rounded-xl overflow-hidden transition-all duration-300 hover:shadow-[0_0_20px_rgba(6,182,212,0.2)] disabled:opacity-50"
              >
                <div className="absolute inset-0 bg-gradient-to-r from-cyan-500 to-violet-500" />
                <span className="relative text-white">{retrying ? "재시도 중..." : "재시도"}</span>
              </button>
            </div>
          </div>
        )}

        {/* ── Downloads ── */}
        {isCompleted && (
          <Section
            title="다운로드"
            icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" /></svg>}
          >
            {/* Main downloads */}
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
              {downloadItems.map(({ key, label, icon, desc }) => (
                <button
                  key={key}
                  onClick={() => handleDownload(key)}
                  className="group/dl relative text-left"
                >
                  <div className="absolute -inset-px rounded-xl bg-gradient-to-br from-cyan-500/20 to-violet-500/20 opacity-0 group-hover/dl:opacity-100 transition-opacity duration-300" />
                  <div className="relative bg-white/[0.02] border border-white/[0.06] rounded-xl p-4 group-hover/dl:border-white/10 transition-all duration-300">
                    <div className="text-lg mb-1">{icon}</div>
                    <p className="text-sm font-medium">{label}</p>
                    <p className="text-xs text-gray-600 mt-0.5">{desc}</p>
                  </div>
                </button>
              ))}

              {/* Review button */}
              <button
                onClick={() => router.push(`/projects/${projectId}/review`)}
                className="group/dl relative text-left"
              >
                <div className="absolute -inset-px rounded-xl bg-gradient-to-br from-violet-500/30 to-cyan-500/30 opacity-60 group-hover/dl:opacity-100 transition-opacity duration-300" />
                <div className="relative bg-violet-500/[0.04] border border-violet-500/10 rounded-xl p-4 group-hover/dl:border-violet-500/20 transition-all duration-300">
                  <div className="text-lg mb-1">🔍</div>
                  <p className="text-sm font-medium text-violet-300">구간 리뷰</p>
                  <p className="text-xs text-gray-600 mt-0.5">AI 판단 검토</p>
                </div>
              </button>
            </div>

            {/* Extra source downloads */}
            {project.extra_sources.length > 0 && (
              <div className="mt-5 pt-5 border-t border-white/[0.04]">
                <p className="text-xs text-gray-500 mb-3 uppercase tracking-wider">멀티캠 소스</p>
                <div className="space-y-2">
                  {project.extra_sources.map((src, i) => (
                    <div key={src.r2_key} className="flex items-center justify-between bg-white/[0.02] border border-white/[0.04] rounded-lg px-4 py-2.5">
                      <div className="flex items-center gap-3 min-w-0">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gray-600 shrink-0">
                          <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z" />
                        </svg>
                        <span className="text-sm truncate">{src.filename}</span>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        <span className="text-xs text-gray-600">{formatSize(src.size_bytes)}</span>
                        <button onClick={() => handleDownloadExtraSource(i)} className="text-cyan-400/70 hover:text-cyan-300 text-xs font-medium transition-colors">
                          다운로드
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Section>
        )}

        {/* ── Multicam Sources ── */}
        {(isCompleted || isFailed) && (
          <Section
            title="멀티캠 소스"
            icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z" /><circle cx="12" cy="13" r="3" /></svg>}
          >
            <p className="text-xs text-gray-500 mb-4">
              오디오 크로스 코릴레이션으로 자동 싱크. 추가 크레딧이 차감됩니다.
            </p>
            <div className="mb-4 rounded-lg border border-white/[0.06] bg-white/[0.025] px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-gray-300">{multicamLabel(project.multicam_state, project.extra_sources.length)}</span>
                {project.multicam_state?.error && (
                  <span className="max-w-[240px] truncate text-xs text-red-300">{project.multicam_state.error}</span>
                )}
              </div>
            </div>

            {/* Registered sources */}
            {project.extra_sources.length > 0 && (
              <div className="mb-4 space-y-2">
                {project.extra_sources.map((src, i) => (
                  <div key={src.r2_key} className="flex items-center justify-between bg-white/[0.02] border border-white/[0.04] rounded-lg px-4 py-2.5">
                    <div className="flex items-center gap-3 min-w-0">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gray-600 shrink-0">
                        <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z" />
                      </svg>
                      <span className="text-sm truncate">{src.filename}</span>
                    </div>
                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-xs text-gray-600">{formatSize(src.size_bytes)}</span>
                      <button onClick={() => handleDownloadExtraSource(i)} className="text-cyan-400/70 hover:text-cyan-300 text-xs font-medium transition-colors">
                        다운로드
                      </button>
                      <button onClick={() => handleRemoveExtraSource(src.r2_key)} className="text-red-400/50 hover:text-red-300 text-xs font-medium transition-colors">
                        삭제
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}


            {project.extra_sources.length > 0 && (
              <div className="mb-5 space-y-5 border-t border-white/[0.04] pt-5">
                <div>
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <p className="text-xs uppercase tracking-wider text-gray-500">카메라 ID</p>
                    {multicamSettingsSaving && <span className="text-xs text-cyan-300">저장 중...</span>}
                  </div>
                  <div className="grid gap-2">
                    {multicamSources.map((source) => (
                      <div key={source.source_key} className="grid gap-2 rounded-lg border border-white/[0.04] bg-white/[0.02] px-3 py-2.5 sm:grid-cols-[76px_1fr_1.2fr] sm:items-center">
                        <span className="w-fit rounded-md border border-cyan-500/20 bg-cyan-500/10 px-2 py-1 text-xs font-medium text-cyan-200">
                          {source.display_id}
                        </span>
                        <input
                          defaultValue={source.display_name}
                          disabled={multicamSettingsSaving || canCancelMulticam}
                          onBlur={(event) => void handleSourceDisplayNameBlur(source.source_key, event.currentTarget.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") event.currentTarget.blur();
                          }}
                          className="min-w-0 rounded-md border border-white/[0.08] bg-black/20 px-2.5 py-1.5 text-sm text-gray-200 outline-none transition focus:border-cyan-400/40 disabled:opacity-50"
                        />
                        <span className="truncate text-xs text-gray-500">{source.filename}</span>
                      </div>
                    ))}
                  </div>
                </div>

                <div>
                  <p className="mb-2 text-xs uppercase tracking-wider text-gray-500">카메라 전환</p>
                  <div className="grid gap-2 sm:grid-cols-3">
                    {([
                      { value: "none", label: "없음" },
                      { value: "follow_speaker", label: "화자 따라 전환" },
                      { value: "conservative_follow_speaker", label: "보수적 전환" },
                    ] as const).map((option) => {
                      const selected = option.value === currentMulticamSwitching;
                      return (
                        <button
                          key={option.value}
                          type="button"
                          disabled={multicamSettingsSaving || canCancelMulticam}
                          onClick={() => void handleMulticamSwitchingChange(option.value)}
                          className={"rounded-lg border px-3 py-2 text-left text-sm transition disabled:cursor-not-allowed disabled:opacity-50 " + (
                            selected
                              ? "border-cyan-400/50 bg-cyan-500/10 text-cyan-100"
                              : "border-white/[0.08] bg-white/[0.02] text-gray-400 hover:border-white/[0.14] hover:bg-white/[0.04]"
                          )}
                        >
                          {option.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div>
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <p className="text-xs uppercase tracking-wider text-gray-500">화자 매핑</p>
                    {reviewSegmentsLoading && <span className="text-xs text-gray-500">불러오는 중...</span>}
                  </div>
                  {!hasSpeakerMetadata ? (
                    <div className="rounded-lg border border-white/[0.04] bg-white/[0.02] px-4 py-3 text-sm text-gray-500">
                      화자 metadata가 있는 segment가 없습니다.
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {speakerSummaries.map((summary) => (
                        <div key={summary.speaker} className="grid gap-3 rounded-lg border border-white/[0.04] bg-white/[0.02] px-4 py-3 sm:grid-cols-[1fr_220px] sm:items-start">
                          <div className="min-w-0">
                            <p className="text-sm font-medium text-gray-200">{summary.speaker}</p>
                            <div className="mt-1 space-y-1">
                              {summary.samples.slice(0, 3).map((sample, index) => (
                                <p key={`${summary.speaker}-${index}`} className="truncate text-xs text-gray-600">
                                  {truncateSample(sample)}
                                </p>
                              ))}
                            </div>
                          </div>
                          <select
                            value={currentSpeakerSourceMap[summary.speaker] || ""}
                            disabled={multicamSettingsSaving || canCancelMulticam}
                            onChange={(event) => void handleSpeakerSourceChange(summary.speaker, event.currentTarget.value)}
                            className="w-full rounded-md border border-white/[0.08] bg-[#050812] px-2.5 py-2 text-sm text-gray-200 outline-none transition focus:border-cyan-400/40 disabled:opacity-50"
                          >
                            <option value="">카메라 선택</option>
                            {multicamSources.map((source) => (
                              <option key={source.source_key} value={source.source_key}>
                                {source.display_id} {source.display_name}
                              </option>
                            ))}
                          </select>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Pending files */}
            {pendingFiles.length > 0 && (
              <div className="mb-4 space-y-2">
                {pendingFiles.map((file, i) => (
                  <div key={`${file.name}-${i}`} className="flex items-center justify-between bg-cyan-500/[0.03] border border-dashed border-cyan-500/10 rounded-lg px-4 py-2.5">
                    <div className="flex items-center gap-3 min-w-0">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-cyan-500/50 shrink-0">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
                      </svg>
                      <span className="text-sm truncate text-gray-300">{file.name}</span>
                    </div>
                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-xs text-gray-600">{formatSize(file.size)}</span>
                      <button
                        onClick={() => setPendingFiles((prev) => prev.filter((_, j) => j !== i))}
                        className="text-gray-500 hover:text-gray-300 text-xs transition-colors"
                      >
                        제거
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Upload progress */}
            {uploadTask && (
              <div className="mb-4">
                <div className="relative h-2 bg-white/[0.05] rounded-full overflow-hidden">
                  <div
                    className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-cyan-500 to-violet-500 transition-all duration-300"
                    style={{ width: `${uploadTask.progress}%` }}
                  />
                </div>
                <div className="mt-1.5 flex items-center justify-between gap-3 text-xs text-gray-500">
                  <span>{uploadTask.progress}% 업로드 중...</span>
                  <button onClick={() => cancelUpload(uploadTask.taskId)} className="text-red-400/70 hover:text-red-300">
                    취소
                  </button>
                </div>
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-3 items-center">
              <input
                ref={fileInputRef}
                type="file"
                accept="video/*"
                multiple
                className="hidden"
                onChange={(e) => {
                  const files = Array.from(e.target.files || []);
                  if (files.length > 0) setPendingFiles((prev) => [...prev, ...files]);
                  e.target.value = "";
                }}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploadingExtraSources}
                className="px-4 py-2 text-sm bg-white/[0.03] border border-white/[0.08] rounded-lg hover:bg-white/[0.06] hover:border-white/[0.12] transition-all disabled:opacity-50"
              >
                파일 추가
              </button>
              {pendingFiles.length > 0 && (
                <button
                  onClick={handleUploadExtraSources}
                  disabled={isUploadingExtraSources}
                  className="px-4 py-2 text-sm font-medium bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 rounded-lg hover:bg-cyan-500/20 transition-all disabled:opacity-50"
                >
                  {isUploadingExtraSources ? "업로드 중..." : "업로드"}
                </button>
              )}
              {canApplyMulticam && (
                <button
                  onClick={handleMulticamReprocess}
                  disabled={multicamProcessing}
                  className={`group relative px-5 py-2 text-sm font-medium rounded-lg overflow-hidden transition-all duration-300 hover:shadow-[0_0_20px_rgba(6,182,212,0.2)] disabled:opacity-50 ${
                    multicamStatus === "pending_apply" ? "ring-1 ring-cyan-300/40" : ""
                  }`}
                >
                  <div className="absolute inset-0 bg-gradient-to-r from-cyan-500 to-violet-500" />
                  <span className="relative text-white">{multicamProcessing ? "적용 중..." : "멀티캠 적용"}</span>
                </button>
              )}
              {canCancelMulticam && (
                <button
                  onClick={handleCancelMulticam}
                  disabled={multicamProcessing || multicamStatus === "canceling"}
                  className="px-4 py-2 text-sm font-medium rounded-lg border border-red-500/20 bg-red-500/10 text-red-300 transition-all hover:bg-red-500/20 disabled:opacity-50"
                >
                  {multicamStatus === "canceling" ? "취소 중..." : "적용 취소"}
                </button>
              )}
            </div>
          </Section>
        )}

        {/* ── Edit Report ── */}
        {project.report && (
          <Section
            title="편집 보고서"
            icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" /></svg>}
          >
            {/* Stats */}
            <div className="grid grid-cols-3 gap-4 mb-6">
              <div className="relative group/stat">
                <div className="absolute -inset-px rounded-xl bg-gradient-to-br from-white/[0.08] to-transparent opacity-0 group-hover/stat:opacity-100 transition-opacity duration-300" />
                <div className="relative bg-white/[0.02] border border-white/[0.04] rounded-xl p-4 text-center">
                  <p className="text-2xl font-bold">{formatDuration(project.report.total_duration_seconds)}</p>
                  <p className="text-xs text-gray-500 mt-1">전체 길이</p>
                </div>
              </div>
              <div className="relative group/stat">
                <div className="absolute -inset-px rounded-xl bg-gradient-to-br from-cyan-500/10 to-transparent opacity-0 group-hover/stat:opacity-100 transition-opacity duration-300" />
                <div className="relative bg-white/[0.02] border border-white/[0.04] rounded-xl p-4 text-center">
                  <p className="text-2xl font-bold text-cyan-400">{formatDuration(project.report.cut_duration_seconds)}</p>
                  <p className="text-xs text-gray-500 mt-1">컷 구간</p>
                </div>
              </div>
              <div className="relative group/stat">
                <div className="absolute -inset-px rounded-xl bg-gradient-to-br from-violet-500/10 to-transparent opacity-0 group-hover/stat:opacity-100 transition-opacity duration-300" />
                <div className="relative bg-white/[0.02] border border-white/[0.04] rounded-xl p-4 text-center">
                  <p className="text-2xl font-bold gradient-text">{project.report.cut_percentage.toFixed(1)}%</p>
                  <p className="text-xs text-gray-500 mt-1">절약률</p>
                </div>
              </div>
            </div>

            {/* Markdown */}
            <div className="prose prose-invert prose-sm max-w-none
              [&_table]:w-full [&_table]:border-collapse
              [&_th]:bg-white/[0.04] [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:text-xs [&_th]:font-medium [&_th]:text-gray-400 [&_th]:border-b [&_th]:border-white/[0.06]
              [&_td]:px-3 [&_td]:py-2 [&_td]:text-sm [&_td]:border-b [&_td]:border-white/[0.04]
              [&_tr:hover]:bg-white/[0.02]
              [&_h1]:text-lg [&_h1]:font-bold [&_h1]:mb-3 [&_h1]:mt-6
              [&_h2]:text-base [&_h2]:font-semibold [&_h2]:mb-2 [&_h2]:mt-5
              [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mb-2 [&_h3]:mt-4
              [&_p]:text-gray-400 [&_p]:text-sm [&_p]:leading-relaxed
              [&_strong]:text-gray-200
              [&_code]:bg-white/[0.04] [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-xs
              [&_ul]:text-sm [&_ul]:text-gray-400
              [&_ol]:text-sm [&_ol]:text-gray-400
            ">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {project.report.report_markdown}
              </ReactMarkdown>
            </div>
          </Section>
        )}

        <Section
          title="프로젝트 관리"
          icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /></svg>}
        >
          <div className="flex items-center justify-between gap-4">
            <p className="text-sm text-gray-500">삭제하면 프로젝트 기록과 결과 파일이 제거됩니다.</p>
            <button
              onClick={() => setConfirmDelete(true)}
              disabled={!canDeleteProject}
              className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2 text-sm font-medium text-red-300 transition hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-40"
            >
              프로젝트 삭제
            </button>
          </div>
        </Section>
      </main>


      {variantModalOpen && (
        <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 px-4">
          <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-[#0a0f1a] p-6 shadow-2xl">
            <h2 className="text-lg font-semibold">편집 강도 변경</h2>
            <div className="mt-4 rounded-xl border border-white/[0.06] bg-white/[0.025] px-4 py-3 text-sm text-gray-300">
              현재 강도: <span className="font-medium text-cyan-300">{currentEditIntensityLabel}</span>
            </div>
            <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
              {EDIT_INTENSITY_OPTIONS.map((option) => {
                const isCurrent = option.value === currentEditIntensity;
                const isSelected = option.value === variantIntensity;
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => !isCurrent && setVariantIntensity(option.value)}
                    disabled={isCurrent || creatingVariant}
                    className={"rounded-xl border p-3 text-left transition disabled:cursor-not-allowed disabled:opacity-45 " + (
                      isSelected && !isCurrent
                        ? "border-cyan-400/60 bg-cyan-500/10"
                        : "border-white/[0.08] bg-white/[0.02] hover:border-white/[0.16] hover:bg-white/[0.04]"
                    )}
                  >
                    <span className="block text-sm font-medium text-gray-100">{option.label}</span>
                    <span className="mt-1 block text-xs text-gray-500">{isCurrent ? "현재 프로젝트" : option.description}</span>
                  </button>
                );
              })}
            </div>
            <div className="mt-4 rounded-xl border border-white/[0.06] bg-white/[0.025] px-4 py-3 text-xs text-gray-500">
              새 프로젝트 이름: <span className="text-gray-300">{project.name} - {EDIT_INTENSITY_LABELS[variantIntensity]}</span>
            </div>
            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setVariantModalOpen(false)}
                disabled={creatingVariant}
                className="rounded-lg border border-white/10 px-4 py-2 text-sm text-gray-300 transition hover:bg-white/5 disabled:opacity-50"
              >
                취소
              </button>
              <button
                onClick={handleCreateVariant}
                disabled={creatingVariant || variantIntensity === currentEditIntensity}
                className="rounded-lg bg-cyan-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-cyan-400 disabled:opacity-50"
              >
                {creatingVariant ? "생성 중..." : "생성"}
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmDelete && (
        <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 px-4">
          <div className="w-full max-w-md rounded-2xl border border-white/10 bg-[#0a0f1a] p-6 shadow-2xl">
            <h2 className="text-lg font-semibold">프로젝트 삭제</h2>
            <p className="mt-3 text-sm leading-6 text-gray-400">
              “{project.name}” 프로젝트를 삭제합니다. 삭제 후에는 복구할 수 없습니다.
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setConfirmDelete(false)}
                disabled={deleting}
                className="rounded-lg border border-white/10 px-4 py-2 text-sm text-gray-300 transition hover:bg-white/5 disabled:opacity-50"
              >
                취소
              </button>
              <button
                onClick={handleDeleteProject}
                disabled={deleting}
                className="rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-red-400 disabled:opacity-50"
              >
                {deleting ? "삭제 중..." : "삭제"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
