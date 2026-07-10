"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import {
  api,
  uploadFile,
  type CutType,
  type SegmentationBoundaryRule,
  type YouTubeInfoResponse,
} from "@/lib/api";
import { sha256File } from "@/lib/hash";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState, type ReactNode } from "react";

type SourceMode = "file" | "youtube";
type EditIntensity = "light" | "normal" | "heavy";
type EditDecisionVersion = "legacy" | "boundary_aware_v1";

const CUT_TYPE_OPTIONS: {
  value: CutType;
  label: string;
  description: string;
}[] = [
  {
    value: "subtitle_cut",
    label: "강의/설명",
    description: "중복, 더듬, 미완성 문장 감지",
  },
  {
    value: "podcast_cut",
    label: "팟캐스트",
    description: "지루한 구간, 반복, 탈선 감지",
  },
  {
    value: "ai_frontier_cut",
    label: "AI Frontier",
    description: "프리롤·포스트롤과 방송 흐름 정리",
  },
];

const CUT_TYPE_ICONS: Record<CutType, ReactNode> = {
  subtitle_cut: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gray-400">
      <rect x="2" y="2" width="20" height="20" rx="2" /><path d="M7 2v20" /><path d="M17 2v20" /><path d="M2 12h20" />
    </svg>
  ),
  podcast_cut: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gray-400">
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    </svg>
  ),
  ai_frontier_cut: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gray-400">
      <path d="m12 2 1.4 5.1L18 9l-4.6 1.9L12 16l-1.4-5.1L6 9l4.6-1.9L12 2Z" /><path d="m19 15 .7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7L19 15Z" />
    </svg>
  ),
};

const EDIT_INTENSITY_OPTIONS: {
  value: EditIntensity;
  label: string;
  description: string;
}[] = [
  { value: "light", label: "적게 편집", description: "꼭 필요한 컷만" },
  { value: "normal", label: "일반 편집", description: "균형 있게 정리" },
  { value: "heavy", label: "많이 편집", description: "적극적으로 압축" },
];

const EDIT_DECISION_VERSION_OPTIONS: {
  value: EditDecisionVersion;
  label: string;
  description: string;
}[] = [
  { value: "legacy", label: "기존 방식", description: "현재 안정화된 cut/keep 판단" },
  { value: "boundary_aware_v1", label: "Boundary-aware v1", description: "80ms 이하 인접 경계를 LLM이 함께 판단" },
];

const SEGMENTATION_BOUNDARY_RULE_OPTIONS: {
  value: SegmentationBoundaryRule;
  label: string;
  description: string;
}[] = [
  { value: "word_boundary", label: "Word boundary", description: "Scribe word timestamp 유지" },
  { value: "midpoint_gap", label: "Midpoint gap", description: "짧은 gap은 midpoint, 긴 gap은 padding 제한" },
  { value: "low_energy_gap_v1", label: "Low-energy v1", description: "짧은 gap에서 가장 조용한 지점 선택" },
];

