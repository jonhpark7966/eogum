"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import { api, uploadFile, YouTubeInfoResponse } from "@/lib/api";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

type SourceMode = "file" | "youtube";

export default function NewProjectPage() {
  const router = useRouter();
  const supabase = createClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Common state
  const [sourceMode, setSourceMode] = useState<SourceMode>("file");
  const [name, setName] = useState("");
  const [cutType, setCutType] = useState("subtitle_cut");
  const [language, setLanguage] = useState("ko");
  const [context, setContext] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [progressLabel, setProgressLabel] = useState("");
  const [error, setError] = useState("");

  // File upload state
  const [file, setFile] = useState<File | null>(null);

  // YouTube state
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [ytInfo, setYtInfo] = useState<YouTubeInfoResponse | null>(null);
  const [ytLoading, setYtLoading] = useState(false);
  const [ytTaskId, setYtTaskId] = useState<string | null>(null);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (!selected) return;
    setFile(selected);
    if (!name) {
      setName(selected.name.replace(/\.[^.]+$/, ""));
    }
  };

  const getVideoDuration = (file: File): Promise<number> => {
    return new Promise((resolve, reject) => {
      const video = document.createElement("video");
      video.preload = "metadata";
      video.onloadedmetadata = () => {
        URL.revokeObjectURL(video.src);
        resolve(Math.ceil(video.duration));
      };
      video.onerror = () => reject(new Error("영상 메타데이터를 읽을 수 없습니다"));
      video.src = URL.createObjectURL(file);
    });
  };

  const formatDuration = (seconds: number) => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    return `${m}:${String(s).padStart(2, "0")}`;
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return "";
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
  };

  // Fetch YouTube info when URL changes
  const fetchYoutubeInfo = async () => {
    if (!youtubeUrl.trim()) return;
    setYtLoading(true);
    setYtInfo(null);
    setError("");

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error("로그인이 필요합니다");

      const info = await api.getYouTubeInfo(session.access_token, youtubeUrl.trim());
      setYtInfo(info);
      if (!name) setName(info.title);
    } catch (err) {
      setError(err instanceof Error ? err.message : "YouTube 정보를 가져올 수 없습니다");
    } finally {
      setYtLoading(false);
    }
  };

  // Poll YouTube download progress
  const pollDownload = useCallback(async (taskId: string, token: string) => {
    const poll = async () => {
      try {
        const task = await api.getYouTubeDownloadStatus(token, taskId);

        if (task.status === "downloading") {
          setProgressLabel("다운로드 중...");
          setUploadProgress(Math.round(task.progress));
        } else if (task.status === "uploading") {
          setProgressLabel("서버에 업로드 중...");
          setUploadProgress(Math.round(task.progress));
        } else if (task.status === "completed" && task.r2_key) {
          setProgressLabel("프로젝트 생성 중...");
          setUploadProgress(95);

          // Create project with downloaded file
          const projectSettings: Record<string, unknown> = {};
          if (context.trim()) {
            projectSettings.transcription_context = context.trim();
          }
          const project = await api.createProject(token, {
            name,
            cut_type: cutType,
            language,
            source_r2_key: task.r2_key,
            source_filename: task.filename || "youtube.mp4",
            source_duration_seconds: task.duration_seconds,
            source_size_bytes: task.filesize_bytes,
            settings: projectSettings,
          });

          setUploadProgress(100);
          router.push(`/projects/${project.id}`);
          return; // Stop polling
        } else if (task.status === "failed") {
          throw new Error(task.error || "다운로드 실패");
        }

        // Continue polling
        setTimeout(poll, 2000);
      } catch (err) {
        setError(err instanceof Error ? err.message : "오류가 발생했습니다");
        setUploading(false);
        setUploadProgress(0);
        setProgressLabel("");
        setYtTaskId(null);
      }
    };

    poll();
  }, [name, cutType, language, context, router]);

  // Start YouTube download flow
  const handleYoutubeSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!youtubeUrl.trim() || !name) return;

    setError("");
    setUploading(true);
    setUploadProgress(0);
    setProgressLabel("다운로드 요청 중...");

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error("로그인이 필요합니다");
      const token = session.access_token;

      const resp = await api.startYouTubeDownload(token, youtubeUrl.trim());
      setYtTaskId(resp.task_id);
      pollDownload(resp.task_id, token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "오류가 발생했습니다");
      setUploading(false);
      setUploadProgress(0);
      setProgressLabel("");
    }
  };

  // File upload flow (existing)
  const handleFileSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;

    setError("");
    setUploading(true);
    setProgressLabel("업로드 중...");

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error("로그인이 필요합니다");
      const token = session.access_token;

      const duration = await getVideoDuration(file);

      setUploadProgress(5);
      const r2Key = await uploadFile(token, file, (loaded, total) => {
        setUploadProgress(5 + Math.round((loaded / total) * 85));
      });

      setUploadProgress(95);
      setProgressLabel("프로젝트 생성 중...");

      const projectSettings: Record<string, unknown> = {};
      if (context.trim()) {
        projectSettings.transcription_context = context.trim();
      }
      const project = await api.createProject(token, {
        name,
        cut_type: cutType,
        language,
        source_r2_key: r2Key,
        source_filename: file.name,
        source_duration_seconds: duration,
        source_size_bytes: file.size,
        settings: projectSettings,
      });

      setUploadProgress(100);
      router.push(`/projects/${project.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "오류가 발생했습니다");
      setUploading(false);
      setUploadProgress(0);
      setProgressLabel("");
    }
  };

  const handleSubmit = sourceMode === "youtube" ? handleYoutubeSubmit : handleFileSubmit;
  const canSubmit = sourceMode === "youtube"
    ? !!youtubeUrl.trim() && !!name && !uploading
    : !!file && !!name && !uploading;

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      <header className="border-b border-gray-800">
        <div className="max-w-6xl mx-auto px-6 py-4">
          <button
            onClick={() => router.back()}
            className="text-gray-400 hover:text-white transition"
          >
            &larr; 돌아가기
          </button>
        </div>
      </header>

      <main className="max-w-xl mx-auto px-6 py-12">
        <h2 className="text-2xl font-semibold mb-8">새 프로젝트</h2>

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Source mode tabs */}
          <div>
            <label className="block text-sm font-medium mb-2">소스</label>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => { setSourceMode("file"); setError(""); }}
                className={`py-2.5 px-4 rounded-lg text-sm font-medium transition ${
                  sourceMode === "file"
                    ? "bg-white text-black"
                    : "bg-gray-800 text-gray-400 hover:text-white"
                }`}
              >
                파일 업로드
              </button>
              <button
                type="button"
                onClick={() => { setSourceMode("youtube"); setError(""); }}
                className={`py-2.5 px-4 rounded-lg text-sm font-medium transition ${
                  sourceMode === "youtube"
                    ? "bg-white text-black"
                    : "bg-gray-800 text-gray-400 hover:text-white"
                }`}
              >
                YouTube URL
              </button>
            </div>
          </div>

          {/* File upload */}
          {sourceMode === "file" && (
            <div>
              <input
                ref={fileInputRef}
                type="file"
                accept="video/*,audio/*"
                onChange={handleFileSelect}
                className="hidden"
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="w-full border-2 border-dashed border-gray-700 rounded-lg p-8 text-center hover:border-gray-500 transition"
              >
                {file ? (
                  <div>
                    <p className="font-medium">{file.name}</p>
                    <p className="text-sm text-gray-400 mt-1">
                      {(file.size / 1024 / 1024).toFixed(1)} MB
                    </p>
                  </div>
                ) : (
                  <div className="text-gray-400">
                    <p>클릭하여 파일 선택</p>
                    <p className="text-sm mt-1">MP4, MOV, WAV, MP3 등</p>
                  </div>
                )}
              </button>
            </div>
          )}

          {/* YouTube URL */}
          {sourceMode === "youtube" && (
            <div className="space-y-3">
              <div className="flex gap-2">
                <input
                  type="text"
                  value={youtubeUrl}
                  onChange={(e) => setYoutubeUrl(e.target.value)}
                  placeholder="https://www.youtube.com/watch?v=..."
                  className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 focus:outline-none focus:border-gray-500"
                />
                <button
                  type="button"
                  onClick={fetchYoutubeInfo}
                  disabled={!youtubeUrl.trim() || ytLoading}
                  className="px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg hover:bg-gray-700 transition disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium whitespace-nowrap"
                >
                  {ytLoading ? "확인 중..." : "정보 확인"}
                </button>
              </div>

              {ytInfo && (
                <div className="bg-gray-900 border border-gray-700 rounded-lg p-4 flex gap-4">
                  {ytInfo.thumbnail && (
                    <img
                      src={ytInfo.thumbnail}
                      alt=""
                      className="w-32 h-20 object-cover rounded flex-shrink-0"
                    />
                  )}
                  <div className="min-w-0">
                    <p className="font-medium text-sm leading-snug line-clamp-2">{ytInfo.title}</p>
                    <p className="text-xs text-gray-400 mt-1">{ytInfo.uploader}</p>
                    <div className="flex gap-3 mt-1 text-xs text-gray-500">
                      <span>{formatDuration(ytInfo.duration_seconds)}</span>
                      {ytInfo.filesize_approx_bytes > 0 && (
                        <span>~{formatBytes(ytInfo.filesize_approx_bytes)}</span>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Project name */}
          <div>
            <label className="block text-sm font-medium mb-2">
              프로젝트 이름
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="프로젝트 이름을 입력하세요"
              required
              className="w-full bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 focus:outline-none focus:border-gray-500"
            />
          </div>

          {/* Cut type */}
          <div>
            <label className="block text-sm font-medium mb-2">편집 타입</label>
            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setCutType("subtitle_cut")}
                className={`p-4 rounded-lg border text-left transition ${
                  cutType === "subtitle_cut"
                    ? "border-white bg-gray-800"
                    : "border-gray-700 hover:border-gray-500"
                }`}
              >
                <p className="font-medium">강의/설명</p>
                <p className="text-sm text-gray-400 mt-1">
                  중복, 더듬, 미완성 문장 감지
                </p>
              </button>
              <button
                type="button"
                onClick={() => setCutType("podcast_cut")}
                className={`p-4 rounded-lg border text-left transition ${
                  cutType === "podcast_cut"
                    ? "border-white bg-gray-800"
                    : "border-gray-700 hover:border-gray-500"
                }`}
              >
                <p className="font-medium">팟캐스트</p>
                <p className="text-sm text-gray-400 mt-1">
                  지루한 구간, 반복, 탈선 감지
                </p>
              </button>
            </div>
          </div>

          {/* Language */}
          <div>
            <label className="block text-sm font-medium mb-2">언어</label>
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              className="w-full bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 focus:outline-none focus:border-gray-500"
            >
              <option value="ko">한국어</option>
              <option value="en">English</option>
              <option value="ja">日本語</option>
            </select>
          </div>

          {/* Context */}
          <div>
            <label className="block text-sm font-medium mb-2">
              컨텍스트 <span className="text-gray-500 font-normal">(선택)</span>
            </label>
            <textarea
              value={context}
              onChange={(e) => setContext(e.target.value)}
              placeholder="전사 정확도를 높이기 위한 배경 정보를 입력하세요. 예: 출연자 이름, 전문 용어, 주제 등"
              rows={3}
              className="w-full bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 focus:outline-none focus:border-gray-500 resize-y"
            />
          </div>

          {error && (
            <div className="bg-red-900/50 border border-red-700 rounded-lg p-4 text-red-200 text-sm">
              {error}
            </div>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={!canSubmit}
            className="w-full py-3 bg-white text-black font-medium rounded-lg hover:bg-gray-200 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {uploading
              ? `${progressLabel} ${uploadProgress}%`
              : "프로젝트 생성"}
          </button>

          {uploading && (
            <div className="w-full bg-gray-800 rounded-full h-2">
              <div
                className="bg-white h-2 rounded-full transition-all duration-300"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
          )}
        </form>
      </main>
    </div>
  );
}
