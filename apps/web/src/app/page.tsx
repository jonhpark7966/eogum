"use client";

export const dynamic = "force-dynamic";

import { createClient } from "@/lib/supabase/client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

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
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-pulse text-gray-400">Loading...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      <div className="max-w-4xl mx-auto px-6 py-24">
        {/* Hero */}
        <div className="text-center mb-16">
          <h1 className="text-5xl font-bold mb-4">어검</h1>
          <p className="text-xl text-gray-400 mb-2">Auto Video Edit</p>
          <p className="text-lg text-gray-500 max-w-2xl mx-auto">
            영상을 올리면 자동으로 자막과 초벌 편집 프로젝트가 만들어집니다.
            <br />
            불필요한 구간을 감지하고 Final Cut Pro 타임라인을 생성합니다.
          </p>
        </div>

        {/* Features */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mb-16">
          <div className="bg-gray-900 rounded-lg p-6">
            <h3 className="text-lg font-semibold mb-2">자동 자막 생성</h3>
            <p className="text-gray-400 text-sm">
              음성 인식으로 자막을 자동 생성하고, 편집에 맞게 조정합니다.
            </p>
          </div>
          <div className="bg-gray-900 rounded-lg p-6">
            <h3 className="text-lg font-semibold mb-2">AI 편집 감지</h3>
            <p className="text-gray-400 text-sm">
              중복, 불필요한 발언, 침묵 구간을 AI가 감지하여 컷 포인트를
              제안합니다.
            </p>
          </div>
          <div className="bg-gray-900 rounded-lg p-6">
            <h3 className="text-lg font-semibold mb-2">FCPXML 프로젝트</h3>
            <p className="text-gray-400 text-sm">
              Final Cut Pro에서 바로 열 수 있는 초벌 편집 프로젝트를 생성합니다.
            </p>
          </div>
        </div>

        {/* CTA */}
        <div className="text-center space-y-4">
          <p className="text-gray-400 mb-6">
            가입 시 <strong className="text-white">5시간</strong> 무료 크레딧
            제공
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <button
              onClick={() => handleLogin("google")}
              className="px-8 py-3 bg-white text-black font-medium rounded-lg hover:bg-gray-200 transition"
            >
              Google로 시작하기
            </button>
            <button
              onClick={() => handleLogin("github")}
              className="px-8 py-3 bg-gray-800 text-white font-medium rounded-lg hover:bg-gray-700 transition border border-gray-700"
            >
              GitHub로 시작하기
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
