"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import { api, type CreditBalance, type CutType, type Project } from "@/lib/api";
import type { MouseEvent, ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import Image from "next/image";

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}시간 ${m}분`;
  return `${m}분`;
}

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: string; bg: string }> = {
  queued:     { label: "대기 중",   color: "text-amber-400",  icon: "◷", bg: "bg-amber-400/10" },
  processing: { label: "처리 중",   color: "text-cyan-400",   icon: "⟳", bg: "bg-cyan-400/10" },
  completed:  { label: "완료",      color: "text-emerald-400",icon: "✓", bg: "bg-emerald-400/10" },
  failed:     { label: "실패",      color: "text-red-400",    icon: "✕", bg: "bg-red-400/10" },
  reprocess_failed: { label: "재적용 실패", color: "text-red-400", icon: "✕", bg: "bg-red-400/10" },
};

type EditIntensity = "light" | "normal" | "heavy";
type ProjectSettingsCacheEntry = {
  updated_at: string;
  settings: Record<string, unknown>;
};

const PROJECT_DETAIL_CONCURRENCY = 4;

const EDIT_INTENSITY_LABELS: Record<EditIntensity, string> = {
  light: "적게 편집",
  normal: "일반 편집",
  heavy: "많이 편집",
};

const CUT_TYPE_LABELS: Record<CutType, string> = {
  subtitle_cut: "강의/설명",
  podcast_cut: "팟캐스트",
  ai_frontier_cut: "AI Frontier",
};

const CUT_TYPE_ICONS: Record<CutType, ReactNode> = {
  subtitle_cut: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gray-500">
      <rect x="2" y="2" width="20" height="20" rx="2" /><path d="M7 2v20" /><path d="M17 2v20" /><path d="M2 12h20" />
    </svg>
  ),
  podcast_cut: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gray-500">
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    </svg>
  ),
  ai_frontier_cut: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gray-500">
      <path d="m12 2 1.4 5.1L18 9l-4.6 1.9L12 16l-1.4-5.1L6 9l4.6-1.9L12 2Z" /><path d="m19 15 .7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7L19 15Z" />
    </svg>
  ),
};

function normalizeEditIntensity(value: unknown): EditIntensity {
  return value === "light" || value === "normal" || value === "heavy" ? value : "normal";
}

async function hydrateProjectSettings(
  projectList: Project[],
  token: string,
  cache: Map<string, ProjectSettingsCacheEntry>
): Promise<Project[]> {
  const activeProjectIds = new Set(projectList.map((project) => project.id));
  for (const projectId of cache.keys()) {
    if (!activeProjectIds.has(projectId)) cache.delete(projectId);
  }

  const hydratedProjects = [...projectList];
  const detailRequests: { project: Project; index: number }[] = [];

  projectList.forEach((project, index) => {
    if (project.settings) {
      cache.set(project.id, {
        updated_at: project.updated_at,
        settings: project.settings,
      });
      return;
    }

    const cached = cache.get(project.id);
    if (cached && cached.updated_at === project.updated_at) {
      hydratedProjects[index] = { ...project, settings: cached.settings };
      return;
    }

    detailRequests.push({ project, index });
  });

  for (let i = 0; i < detailRequests.length; i += PROJECT_DETAIL_CONCURRENCY) {
    const chunk = detailRequests.slice(i, i + PROJECT_DETAIL_CONCURRENCY);
    await Promise.all(
      chunk.map(async ({ project, index }) => {
        try {
          const detail = await api.getProject(token, project.id);
          cache.set(project.id, {
            updated_at: project.updated_at,
            settings: detail.settings,
          });
          hydratedProjects[index] = { ...project, settings: detail.settings };
        } catch (error) {
          console.warn("Project detail preload failed:", project.id, error);
          hydratedProjects[index] = project;
        }
      })
    );
  }

  return hydratedProjects;
}

function StatusBadge({ status }: { status: string }) {
  const config = STATUS_CONFIG[status] ?? { label: status, color: "text-gray-400", icon: "?", bg: "bg-gray-400/10" };
  const isAnimated = status === "processing" || status === "queued";

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${config.color} ${config.bg}`}>
      <span className={isAnimated ? "animate-spin" : ""}>{config.icon}</span>
      {config.label}
    </span>
  );
}

