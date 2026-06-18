"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import {
  api,
  type EvalSegment,
  type EvalReportResponse,
  type EvaluationSavePayload,
  type FinalPreviewTimelineMap,
  type SegmentWithDecision,
} from "@/lib/api";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

// ── Constants ──

const CUT_REASONS = [
  { value: "duplicate", label: "중복" },
  { value: "incomplete", label: "불완전" },
  { value: "filler", label: "필러" },
  { value: "fumble", label: "말실수" },
  { value: "retake_signal", label: "재촬영 신호" },
  { value: "meta_comment", label: "메타 발언" },
  { value: "tangent", label: "탈선" },
  { value: "boring", label: "지루함" },
  { value: "repetitive", label: "반복" },
  { value: "long_pause", label: "긴 침묵" },
  { value: "crosstalk", label: "겹침" },
  { value: "irrelevant", label: "무관함" },
  { value: "dragging", label: "늘어짐" },
];

const KEEP_REASONS = [
  { value: "funny", label: "웃김" },
  { value: "witty", label: "재치" },
  { value: "chemistry", label: "케미" },
  { value: "reaction", label: "리액션" },
  { value: "callback", label: "콜백" },
  { value: "climax", label: "클라이맥스" },
  { value: "engaging", label: "몰입감" },
  { value: "emotional", label: "감정선" },
  { value: "best_take", label: "최적 테이크" },
  { value: "unique", label: "유일한 내용" },
  { value: "essential", label: "필수 내용" },
];

// ── Helpers ──

