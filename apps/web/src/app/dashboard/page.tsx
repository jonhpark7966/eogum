"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import { api, type CreditBalance, type Project } from "@/lib/api";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}시간 ${m}분`;
  return `${m}분`;
}

const STATUS_LABELS: Record<string, string> = {
  created: "생성됨",
  uploading: "업로드 중",
  queued: "대기 중",
  processing: "처리 중",
  completed: "완료",
  failed: "실패",
};

const STATUS_COLORS: Record<string, string> = {
  created: "text-gray-400",
  uploading: "text-blue-400",
  queued: "text-yellow-400",
  processing: "text-blue-400",
  completed: "text-green-400",
  failed: "text-red-400",
};

export default function DashboardPage() {
  const router = useRouter();
  const supabase = createClient();
  const [projects, setProjects] = useState<Project[]>([]);
  const [credits, setCredits] = useState<CreditBalance | null>(null);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async () => {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session) {
      router.replace("/");
      return;
    }

    const token = session.access_token;
    const [projectList, creditBalance] = await Promise.all([
      api.listProjects(token),
      api.getCredits(token),
    ]);
    setProjects(projectList);
    setCredits(creditBalance);
    setLoading(false);
  }, []);

  useEffect(() => {
    loadData();
    // Poll for status updates
    const interval = setInterval(loadData, 10000);
    return () => clearInterval(interval);
  }, [loadData]);

  const handleLogout = async () => {
    await supabase.auth.signOut();
    router.replace("/");
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-950">
        <div className="animate-pulse text-gray-400">Loading...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      {/* Header */}
      <header className="border-b border-gray-800">
        <div className="max-w-6xl mx-auto px-6 py-4 flex justify-between items-center">
          <h1 className="text-xl font-bold">어검</h1>
          <div className="flex items-center gap-6">
            {credits && (
              <span className="text-sm text-gray-400">
                크레딧:{" "}
                <span className="text-white font-medium">
                  {formatDuration(credits.available_seconds)}
                </span>
              </span>
            )}
            <button
              onClick={handleLogout}
              className="text-sm text-gray-400 hover:text-white transition"
            >
              로그아웃
            </button>
          </div>
        </div>
      </header>

      {/* Content */}
      <main className="max-w-6xl mx-auto px-6 py-8">
        <div className="flex justify-between items-center mb-8">
          <h2 className="text-2xl font-semibold">프로젝트</h2>
          <button
            onClick={() => router.push("/dashboard/new")}
            className="px-6 py-2 bg-white text-black font-medium rounded-lg hover:bg-gray-200 transition"
          >
            새 프로젝트
          </button>
        </div>

        {projects.length === 0 ? (
          <div className="text-center py-16 text-gray-500">
            <p className="text-lg mb-2">아직 프로젝트가 없습니다</p>
            <p className="text-sm">영상을 업로드해서 시작해보세요</p>
          </div>
        ) : (
          <div className="space-y-3">
            {projects.map((project) => (
              <button
                key={project.id}
                onClick={() => router.push(`/projects/${project.id}`)}
                className="w-full bg-gray-900 rounded-lg p-4 flex justify-between items-center hover:bg-gray-800 transition text-left"
              >
                <div>
                  <h3 className="font-medium">{project.name}</h3>
                  <div className="flex gap-4 mt-1 text-sm text-gray-400">
                    <span>
                      {project.cut_type === "subtitle_cut"
                        ? "강의/설명"
                        : "팟캐스트"}
                    </span>
                    {project.source_duration_seconds && (
                      <span>
                        {formatDuration(project.source_duration_seconds)}
                      </span>
                    )}
                    <span>
                      {new Date(project.created_at).toLocaleDateString("ko-KR")}
                    </span>
                  </div>
                </div>
                <span
                  className={`text-sm font-medium ${STATUS_COLORS[project.status] ?? "text-gray-400"}`}
                >
                  {STATUS_LABELS[project.status] ?? project.status}
                </span>
              </button>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
