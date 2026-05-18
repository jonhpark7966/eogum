"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import { ProjectUploadStatus, useUploads } from "@/app/_providers/upload-provider";
import { api, type Job, type ProjectDetail } from "@/lib/api";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Image from "next/image";

interface ReportSummaryRow {
  label: string;
  count: string;
  duration: string;
  isTotal: boolean;
}

interface ReportDetailSection {
  title: string;
  count: number;
  markdown: string;
}

interface ParsedEditReportMarkdown {
  intro: string;
  summaryRows: ReportSummaryRow[];
  detailSections: ReportDetailSection[];
}

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

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function stripMarkdownEmphasis(value: string): string {
  return value.replace(/\*\*/g, "").trim();
}

function parseEditReportMarkdown(markdown: string): ParsedEditReportMarkdown {
  const lines = markdown.split("\n");
  const detailStartIndex = lines.findIndex((line) => /^## (?!요약\b).+ \(\d+개\)\s*$/.test(line));
  const summaryLines = detailStartIndex === -1 ? lines : lines.slice(0, detailStartIndex);
  const summaryTitleIndex = summaryLines.findIndex((line) => line.trim() === "## 요약");
  const intro = summaryTitleIndex === -1 ? summaryLines.join("\n").trimEnd() : summaryLines.slice(0, summaryTitleIndex).join("\n").trimEnd();
  const summaryRows = summaryLines
    .slice(summaryTitleIndex === -1 ? 0 : summaryTitleIndex + 1)
    .map((line) => line.match(/^\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|$/))
    .filter((match): match is RegExpMatchArray => Boolean(match))
    .map((match) => ({
      label: stripMarkdownEmphasis(match[1]),
      count: stripMarkdownEmphasis(match[2]),
      duration: stripMarkdownEmphasis(match[3]),
      isTotal: stripMarkdownEmphasis(match[1]) === "합계",
    }))
    .filter((row) => row.label !== "유형" && !/^-+$/.test(row.label));

  if (detailStartIndex === -1) {
    return { intro, summaryRows, detailSections: [] };
  }

  const detailSections: ReportDetailSection[] = [];
  let currentTitle = "";
  let currentCount = 0;
  let currentLines: string[] = [];

  for (const line of lines.slice(detailStartIndex)) {
    const sectionMatch = line.match(/^## (.+) \((\d+)개\)\s*$/);
    if (sectionMatch) {
      if (currentLines.length > 0) {
        detailSections.push({
          title: currentTitle,
          count: currentCount,
          markdown: currentLines.join("\n").trim(),
        });
      }
      currentTitle = sectionMatch[1];
      currentCount = Number(sectionMatch[2]);
      currentLines = [line];
      continue;
    }
    currentLines.push(line);
  }

  if (currentLines.length > 0) {
    detailSections.push({
      title: currentTitle,
      count: currentCount,
      markdown: currentLines.join("\n").trim(),
    });
  }

  return { intro, summaryRows, detailSections };
}

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: string; bg: string }> = {
  queued:     { label: "대기 중",   color: "text-amber-400",   icon: "◷", bg: "bg-amber-400/10" },
  processing: { label: "처리 중",   color: "text-cyan-400",    icon: "⟳", bg: "bg-cyan-400/10" },
  completed:  { label: "완료",      color: "text-emerald-400", icon: "✓", bg: "bg-emerald-400/10" },
  failed:     { label: "실패",      color: "text-red-400",     icon: "✕", bg: "bg-red-400/10" },
};

const JOB_TYPE_LABELS: Record<string, string> = {
  subtitle_cut: "편집 처리",
  podcast_cut: "편집 처리",
  reprocess_multicam: "멀티캠 적용",
};

type ProcessingStep = {
  index: number;
  title: string;
  detail: string;
  done: boolean;
  current: boolean;
};

type ProcessingProgressInfo = {
  title: string;
  detail: string;
  steps: ProcessingStep[];
};

function getProcessingProgressInfo(job: Job): ProcessingProgressInfo {
  const progress = Math.max(0, Math.min(100, job.progress || 0));

  if (job.type === "reprocess_multicam") {
    const definitions = [
      { threshold: 0, title: "작업 대기", detail: "멀티캠 적용 작업을 시작할 준비 중입니다." },
      { threshold: 5, title: "소스 다운로드", detail: "기존 프로젝트와 멀티캠 소스를 로컬 작업 공간으로 가져오는 중입니다." },
      { threshold: 25, title: "멀티캠 싱크", detail: "오디오 싱크를 맞추고 멀티캠 타임라인을 다시 구성하는 중입니다." },
      { threshold: 70, title: "결과 내보내기", detail: "새 프로젝트 파일과 결과물을 생성하는 중입니다." },
      { threshold: 85, title: "결과 저장", detail: "생성된 결과물을 R2에 저장하고 프로젝트를 갱신하는 중입니다." },
    ];
    return buildProcessingProgressInfo(progress, definitions);
  }

  const definitions = [
    { threshold: 0, title: "작업 대기", detail: "처리 작업을 시작할 준비 중입니다." },
    { threshold: 5, title: "원본 다운로드", detail: "업로드된 원본 영상을 R2에서 로컬 작업 공간으로 가져오는 중입니다." },
    { threshold: 10, title: "음성 전사", detail: "Chalna API로 음성을 텍스트로 변환하고 있습니다." },
    { threshold: 30, title: "전사 요약", detail: "전사 내용을 바탕으로 전체 흐름과 문맥을 정리하는 중입니다." },
    { threshold: 50, title: "컷 분석", detail: "편집 타입에 맞춰 자를 구간과 유지할 구간을 분석하는 중입니다." },
    { threshold: 75, title: "미리보기 생성", detail: "리뷰에 사용할 저용량 미리보기와 결과 파일을 만드는 중입니다." },
    { threshold: 85, title: "결과 저장", detail: "생성된 결과물을 R2에 업로드하고 리포트를 저장하는 중입니다." },
  ];
  return buildProcessingProgressInfo(progress, definitions);
}

function buildProcessingProgressInfo(
  progress: number,
  definitions: { threshold: number; title: string; detail: string }[],
): ProcessingProgressInfo {
  let activeIndex = 0;
  for (let i = 0; i < definitions.length; i += 1) {
    if (progress >= definitions[i].threshold) activeIndex = i;
  }

  return {
    title: definitions[activeIndex].title,
    detail: definitions[activeIndex].detail,
    steps: definitions.map((step, index) => ({
      ...step,
      index: index + 1,
      done: index < activeIndex,
      current: index === activeIndex,
    })),
  };
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
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [selectedReportReason, setSelectedReportReason] = useState("무음");
  const { enqueueExtraSources, jobsFor } = useUploads();

  // Multicam state
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [multicamProcessing, setMulticamProcessing] = useState(false);
  const [cancelingMulticam, setCancelingMulticam] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const reportDetailsRef = useRef<HTMLDivElement>(null);
  const loadedUploadJobIdsRef = useRef<Set<string>>(new Set());

  const projectId = params.id as string;
  const uploadJobs = jobsFor(projectId);
  const uploading = uploadJobs.some((job) => job.status === "queued" || job.status === "uploading");

  const loadProject = useCallback(async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) { router.replace("/"); return; }
    try {
      const data = await api.getProject(session.access_token, projectId);
      setProject(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "오류가 발생했습니다");
    }
    setLoading(false);
  }, [projectId]);

  useEffect(() => {
    loadProject();
    const interval = setInterval(() => {
      if (project?.status === "processing" || project?.status === "queued") {
        loadProject();
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [loadProject, project?.status]);

  useEffect(() => {
    const newlyDoneJobs = uploadJobs.filter(
      (job) => job.status === "done" && !loadedUploadJobIdsRef.current.has(job.id)
    );
    if (newlyDoneJobs.length === 0) return;

    for (const job of newlyDoneJobs) {
      loadedUploadJobIdsRef.current.add(job.id);
    }
    loadProject();
  }, [loadProject, uploadJobs]);

  useEffect(() => {
    reportDetailsRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  }, [selectedReportReason]);

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
    const { data: { session } } = await supabase.auth.getSession();
    if (!session || !project || pendingFiles.length === 0) return;
    enqueueExtraSources(projectId, pendingFiles, session.access_token);
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

  const handleCancelMulticamReprocess = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) return;
    setCancelingMulticam(true);
    try {
      await api.cancelMulticamReprocess(session.access_token, projectId);
      await loadProject();
    } catch (err) {
      setError(err instanceof Error ? err.message : "멀티캠 적용 취소에 실패했습니다");
    } finally {
      setCancelingMulticam(false);
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
  const isFailed = project.status === "failed";
  const cutTypeLabel = project.cut_type === "subtitle_cut" ? "강의/설명" : "팟캐스트";
  const activeJobs = project.jobs.filter((job) => job.status === "pending" || job.status === "running");
  const activeReprocessJob = project.jobs.find(
    (job) => job.type === "reprocess_multicam" && (job.status === "pending" || job.status === "running")
  );
  const multicamStatus = project.multicam_status ?? { applied: false, applied_at: null, source_count: 0 };
  const multicamApplied = Boolean(multicamStatus.applied);
  const multicamAppliedAt = multicamStatus.applied_at ? formatDateTime(multicamStatus.applied_at) : null;
  const reportMarkdown = project.report ? parseEditReportMarkdown(project.report.report_markdown) : null;
  const selectableReportRows = reportMarkdown?.summaryRows.filter((row) => !row.isTotal) ?? [];
  const selectedReportSection =
    reportMarkdown?.detailSections.find((section) => section.title === selectedReportReason) ??
    reportMarkdown?.detailSections.find((section) => section.title === "무음") ??
    reportMarkdown?.detailSections[0] ??
    null;
  const handleSelectReportReason = (reason: string) => {
    setSelectedReportReason(reason);
    window.requestAnimationFrame(() => {
      reportDetailsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      reportDetailsRef.current?.scrollTo({ top: 0, behavior: "smooth" });
    });
  };

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
                  {project.source_duration_seconds && (
                    <span className="inline-flex items-center gap-1.5">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></svg>
                      {formatDuration(project.source_duration_seconds)}
                    </span>
                  )}
                  <span>{project.language === "ko" ? "한국어" : project.language === "en" ? "English" : project.language}</span>
                  <span>{new Date(project.created_at).toLocaleDateString("ko-KR")}</span>
                  {multicamApplied && (
                    <span className="inline-flex items-center gap-1.5 text-emerald-400">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                        <path d="M20 6 9 17l-5-5" />
                      </svg>
                      멀티캠 적용됨
                    </span>
                  )}
                </div>
              </div>
              <span className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium ${statusConfig.color} ${statusConfig.bg}`}>
                <span className={isProcessing ? "animate-spin" : ""}>{statusConfig.icon}</span>
                {statusConfig.label}
              </span>
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

        {/* ── Processing Status ── */}
        {isProcessing && (
          <Section
            title="처리 상태"
            icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 2v4" /><path d="M12 18v4" /><path d="M4.93 4.93l2.83 2.83" /><path d="M16.24 16.24l2.83 2.83" /><path d="M2 12h4" /><path d="M18 12h4" /><path d="M4.93 19.07l2.83-2.83" /><path d="M16.24 7.76l2.83-2.83" /></svg>}
          >
            <div className="space-y-4">
              {activeJobs.length === 0 && (
                <div className="rounded-xl border border-white/[0.06] bg-black/20 px-4 py-3">
                  <div className="mb-2 flex items-center justify-between text-sm">
                    <span className="font-medium text-gray-300">작업 대기 중</span>
                    <span className="text-gray-500">0%</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-white/[0.05]">
                    <div className="h-full w-0 rounded-full bg-gradient-to-r from-cyan-500/60 to-violet-500/60" />
                  </div>
                  <p className="mt-3 text-xs text-gray-500">
                    처리 작업이 큐에 등록되었고 곧 시작됩니다.
                  </p>
                </div>
              )}

              {activeJobs.map((job) => {
                const progressInfo = getProcessingProgressInfo(job);
                return (
                  <div key={job.id} className="rounded-xl border border-white/[0.06] bg-black/20 px-4 py-4">
                    <div className="mb-3 flex items-start justify-between gap-4">
                      <div>
                        <p className="text-sm font-medium text-gray-200">{JOB_TYPE_LABELS[job.type] ?? job.type}</p>
                        <p className="mt-1 text-xs text-gray-500">
                          {progressInfo.title} - {progressInfo.detail}
                        </p>
                      </div>
                      <span className="shrink-0 font-mono text-sm text-gray-400">{job.progress}%</span>
                    </div>

                    <div className="relative h-2 overflow-hidden rounded-full bg-white/[0.05]">
                      {job.status === "failed" ? (
                        <div className="absolute inset-y-0 left-0 rounded-full bg-red-500/60" style={{ width: `${job.progress}%` }} />
                      ) : job.progress < 100 ? (
                        <>
                          <div className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-cyan-500/60 to-violet-500/60 transition-all duration-1000" style={{ width: `${job.progress}%` }} />
                          <div className="absolute inset-y-0 w-1/4 rounded-full bg-gradient-to-r from-transparent via-white/20 to-transparent animate-[shimmer_2s_ease-in-out_infinite]" />
                        </>
                      ) : (
                        <div className="absolute inset-y-0 left-0 w-full rounded-full bg-gradient-to-r from-cyan-500 to-violet-500" />
                      )}
                    </div>

                    <div className="mt-4 rounded-lg border border-gray-800 bg-black/30 p-3">
                      <div className="mb-2 flex items-center justify-between">
                        <p className="text-xs font-medium text-gray-300">진행 로그</p>
                        <p className="text-xs text-gray-500">{progressInfo.steps.length} steps</p>
                      </div>
                      <div className="space-y-1 font-mono text-xs leading-relaxed">
                        {progressInfo.steps.map((step) => (
                          <p
                            key={step.index}
                            className={
                              step.current
                                ? "text-cyan-300"
                                : step.done
                                  ? "text-gray-500"
                                  : "text-gray-700"
                            }
                          >
                            [{step.index}/{progressInfo.steps.length}] {step.done ? "완료" : step.current ? "진행 중" : "대기"} - {step.title}
                          </p>
                        ))}
                      </div>
                    </div>
                  </div>
                );
              })}
              {activeReprocessJob && (
                <div className="flex items-center justify-between gap-4 rounded-xl border border-red-500/10 bg-red-500/[0.04] px-4 py-3">
                  <div>
                    <p className="text-sm font-medium text-red-200">멀티캠 적용 작업 실행 중</p>
                    <p className="mt-0.5 text-xs text-gray-500">
                      취소하면 다운로드와 오디오 싱크 작업을 중단하고 기존 완료 결과로 돌아갑니다.
                    </p>
                  </div>
                  <button
                    onClick={handleCancelMulticamReprocess}
                    disabled={cancelingMulticam}
                    className="shrink-0 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2 text-sm font-medium text-red-300 transition-colors hover:bg-red-500/20 disabled:opacity-50"
                  >
                    {cancelingMulticam ? "취소 중..." : "멀티캠 적용 취소"}
                  </button>
                </div>
              )}
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
              {[
                { key: "source", label: "원본 소스", icon: "📁", desc: "원본 영상 파일" },
                { key: "fcpxml", label: "FCPXML", icon: "🎬", desc: "Final Cut Pro 프로젝트" },
                { key: "srt", label: "SRT 자막", icon: "💬", desc: "자막 파일" },
                { key: "report", label: "편집 리포트", icon: "📄", desc: "편집 보고서 (.md)" },
                { key: "project_json", label: "프로젝트 JSON", icon: "📦", desc: "avid 프로젝트 파일" },
                { key: "storyline", label: "스토리라인", icon: "📋", desc: "구조 분석 JSON" },
              ].map(({ key, label, icon, desc }) => (
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

            {project.extra_sources.length > 0 && (
              <div className={`mb-4 rounded-xl border px-4 py-3 ${
                multicamApplied
                  ? "border-emerald-500/20 bg-emerald-500/[0.05]"
                  : activeReprocessJob
                    ? "border-cyan-500/20 bg-cyan-500/[0.05]"
                    : "border-amber-500/20 bg-amber-500/[0.05]"
              }`}>
                <div className="flex items-start gap-3">
                  <div className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${
                    multicamApplied
                      ? "bg-emerald-500/10 text-emerald-300"
                      : activeReprocessJob
                        ? "bg-cyan-500/10 text-cyan-300"
                        : "bg-amber-500/10 text-amber-300"
                  }`}>
                    {multicamApplied ? (
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                        <path d="M20 6 9 17l-5-5" />
                      </svg>
                    ) : activeReprocessJob ? (
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className="animate-spin">
                        <path d="M12 2v4" /><path d="M12 18v4" /><path d="M4.93 4.93l2.83 2.83" /><path d="M16.24 16.24l2.83 2.83" /><path d="M2 12h4" /><path d="M18 12h4" /><path d="M4.93 19.07l2.83-2.83" /><path d="M16.24 7.76l2.83-2.83" />
                      </svg>
                    ) : (
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                        <circle cx="12" cy="12" r="10" /><line x1="12" y1="7" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
                      </svg>
                    )}
                  </div>
                  <div>
                    <p className={`text-sm font-medium ${
                      multicamApplied ? "text-emerald-200" : activeReprocessJob ? "text-cyan-200" : "text-amber-200"
                    }`}>
                      {multicamApplied ? "멀티캠 적용 완료" : activeReprocessJob ? "멀티캠 적용 중" : "멀티캠 반영 전"}
                    </p>
                    <p className="mt-1 text-xs leading-relaxed text-gray-500">
                      {multicamApplied
                        ? `${multicamStatus.source_count}개 소스가 현재 결과물에 반영되었습니다${multicamAppliedAt ? ` · ${multicamAppliedAt}` : ""}.`
                        : activeReprocessJob
                          ? `${project.extra_sources.length}개 소스를 결과물에 반영하는 중입니다.`
                          : `${project.extra_sources.length}개 소스가 업로드되었지만 현재 결과물에는 아직 반영되지 않았습니다.`}
                    </p>
                  </div>
                </div>
              </div>
            )}

            <ProjectUploadStatus projectId={projectId} className="mb-4" />

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
                disabled={uploading}
                className="px-4 py-2 text-sm bg-white/[0.03] border border-white/[0.08] rounded-lg hover:bg-white/[0.06] hover:border-white/[0.12] transition-all disabled:opacity-50"
              >
                파일 추가
              </button>
              {pendingFiles.length > 0 && (
                <button
                  onClick={handleUploadExtraSources}
                  disabled={uploading}
                  className="px-4 py-2 text-sm font-medium bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 rounded-lg hover:bg-cyan-500/20 transition-all disabled:opacity-50"
                >
                  {uploading ? "업로드 중..." : "업로드"}
                </button>
              )}
              {project.extra_sources.length > 0 && !uploading && !activeReprocessJob && (
                <button
                  onClick={handleMulticamReprocess}
                  disabled={multicamProcessing}
                  className="group relative px-5 py-2 text-sm font-medium rounded-lg overflow-hidden transition-all duration-300 hover:shadow-[0_0_20px_rgba(6,182,212,0.2)] disabled:opacity-50"
                >
                  <div className="absolute inset-0 bg-gradient-to-r from-cyan-500 to-violet-500" />
                  <span className="relative text-white">
                    {multicamProcessing ? "적용 중..." : multicamApplied ? "멀티캠 재적용" : "멀티캠 적용"}
                  </span>
                </button>
              )}
              {activeReprocessJob && (
                <button
                  onClick={handleCancelMulticamReprocess}
                  disabled={cancelingMulticam}
                  className="px-4 py-2 text-sm font-medium bg-red-500/10 text-red-300 border border-red-500/20 rounded-lg hover:bg-red-500/20 transition-all disabled:opacity-50"
                >
                  {cancelingMulticam ? "취소 중..." : "멀티캠 적용 취소"}
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
              [&_h1]:text-lg [&_h1]:font-bold [&_h1]:mb-3 [&_h1]:mt-6
              [&_p]:text-gray-400 [&_p]:text-sm [&_p]:leading-relaxed
              [&_strong]:text-gray-200
              [&_code]:bg-white/[0.04] [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-xs
              [&_ul]:text-sm [&_ul]:text-gray-400
              [&_ol]:text-sm [&_ol]:text-gray-400
            ">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {reportMarkdown?.intro ?? project.report.report_markdown}
              </ReactMarkdown>
            </div>
            {reportMarkdown && reportMarkdown.summaryRows.length > 0 && (
              <div className="mt-5">
                <h2 className="mb-2 text-base font-semibold">요약</h2>
                <div className="overflow-hidden rounded-xl border border-white/[0.06]">
                  <table className="w-full border-collapse text-sm">
                    <thead className="bg-white/[0.04] text-left text-xs font-medium text-gray-400">
                      <tr>
                        <th className="border-b border-white/[0.06] px-3 py-2">유형</th>
                        <th className="border-b border-white/[0.06] px-3 py-2">개수</th>
                        <th className="border-b border-white/[0.06] px-3 py-2">총 시간</th>
                      </tr>
                    </thead>
                    <tbody>
                      {reportMarkdown.summaryRows.map((row) => {
                        const isSelected = selectedReportSection?.title === row.label;
                        return (
                          <tr key={row.label} className={row.isTotal ? "bg-white/[0.025] font-semibold text-gray-200" : "hover:bg-white/[0.02]"}>
                            <td className="border-b border-white/[0.04] px-3 py-2">
                              {row.isTotal ? (
                                <span>{row.label}</span>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => handleSelectReportReason(row.label)}
                                  className={`rounded-md px-2 py-1 text-left font-medium transition-colors ${
                                    isSelected
                                      ? "bg-cyan-500/10 text-cyan-300"
                                      : "text-gray-300 hover:bg-white/[0.05] hover:text-white"
                                  }`}
                                >
                                  {row.label}
                                </button>
                              )}
                            </td>
                            <td className="border-b border-white/[0.04] px-3 py-2 text-gray-400">{row.count}</td>
                            <td className="border-b border-white/[0.04] px-3 py-2 text-gray-400">{row.duration}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            {selectedReportSection && selectableReportRows.length > 0 && (
              <div ref={reportDetailsRef} className="mt-6 max-h-[560px] overflow-y-auto overscroll-contain rounded-xl border border-white/[0.06] bg-[#080d17] px-4 pb-1 pt-0 pr-3">
                <div className="prose prose-invert prose-sm max-w-none
                  [&_h2]:sticky [&_h2]:top-0 [&_h2]:z-20 [&_h2]:-mx-4 [&_h2]:mt-0 [&_h2]:mb-3 [&_h2]:border-b [&_h2]:border-white/[0.06] [&_h2]:bg-[#080d17] [&_h2]:px-4 [&_h2]:py-3 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:shadow-[0_10px_18px_rgba(8,13,23,0.95)]
                  [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mb-2 [&_h3]:mt-4
                  [&_p]:text-gray-400 [&_p]:text-sm [&_p]:leading-relaxed
                  [&_strong]:text-gray-200
                  [&_ul]:text-sm [&_ul]:text-gray-400
                ">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {selectedReportSection.markdown}
                  </ReactMarkdown>
                </div>
              </div>
            )}
          </Section>
        )}
      </main>
    </div>
  );
}
