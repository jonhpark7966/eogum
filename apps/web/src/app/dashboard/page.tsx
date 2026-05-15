"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import { api, type CreditBalance, type Job, type Project } from "@/lib/api";
import type { MouseEvent } from "react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import Image from "next/image";

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}시간 ${m}분`;
  return `${m}분`;
}

function getProjectDateKey(project: Project): string {
  const createdAt = new Date(project.created_at);
  const year = createdAt.getFullYear();
  const month = String(createdAt.getMonth() + 1).padStart(2, "0");
  const day = String(createdAt.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatProjectDateLabel(dateKey: string): string {
  const [, month, day] = dateKey.split("-");
  return `${Number(month)}월 ${Number(day)}일`;
}

function getProjectDateGroups(projects: Project[]): { key: string; label: string; projects: Project[] }[] {
  const groups = new Map<string, Project[]>();

  for (const project of projects) {
    const key = getProjectDateKey(project);
    groups.set(key, [...(groups.get(key) ?? []), project]);
  }

  return Array.from(groups.entries())
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([key, groupProjects]) => ({
      key,
      label: formatProjectDateLabel(key),
      projects: groupProjects,
    }));
}

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: string; bg: string }> = {
  queued:     { label: "대기 중",   color: "text-amber-400",  icon: "◷", bg: "bg-amber-400/10" },
  processing: { label: "처리 중",   color: "text-cyan-400",   icon: "⟳", bg: "bg-cyan-400/10" },
  completed:  { label: "완료",      color: "text-emerald-400",icon: "✓", bg: "bg-emerald-400/10" },
  failed:     { label: "실패",      color: "text-red-400",    icon: "✕", bg: "bg-red-400/10" },
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
  retrying,
  onClick,
}: {
  project: Project;
  onRetry: (e: MouseEvent) => void;
  retrying: boolean;
  onClick: () => void;
}) {
  const config = STATUS_CONFIG[project.status] ?? STATUS_CONFIG.created;
  const isProcessing = project.status === "processing" || project.status === "queued";
  const isFailed = project.status === "failed";
  const isCompleted = project.status === "completed";
  const activeJobs = (project.jobs ?? []).filter((job) => job.status === "pending" || job.status === "running");
  const cutTypeLabel = project.cut_type === "subtitle_cut" ? "강의/설명" : "팟캐스트";
  const cutTypeIcon = project.cut_type === "subtitle_cut" ? (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gray-500">
      <rect x="2" y="2" width="20" height="20" rx="2" /><path d="M7 2v20" /><path d="M17 2v20" /><path d="M2 12h20" />
    </svg>
  ) : (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gray-500">
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    </svg>
  );

  return (
    <button
      onClick={onClick}
      className="group relative w-full text-left"
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
            <div className="flex items-center gap-3 mt-2 text-xs text-gray-500">
              <span className="inline-flex items-center gap-1">
                {cutTypeIcon}
                {cutTypeLabel}
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
            <StatusBadge status={project.status} />
          </div>
        </div>

        {/* Processing status */}
        {isProcessing && (
          <div className="mt-4 space-y-3">
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
              const progress = Math.max(0, Math.min(100, job.progress || 0));
              return (
                <div key={job.id} className="rounded-xl border border-white/[0.06] bg-black/20 px-4 py-4">
                  <div className="mb-3 flex items-start justify-between gap-4">
                    <div>
                      <p className="text-sm font-medium text-gray-200">{JOB_TYPE_LABELS[job.type] ?? job.type}</p>
                      <p className="mt-1 text-xs text-gray-500">
                        {progressInfo.title} - {progressInfo.detail}
                      </p>
                    </div>
                    <span className="shrink-0 font-mono text-sm text-gray-400">{progress}%</span>
                  </div>

                  <div className="relative h-2 overflow-hidden rounded-full bg-white/[0.05]">
                    {job.status === "failed" ? (
                      <div className="absolute inset-y-0 left-0 rounded-full bg-red-500/60" style={{ width: `${progress}%` }} />
                    ) : progress < 100 ? (
                      <>
                        <div className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-cyan-500/60 to-violet-500/60 transition-all duration-1000" style={{ width: `${progress}%` }} />
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
          </div>
        )}
      </div>
    </button>
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
  const [projects, setProjects] = useState<Project[]>([]);
  const [credits, setCredits] = useState<CreditBalance | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [selectedDateKey, setSelectedDateKey] = useState<string | null>(null);

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

  const loadData = useCallback(async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) {
      router.replace("/");
      return;
    }

    try {
      const token = session.access_token;
      const [projectList, creditBalance] = await Promise.all([
        api.listProjects(token),
        api.getCredits(token),
      ]);
      setProjects(projectList);
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

  const projectDateGroups = useMemo(() => getProjectDateGroups(projects), [projects]);
  const selectedDateGroup = projectDateGroups.find((group) => group.key === selectedDateKey) ?? projectDateGroups[0] ?? null;

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
          <div>
            <div className="mb-5 flex gap-2 overflow-x-auto pb-1">
              {projectDateGroups.map((group) => {
                const isSelected = group.key === selectedDateGroup?.key;
                return (
                  <button
                    key={group.key}
                    type="button"
                    onClick={() => setSelectedDateKey(group.key)}
                    className={`shrink-0 rounded-full border px-4 py-2 text-sm font-medium transition-colors ${
                      isSelected
                        ? "border-cyan-500/30 bg-cyan-500/10 text-cyan-300"
                        : "border-white/[0.06] bg-white/[0.02] text-gray-400 hover:border-white/[0.12] hover:bg-white/[0.05] hover:text-gray-200"
                    }`}
                  >
                    {group.label}
                  </button>
                );
              })}
            </div>
            <div className="space-y-3">
              {(selectedDateGroup?.projects ?? []).map((project) => (
                <ProjectCard
                  key={project.id}
                  project={project}
                  onRetry={(e) => handleRetry(e, project.id)}
                  retrying={retryingId === project.id}
                  onClick={() => router.push(`/projects/${project.id}`)}
                />
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