function CreditCard({ credits }: { credits: CreditBalance }) {
  const totalSeconds = credits.balance_seconds;
  const usedSeconds = totalSeconds - credits.available_seconds;
  const usagePercent = totalSeconds > 0 ? Math.round((usedSeconds / totalSeconds) * 100) : 0;
  const remainPercent = 100 - usagePercent;

  return (
    <div className="relative group">
      <div className="absolute -inset-px rounded-2xl bg-gradient-to-r from-cyan-500/30 via-violet-500/20 to-cyan-500/30 opacity-60 group-hover:opacity-100 transition-opacity duration-500 blur-[1px]" />
      <div className="relative bg-[#0a0f1a] border border-white/[0.06] rounded-2xl p-6">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-cyan-400">
              <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
            </svg>
            <span className="text-sm text-gray-400">크레딧 잔액</span>
          </div>
          <span className="text-xs text-gray-600">
            {credits.held_seconds > 0 && `${formatDuration(credits.held_seconds)} 사용 예약`}
          </span>
        </div>

        <p className="text-2xl font-bold mb-4">
          <span className="gradient-text">{formatDuration(credits.available_seconds)}</span>
          <span className="text-sm text-gray-500 font-normal ml-2">사용 가능</span>
        </p>

        <div className="relative h-2 bg-white/[0.05] rounded-full overflow-hidden">
          <div
            className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-cyan-500 to-violet-500 transition-all duration-1000"
            style={{ width: `${remainPercent}%` }}
          />
        </div>
        <div className="flex justify-between mt-2 text-xs text-gray-600">
          <span>전체 {formatDuration(totalSeconds)}</span>
          <span>{remainPercent}% 남음</span>
        </div>
      </div>
    </div>
  );
}

