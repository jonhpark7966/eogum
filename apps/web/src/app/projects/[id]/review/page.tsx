"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import {
  api,
  type EvalSegment,
  type SegmentWithDecision,
} from "@/lib/api";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

// ── Constants ──

const CUT_REASONS = [
  { value: "duplicate", label: "중복" },
  { value: "incomplete", label: "불완전" },
  { value: "filler", label: "필러" },
  { value: "fumble", label: "말실수" },
  { value: "retake_signal", label: "재촬영 신호" },
  { value: "meta_comment", label: "메타 발언" },
  { value: "tangent", label: "탈선" },
];

const KEEP_REASONS = [
  { value: "best_take", label: "최적 테이크" },
  { value: "unique", label: "유일한 내용" },
  { value: "essential", label: "필수 내용" },
];

// ── Helpers ──

function formatTime(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// ── Component ──

export default function ReviewPage() {
  const params = useParams();
  const router = useRouter();
  const supabase = createClient();
  const projectId = params.id as string;

  const videoRef = useRef<HTMLVideoElement>(null);
  const segmentRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const playEndRef = useRef<number | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [durationMs, setDurationMs] = useState(0);
  const [segments, setSegments] = useState<EvalSegment[]>([]);
  const [currentIndex, setCurrentIndex] = useState(-1);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  // Load all data
  const loadData = useCallback(async () => {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session) {
      router.replace("/");
      return;
    }
    const token = session.access_token;

    try {
      const [segRes, vidRes, evalRes] = await Promise.all([
        api.getSegments(token, projectId),
        api.getVideoUrl(token, projectId),
        api.getEvaluation(token, projectId),
      ]);

      setVideoUrl(vidRes.video_url);
      setDurationMs(vidRes.duration_ms || segRes.source_duration_ms);

      // Merge: start from segments, overlay saved evaluation if exists
      const evalMap = new Map<number, EvalSegment>();
      if (evalRes?.segments) {
        for (const es of evalRes.segments) {
          evalMap.set(es.index, es);
        }
      }

      const merged: EvalSegment[] = segRes.segments.map((seg: SegmentWithDecision) => {
        const saved = evalMap.get(seg.index);
        return {
          index: seg.index,
          start_ms: seg.start_ms,
          end_ms: seg.end_ms,
          text: seg.text,
          ai: seg.ai,
          human: saved?.human ?? null,
        };
      });

      setSegments(merged);
    } catch (err) {
      setError(err instanceof Error ? err.message : "데이터 로딩 실패");
    }
    setLoading(false);
  }, [projectId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Video timeupdate → highlight current segment
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const onTimeUpdate = () => {
      const currentMs = video.currentTime * 1000;

      // Stop at segment end if playing a specific segment
      if (playEndRef.current !== null && currentMs >= playEndRef.current) {
        video.pause();
        playEndRef.current = null;
      }

      // Find current segment
      let found = -1;
      for (let i = 0; i < segments.length; i++) {
        if (currentMs >= segments[i].start_ms && currentMs < segments[i].end_ms) {
          found = i;
          break;
        }
      }

      if (found !== currentIndex) {
        setCurrentIndex(found);
        if (found >= 0) {
          const el = segmentRefs.current.get(found);
          el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      }
    };

    video.addEventListener("timeupdate", onTimeUpdate);
    return () => video.removeEventListener("timeupdate", onTimeUpdate);
  }, [segments, currentIndex]);

  // Play segment
  const playSegment = (seg: EvalSegment) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = seg.start_ms / 1000;
    playEndRef.current = seg.end_ms;
    video.play();
  };

  // Set human decision
  const setHumanAction = (index: number, action: "keep" | "cut") => {
    setSegments((prev) =>
      prev.map((seg) => {
        if (seg.index !== index) return seg;
        const existing = seg.human;
        if (existing?.action === action) {
          // Toggle off
          return { ...seg, human: null };
        }
        return {
          ...seg,
          human: {
            action,
            reason: existing?.reason ?? "",
            note: existing?.note ?? "",
          },
        };
      })
    );
    setDirty(true);
  };

  const setHumanReason = (index: number, reason: string) => {
    setSegments((prev) =>
      prev.map((seg) => {
        if (seg.index !== index || !seg.human) return seg;
        return { ...seg, human: { ...seg.human, reason } };
      })
    );
    setDirty(true);
  };

  const setHumanNote = (index: number, note: string) => {
    setSegments((prev) =>
      prev.map((seg) => {
        if (seg.index !== index || !seg.human) return seg;
        return { ...seg, human: { ...seg.human, note } };
      })
    );
    setDirty(true);
  };

  // Save
  const handleSave = async () => {
    setSaving(true);
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) return;
      await api.saveEvaluation(session.access_token, projectId, segments);
      setDirty(false);
    } catch (err) {
      alert(err instanceof Error ? err.message : "저장 실패");
    }
    setSaving(false);
  };

  // Stats
  const totalSegments = segments.length;
  const reviewedCount = segments.filter((s) => s.human !== null).length;
  const aiCutCount = segments.filter((s) => s.ai?.action === "cut").length;
  const agreeCount = segments.filter(
    (s) => s.human && s.ai && s.human.action === s.ai.action
  ).length;
  const agreePct = reviewedCount > 0 ? Math.round((agreeCount / reviewedCount) * 100) : 0;

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-950">
        <div className="animate-pulse text-gray-400">Loading...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-950 text-red-400">
        {error}
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      {/* Header */}
      <header className="border-b border-gray-800 sticky top-0 z-30 bg-gray-950">
        <div className="max-w-6xl mx-auto px-6 py-3 flex justify-between items-center">
          <button
            onClick={() => router.push(`/projects/${projectId}`)}
            className="text-gray-400 hover:text-white transition text-sm"
          >
            ← 프로젝트
          </button>
          <div className="flex items-center gap-3">
            {dirty && (
              <span className="text-amber-400 text-xs">● 변경사항 있음</span>
            )}
            <button
              onClick={handleSave}
              disabled={saving || !dirty}
              className={`px-4 py-1.5 rounded-lg text-sm font-medium transition ${
                dirty
                  ? "bg-white text-black hover:bg-gray-200"
                  : "bg-gray-800 text-gray-500 cursor-not-allowed"
              }`}
            >
              {saving ? "저장 중..." : "저장"}
            </button>
          </div>
        </div>
      </header>

      {/* Video Player (sticky) */}
      <div className="sticky top-[53px] z-20 bg-gray-950 border-b border-gray-800">
        <div className="max-w-4xl mx-auto">
          <video
            ref={videoRef}
            src={videoUrl}
            controls
            className="w-full max-h-[40vh] bg-black"
            preload="metadata"
          />
        </div>
      </div>

      {/* Stats Bar */}
      <div className="bg-gray-900/80 backdrop-blur border-b border-gray-800">
        <div className="max-w-4xl mx-auto px-6 py-2 flex gap-6 text-xs text-gray-400">
          <span>전체 <strong className="text-white">{totalSegments}</strong></span>
          <span>리뷰완료 <strong className="text-white">{reviewedCount}</strong></span>
          <span>AI삭제 <strong className="text-red-400">{aiCutCount}</strong></span>
          <span>일치율 <strong className="text-white">{agreePct}%</strong></span>
        </div>
      </div>

      {/* Segment List */}
      <main className="max-w-4xl mx-auto px-6 py-4">
        <div className="space-y-2">
          {segments.map((seg) => {
            const isCurrent = seg.index === currentIndex;
            const aiAction = seg.ai?.action ?? "keep";
            const disagree =
              seg.human !== null &&
              seg.ai !== null &&
              seg.human.action !== seg.ai.action;

            return (
              <div
                key={seg.index}
                ref={(el) => {
                  if (el) segmentRefs.current.set(seg.index, el);
                }}
                className={`rounded-lg p-3 transition-all ${
                  aiAction === "cut"
                    ? "border-l-4 border-red-500"
                    : "border-l-4 border-green-500"
                } ${isCurrent ? "ring-2 ring-blue-500" : ""} ${
                  disagree ? "bg-amber-900/20" : "bg-gray-900"
                }`}
              >
                {/* Row 1: index, time, play, AI badge */}
                <div className="flex items-center gap-3 mb-1">
                  <span className="text-xs text-gray-500 w-8">#{seg.index}</span>
                  <span className="text-xs text-gray-400 font-mono">
                    {formatTime(seg.start_ms)}→{formatTime(seg.end_ms)}
                  </span>
                  <button
                    onClick={() => playSegment(seg)}
                    className="text-blue-400 hover:text-blue-300 text-xs"
                    title="이 구간 재생"
                  >
                    ▶
                  </button>
                  <div className="ml-auto flex items-center gap-2">
                    <span
                      className={`text-xs px-2 py-0.5 rounded ${
                        aiAction === "cut"
                          ? "bg-red-900/50 text-red-300"
                          : "bg-green-900/50 text-green-300"
                      }`}
                    >
                      AI: {aiAction.toUpperCase()}
                    </span>
                    {seg.ai?.reason && (
                      <span className="text-xs text-gray-500">
                        {seg.ai.reason}
                      </span>
                    )}
                  </div>
                </div>

                {/* Row 2: text */}
                <p className="text-sm text-gray-300 mb-2 pl-8">{seg.text}</p>

                {/* Row 3: human evaluation controls */}
                <div className="flex items-center gap-2 pl-8 flex-wrap">
                  <span className="text-xs text-gray-500 mr-1">내 평가:</span>
                  <button
                    onClick={() => setHumanAction(seg.index, "keep")}
                    className={`text-xs px-2 py-1 rounded transition ${
                      seg.human?.action === "keep"
                        ? "bg-green-600 text-white"
                        : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                    }`}
                  >
                    Keep
                  </button>
                  <button
                    onClick={() => setHumanAction(seg.index, "cut")}
                    className={`text-xs px-2 py-1 rounded transition ${
                      seg.human?.action === "cut"
                        ? "bg-red-600 text-white"
                        : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                    }`}
                  >
                    Cut
                  </button>

                  {seg.human && (
                    <>
                      <select
                        value={seg.human.reason}
                        onChange={(e) =>
                          setHumanReason(seg.index, e.target.value)
                        }
                        className="text-xs bg-gray-800 text-gray-300 rounded px-2 py-1 border border-gray-700"
                      >
                        <option value="">이유 선택</option>
                        {(seg.human.action === "cut"
                          ? CUT_REASONS
                          : KEEP_REASONS
                        ).map((r) => (
                          <option key={r.value} value={r.value}>
                            {r.label}
                          </option>
                        ))}
                      </select>
                      <input
                        type="text"
                        value={seg.human.note}
                        onChange={(e) =>
                          setHumanNote(seg.index, e.target.value)
                        }
                        placeholder="메모"
                        className="text-xs bg-gray-800 text-gray-300 rounded px-2 py-1 border border-gray-700 flex-1 min-w-[100px]"
                      />
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </main>
    </div>
  );
}