function formatTime(ms: number): string {
  const totalMs = Math.max(0, Math.round(ms));
  const m = Math.floor(totalMs / 60000);
  const s = Math.floor((totalMs % 60000) / 1000);
  const millis = totalMs % 1000;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

function rawSegmentStartMs(seg: EvalSegment): number {
  return seg.raw_start_ms ?? seg.start_ms;
}

function rawSegmentEndMs(seg: EvalSegment): number {
  return seg.raw_end_ms ?? seg.end_ms;
}

function formatGap(ms: number | null): string {
  if (ms === null) return "-";
  if (ms > 0) return `+${formatTime(ms)}`;
  if (ms < 0) return `-${formatTime(Math.abs(ms))}`;
  return "0ms";
}

function msToMediaTime(ms: number): number {
  return Number((Math.max(0, Math.round(ms)) / 1000).toFixed(3));
}

function mediaTimeToMs(video: HTMLVideoElement): number {
  return Math.round(video.currentTime * 1000);
}

function sourceMsToPreviewMs(sourceMs: number, map: FinalPreviewTimelineMap | null): number | null {
  const intervals = map?.intervals || [];
  if (intervals.length === 0) return null;
  for (const interval of intervals) {
    if (sourceMs >= interval.source_start_ms && sourceMs <= interval.source_end_ms) {
      const requested = Math.max(1, interval.requested_duration_ms);
      const scale = interval.actual_duration_ms / requested;
      return interval.preview_start_ms + (sourceMs - interval.source_start_ms) * scale;
    }
    if (sourceMs < interval.source_start_ms) return interval.preview_start_ms;
  }
  return intervals[intervals.length - 1].preview_end_ms;
}

function previewMsToSourceMs(previewMs: number, map: FinalPreviewTimelineMap | null): number | null {
  const intervals = map?.intervals || [];
  if (intervals.length === 0) return null;
  for (const interval of intervals) {
    if (previewMs >= interval.preview_start_ms && previewMs <= interval.preview_end_ms) {
      const actual = Math.max(1, interval.actual_duration_ms);
      const scale = interval.requested_duration_ms / actual;
      return interval.source_start_ms + (previewMs - interval.preview_start_ms) * scale;
    }
    if (previewMs < interval.preview_start_ms) return interval.source_start_ms;
  }
  return intervals[intervals.length - 1].source_end_ms;
}

function aiActionForSegment(seg: EvalSegment): "keep" | "cut" {
  return seg.ai?.action === "cut" ? "cut" : "keep";
}

function decisionActionForSegment(seg: EvalSegment): "keep" | "cut" {
  if (seg.human?.action === "cut") return "cut";
  if (seg.human?.action === "keep") return "keep";
  return aiActionForSegment(seg);
}

type ReviewMetadata = Pick<
  EvaluationSavePayload,
  "schema_version" | "review_scope" | "join_strategy"
>;

// ── Component ──

export default function ReviewPage() {
  const params = useParams();
  const router = useRouter();
  const supabase = createClient();
  const projectId = params.id as string;

  const videoRef = useRef<HTMLVideoElement>(null);
  const segmentRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const playEndRef = useRef<number | null>(null);
  const playFrameRef = useRef<number | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [originalVideoUrl, setOriginalVideoUrl] = useState("");
  const [durationMs, setDurationMs] = useState(0);
  const [segments, setSegments] = useState<EvalSegment[]>([]);
  const [reviewMetadata, setReviewMetadata] = useState<ReviewMetadata>({
    schema_version: null,
    review_scope: null,
    join_strategy: null,
  });
  const [currentIndex, setCurrentIndex] = useState(-1);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [report, setReport] = useState<EvalReportResponse | null>(null);
  const [showReport, setShowReport] = useState(false);
  const [loadingReport, setLoadingReport] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [finalPreviewJobId, setFinalPreviewJobId] = useState<string | null>(null);
  const [finalPreviewStatus, setFinalPreviewStatus] = useState<string | null>(null);
  const [finalPreviewProgress, setFinalPreviewProgress] = useState(0);
  const [finalPreviewError, setFinalPreviewError] = useState("");
  const [previewJobKind, setPreviewJobKind] = useState<"final" | "junction" | null>(null);
  const [activePreviewKind, setActivePreviewKind] = useState<"original" | "final" | "junction">("original");
  const [usingFinalPreview, setUsingFinalPreview] = useState(false);
  const [finalPreviewCaptionsUrl, setFinalPreviewCaptionsUrl] = useState("");
  const [finalPreviewTimelineMap, setFinalPreviewTimelineMap] =
    useState<FinalPreviewTimelineMap | null>(null);
  const [showJunctionOnly, setShowJunctionOnly] = useState(false);
  const [selectedSegmentIndexes, setSelectedSegmentIndexes] = useState<Set<number>>(
    () => new Set<number>()
  );

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
        api.getVideoUrl(token, projectId).catch(() => null),
        api.getEvaluation(token, projectId),
      ]);

      if (vidRes) {
        setVideoUrl(vidRes.video_url);
        setOriginalVideoUrl(vidRes.video_url);
        setDurationMs(vidRes.duration_ms || segRes.source_duration_ms);
      } else {
        setDurationMs(segRes.source_duration_ms);
      }

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
          ...seg,
          human: saved?.human ?? null,
        };
      });

      setReviewMetadata({
        schema_version: evalRes?.schema_version ?? segRes.schema_version ?? null,
        review_scope: evalRes?.review_scope ?? segRes.review_scope ?? null,
        join_strategy: evalRes?.join_strategy ?? segRes.join_strategy ?? null,
      });
      setSegments(merged);
    } catch (err) {
      setError(err instanceof Error ? err.message : "데이터 로딩 실패");
    }
    setLoading(false);
  }, [projectId, router, supabase.auth]);

  useEffect(() => {
    void Promise.resolve().then(loadData);
  }, [loadData]);

  useEffect(() => {
    if (!finalPreviewJobId || finalPreviewStatus === "completed" || finalPreviewStatus === "failed") return;

    let canceled = false;
    const poll = async () => {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session || canceled) return;
      try {
        const job = await api.getFinalPreview(session.access_token, projectId, finalPreviewJobId);
        if (canceled) return;
        setFinalPreviewStatus(job.status);
        setFinalPreviewProgress(job.progress);
        if (job.status === "completed" && job.video_url) {
          let timelineMap: FinalPreviewTimelineMap | null = null;
          if (job.timeline_map_url) {
            const mapRes = await fetch(job.timeline_map_url);
            if (mapRes.ok) {
              timelineMap = await mapRes.json();
            }
          }
          setVideoUrl(job.video_url);
          setFinalPreviewCaptionsUrl(job.captions_url || "");
          setFinalPreviewTimelineMap(timelineMap);
          setUsingFinalPreview(true);
          setActivePreviewKind(previewJobKind ?? "final");
          if ((previewJobKind ?? "final") === "final") setDirty(false);
          if (job.duration_ms) setDurationMs(job.duration_ms);
        } else if (job.status === "failed") {
          setFinalPreviewError(job.error_message || "완성본 미리보기 생성에 실패했습니다");
        }
      } catch (err) {
        if (!canceled) {
          setFinalPreviewError(err instanceof Error ? err.message : "미리보기 상태 확인에 실패했습니다");
        }
      }
    };

    void poll();
    const interval = window.setInterval(poll, 3000);
    return () => {
      canceled = true;
      window.clearInterval(interval);
    };
  }, [finalPreviewJobId, finalPreviewStatus, previewJobKind, projectId, supabase]);

  const junctionMetadata = useMemo(() => {
    const indexes = new Set<number>();
    const pairs: Array<{
      id: string;
      before: EvalSegment;
      after: EvalSegment;
      cutSegments: EvalSegment[];
      cutDurationMs: number;
    }> = [];

    let i = 0;
    while (i < segments.length) {
      if (decisionActionForSegment(segments[i]) !== "cut") {
        i += 1;
        continue;
      }

      const cutStart = i;
      const cutSegments: EvalSegment[] = [];
      while (i < segments.length && decisionActionForSegment(segments[i]) === "cut") {
        cutSegments.push(segments[i]);
        i += 1;
      }

      const before = segments[cutStart - 1];
      const after = segments[i];
      if (
        before &&
        after &&
        decisionActionForSegment(before) === "keep" &&
        decisionActionForSegment(after) === "keep"
      ) {
        indexes.add(before.index);
        indexes.add(after.index);
        pairs.push({
          id: `${before.index}-${after.index}-${cutStart}`,
          before,
          after,
          cutSegments,
          cutDurationMs: cutSegments.reduce(
            (total, seg) => total + Math.max(0, seg.end_ms - seg.start_ms),
            0
          ),
        });
      }
    }

    return { indexes, pairs };
  }, [segments]);

  const visibleSegments = useMemo(
    () =>
      showJunctionOnly
        ? segments.filter((seg) => junctionMetadata.indexes.has(seg.index))
        : segments,
    [junctionMetadata, segments, showJunctionOnly]
  );

  const selectedSegments = useMemo(
    () => segments.filter((seg) => selectedSegmentIndexes.has(seg.index)),
    [segments, selectedSegmentIndexes]
  );

  const selectedVisibleCount = useMemo(
    () => visibleSegments.filter((seg) => selectedSegmentIndexes.has(seg.index)).length,
    [selectedSegmentIndexes, visibleSegments]
  );

  const nextSegmentByIndex = useMemo(() => {
    const map = new Map<number, EvalSegment | null>();
    for (let i = 0; i < segments.length; i++) {
      map.set(segments[i].index, segments[i + 1] ?? null);
    }
    return map;
  }, [segments]);

  const stopSegmentPlayback = useCallback(() => {
    const video = videoRef.current;
    if (video) video.pause();
    playEndRef.current = null;
    if (playFrameRef.current !== null) {
      window.cancelAnimationFrame(playFrameRef.current);
      playFrameRef.current = null;
    }
  }, []);

  const scheduleSegmentPlaybackMonitor = useCallback(() => {
    if (playFrameRef.current !== null) {
      window.cancelAnimationFrame(playFrameRef.current);
      playFrameRef.current = null;
    }

    const tick = () => {
      const video = videoRef.current;
      const endMs = playEndRef.current;
      if (!video || endMs === null || video.paused || video.ended) {
        playFrameRef.current = null;
        return;
      }

      if (mediaTimeToMs(video) >= endMs) {
        stopSegmentPlayback();
        return;
      }

      playFrameRef.current = window.requestAnimationFrame(tick);
    };

    playFrameRef.current = window.requestAnimationFrame(tick);
  }, [stopSegmentPlayback]);

  useEffect(() => {
    return () => {
      if (playFrameRef.current !== null) {
        window.cancelAnimationFrame(playFrameRef.current);
      }
    };
  }, []);

  // Video timeupdate → highlight current segment
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const onTimeUpdate = () => {
      const currentMs = mediaTimeToMs(video);
      const currentSourceMs = usingFinalPreview
        ? previewMsToSourceMs(currentMs, finalPreviewTimelineMap)
        : currentMs;

      // Stop at segment end if playing a specific segment
      if (playEndRef.current !== null && currentMs >= playEndRef.current) {
        stopSegmentPlayback();
      }

      // Find current segment
      let found = -1;
      if (currentSourceMs !== null) {
        for (let i = 0; i < segments.length; i++) {
          if (currentSourceMs >= segments[i].start_ms && currentSourceMs < segments[i].end_ms) {
            found = segments[i].index;
            break;
          }
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
  }, [segments, currentIndex, stopSegmentPlayback, usingFinalPreview, finalPreviewTimelineMap]);

  // Play segment
  const playSegment = (seg: EvalSegment) => {
    const video = videoRef.current;
    if (!video) return;
    stopSegmentPlayback();
    const sourceStartMs = Math.round(seg.start_ms);
    const sourceEndMs = Math.round(seg.end_ms);
    const startMs = usingFinalPreview
      ? sourceMsToPreviewMs(sourceStartMs, finalPreviewTimelineMap)
      : sourceStartMs;
    const endMs = usingFinalPreview
      ? sourceMsToPreviewMs(sourceEndMs, finalPreviewTimelineMap)
      : sourceEndMs;
    if (startMs === null || endMs === null) return;
    video.currentTime = msToMediaTime(startMs);
    playEndRef.current = Math.max(startMs + 1, endMs);
    setCurrentIndex(seg.index);
    const playPromise = video.play();
    scheduleSegmentPlaybackMonitor();
    if (playPromise) {
      playPromise.catch(() => {
        stopSegmentPlayback();
      });
    }
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
    setSaveError("");
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) { setSaving(false); return; }
      await api.saveEvaluation(session.access_token, projectId, {
        ...reviewMetadata,
        segments,
      });
      setDirty(false);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "저장 실패");
    }
    setSaving(false);
  };

  const handleGenerateFinalPreview = async () => {
    setFinalPreviewError("");
    setPreviewJobKind("final");
    setFinalPreviewStatus("pending");
    setFinalPreviewProgress(0);
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) return;
      const job = await api.startFinalPreview(session.access_token, projectId, {
        ...reviewMetadata,
        segments,
      });
      setFinalPreviewJobId(job.job_id);
      setFinalPreviewStatus(job.status);
      setFinalPreviewProgress(job.progress);
      setFinalPreviewTimelineMap(null);
      if (job.status === "completed" && job.video_url) {
        let timelineMap: FinalPreviewTimelineMap | null = null;
        if (job.timeline_map_url) {
          const mapRes = await fetch(job.timeline_map_url);
          if (mapRes.ok) {
            timelineMap = await mapRes.json();
          }
        }
        setVideoUrl(job.video_url);
        setFinalPreviewCaptionsUrl(job.captions_url || "");
        setFinalPreviewTimelineMap(timelineMap);
        setUsingFinalPreview(true);
        setActivePreviewKind("final");
        if (job.duration_ms) setDurationMs(job.duration_ms);
      }
      setDirty(false);
    } catch (err) {
      setFinalPreviewStatus("failed");
      setFinalPreviewError(err instanceof Error ? err.message : "완성본 미리보기 생성에 실패했습니다");
    }
  };


  const handleGenerateJunctionPreview = async () => {
    setFinalPreviewError("");
    setPreviewJobKind("junction");
    setFinalPreviewStatus("pending");
    setFinalPreviewProgress(0);
    setShowJunctionOnly(true);
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) return;
      const job = await api.startJunctionPreview(session.access_token, projectId, {
        ...reviewMetadata,
        segments,
      });
      setFinalPreviewJobId(job.job_id);
      setFinalPreviewStatus(job.status);
      setFinalPreviewProgress(job.progress);
      setFinalPreviewTimelineMap(null);
      if (job.status === "completed" && job.video_url) {
        let timelineMap: FinalPreviewTimelineMap | null = null;
        if (job.timeline_map_url) {
          const mapRes = await fetch(job.timeline_map_url);
          if (mapRes.ok) {
            timelineMap = await mapRes.json();
          }
        }
        setVideoUrl(job.video_url);
        setFinalPreviewCaptionsUrl(job.captions_url || "");
        setFinalPreviewTimelineMap(timelineMap);
        setUsingFinalPreview(true);
        setActivePreviewKind("junction");
        if (job.duration_ms) setDurationMs(job.duration_ms);
      }
    } catch (err) {
      setFinalPreviewStatus("failed");
      setFinalPreviewError(err instanceof Error ? err.message : "연결부 미리보기 생성에 실패했습니다");
    }
  };

  const restoreOriginalPreview = () => {
    if (!originalVideoUrl) return;
    setVideoUrl(originalVideoUrl);
    setFinalPreviewCaptionsUrl("");
    setFinalPreviewTimelineMap(null);
    setUsingFinalPreview(false);
    setActivePreviewKind("original");
  };

  const toggleSegmentSelection = (index: number) => {
    setSelectedSegmentIndexes((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };

  const selectVisibleSegments = () => {
    setSelectedSegmentIndexes((prev) => {
      const next = new Set(prev);
      for (const seg of visibleSegments) {
        next.add(seg.index);
      }
      return next;
    });
  };

  const clearSelectedSegments = () => {
    setSelectedSegmentIndexes(new Set<number>());
  };

  const exportSelectedSegments = () => {
    if (selectedSegments.length === 0) return;

    const payload = {
      project_id: projectId,
      exported_at: new Date().toISOString(),
      count: selectedSegments.length,
      segments: selectedSegments,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `project-${projectId}-selected-segments.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  // Load report
  const loadReport = async () => {
    setLoadingReport(true);
    setSaveError("");
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) { setLoadingReport(false); return; }
      const r = await api.getEvalReport(session.access_token, projectId);
      setReport(r);
      setShowReport(true);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "리포트 로딩 실패");
    }
    setLoadingReport(false);
  };

  // Stats
  const totalSegments = segments.length;
  const reviewedCount = segments.filter((s) => s.human !== null).length;
  const aiCutCount = segments.filter((s) => s.ai?.action === "cut").length;
  const agreeCount = segments.filter(
    (s) => s.human && s.ai && s.human.action === s.ai.action
  ).length;
  const agreePct = reviewedCount > 0 ? Math.round((agreeCount / reviewedCount) * 100) : 0;

  const previewLabel =
    activePreviewKind === "junction"
      ? "연결부만 모은 미리보기"
      : "현재 decision 기준 완성본 미리보기";
  const isPreviewRendering = finalPreviewStatus === "pending" || finalPreviewStatus === "running";


  const renderSegmentRow = (seg: EvalSegment) => {
    const isCurrent = seg.index === currentIndex;
    const aiAction = aiActionForSegment(seg);
    const rawStartMs = rawSegmentStartMs(seg);
    const rawEndMs = rawSegmentEndMs(seg);
    const nextSegment = nextSegmentByIndex.get(seg.index) ?? null;
    const gapMs = nextSegment ? rawSegmentStartMs(nextSegment) - rawEndMs : null;
    const gapClass =
      gapMs === null
        ? "text-gray-600"
        : gapMs < 0
          ? "text-amber-400"
          : gapMs > 0
            ? "text-cyan-300"
            : "text-gray-500";
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
        className="grid grid-cols-1 gap-2 sm:grid-cols-[7.5rem_minmax(0,1fr)] sm:gap-3"
      >
        <aside
          className="rounded-md border border-gray-800 bg-gray-950/70 px-3 py-2 font-mono text-[11px] leading-5 text-gray-500 sm:sticky sm:top-28 sm:self-start"
          title={
            nextSegment
              ? `raw segment time, gap to segment #${nextSegment.index}`
              : "raw segment time"
          }
        >
          <div className="grid grid-cols-3 gap-2 sm:block">
            <div className="flex items-center justify-between gap-2">
              <span className="font-sans text-gray-600">S</span>
              <span>{formatTime(rawStartMs)}</span>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="font-sans text-gray-600">E</span>
              <span>{formatTime(rawEndMs)}</span>
            </div>
            <div className={`flex items-center justify-between gap-2 ${gapClass}`}>
              <span className="font-sans text-gray-600">G</span>
              <span>{formatGap(gapMs)}</span>
            </div>
          </div>
        </aside>

        <div
          className={`rounded-lg p-3 transition-all ${
            aiAction === "cut"
              ? "border-l-4 border-red-500"
              : "border-l-4 border-green-500"
          } ${isCurrent ? "ring-2 ring-blue-500" : ""} ${
            disagree ? "bg-amber-900/20" : "bg-gray-900"
          }`}
        >
          <div className="flex items-center gap-3 mb-1">
            <input
              type="checkbox"
              checked={selectedSegmentIndexes.has(seg.index)}
              onChange={() => toggleSegmentSelection(seg.index)}
              className="h-4 w-4 shrink-0 rounded border-gray-600 bg-gray-950 accent-cyan-500"
              aria-label={`세그먼트 ${seg.index} 선택`}
            />
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

          <p className="text-sm text-gray-300 mb-2 pl-14">{seg.text}</p>

          <div className="flex items-center gap-2 pl-14 flex-wrap">
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
                  {(seg.human.action === "cut" ? CUT_REASONS : KEEP_REASONS).map((r) => (
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
      </div>
    );
  };

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
              onClick={handleGenerateFinalPreview}
              disabled={isPreviewRendering}
              className="px-4 py-1.5 rounded-lg text-sm font-medium transition bg-cyan-500/10 text-cyan-300 hover:bg-cyan-500/20 disabled:opacity-50"
            >
              {isPreviewRendering && previewJobKind === "final"
                ? `생성 중 ${finalPreviewProgress}%`
                : "완성본 미리보기 생성"}
            </button>
            {usingFinalPreview && (
              <button
                onClick={restoreOriginalPreview}
                className="px-4 py-1.5 rounded-lg text-sm font-medium transition bg-gray-800 text-gray-300 hover:bg-gray-700"
              >
                원본 미리보기
              </button>
            )}
            <button
              onClick={() => {
                if (showReport) {
                  setShowReport(false);
                } else {
                  loadReport();
                }
              }}
              disabled={loadingReport}
              className="px-4 py-1.5 rounded-lg text-sm font-medium transition bg-gray-800 text-gray-300 hover:bg-gray-700"
            >
              {loadingReport ? "분석 중..." : showReport ? "목록으로" : "리포트"}
            </button>
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
            <span className="text-[11px] text-gray-500">
              {reviewMetadata.schema_version ?? "review-unknown"} / {reviewMetadata.join_strategy ?? "join-unknown"}
            </span>
          </div>
        </div>
      </header>

      {/* Error banner */}
      {saveError && (
        <div className="max-w-6xl mx-auto px-6 py-2">
          <div className="bg-red-900/50 border border-red-700 rounded-lg px-4 py-2 text-red-200 text-sm flex items-center justify-between">
            <span>{saveError}</span>
            <button onClick={() => setSaveError("")} className="text-red-400 hover:text-red-200 ml-3">✕</button>
          </div>
        </div>
      )}

      {finalPreviewError && (
        <div className="max-w-6xl mx-auto px-6 py-2">
          <div className="bg-red-900/50 border border-red-700 rounded-lg px-4 py-2 text-red-200 text-sm flex items-center justify-between">
            <span>{finalPreviewError}</span>
            <button onClick={() => setFinalPreviewError("")} className="text-red-400 hover:text-red-200 ml-3">✕</button>
          </div>
        </div>
      )}

      {/* Video Player (sticky) */}
      {videoUrl ? (
        <div className="sticky top-[53px] z-20 bg-gray-950 border-b border-gray-800">
          <div className="max-w-4xl mx-auto">
            <video
              key={`${videoUrl}:${finalPreviewCaptionsUrl}`}
              ref={videoRef}
              controls
              crossOrigin={finalPreviewCaptionsUrl ? "anonymous" : undefined}
              className="w-full max-h-[40vh] bg-black"
              preload="metadata"
            >
              <source src={videoUrl} />
              {usingFinalPreview && finalPreviewCaptionsUrl && (
                <track
                  kind="subtitles"
                  src={finalPreviewCaptionsUrl}
                  srcLang="ko"
                  label="한국어"
                  default
                />
              )}
            </video>
            {usingFinalPreview && (
              <div className="px-3 py-2 text-center text-xs text-cyan-300 bg-cyan-500/10">
                {previewLabel}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="bg-gray-900 border-b border-gray-800">
          <div className="max-w-4xl mx-auto px-6 py-3 text-center text-sm text-gray-500">
            영상 미리보기를 사용할 수 없습니다
          </div>
        </div>
      )}

      {/* Stats Bar */}
      <div className="bg-gray-900/80 backdrop-blur border-b border-gray-800">
        <div className="max-w-4xl mx-auto px-6 py-2 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-gray-400">
          <span>전체 <strong className="text-white">{totalSegments}</strong></span>
          <span>리뷰완료 <strong className="text-white">{reviewedCount}</strong></span>
          <span>AI삭제 <strong className="text-red-400">{aiCutCount}</strong></span>
          <span>일치율 <strong className="text-white">{agreePct}%</strong></span>
          <span>미리보기 <strong className="text-white">{formatTime(durationMs)}</strong></span>
          {showJunctionOnly && (
            <span>
              연결부 <strong className="text-cyan-300">{junctionMetadata.pairs.length}</strong>
            </span>
          )}
          <span>
            선택 <strong className="text-cyan-300">{selectedSegmentIndexes.size}</strong>
            {showJunctionOnly && selectedSegmentIndexes.size > 0 && (
              <span className="text-gray-500"> ({selectedVisibleCount}/{visibleSegments.length})</span>
            )}
          </span>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setShowJunctionOnly((value) => !value)}
              className={`px-2.5 py-1 rounded text-xs font-medium transition ${
                showJunctionOnly
                  ? "bg-cyan-500 text-gray-950 hover:bg-cyan-400"
                  : "bg-gray-800 text-gray-300 hover:bg-gray-700"
              }`}
            >
              {showJunctionOnly ? "전체 보기" : "연결부만 골라보기"}
            </button>
            <button
              type="button"
              onClick={handleGenerateJunctionPreview}
              disabled={junctionMetadata.pairs.length === 0 || isPreviewRendering}
              className="px-2.5 py-1 rounded bg-cyan-500/10 text-cyan-300 text-xs font-medium transition hover:bg-cyan-500/20 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {isPreviewRendering && previewJobKind === "junction"
                ? `영상 생성 중 ${finalPreviewProgress}%`
                : "연결부 미리보기 생성"}
            </button>
            <button
              type="button"
              onClick={selectVisibleSegments}
              disabled={visibleSegments.length === 0 || selectedVisibleCount === visibleSegments.length}
              className="px-2.5 py-1 rounded bg-gray-800 text-gray-300 text-xs font-medium transition hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              현재 보기 선택
            </button>
            <button
              type="button"
              onClick={clearSelectedSegments}
              disabled={selectedSegmentIndexes.size === 0}
              className="px-2.5 py-1 rounded bg-gray-800 text-gray-300 text-xs font-medium transition hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              선택 해제
            </button>
            <button
              type="button"
              onClick={exportSelectedSegments}
              disabled={selectedSegments.length === 0}
              className="px-2.5 py-1 rounded bg-white text-gray-950 text-xs font-medium transition hover:bg-gray-200 disabled:bg-gray-800 disabled:text-gray-500 disabled:cursor-not-allowed"
            >
              선택 Export
            </button>
          </div>
        </div>
      </div>

      {/* Report Panel */}
      {showReport && report && (
        <main className="max-w-4xl mx-auto px-6 py-6">
          <div className="space-y-6">
            {/* Header */}
            <div>
              <h2 className="text-lg font-bold mb-1">Eval Report</h2>
              <p className="text-xs text-gray-500">
                avid: {report.avid_version ?? "?"} / eogum: {report.eogum_version ?? "?"}
              </p>
            </div>

            {/* Overview */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { label: "전체 세그먼트", value: report.total_segments, color: "text-white" },
                { label: "직접 리뷰", value: report.human_reviewed, color: "text-white" },
                { label: "일치율", value: `${(report.agreement_rate * 100).toFixed(1)}%`, color: report.agreement_rate >= 0.9 ? "text-green-400" : report.agreement_rate >= 0.8 ? "text-yellow-400" : "text-red-400" },
                { label: "불일치", value: report.confusion.fp + report.confusion.fn, color: "text-red-400" },
              ].map((item) => (
                <div key={item.label} className="bg-gray-900 rounded-lg p-3">
                  <div className="text-xs text-gray-500 mb-1">{item.label}</div>
                  <div className={`text-xl font-bold ${item.color}`}>{item.value}</div>
                </div>
              ))}
            </div>

            {/* Confusion Matrix */}
            <div className="bg-gray-900 rounded-lg p-4">
              <h3 className="text-sm font-semibold mb-3">Confusion Matrix (cut = positive)</h3>
              <div className="grid grid-cols-3 gap-px bg-gray-700 rounded overflow-hidden text-center text-sm">
                <div className="bg-gray-900 p-2"></div>
                <div className="bg-gray-900 p-2 text-gray-400 font-medium">Human: Cut</div>
                <div className="bg-gray-900 p-2 text-gray-400 font-medium">Human: Keep</div>
                <div className="bg-gray-900 p-2 text-gray-400 font-medium">AI: Cut</div>
                <div className="bg-green-900/30 p-2 font-bold text-green-400">TP {report.confusion.tp}</div>
                <div className="bg-red-900/30 p-2 font-bold text-red-400">FP {report.confusion.fp}</div>
                <div className="bg-gray-900 p-2 text-gray-400 font-medium">AI: Keep</div>
                <div className="bg-red-900/30 p-2 font-bold text-red-400">FN {report.confusion.fn}</div>
                <div className="bg-green-900/30 p-2 font-bold text-green-400">TN {report.confusion.tn}</div>
              </div>
            </div>

            {/* Metrics */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { label: "Accuracy", value: report.metrics.accuracy, desc: "전체 정확도" },
                { label: "Precision", value: report.metrics.precision, desc: "AI cut 중 맞은 비율" },
                { label: "Recall", value: report.metrics.recall, desc: "실제 cut 중 AI가 찾은 비율" },
                { label: "F1", value: report.metrics.f1, desc: "Precision·Recall 조화평균" },
              ].map((m) => (
                <div key={m.label} className="bg-gray-900 rounded-lg p-3">
                  <div className="text-xs text-gray-500 mb-1" title={m.desc}>{m.label}</div>
                  <div className="text-xl font-bold">{(m.value * 100).toFixed(1)}%</div>
                </div>
              ))}
            </div>

            {/* Time Impact */}
            <div className="bg-gray-900 rounded-lg p-4">
              <h3 className="text-sm font-semibold mb-3">시간 비교</h3>
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <span className="text-gray-500">AI가 자른 시간:</span>{" "}
                  <span className="font-mono">{formatTime(report.ai_cut_ms)}</span>
                  <span className="text-gray-600 text-xs ml-1">({report.ai_cut_count}개)</span>
                </div>
                <div>
                  <span className="text-gray-500">실제 잘라야 할 시간:</span>{" "}
                  <span className="font-mono">{formatTime(report.truth_cut_ms)}</span>
                  <span className="text-gray-600 text-xs ml-1">({report.truth_cut_count}개)</span>
                </div>
              </div>
            </div>

            {/* Error Analysis */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {/* FP: AI가 잘못 자른 것 */}
              <div className="bg-gray-900 rounded-lg p-4">
                <h3 className="text-sm font-semibold mb-2 text-red-400">
                  FP — AI가 잘못 자름 ({report.confusion.fp}개)
                </h3>
                <p className="text-xs text-gray-500 mb-3">AI가 cut했지만 사람은 keep</p>
                {report.fp_reasons.length === 0 ? (
                  <p className="text-xs text-gray-600">없음</p>
                ) : (
                  <div className="space-y-1">
                    {report.fp_reasons.map((r) => (
                      <div key={r.reason} className="flex justify-between text-sm">
                        <span className="text-gray-300">{r.reason}</span>
                        <span className="text-gray-500">{r.count}개 · {formatTime(r.total_ms)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* FN: AI가 놓친 것 */}
              <div className="bg-gray-900 rounded-lg p-4">
                <h3 className="text-sm font-semibold mb-2 text-amber-400">
                  FN — AI가 놓침 ({report.confusion.fn}개)
                </h3>
                <p className="text-xs text-gray-500 mb-3">사람은 cut했지만 AI는 keep</p>
                {report.fn_reasons.length === 0 ? (
                  <p className="text-xs text-gray-600">없음</p>
                ) : (
                  <div className="space-y-1">
                    {report.fn_reasons.map((r) => (
                      <div key={r.reason} className="flex justify-between text-sm">
                        <span className="text-gray-300">{r.reason}</span>
                        <span className="text-gray-500">{r.count}개 · {formatTime(r.total_ms)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Disagreement Detail List */}
            <div className="bg-gray-900 rounded-lg p-4">
              <h3 className="text-sm font-semibold mb-3">
                불일치 세그먼트 ({report.disagreements.length}개)
              </h3>
              <div className="space-y-2 max-h-[400px] overflow-y-auto">
                {report.disagreements.map((d) => (
                  <div
                    key={d.index}
                    className="bg-gray-800 rounded p-2 text-sm cursor-pointer hover:bg-gray-750"
                    onClick={() => {
                      setShowJunctionOnly(false);
                      setShowReport(false);
                      setTimeout(() => {
                        const el = segmentRefs.current.get(d.index);
                        el?.scrollIntoView({ behavior: "smooth", block: "center" });
                      }, 100);
                    }}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs text-gray-500">#{d.index}</span>
                      <span className="text-xs font-mono text-gray-400">
                        {formatTime(d.start_ms)}→{formatTime(d.end_ms)}
                      </span>
                      <span className={`text-xs px-1.5 py-0.5 rounded ${d.ai_action === "cut" ? "bg-red-900/50 text-red-300" : "bg-green-900/50 text-green-300"}`}>
                        AI: {d.ai_action} {d.ai_reason && `(${d.ai_reason})`}
                      </span>
                      <span className="text-xs text-gray-600">→</span>
                      <span className={`text-xs px-1.5 py-0.5 rounded ${d.human_action === "cut" ? "bg-red-900/50 text-red-300" : "bg-green-900/50 text-green-300"}`}>
                        사람: {d.human_action} {d.human_reason && `(${d.human_reason})`}
                      </span>
                    </div>
                    <p className="text-xs text-gray-400 truncate">{d.text}</p>
                    {d.human_note && (
                      <p className="text-xs text-gray-500 mt-1">메모: {d.human_note}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </main>
      )}

      {/* Segment List */}
      {!showReport && (
        <main className="max-w-4xl mx-auto px-6 py-4">
          {showJunctionOnly ? (
            junctionMetadata.pairs.length === 0 ? (
              <div className="rounded-lg border border-gray-800 bg-gray-900 px-4 py-8 text-center text-sm text-gray-500">
                검토할 연결부가 없습니다
              </div>
            ) : (
              <div className="space-y-4">
                {junctionMetadata.pairs.map((pair) => {
                  const firstCut = pair.cutSegments[0];
                  const lastCut = pair.cutSegments[pair.cutSegments.length - 1];
                  const cutLabel = firstCut && lastCut
                    ? firstCut.index === lastCut.index
                      ? `#${firstCut.index}`
                      : `#${firstCut.index}-#${lastCut.index}`
                    : "-";

                  return (
                    <section
                      key={pair.id}
                      className="rounded-lg border border-cyan-900/60 bg-cyan-950/10 p-3 space-y-3"
                    >
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <span className="font-semibold text-cyan-200">
                          #{pair.before.index} → #{pair.after.index}
                        </span>
                        <span className="text-xs text-gray-500">
                          중간 CUT {cutLabel} · {pair.cutSegments.length}개 · {formatTime(pair.cutDurationMs)}
                        </span>
                      </div>
                      <div className="grid gap-2 text-xs text-gray-400 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
                        <p className="rounded bg-gray-950/60 px-2 py-1">
                          <span className="text-gray-500">#{pair.before.index}</span> {pair.before.text}
                        </p>
                        <span className="text-center text-cyan-400">→</span>
                        <p className="rounded bg-gray-950/60 px-2 py-1">
                          <span className="text-gray-500">#{pair.after.index}</span> {pair.after.text}
                        </p>
                      </div>
                      <div className="space-y-2">
                        {renderSegmentRow(pair.before)}
                        {renderSegmentRow(pair.after)}
                      </div>
                    </section>
                  );
                })}
              </div>
            )
          ) : visibleSegments.length === 0 ? (
            <div className="rounded-lg border border-gray-800 bg-gray-900 px-4 py-8 text-center text-sm text-gray-500">
              표시할 segment가 없습니다
            </div>
          ) : (
            <div className="space-y-2">
              {visibleSegments.map((seg) => renderSegmentRow(seg))}
            </div>
          )}
        </main>
      )}
    </div>
  );
}
