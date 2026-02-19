"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import { useRouter } from "next/navigation";
import { useEffect, useState, useMemo } from "react";
import Image from "next/image";

/* ── Animated Waveform ── */
function Waveform({ barCount = 48 }: { barCount?: number }) {
  const bars = useMemo(
    () =>
      Array.from({ length: barCount }, (_, i) => ({
        height: 20 + Math.random() * 80,
        delay: (i * 0.06) % 1.2,
      })),
    [barCount]
  );
  return (
    <div className="flex items-end justify-center gap-[2px] h-16">
      {bars.map((bar, i) => (
        <div
          key={i}
          className="waveform-bar w-[3px] rounded-full bg-gradient-to-t from-cyan-500/60 to-violet-500/60"
          style={{
            height: `${bar.height}%`,
            animationDelay: `${bar.delay}s`,
          }}
        />
      ))}
    </div>
  );
}

/* ── Animated Timeline ── */
function Timeline() {
  const segments = useMemo(
    () => [
      { type: "keep", width: 80, label: "인트로" },
      { type: "cut", width: 35, label: "더듬" },
      { type: "keep", width: 120, label: "본론 설명" },
      { type: "cut", width: 25, label: "침묵" },
      { type: "keep", width: 90, label: "핵심 내용" },
      { type: "cut", width: 45, label: "반복" },
      { type: "keep", width: 110, label: "예시 설명" },
      { type: "cut", width: 30, label: "음..." },
      { type: "keep", width: 70, label: "정리" },
      { type: "keep", width: 80, label: "인트로" },
      { type: "cut", width: 35, label: "더듬" },
      { type: "keep", width: 120, label: "본론 설명" },
      { type: "cut", width: 25, label: "침묵" },
      { type: "keep", width: 90, label: "핵심 내용" },
      { type: "cut", width: 45, label: "반복" },
      { type: "keep", width: 110, label: "예시 설명" },
      { type: "cut", width: 30, label: "음..." },
      { type: "keep", width: 70, label: "정리" },
    ],
    []
  );

  return (
    <div className="relative overflow-hidden rounded-xl border border-white/5 bg-white/[0.02] p-4">
      {/* Track labels */}
      <div className="flex items-center gap-4 mb-3 text-xs text-gray-500">
        <div className="flex items-center gap-1.5">
          <div className="w-2.5 h-2.5 rounded-sm bg-cyan-500/70" />
          <span>유지</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2.5 h-2.5 rounded-sm bg-red-500/30" />
          <span>컷</span>
        </div>
      </div>

      {/* Timeline track */}
      <div className="relative h-12 overflow-hidden rounded-lg">
        <div className="timeline-scroll flex gap-[2px] absolute">
          {segments.map((seg, i) => (
            <div
              key={i}
              className={`h-12 rounded-sm flex items-center justify-center text-[10px] font-medium transition-all ${
                seg.type === "keep"
                  ? "bg-cyan-500/20 text-cyan-400/70 border border-cyan-500/10"
                  : "cut-segment bg-red-500/10 text-red-400/50 border border-red-500/10 line-through"
              }`}
              style={{ width: seg.width }}
            >
              {seg.width > 35 && seg.label}
            </div>
          ))}
        </div>
      </div>

      {/* Playhead */}
      <div className="absolute left-1/2 top-[52px] bottom-4 w-[2px] bg-white/80 rounded-full">
        <div className="absolute -top-1 left-1/2 -translate-x-1/2 w-2 h-2 bg-white rounded-full shadow-[0_0_8px_rgba(255,255,255,0.6)]" />
      </div>
    </div>
  );
}

/* ── Step Card ── */
function StepCard({
  step,
  title,
  desc,
  icon,
}: {
  step: number;
  title: string;
  desc: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="relative group">
      <div className="absolute -inset-px rounded-2xl bg-gradient-to-b from-white/10 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
      <div className="relative bg-white/[0.03] border border-white/[0.06] rounded-2xl p-8 hover:border-white/10 transition-colors duration-500">
        <div className="flex items-center gap-3 mb-4">
          <span className="flex items-center justify-center w-8 h-8 rounded-full bg-cyan-500/10 text-cyan-400 text-sm font-bold">
            {step}
          </span>
          <div className="text-2xl">{icon}</div>
        </div>
        <h3 className="text-lg font-semibold mb-2">{title}</h3>
        <p className="text-gray-400 text-sm leading-relaxed">{desc}</p>
      </div>
    </div>
  );
}

