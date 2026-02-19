"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import { api, type ProjectDetail } from "@/lib/api";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}시간 ${m}분 ${s}초`;
  if (m > 0) return `${m}분 ${s}초`;
  return `${s}초`;
}

const STATUS_LABELS: Record<string, string> = {
  created: "생성됨",
  uploading: "업로드 중",
  queued: "대기 중",
  processing: "처리 중",
  completed: "완료",
  failed: "실패",
};

const JOB_TYPE_LABELS: Record<string, string> = {
  transcribe: "자막 생성",
  transcript_overview: "구조 분석",
  subtitle_cut: "강의 편집",
  podcast_cut: "팟캐스트 편집",
};

export default function ProjectDetailPage() {
  const params = useParams();
  const router = useRouter();
  const supabase = createClient();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);

  const projectId = params.id as string;

  const loadProject = useCallback(async () => {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session) {
      router.replace("/");
      return;
    }

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
    // Poll while processing
    const interval = setInterval(() => {
      if (
        project?.status === "processing" ||
        project?.status === "queued"
      ) {
        loadProject();
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [loadProject, project?.status]);

  const handleRetry = async () => {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session) return;

    setRetrying(true);
    try {
      await api.retryProject(session.access_token, projectId);
      await loadProject();
    } catch (err) {
      setError(err instanceof Error ? err.message : "재시도에 실패했습니다");
    } finally {
      setRetrying(false);
    }
  };

  const handleDownload = async (fileType: string) => {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session) return;

    const result = await api.getDownload(
      session.access_token,
      projectId,
      fileType
    );
    window.open(result.download_url, "_blank");
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-950">
        <div className="animate-pulse text-gray-400">Loading...</div>
      </div>
    );
  }

  if (error || !project) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-950 text-red-400">
        {error || "프로젝트를 찾을 수 없습니다"}
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      <header className="border-b border-gray-800">
        <div className="max-w-6xl mx-auto px-6 py-4 flex justify-between items-center">
          <button
            onClick={() => router.push("/dashboard")}
            className="text-gray-400 hover:text-white transition"
          >
            ← 대시보드
          </button>
          <h1 className="text-xl font-bold">어검</h1>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-8">
        {/* Project Info */}
        <div className="mb-8">
          <h2 className="text-2xl font-semibold mb-2">{project.name}</h2>
          <div className="flex gap-4 text-sm text-gray-400">
            <span>
              {project.cut_type === "subtitle_cut" ? "강의/설명" : "팟캐스트"}
            </span>
            {project.source_duration_seconds && (
              <span>{formatDuration(project.source_duration_seconds)}</span>
            )}
            <span>{STATUS_LABELS[project.status] ?? project.status}</span>
          </div>
        </div>

        {/* Processing Status */}
        {(project.status === "queued" || project.status === "processing") && (
          <div className="bg-gray-900 rounded-lg p-6 mb-8">
            <h3 className="font-medium mb-4">처리 상태</h3>
            {project.jobs.map((job) => (
              <div key={job.id} className="mb-3 last:mb-0">
                <div className="flex justify-between text-sm mb-1">
                  <span>{JOB_TYPE_LABELS[job.type] ?? job.type}</span>
                  <span className="text-gray-400">{job.progress}%</span>
                </div>
                <div className="w-full bg-gray-800 rounded-full h-2">
                  <div
                    className={`h-2 rounded-full transition-all duration-500 ${
                      job.status === "failed" ? "bg-red-500" : "bg-blue-500"
                    }`}
                    style={{ width: `${job.progress}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Error */}
        {project.status === "failed" && (
          <div className="bg-red-900/30 border border-red-700 rounded-lg p-6 mb-8">
            <h3 className="font-medium text-red-200 mb-2">처리 실패</h3>
            {project.jobs
              .filter((j) => j.status === "failed")
              .map((j) => (
                <p key={j.id} className="text-sm text-red-300">
                  {j.error_message}
                </p>
              ))}
            <p className="text-sm text-gray-400 mt-2">
              홀딩된 크레딧은 자동으로 복구되었습니다.
            </p>
            <button
              onClick={handleRetry}
              disabled={retrying}
              className="mt-4 px-5 py-2 bg-white text-black font-medium rounded-lg hover:bg-gray-200 transition disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {retrying ? "재시도 중..." : "재시도"}
            </button>
          </div>
        )}

        {/* Downloads */}
        {project.status === "completed" && (
          <div className="bg-gray-900 rounded-lg p-6 mb-8">
            <h3 className="font-medium mb-4">다운로드</h3>
            <div className="flex gap-3">
              <button
                onClick={() => handleDownload("fcpxml")}
                className="px-4 py-2 bg-white text-black font-medium rounded-lg hover:bg-gray-200 transition"
              >
                FCPXML 다운로드
              </button>
              <button
                onClick={() => handleDownload("srt")}
                className="px-4 py-2 bg-gray-800 rounded-lg hover:bg-gray-700 transition border border-gray-700"
              >
                SRT 다운로드
              </button>
              <button
                onClick={() => router.push(`/projects/${projectId}/review`)}
                className="px-4 py-2 bg-blue-600 rounded-lg hover:bg-blue-500 transition font-medium"
              >
                구간 리뷰
              </button>
            </div>
          </div>
        )}

        {/* Edit Report */}
        {project.report && (
          <div className="bg-gray-900 rounded-lg p-6">
            <h3 className="font-medium mb-4">편집 보고서</h3>
            <div className="grid grid-cols-3 gap-4 mb-6">
              <div className="bg-gray-800 rounded-lg p-4 text-center">
                <p className="text-2xl font-bold">
                  {formatDuration(project.report.total_duration_seconds)}
                </p>
                <p className="text-sm text-gray-400">전체 길이</p>
              </div>
              <div className="bg-gray-800 rounded-lg p-4 text-center">
                <p className="text-2xl font-bold">
                  {formatDuration(project.report.cut_duration_seconds)}
                </p>
                <p className="text-sm text-gray-400">컷 구간</p>
              </div>
              <div className="bg-gray-800 rounded-lg p-4 text-center">
                <p className="text-2xl font-bold">
                  {project.report.cut_percentage.toFixed(1)}%
                </p>
                <p className="text-sm text-gray-400">절약률</p>
              </div>
            </div>

            {/* Markdown report */}
            <div className="prose prose-invert prose-sm max-w-none">
              <pre className="whitespace-pre-wrap text-sm text-gray-300 bg-gray-800 rounded-lg p-4 overflow-auto">
                {project.report.report_markdown}
              </pre>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