function ProjectCard({
  project,
  onRetry,
  onDelete,
  retrying,
  deleting,
  onClick,
  currentUserId,
}: {
  project: Project;
  onRetry: (e: MouseEvent) => void;
  onDelete: (e: MouseEvent) => void;
  retrying: boolean;
  deleting: boolean;
  onClick: () => void;
  currentUserId: string | null;
}) {
  const isProcessing = project.status === "processing" || project.status === "queued";
  const isFailed = project.status === "failed";
  const isCompleted = project.status === "completed";
  const isOwnProject = project.user_id === currentUserId;
  const ownerShortId = project.user_id.slice(0, 8);
  const ownerBadgeClass = isOwnProject
    ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300"
    : "border-amber-400/20 bg-amber-400/10 text-amber-300";
  const cutTypeLabel = CUT_TYPE_LABELS[project.cut_type];
  const editIntensity = normalizeEditIntensity(project.settings?.edit_intensity);
  const editIntensityLabel = EDIT_INTENSITY_LABELS[editIntensity];
  const cutTypeIcon = CUT_TYPE_ICONS[project.cut_type];

  return (
    <div
      onClick={onClick}
      className="group relative w-full cursor-pointer text-left"
      role="button"
      tabIndex={0}
    >
      {/* Gradient border on hover */}
      <div className={`absolute -inset-px rounded-xl transition-opacity duration-500 ${
        isCompleted
          ? "bg-gradient-to-r from-emerald-500/20 via-transparent to-emerald-500/20 opacity-0 group-hover:opacity-100"
          : isFailed
          ? "bg-gradient-to-r from-red-500/20 via-transparent to-red-500/20 opacity-0 group-hover:opacity-100"
          : isProcessing
          ? "bg-gradient-to-r from-cyan-500/20 via-violet-500/20 to-cyan-500/20 opacity-50"
          : "bg-gradient-to-r from-white/10 via-transparent to-white/10 opacity-0 group-hover:opacity-100"
      }`} />

      <div className="relative bg-white/[0.02] border border-white/[0.06] rounded-xl p-5 group-hover:border-white/[0.1] transition-all duration-300">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-[15px] truncate group-hover:text-white transition-colors">{project.name}</h3>
            <div className="flex flex-wrap items-center gap-3 mt-2 text-xs text-gray-500">
              <span
                className={"inline-flex items-center rounded-md border px-2 py-0.5 " + ownerBadgeClass}
                title={isOwnProject ? "내 프로젝트" : "소유자: " + project.user_id}
              >
                {isOwnProject ? "내 프로젝트" : "유저 프로젝트 · " + ownerShortId}
              </span>
              <span className="inline-flex items-center gap-1">
                {cutTypeIcon}
                {cutTypeLabel}
              </span>
              <span className="inline-flex items-center gap-1 text-cyan-300">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-cyan-400/70">
                  <path d="M4 21v-7" /><path d="M4 10V3" /><path d="M12 21v-9" /><path d="M12 8V3" /><path d="M20 21v-5" /><path d="M20 12V3" /><path d="M2 14h4" /><path d="M10 8h4" /><path d="M18 16h4" />
                </svg>
                {editIntensityLabel}
              </span>
              {project.source_duration_seconds && (
                <span>{formatDuration(project.source_duration_seconds)}</span>
              )}
              <span>{new Date(project.created_at).toLocaleDateString("ko-KR")}</span>
              {project.extra_sources?.length > 0 && (
                <span className="inline-flex items-center gap-1">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gray-600">
                    <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z" />
                  </svg>
                  {project.extra_sources.length}캠
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {isFailed && (
              <button
                onClick={onRetry}
                disabled={retrying}
                className="px-3 py-1.5 text-xs font-medium rounded-lg bg-gradient-to-r from-cyan-500 to-violet-500 text-white hover:opacity-90 transition disabled:opacity-50"
              >
                {retrying ? "재시도 중..." : "재시도"}
              </button>
            )}
            <button
              onClick={onDelete}
              disabled={isProcessing || deleting}
              className="px-3 py-1.5 text-xs font-medium rounded-lg border border-red-500/20 text-red-300 hover:bg-red-500/10 transition disabled:opacity-40 disabled:cursor-not-allowed"
              title={isProcessing ? "처리 중인 프로젝트는 삭제할 수 없습니다" : "프로젝트 삭제"}
            >
              {deleting ? "삭제 중..." : "삭제"}
            </button>
            <StatusBadge status={project.status} />
          </div>
        </div>

        {/* Processing animation bar */}
        {isProcessing && (
          <div className="mt-4 relative h-1 bg-white/[0.05] rounded-full overflow-hidden">
            <div className="absolute inset-y-0 w-1/3 bg-gradient-to-r from-cyan-500/60 to-violet-500/60 rounded-full animate-[shimmer_2s_ease-in-out_infinite]" />
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="relative py-20 text-center">
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="w-[300px] h-[200px] bg-cyan-500/[0.03] rounded-full blur-[80px]" />
      </div>
      <div className="relative">
        <div className="flex justify-center mb-6">
          <div className="w-20 h-20 rounded-2xl bg-white/[0.03] border border-white/[0.06] flex items-center justify-center">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" className="text-gray-600">
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
          </div>
        </div>
        <p className="text-lg text-gray-400 mb-2">아직 프로젝트가 없습니다</p>
        <p className="text-sm text-gray-600 mb-8">영상을 업로드하면 AI가 편집 포인트를 찾아드립니다</p>
        <button
          onClick={onNew}
          className="group relative inline-flex px-8 py-3 font-medium rounded-xl overflow-hidden transition-all duration-300 hover:shadow-[0_0_30px_rgba(6,182,212,0.2)]"
        >
          <div className="absolute inset-0 bg-gradient-to-r from-cyan-500 to-violet-500" />
          <div className="absolute inset-0 bg-gradient-to-r from-cyan-400 to-violet-400 opacity-0 group-hover:opacity-100 transition-opacity" />
          <span className="relative text-white flex items-center gap-2">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            첫 프로젝트 만들기
          </span>
        </button>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const supabase = createClient();
  const projectSettingsCache = useRef<Map<string, ProjectSettingsCacheEntry>>(new Map());
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  const [credits, setCredits] = useState<CreditBalance | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);

  const handleRetry = async (e: MouseEvent, projectId: string) => {
    e.stopPropagation();
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) return;

    setRetryingId(projectId);
    try {
      await api.retryProject(session.access_token, projectId);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "재시도에 실패했습니다");
    } finally {
      setRetryingId(null);
    }
  };

  const handleDelete = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session || !deleteTarget) return;

    setDeletingId(deleteTarget.id);
    try {
      await api.deleteProject(session.access_token, deleteTarget.id);
      setProjects((prev) => prev.filter((project) => project.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "프로젝트 삭제에 실패했습니다");
    } finally {
      setDeletingId(null);
    }
  };

  const loadData = useCallback(async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) {
      router.replace("/");
      return;
    }

    setCurrentUserId(session.user.id);

    try {
      const token = session.access_token;
      const [projectList, creditBalance] = await Promise.all([
        api.listProjects(token),
        api.getCredits(token),
      ]);
      const projectsWithSettings = await hydrateProjectSettings(
        projectList,
        token,
        projectSettingsCache.current
      );
      setProjects(projectsWithSettings);
      setCredits(creditBalance);
      setError(null);
    } catch (e) {
      console.error("API 호출 실패:", e);
      setError("서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 10000);
    return () => clearInterval(interval);
  }, [loadData]);

  const handleLogout = async () => {
    await supabase.auth.signOut();
    router.replace("/");
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#030712]">
        <Image src="/logo.png" alt="어검" width={48} height={48} className="animate-pulse rounded" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#030712] text-white dot-grid">
      {/* ── Nav ── */}
      <nav className="sticky top-0 z-50 border-b border-white/[0.04] bg-[#030712]/80 backdrop-blur-xl">
        <div className="max-w-5xl mx-auto px-6 h-16 flex items-center justify-between">
          <button onClick={() => router.push("/dashboard")} className="flex items-center gap-2">
            <Image src="/logo.png" alt="어검" width={28} height={28} className="rounded" />
            <span className="font-bold text-lg tracking-tight">어검</span>
          </button>
          <div className="flex items-center gap-5">
            {credits && (
              <span className="text-sm text-gray-500">
                <span className="text-gray-300 font-medium">{formatDuration(credits.available_seconds)}</span>
                {" "}남음
              </span>
            )}
            <button
              onClick={handleLogout}
              className="text-sm text-gray-500 hover:text-gray-300 transition-colors"
            >
              로그아웃
            </button>
          </div>
        </div>
      </nav>

      {/* ── Content ── */}
      <main className="max-w-5xl mx-auto px-6 py-8">
        {/* Error */}
        {error && (
          <div className="mb-6 p-4 bg-red-500/[0.06] border border-red-500/20 rounded-xl text-red-300 text-sm flex items-center gap-3">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="shrink-0">
              <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            {error}
          </div>
        )}

        {/* Credit Card */}
        {credits && (
          <div className="mb-8">
            <CreditCard credits={credits} />
          </div>
        )}

        {/* Project header */}
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-xl font-semibold">프로젝트</h2>
          <button
            onClick={() => router.push("/dashboard/new")}
            className="group relative px-5 py-2 text-sm font-medium rounded-xl overflow-hidden transition-all duration-300 hover:shadow-[0_0_20px_rgba(6,182,212,0.2)]"
          >
            <div className="absolute inset-0 bg-gradient-to-r from-cyan-500 to-violet-500" />
            <div className="absolute inset-0 bg-gradient-to-r from-cyan-400 to-violet-400 opacity-0 group-hover:opacity-100 transition-opacity" />
            <span className="relative text-white flex items-center gap-1.5">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              새 프로젝트
            </span>
          </button>
        </div>

        {/* Project list */}
        {projects.length === 0 ? (
          <EmptyState onNew={() => router.push("/dashboard/new")} />
        ) : (
          <div className="space-y-3">
            {projects.map((project) => (
              <ProjectCard
                key={project.id}
                project={project}
                onRetry={(e) => handleRetry(e, project.id)}
                onDelete={(e) => {
                  e.stopPropagation();
                  setDeleteTarget(project);
                }}
                retrying={retryingId === project.id}
                deleting={deletingId === project.id}
                onClick={() => router.push("/projects/" + project.id)}
                currentUserId={currentUserId}
              />
            ))}
          </div>
        )}
      </main>
      {deleteTarget && (
        <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 px-4">
          <div className="w-full max-w-md rounded-2xl border border-white/10 bg-[#0a0f1a] p-6 shadow-2xl">
            <h2 className="text-lg font-semibold">프로젝트 삭제</h2>
            <p className="mt-3 text-sm leading-6 text-gray-400">
              “{deleteTarget.name}” 프로젝트를 삭제합니다. 삭제 후에는 복구할 수 없습니다.
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setDeleteTarget(null)}
                disabled={deletingId === deleteTarget.id}
                className="rounded-lg border border-white/10 px-4 py-2 text-sm text-gray-300 transition hover:bg-white/5 disabled:opacity-50"
              >
                취소
              </button>
              <button
                onClick={handleDelete}
                disabled={deletingId === deleteTarget.id}
                className="rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-red-400 disabled:opacity-50"
              >
                {deletingId === deleteTarget.id ? "삭제 중..." : "삭제"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
