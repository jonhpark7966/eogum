"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import { api, uploadFile } from "@/lib/api";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

export default function NewProjectPage() {
  const router = useRouter();
  const supabase = createClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [name, setName] = useState("");
  const [cutType, setCutType] = useState("subtitle_cut");
  const [language, setLanguage] = useState("ko");
  const [file, setFile] = useState<File | null>(null);
  const [context, setContext] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState("");

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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;

    setError("");
    setUploading(true);

    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) throw new Error("로그인이 필요합니다");
      const token = session.access_token;

      // Get video duration
      const duration = await getVideoDuration(file);

      // Upload to R2 via multipart
      setUploadProgress(5);
      const r2Key = await uploadFile(token, file, (loaded, total) => {
        setUploadProgress(5 + Math.round((loaded / total) * 85));
      });

      setUploadProgress(95);

      // Create project
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
    }
  };

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      <header className="border-b border-gray-800">
        <div className="max-w-6xl mx-auto px-6 py-4">
          <button
            onClick={() => router.back()}
            className="text-gray-400 hover:text-white transition"
          >
            ← 돌아가기
          </button>
        </div>
      </header>

      <main className="max-w-xl mx-auto px-6 py-12">
        <h2 className="text-2xl font-semibold mb-8">새 프로젝트</h2>

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* File upload */}
          <div>
            <label className="block text-sm font-medium mb-2">영상 파일</label>
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
            disabled={!file || !name || uploading}
            className="w-full py-3 bg-white text-black font-medium rounded-lg hover:bg-gray-200 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {uploading
              ? `업로드 중... ${uploadProgress}%`
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