/* ── Feature Card ── */
function FeatureCard({
  title,
  desc,
  icon,
}: {
  title: string;
  desc: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="group relative">
      <div className="absolute -inset-px rounded-xl bg-gradient-to-br from-cyan-500/20 via-transparent to-violet-500/20 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
      <div className="relative bg-white/[0.02] border border-white/[0.05] rounded-xl p-6 hover:border-white/10 transition-all duration-500">
        <div className="text-2xl mb-3">{icon}</div>
        <h3 className="font-semibold mb-1.5">{title}</h3>
        <p className="text-gray-500 text-sm leading-relaxed">{desc}</p>
      </div>
    </div>
  );
}

/* ── Main Page ── */
export default function LandingPage() {
  const router = useRouter();
  const supabase = createClient();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    supabase.auth.getUser().then(({ data: { user } }) => {
      if (user) {
        router.replace("/dashboard");
      } else {
        setLoading(false);
      }
    });
  }, []);

  const handleLogin = async (provider: "google" | "github") => {
    await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
      },
    });
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
      <nav className="fixed top-0 left-0 right-0 z-50 border-b border-white/[0.04] bg-[#030712]/80 backdrop-blur-xl">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Image src="/logo.png" alt="어검" width={32} height={32} className="rounded" />
            <span className="font-bold text-lg tracking-tight">어검</span>
          </div>
          <button
            onClick={() => handleLogin("google")}
            className="px-5 py-2 text-sm font-medium bg-white text-black rounded-lg hover:bg-gray-100 transition-colors"
          >
            시작하기
          </button>
        </div>
      </nav>

      {/* ── Hero ── */}
      <section className="relative pt-32 pb-20 px-6 overflow-hidden">
        {/* Background glow */}
        <div className="absolute top-20 left-1/2 -translate-x-1/2 w-[600px] h-[400px] bg-cyan-500/[0.07] rounded-full blur-[120px] glow-pulse" />
        <div className="absolute top-40 left-1/3 w-[400px] h-[300px] bg-violet-500/[0.05] rounded-full blur-[100px] glow-pulse" style={{ animationDelay: "1.5s" }} />

        <div className="relative max-w-4xl mx-auto text-center">
          <div className="fade-in-up mb-8">
            <Image src="/logo-large.png" alt="어검" width={120} height={120} className="mx-auto rounded-2xl drop-shadow-[0_0_30px_rgba(6,182,212,0.2)]" priority />
          </div>

          <div className="fade-in-up fade-in-up-delay-1">
            <p className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-white/10 bg-white/[0.03] text-sm text-gray-400 mb-8">
              <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
              영상 편집을 10배 빠르게
            </p>
          </div>

          <h1 className="fade-in-up fade-in-up-delay-2 text-5xl sm:text-6xl md:text-7xl font-bold tracking-tight leading-[1.1] mb-6">
            AI가 찾아주는
            <br />
            <span className="gradient-text">편집 포인트</span>
          </h1>

          <p className="fade-in-up fade-in-up-delay-3 text-lg sm:text-xl text-gray-400 max-w-2xl mx-auto mb-10 leading-relaxed">
            영상을 올리면 불필요한 구간을 자동으로 감지합니다.
            <br className="hidden sm:block" />
            자막과 Final Cut Pro 타임라인까지 한번에.
          </p>

          <div className="fade-in-up fade-in-up-delay-4 flex flex-col sm:flex-row gap-4 justify-center mb-6">
            <button
              onClick={() => handleLogin("google")}
              className="group relative px-8 py-3.5 font-medium rounded-xl overflow-hidden transition-all duration-300 hover:shadow-[0_0_30px_rgba(6,182,212,0.3)]"
            >
              <div className="absolute inset-0 bg-gradient-to-r from-cyan-500 to-violet-500" />
              <div className="absolute inset-0 bg-gradient-to-r from-cyan-400 to-violet-400 opacity-0 group-hover:opacity-100 transition-opacity" />
              <span className="relative text-white flex items-center gap-2">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" /><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" /><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" /><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" /></svg>
                Google로 시작하기
              </span>
            </button>
            <button
              onClick={() => handleLogin("github")}
              className="px-8 py-3.5 font-medium rounded-xl border border-white/10 bg-white/[0.03] hover:bg-white/[0.06] hover:border-white/20 transition-all duration-300 flex items-center gap-2 justify-center"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z" /></svg>
              GitHub로 시작하기
            </button>
          </div>

          <p className="fade-in-up fade-in-up-delay-5 text-sm text-gray-600">
            가입 시 <span className="text-gray-400">5시간</span> 무료 크레딧 제공 &middot; 카드 등록 불필요
          </p>
        </div>
      </section>

      {/* ── Timeline Demo ── */}
      <section className="relative px-6 pb-24">
        <div className="max-w-3xl mx-auto fade-in-up fade-in-up-delay-5">
          <Waveform />
          <div className="mt-6">
            <Timeline />
          </div>
        </div>
      </section>

      {/* ── Divider ── */}
      <div className="max-w-6xl mx-auto px-6">
        <div className="h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
      </div>

      {/* ── How it works ── */}
      <section className="py-24 px-6">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-16">
            <p className="text-sm font-medium text-cyan-400 mb-3 tracking-wider uppercase">How it works</p>
            <h2 className="text-3xl sm:text-4xl font-bold">세 단계면 충분합니다</h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <StepCard
              step={1}
              icon={
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-cyan-400">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
                </svg>
              }
              title="영상 업로드"
              desc="영상 파일을 업로드하면 AI가 음성을 분석하고, 자막을 자동으로 생성합니다. LLM이 전사 결과를 다듬어 정확도를 높입니다."
            />
            <StepCard
              step={2}
              icon={
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-violet-400">
                  <rect x="2" y="2" width="20" height="20" rx="2" /><path d="M7 2v20" /><path d="M17 2v20" /><path d="M2 12h20" /><path d="M2 7h5" /><path d="M2 17h5" /><path d="M17 17h5" /><path d="M17 7h5" />
                </svg>
              }
              title="AI 편집 분석"
              desc="더듬, 반복, 침묵, 불필요한 발언을 AI가 감지합니다. 스토리 흐름을 파악해 맥락에 맞는 편집 포인트를 제안합니다."
            />
            <StepCard
              step={3}
              icon={
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-emerald-400">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" />
                </svg>
              }
              title="결과 다운로드"
              desc="Final Cut Pro용 FCPXML, 자막 SRT, 상세 편집 보고서를 다운로드하세요. 타임라인을 열면 바로 편집을 이어갈 수 있습니다."
            />
          </div>
        </div>
      </section>

      {/* ── Divider ── */}
      <div className="max-w-6xl mx-auto px-6">
        <div className="h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
      </div>

      {/* ── Features ── */}
      <section className="py-24 px-6">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-16">
            <p className="text-sm font-medium text-violet-400 mb-3 tracking-wider uppercase">Features</p>
            <h2 className="text-3xl sm:text-4xl font-bold">편집자를 위한 기능들</h2>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
            <FeatureCard
              icon={
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-cyan-400">
                  <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" /><line x1="12" y1="19" x2="12" y2="23" /><line x1="8" y1="23" x2="16" y2="23" />
                </svg>
              }
              title="자동 자막 생성"
              desc="Whisper 기반 음성 인식 + LLM 후처리로 높은 정확도의 자막을 생성합니다."
            />
            <FeatureCard
              icon={
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-red-400">
                  <circle cx="12" cy="12" r="10" /><line x1="4.93" y1="4.93" x2="19.07" y2="19.07" />
                </svg>
              }
              title="불필요 구간 감지"
              desc="더듬, 반복, 침묵, 미완성 문장을 자동으로 찾아 컷 포인트를 표시합니다."
            />
            <FeatureCard
              icon={
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-violet-400">
                  <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" /><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
                </svg>
              }
              title="스토리 흐름 분석"
              desc="전체 스크립트를 분석해 맥락을 파악하고, 흐름에 맞는 편집을 제안합니다."
            />
            <FeatureCard
              icon={
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-emerald-400">
                  <rect x="2" y="3" width="20" height="14" rx="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
                </svg>
              }
              title="FCPXML 타임라인"
              desc="Final Cut Pro에서 바로 열 수 있는 편집 프로젝트를 자동 생성합니다."
            />
            <FeatureCard
              icon={
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-amber-400">
                  <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z" /><circle cx="12" cy="13" r="3" />
                </svg>
              }
              title="멀티캠 지원"
              desc="여러 카메라 소스를 동시에 관리하고, 동기화된 편집을 지원합니다."
            />
            <FeatureCard
              icon={
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-pink-400">
                  <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22" />
                </svg>
              }
              title="편집 보고서"
              desc="얼마나 절약됐는지, 어떤 구간이 컷됐는지 상세한 마크다운 보고서를 제공합니다."
            />
          </div>
        </div>
      </section>

      {/* ── Divider ── */}
      <div className="max-w-6xl mx-auto px-6">
        <div className="h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
      </div>

      {/* ── Stats ── */}
      <section className="py-24 px-6">
        <div className="max-w-4xl mx-auto">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8 text-center">
            {[
              { value: "10x", label: "편집 속도 향상" },
              { value: "30%", label: "평균 절약 구간" },
              { value: "3분", label: "평균 처리 시간" },
              { value: "99%", label: "자막 정확도" },
            ].map((stat) => (
              <div key={stat.label}>
                <p className="text-3xl sm:text-4xl font-bold gradient-text mb-2">{stat.value}</p>
                <p className="text-sm text-gray-500">{stat.label}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Divider ── */}
      <div className="max-w-6xl mx-auto px-6">
        <div className="h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
      </div>

      {/* ── Bottom CTA ── */}
      <section className="relative py-32 px-6 overflow-hidden">
        <div className="absolute inset-0">
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[300px] bg-cyan-500/[0.06] rounded-full blur-[100px]" />
          <div className="absolute top-1/2 left-1/3 -translate-y-1/2 w-[300px] h-[200px] bg-violet-500/[0.04] rounded-full blur-[80px]" />
        </div>

        <div className="relative max-w-2xl mx-auto text-center">
          <h2 className="text-3xl sm:text-4xl font-bold mb-4">
            편집 시간을 <span className="gradient-text">절약</span>하세요
          </h2>
          <p className="text-gray-400 mb-10 leading-relaxed">
            반복적인 초벌 편집은 AI에게 맡기고,
            <br />
            크리에이티브에 집중하세요.
          </p>

          <button
            onClick={() => handleLogin("google")}
            className="group relative px-10 py-4 font-semibold rounded-xl overflow-hidden transition-all duration-300 hover:shadow-[0_0_40px_rgba(6,182,212,0.3)]"
          >
            <div className="absolute inset-0 bg-gradient-to-r from-cyan-500 to-violet-500" />
            <div className="absolute inset-0 bg-gradient-to-r from-cyan-400 to-violet-400 opacity-0 group-hover:opacity-100 transition-opacity" />
            <span className="relative text-white text-lg">무료로 시작하기</span>
          </button>

          <p className="mt-6 text-sm text-gray-600">
            5시간 무료 &middot; 신용카드 불필요 &middot; 즉시 시작
          </p>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="border-t border-white/[0.04] py-12 px-6">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2 text-gray-500">
            <Image src="/logo.png" alt="어검" width={20} height={20} className="rounded opacity-60" />
            <span className="text-sm">&copy; 2025 어검 (eogum)</span>
          </div>
          <div className="flex gap-6 text-sm text-gray-600">
            <span>Auto Video Edit</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