export default function NewProjectPage() {
  const router = useRouter();
  const supabase = createClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Common state
  const [sourceMode, setSourceMode] = useState<SourceMode>("file");
  const [name, setName] = useState("");
  const [cutType, setCutType] = useState<CutType>("subtitle_cut");
  const [editIntensity, setEditIntensity] = useState<EditIntensity>("normal");
  const [editDecisionVersion, setEditDecisionVersion] = useState<EditDecisionVersion>("legacy");
  const [segmentationBoundaryRule, setSegmentationBoundaryRule] =
    useState<SegmentationBoundaryRule>("word_boundary");
  const [language, setLanguage] = useState("ko");
  const [context, setContext] = useState("");
  const [diarize, setDiarize] = useState(true);
  const [tagAudioEvents, setTagAudioEvents] = useState(true);
  const [numSpeakers, setNumSpeakers] = useState("");
  const [useLlmSegmentation, setUseLlmSegmentation] = useState(true);
  const [useLlmRefinement, setUseLlmRefinement] = useState(true);
  const [overlapProtectionEnabled, setOverlapProtectionEnabled] = useState(false);
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

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (!selected) return;
    setFile(selected);
    setError("");
    if (!name) {
      setName(selected.name.replace(/\.[^.]+$/, ""));
    }
    try {
      await getVideoDuration(selected);
    } catch (err) {
      setError(err instanceof Error ? err.message : "영상 메타데이터를 읽을 수 없습니다");
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

  const buildProjectSettings = useCallback(() => {
    const projectSettings: Record<string, unknown> = {
      edit_intensity: editIntensity,
      edit_decision_version: editDecisionVersion,
      segmentation_boundary_rule: segmentationBoundaryRule,
      diarize,
      tag_audio_events: tagAudioEvents,
      use_llm_segmentation: useLlmSegmentation,
      use_llm_refinement: useLlmRefinement,
      overlap_protection_enabled: overlapProtectionEnabled,
    };
    const speakerCount = Number(numSpeakers);
    if (numSpeakers.trim() && Number.isInteger(speakerCount)) {
      projectSettings.num_speakers = speakerCount;
    }
    if (context.trim()) {
      projectSettings.transcription_context = context.trim();
    }
    return projectSettings;
  }, [
    context,
    diarize,
    editIntensity,
    editDecisionVersion,
    segmentationBoundaryRule,
    numSpeakers,
    overlapProtectionEnabled,
    tagAudioEvents,
    useLlmSegmentation,
    useLlmRefinement,
  ]);

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
          const project = await api.createProject(token, {
            name,
            cut_type: cutType,
            language,
            source_r2_key: task.r2_key,
            source_filename: task.filename || "youtube.mp4",
            source_duration_seconds: task.duration_seconds,
            source_size_bytes: task.filesize_bytes,
            settings: buildProjectSettings(),
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
      }
    };

    poll();
  }, [buildProjectSettings, cutType, language, name, router]);

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
    setProgressLabel("파일 지문 계산 중...");

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error("로그인이 필요합니다");
      const token = session.access_token;

      const duration = await getVideoDuration(file);
      const sourceSha256 = await sha256File(file, (loaded, total) => {
        setUploadProgress(Math.round((loaded / total) * 10));
      });

      setProgressLabel("기존 원본 확인 중...");
      const cachedSource = await api.lookupSource(token, {
        sha256: sourceSha256,
        size_bytes: file.size,
      });

      let r2Key = cachedSource.r2_key || "";
      if (cachedSource.hit && r2Key) {
        setUploadProgress(90);
        setProgressLabel("기존 원본 파일 재사용 중...");
      } else {
        setUploadProgress(10);
        setProgressLabel("업로드 중...");
        r2Key = await uploadFile(token, file, (loaded, total) => {
          setUploadProgress(10 + Math.round((loaded / total) * 80));
        });
      }

      setUploadProgress(95);
      setProgressLabel("프로젝트 생성 중...");

      const project = await api.createProject(token, {
        name,
        cut_type: cutType,
        language,
        source_r2_key: r2Key,
        source_filename: file.name,
        source_duration_seconds: duration,
        source_size_bytes: file.size,
        source_sha256: sourceSha256,
        settings: buildProjectSettings(),
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
  const speakerCount = Number(numSpeakers);
  const numSpeakersInvalid = numSpeakers.trim() !== ""
    && (!Number.isInteger(speakerCount) || speakerCount < 1 || speakerCount > 32);
  const canSubmit = sourceMode === "youtube"
    ? !!youtubeUrl.trim() && !!name && !uploading && !numSpeakersInvalid
    : !!file && !!name && !uploading && !numSpeakersInvalid;

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
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {CUT_TYPE_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setCutType(option.value)}
                  className={`p-4 rounded-lg border text-left transition ${
                    cutType === option.value
                      ? "border-white bg-gray-800"
                      : "border-gray-700 hover:border-gray-500"
                  }`}
                >
                  <p className="flex items-center gap-2 font-medium">
                    {CUT_TYPE_ICONS[option.value]}
                    {option.label}
                  </p>
                  <p className="text-sm text-gray-400 mt-1">{option.description}</p>
                </button>
              ))}
            </div>
          </div>

          {/* Edit intensity */}
          <div>
            <label className="block text-sm font-medium mb-2">편집 강도</label>
            <div className="grid grid-cols-3 gap-2">
              {EDIT_INTENSITY_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setEditIntensity(option.value)}
                  className={`p-3 rounded-lg border text-left transition ${
                    editIntensity === option.value
                      ? "border-white bg-gray-800"
                      : "border-gray-700 hover:border-gray-500"
                  }`}
                >
                  <p className="font-medium">{option.label}</p>
                  <p className="text-xs text-gray-400 mt-1">{option.description}</p>
                </button>
              ))}
            </div>
          </div>

          {/* Edit decision version */}
          <div>
            <label className="block text-sm font-medium mb-2">Edit Decision</label>
            <select
              value={editDecisionVersion}
              onChange={(e) => setEditDecisionVersion(e.target.value as EditDecisionVersion)}
              className="w-full bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 focus:outline-none focus:border-gray-500"
            >
              {EDIT_DECISION_VERSION_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label} - {option.description}
                </option>
              ))}
            </select>
          </div>

          {/* Segmentation boundary rule */}
          <div>
            <label className="block text-sm font-medium mb-2">Segmentation Boundary</label>
            <select
              value={segmentationBoundaryRule}
              onChange={(e) => setSegmentationBoundaryRule(e.target.value as SegmentationBoundaryRule)}
              className="w-full bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 focus:outline-none focus:border-gray-500"
            >
              {SEGMENTATION_BOUNDARY_RULE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label} - {option.description}
                </option>
              ))}
            </select>
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
              <option value="auto">자동 감지</option>
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

          {/* Scribe options */}
          <div>
            <label className="block text-sm font-medium mb-3">자막 생성 옵션</label>
            <div className="space-y-3 rounded-lg border border-gray-800 bg-gray-900/70 p-4">
              <label className="flex items-center justify-between gap-4 text-sm">
                <span>
                  <span className="block font-medium">화자 분리</span>
                  <span className="block text-xs text-gray-500">Scribe V2 diarization 사용</span>
                </span>
                <input
                  type="checkbox"
                  checked={diarize}
                  onChange={(e) => setDiarize(e.target.checked)}
                  className="h-4 w-4 accent-white"
                />
              </label>

              <div>
                <label className="block text-sm font-medium mb-2">
                  예상 화자 수 <span className="text-gray-500 font-normal">(선택)</span>
                </label>
                <input
                  type="number"
                  min={1}
                  max={32}
                  value={numSpeakers}
                  onChange={(e) => setNumSpeakers(e.target.value)}
                  placeholder="자동"
                  disabled={!diarize}
                  className="w-full bg-gray-950 border border-gray-700 rounded-lg px-4 py-3 focus:outline-none focus:border-gray-500 disabled:opacity-50"
                />
                {numSpeakersInvalid && (
                  <p className="text-sm text-red-300 mt-2">예상 화자 수는 1에서 32 사이여야 합니다.</p>
                )}
              </div>

              <label className="flex items-center justify-between gap-4 text-sm">
                <span>
                  <span className="block font-medium">오디오 이벤트 태깅</span>
                  <span className="block text-xs text-gray-500">웃음, 음악 같은 비언어 이벤트 포함</span>
                </span>
                <input
                  type="checkbox"
                  checked={tagAudioEvents}
                  onChange={(e) => setTagAudioEvents(e.target.checked)}
                  className="h-4 w-4 accent-white"
                />
              </label>

              <label className="flex items-center justify-between gap-4 text-sm">
                <span>
                  <span className="block font-medium">LLM 자막 구간 나누기</span>
                  <span className="block text-xs text-gray-500">
                    Scribe word timestamp 기준으로 의미 단위 자막 구간 결정
                  </span>
                </span>
                <input
                  type="checkbox"
                  checked={useLlmSegmentation}
                  onChange={(e) => setUseLlmSegmentation(e.target.checked)}
                  className="h-4 w-4 accent-white"
                />
              </label>

              <label className="flex items-center justify-between gap-4 text-sm">
                <span>
                  <span className="block font-medium">LLM 자막 교정</span>
                  <span className="block text-xs text-gray-500">Scribe V2 결과 이후 텍스트만 다듬기</span>
                </span>
                <input
                  type="checkbox"
                  checked={useLlmRefinement}
                  onChange={(e) => setUseLlmRefinement(e.target.checked)}
                  className="h-4 w-4 accent-white"
                />
              </label>

              <label className="flex items-center justify-between gap-4 text-sm">
                <span>
                  <span className="block font-medium">겹치는 구간 보호</span>
                  <span className="block text-xs text-gray-500">동시 발화 감지 구간은 최종 segment에서 다시 합치기</span>
                </span>
                <input
                  type="checkbox"
                  checked={overlapProtectionEnabled}
                  onChange={(e) => setOverlapProtectionEnabled(e.target.checked)}
                  className="h-4 w-4 accent-white"
                />
              </label>
            </div>
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
