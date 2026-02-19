"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import { api, uploadFile, type ProjectDetail, type ExtraSource } from "@/lib/api";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
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
  created:    { label: "생성됨",    color: "text-gray-400",    icon: "○", bg: "bg-gray-400/10" },
  uploading:  { label: "업로드 중", color: "text-blue-400",    icon: "↑", bg: "bg-blue-400/10" },
  queued:     { label: "대기 중",   color: "text-amber-400",   icon: "◷", bg: "bg-amber-400/10" },
  processing: { label: "처리 중",   color: "text-cyan-400",    icon: "⟳", bg: "bg-cyan-400/10" },
  completed:  { label: "완료",      color: "text-emerald-400", icon: "✓", bg: "bg-emerald-400/10" },
  failed:     { label: "실패",      color: "text-red-400",     icon: "✕", bg: "bg-red-400/10" },
};

const JOB_TYPE_LABELS: Record<string, string> = {
  transcribe: "자막 생성",
  transcript_overview: "구조 분석",
  subtitle_cut: "강의 편집",
  podcast_cut: "팟캐스트 편집",
};

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

  // Multicam state
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [multicamProcessing, setMulticamProcessing] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const projectId = params.id as string;

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
    const result = await api.getDownload(session.access_token, projectId, fileType);
    window.open(result.download_url, "_blank");
  };

  const handleDownloadExtraSource = async (index: number) => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) return;
    const result = await api.downloadExtraSource(session.access_token, projectId, index);
    window.open(result.download_url, "_blank");
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
    setUploading(true);
    setUploadProgress(0);
    try {
      const newSources: ExtraSource[] = [];
      const totalSize = pendingFiles.reduce((sum, f) => sum + f.size, 0);
      let prevUploaded = 0;
      for (let i = 0; i < pendingFiles.length; i++) {
        const file = pendingFiles[i];
        const baseUploaded = prevUploaded;
        const r2Key = await uploadFile(session.access_token, file, (loaded) => {
          setUploadProgress(Math.round(((baseUploaded + loaded) / totalSize) * 100));
        });
        prevUploaded += file.size;
        newSources.push({ r2_key: r2Key, filename: file.name, size_bytes: file.size });
      }
      const allSources = [...project.extra_sources, ...newSources];
      await api.updateExtraSources(session.access_token, projectId, allSources);
      setPendingFiles([]);
      setUploadProgress(100);
      await loadProject();
    } catch (err) {
      setError(err instanceof Error ? err.message : "업로드에 실패했습니다");
    } finally {
      setUploading(false);
      setUploadProgress(null);
    }
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

  const statusConfig = STATUS_CONFIG[project.status] ?? STATUS_CONFIG.created;
  const isProcessing = project.status === "processing" || project.status === "queued";
  const isCompleted = project.status === "completed";
  const isFailed = project.status === "failed";
  const cutTypeLabel = project.cut_type === "subtitle_cut" ? "강의/설명" : "팟캐스트";

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
              {project.jobs.map((job) => (
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
              {[
                { key: "source", label: "원본 소스", icon: "📁", desc: "원본 영상 파일" },
                { key: "fcpxml", label: "FCPXML", icon: "🎬", desc: "Final Cut Pro 프로젝트" },
                { key: "srt", label: "SRT 자막", icon: "💬", desc: "자막 파일" },
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

            {/* Upload progress */}
            {uploadProgress !== null && (
              <div className="mb-4">
                <div className="relative h-2 bg-white/[0.05] rounded-full overflow-hidden">
                  <div
                    className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-cyan-500 to-violet-500 transition-all duration-300"
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
                <p className="text-xs text-gray-500 mt-1.5">{uploadProgress}% 업로드 중...</p>
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
                  if (e.target.files) setPendingFiles((prev) => [...prev, ...Array.from(e.target.files!)]);
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
              {project.extra_sources.length > 0 && !uploading && (
                <button
                  onClick={handleMulticamReprocess}
                  disabled={multicamProcessing}
                  className="group relative px-5 py-2 text-sm font-medium rounded-lg overflow-hidden transition-all duration-300 hover:shadow-[0_0_20px_rgba(6,182,212,0.2)] disabled:opacity-50"
                >
                  <div className="absolute inset-0 bg-gradient-to-r from-cyan-500 to-violet-500" />
                  <span className="relative text-white">{multicamProcessing ? "적용 중..." : "멀티캠 적용"}</span>
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
      </main>
    </div>
  );
}
