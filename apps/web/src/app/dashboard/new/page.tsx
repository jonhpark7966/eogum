"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import { api, uploadFile, type UploadProgressDetail, YouTubeInfoResponse } from "@/lib/api";
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
  const [contextLoading, setContextLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [progressLabel, setProgressLabel] = useState("");
  const [uploadLogs, setUploadLogs] = useState<string[]>([]);
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
    setUploadProgress(0);
    setProgressLabel("");
    setUploadLogs([]);
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

  const appendUploadLog = useCallback((message: string) => {
    const timestamp = new Date().toLocaleTimeString("ko-KR", { hour12: false });
    setUploadLogs((prev) => [...prev, `${timestamp} ${message}`].slice(-80));
  }, []);

  const handleUploadProgressDetail = useCallback((loaded: number, total: number, detail?: UploadProgressDetail) => {
    setUploadProgress(5 + Math.round((loaded / total) * 85));
    if (!detail) return;

    if (detail.phase === "initiated") {
      appendUploadLog(`[3/5] 업로드 세션 생성 완료 - ${detail.totalParts}개 part, part 크기 ${formatBytes(detail.partSize)}`);
      return;
    }

    if (detail.phase === "part_attempt") {
      appendUploadLog(
        `[4/5][${detail.partNumber}/${detail.totalParts}] part 업로드 시작` +
          (detail.attempt > 1 ? ` - 재시도 ${detail.attempt}/${detail.maxAttempts}` : "")
      );
      return;
    }

    if (detail.phase === "part_retry") {
      appendUploadLog(
        `[4/5][${detail.partNumber}/${detail.totalParts}] 실패: ${detail.error}. ${detail.delayMs / 1000}초 후 재시도`
      );
      return;
    }

    if (detail.phase === "part_complete") {
      appendUploadLog(
        `[4/5][${detail.completedParts}/${detail.totalParts}] part ${detail.partNumber} 완료 - ${formatBytes(loaded)} / ${formatBytes(total)}`
      );
      return;
    }

    if (detail.phase === "completing") {
      appendUploadLog(`[4/5] ${detail.totalParts}개 part 업로드 완료, R2 multipart 완료 요청 중`);
      return;
    }

    if (detail.phase === "completed") {
      appendUploadLog("[4/5] R2 업로드 완료");
    }
  }, [appendUploadLog]);

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

  const handleLoadContext = async () => {
    setError("");
    setContextLoading(true);

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error("로그인이 필요합니다");

      const result = await api.getTranscriptionContext(session.access_token);
      const loadedContext = result.context.trim();
      if (!loadedContext) throw new Error("불러올 컨텍스트가 없습니다");

      setContext((prev) => {
        const current = prev.trim();
        if (!current) return loadedContext;
        if (current.includes(loadedContext)) return current;
        return `${current}\n\n${loadedContext}`;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "컨텍스트를 불러올 수 없습니다");
    } finally {
      setContextLoading(false);
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
    setUploadProgress(0);
    setProgressLabel("업로드 중...");
    setUploadLogs([]);
    appendUploadLog(`[1/5] 세션 확인 중 - ${file.name} (${formatBytes(file.size)})`);

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error("로그인이 필요합니다");
      const token = session.access_token;
      appendUploadLog("[1/5] 세션 확인 완료");

      appendUploadLog("[2/5] 영상 메타데이터 분석 중");
      const duration = await getVideoDuration(file);
      appendUploadLog(`[2/5] 영상 메타데이터 분석 완료 - 길이 ${formatDuration(duration)}`);

      setUploadProgress(5);
      appendUploadLog("[3/5] R2 multipart 업로드 세션 생성 중");
      const r2Key = await uploadFile(token, file, handleUploadProgressDetail);

      setUploadProgress(95);
      setProgressLabel("프로젝트 생성 중...");
      appendUploadLog("[5/5] 프로젝트 생성 요청 중");

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
      appendUploadLog("[5/5] 프로젝트 생성 완료, 프로젝트 화면으로 이동합니다");
      router.push(`/projects/${project.id}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "오류가 발생했습니다";
      appendUploadLog(`[오류] ${message}`);
      setError(message);
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
            <div className="mb-2 flex items-center justify-between gap-3">
              <label className="text-sm font-medium">
                컨텍스트 <span className="text-gray-500 font-normal">(선택)</span>
              </label>
              <button
                type="button"
                onClick={handleLoadContext}
                disabled={contextLoading || uploading}
                className="rounded-md border border-gray-700 bg-gray-900 px-2.5 py-1 text-xs font-medium text-gray-300 transition hover:border-gray-500 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                {contextLoading ? "불러오는 중..." : "불러오기"}
              </button>
            </div>
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
            <div className="space-y-3">
              <div>
                <div className="flex items-center justify-between text-xs text-gray-400 mb-2">
                  <span>{progressLabel || "진행 중..."}</span>
                  <span>{uploadProgress}%</span>
                </div>
                <div className="w-full bg-gray-800 rounded-full h-2">
                  <div
                    className="bg-white h-2 rounded-full transition-all duration-300"
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
              </div>

              {sourceMode === "file" && uploadLogs.length > 0 && (
                <div className="rounded-lg border border-gray-800 bg-black/40 p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <p className="text-xs font-medium text-gray-300">업로드 로그</p>
                    <p className="text-xs text-gray-500">{uploadLogs.length} lines</p>
                  </div>
                  <div className="max-h-48 space-y-1 overflow-y-auto font-mono text-xs leading-relaxed text-gray-400">
                    {uploadLogs.map((line, index) => (
                      <p key={`${line}-${index}`} className="whitespace-pre-wrap break-words">
                        {line}
                      </p>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </form>
      </main>
    </div>
  );
}
