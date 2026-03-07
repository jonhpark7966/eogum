# 어검 (Eogum) — 아키텍처 & 워크플로우 문서

> 최종 갱신: 2026-03-07
> 대상: avid CLI + FastAPI backend + Next.js frontend

---

## 목차

1. [시스템 구성도](#1-시스템-구성도)
2. [워크플로우 상세](#2-워크플로우-상세)
3. [DB 스키마](#3-db-스키마)
4. [API 엔드포인트 맵](#4-api-엔드포인트-맵)
5. [파일 & 스토리지 경로](#5-파일--스토리지-경로)
6. [문제점 분석](#6-문제점-분석)
7. [수정 우선순위](#7-수정-우선순위)

---

## 1. 시스템 구성도

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          사용자 브라우저                                 │
│  Next.js 16 + React 19 + Tailwind v4 (Vercel: eogum.sudoremove.com)    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌────────────────┐  │
│  │  Landing  │ │Dashboard │ │New Project│ │Project │ │ Review Page    │  │
│  │  page.tsx │ │ page.tsx │ │ page.tsx  │ │page.tsx│ │ review/page.tsx│  │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘ └────────────────┘  │
│         │             │           │           │              │          │
│         └─────────────┴───────────┴───────────┴──────────────┘          │
│                               │ lib/api.ts                              │
│                    fetch(Bearer JWT) + R2 presigned PUT                  │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │   Cloudflare R2          │
                    │   (presigned URL 직접)   │
                    │   sources/{uuid}.ext     │
                    │   results/{pid}/{files}  │
                    └─────────────────────────┘
                                 │
┌────────────────────────────────┴────────────────────────────────────────┐
│                FastAPI Backend (:8000, 홈서버)                           │
│                api-eogum.sudoremove.com (Cloudflare Tunnel)             │
│                                                                         │
│  ┌─── Routes ───────────────────────────────────────────────────┐       │
│  │ upload.py   projects.py  downloads.py  credits.py  eval.py   │       │
│  └──────────────────────┬───────────────────────────────────────┘       │
│                         │                                               │
│  ┌─── Services ─────────┴───────────────────────────────────────┐       │
│  │ job_runner.py → avid.py → [subprocess] → avid CLI            │       │
│  │      │                                     │                 │       │
│  │      ├─ credit.py (hold → confirm/release) │                 │       │
│  │      ├─ r2.py (download/upload)            ▼                 │       │
│  │      └─ email.py (Resend)         Chalna STT (:7861)         │       │
│  └──────────────────────────────────────────────────────────────┘       │
│                                                                         │
│  External: Supabase (DB+Auth) │ Cloudflare R2 │ Resend (Email)         │
└─────────────────────────────────────────────────────────────────────────┘
```

**Tech Stack:**

| Layer | Technology | Hosting |
|-------|-----------|---------|
| Frontend | Next.js 16 + React 19 + Tailwind v4 | Vercel |
| Auth | Supabase Auth (Google/GitHub OAuth) | Supabase Cloud |
| Database | PostgreSQL | Supabase Cloud |
| API Server | FastAPI (Python 3.12) | 홈 서버 |
| Processing | avid CLI (auto-video-edit) | 홈 서버 |
| STT | Chalna (RTX 5090) | 홈 서버 (GPU) |
| AI | Codex CLI (transcript-overview) | 홈 서버 |
| Storage | Cloudflare R2 | Cloudflare |
| Email | Resend | SaaS |

---

## 2. 워크플로우 상세

### 2.1 회원가입/로그인

```
사용자 → "Google/GitHub 로그인" 클릭
         │
         ▼
[page.tsx] supabase.auth.signInWithOAuth({provider, redirectTo: '/auth/callback'})
         │
         ▼
OAuth 프로바이더 → 인증 → /auth/callback?code=xxx
         │
         ▼
[auth/callback/route.ts] supabase.auth.exchangeCodeForSession(code)
         │                → cookie에 access_token 저장
         ▼
redirect → /dashboard
```

**관련 파일:** `page.tsx`, `auth/callback/route.ts`, `lib/supabase/middleware.ts`
**DB:** `auth.users` (Supabase 관리)

---

### 2.2 프로젝트 생성 (업로드)

```
┌─ STEP 1: 파일 선택 & 메타데이터 ──────────────────────────────────────┐
│                                                                        │
│ [dashboard/new/page.tsx]                                               │
│  파일 선택 → getVideoDuration() → <video> 태그로 duration 추출          │
│  name, cutType(subtitle_cut|podcast_cut), language(ko|en|ja)           │
│  context (선택사항, 전문용어 힌트)                                       │
│                                                                        │
├─ STEP 2: Multipart Upload ─────────────────────────────────────────────┤
│                                                                        │
│ [lib/api.ts: uploadFile()]                                             │
│  POST /upload/multipart/initiate                                       │
│    → [upload.py] → r2.create_multipart_upload()                        │
│    → r2_key = "sources/{uuid}.{ext}", part_size = 100MB                │
│    ← {upload_id, r2_key, part_urls[]}                                  │
│                                                                        │
│  PUT {part_url} × N (3개씩 동시, chunk = file.slice)                   │
│    → R2에 직접 업로드, ETag 수집                                        │
│    → progress: 5% → 90%                                                │
│                                                                        │
│  POST /upload/multipart/complete                                       │
│    → [upload.py] → r2.complete_multipart_upload()                      │
│    ← {r2_key}                                                          │
│                                                                        │
├─ STEP 3: 프로젝트 생성 ───────────────────────────────────────────────┤
│                                                                        │
│  POST /projects                                                        │
│    → [projects.py: create_project()]                                   │
│      ├─ credit.get_balance() → available >= duration ?                 │
│      ├─ DB INSERT → projects (status: "queued")                        │
│      ├─ job_runner.enqueue(project_id) → deque에 추가                  │
│      └─ ← ProjectResponse                                             │
│                                                                        │
│  → progress: 95% → redirect → /projects/{id}                          │
└────────────────────────────────────────────────────────────────────────┘
```

**DB 변경:** `projects` INSERT (status="queued")
**R2:** `sources/{uuid}.{ext}` 생성

---

### 2.3 프로세싱 파이프라인 (핵심)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ [job_runner.py: _process_project(project_id)]                            │
│                                                                          │
│  ① Load project + user email                                             │
│     DB: SELECT * FROM projects WHERE id=?                                │
│     DB: SELECT FROM profiles + auth.admin.get_user_by_id()               │
│                                                                          │
│  ② Update status + create job                                            │
│     DB: UPDATE projects SET status="processing"                          │
│     DB: INSERT jobs (status="running", type=cut_type)                    │
│                                                                          │
│  ③ Hold credits                     ┌────────────────────────────────┐   │
│     DB: SELECT credits              │ credit.py: hold_credits()      │   │
│     DB: UPDATE credits              │  → read balance                │   │
│         SET held += duration        │  → check available >= duration │   │
│     DB: INSERT credit_transactions  │  → update held_seconds        │   │
│         (type="hold")               │  → insert transaction         │   │
│                                     └────────────────────────────────┘   │
│  ④ Download source from R2 → /tmp/eogum/{pid}/source.ext   [progress 5%]│
│                                                                          │
│  ⑤ Download extra_sources (multicam, 있으면)                [progress 10%]│
│                                                                          │
│  ⑥ avid CLI: transcribe                                    [progress 30%]│
│     cmd: python -m avid.cli transcribe {source}                          │
│          -l {lang} --chalna-url http://localhost:7861 --llm-refine       │
│          [-d output_dir] [--context "전문용어..."]                        │
│     → Chalna STT → SRT 파일 생성                                         │
│     → stdout에서 "완료: /path.srt" 파싱 (or glob fallback)               │
│     timeout: 7200s (2h)                                                  │
│                                                                          │
│  ⑦ avid CLI: transcript-overview                            [progress 50%]│
│     cmd: python -m avid.cli transcript-overview {srt} [-o storyline.json]│
│     → Codex LLM → storyline.json (내러티브 구조 분석)                     │
│     timeout: 1800s (30m)                                                 │
│                                                                          │
│  ⑧ avid CLI: subtitle-cut (or podcast-cut)                  [progress 75%]│
│     cmd: python -m avid.cli subtitle-cut {source} --srt {srt}           │
│          --context {storyline.json} -d {output_dir} --final              │
│          [--extra-source path1 --extra-source path2]                     │
│     → .fcpxml, .srt, .report.md, .avid.json 생성                        │
│     → _collect_results(): glob으로 결과 파일 수집                         │
│     timeout: 1800s (30m)                                                 │
│                                                                          │
│  ⑨ ffmpeg: preview 생성 (실패해도 job 계속)                              │
│     cmd: ffmpeg -i source -vf scale=-2:480 -crf 28 preview.mp4          │
│     timeout: 600s                                                        │
│                                                                          │
│  ⑩ Upload results to R2                                     [progress 85%]│
│     r2_keys = {fcpxml, srt, report, project_json, storyline, preview}    │
│     → r2.upload_file() for each                                          │
│                                                                          │
│  ⑪ Save edit report                                                      │
│     report.md → regex 파싱("합계" 행) → cut_duration 추출                 │
│     DB: INSERT edit_reports                                              │
│                                                                          │
│  ⑫ Confirm credit usage                                                  │
│     DB: UPDATE credits SET balance -= duration, held -= duration         │
│     DB: INSERT credit_transactions (type="usage")                        │
│                                                                          │
│  ⑬ Mark complete                                                         │
│     DB: UPDATE jobs SET status="completed", progress=100, result_r2_keys │
│     DB: UPDATE projects SET status="completed"                           │
│                                                                          │
│  ⑭ Send completion email (Resend, 미설정 시 skip)                        │
│                                                                          │
│  ⑮ Cleanup: rm -rf /tmp/eogum/{pid}/                                     │
│                                                                          │
│  ──── 실패 시 (exception handler) ────                                    │
│  → credit.release_hold() → held -= duration                              │
│  → DB: jobs.status="failed", error_message                               │
│  → DB: projects.status="failed"                                          │
│  → email.send_failure_email()                                            │
│  → cleanup                                                               │
└──────────────────────────────────────────────────────────────────────────┘
```

**Frontend 폴링 (처리 중):**
```
[projects/[id]/page.tsx]
  status가 "queued" or "processing"이면:
    setInterval(GET /projects/{id}, 5000)  → job.progress 기반 프로그레스 바
    completed/failed 되면 폴링 중지
```

---

### 2.4 결과 확인 & 다운로드

```
[projects/[id]/page.tsx — completed 상태]

  결과 표시:
    edit_report → cut_percentage, cut_duration, report_markdown

  다운로드 버튼 클릭:
    handleDownload(fileType) → GET /projects/{id}/download/{type}
      [downloads.py]
        type="source" → project.source_r2_key → presigned URL (1h)
        나머지      → job.result_r2_keys[type] → presigned URL (1h)
    → window.open(download_url)

  지원 타입: source, fcpxml, srt, report, project_json, storyline
  (주의: preview는 download type에 없음, video-url 스트리밍만 가능)
```

---

### 2.5 세그먼트 리뷰 (평가)

```
[projects/[id]/review/page.tsx]

  ① 데이터 로드:
     GET /segments    → avid.json에서 세그먼트 + AI 판단 추출 (overlap 매칭)
     GET /video-url   → preview or source 스트리밍 URL
     GET /evaluation  → 기존 평가 (없으면 null)

  ② UI:
     비디오 플레이어 (sticky) + 세그먼트 목록 (AI cut=빨강, keep=초록)
     각 세그먼트에 Human Decision (keep/cut + reason + note)

  ③ 저장:
     POST /evaluation → check-then-insert upsert → evaluations 테이블

  ④ 리포트 (on-demand):
     GET /eval-report → AI vs Human 비교 → confusion matrix + F1
```

---

### 2.6 멀티캠 & 재시도

```
[멀티캠 추가 — projects/[id]/page.tsx]
  파일 추가 → 순차 업로드 → PUT /extra-sources → POST /multicam
  → 기존 jobs/reports 삭제 → status="queued" → 2.3 파이프라인 재실행

[재시도 — dashboard 또는 projects/[id]]
  POST /projects/{id}/retry
  → status=="failed" 확인 → 크레딧 확인 → jobs/reports 삭제
  → status="queued" → 2.3 파이프라인 재실행
```

---

### 2.7 서버 시작 & 복구

```
[main.py: lifespan()]
  SELECT * FROM projects WHERE status IN ("queued", "processing")
  각각: DELETE jobs, DELETE reports, SET status="queued", enqueue()

  빠지는 케이스:
  - status="failed" + job 0개 → 복구 안 됨
  - worker가 이미 "failed" 마킹한 후 서버 죽음 → 복구 안 됨
```

---

## 3. DB 스키마

```
projects ─────────────────────────────────────────
  id                    uuid PK
  user_id               uuid FK→auth.users
  name                  text
  status                text  (queued|processing|completed|failed)
  cut_type              text  (subtitle_cut|podcast_cut)
  language              text  (ko|en|ja)
  source_r2_key         text
  source_filename       text
  source_duration_seconds int
  source_size_bytes     bigint
  extra_sources         jsonb [{r2_key, filename, size_bytes}]
  settings              jsonb {transcription_context?}
  created_at, updated_at timestamptz

jobs ─────────────────────────────────────────────
  id                    uuid PK
  project_id            uuid FK→projects
  user_id               uuid FK→auth.users
  type                  text  (subtitle_cut|podcast_cut)
  status                text  (running|completed|failed)
  progress              int   (0-100)
  error_message         text  nullable
  result_r2_keys        jsonb {fcpxml?,srt?,report?,project_json?,storyline?,preview?}
  started_at, completed_at, created_at timestamptz

edit_reports ──────────────────────────────────────
  id                    uuid PK
  project_id            uuid FK→projects
  total_duration_seconds int
  cut_duration_seconds  int
  cut_percentage        float
  edit_summary          jsonb
  report_markdown       text
  created_at, updated_at timestamptz

credits ───────────────────────────────────────────
  user_id               uuid PK FK→auth.users
  balance_seconds       int
  held_seconds          int
  total_granted_seconds int
  updated_at            timestamptz

credit_transactions ───────────────────────────────
  id                    uuid PK
  user_id               uuid FK→auth.users
  job_id                uuid FK→jobs nullable
  amount_seconds        int
  type                  text  (hold|usage|hold_release)
  description           text
  created_at            timestamptz

evaluations ───────────────────────────────────────
  id                    uuid PK
  project_id            uuid FK→projects
  evaluator_id          uuid FK→auth.users
  segments              jsonb [{index, start_ms, end_ms, text, ai:{}, human:{}}]
  version               text
  avid_version          text nullable
  eogum_version         text nullable
  created_at, updated_at timestamptz
```

---

## 4. API 엔드포인트 맵

모든 엔드포인트 prefix: `/api/v1`

| Method | Path | Handler | 역할 |
|--------|------|---------|------|
| GET | `/health` | health.py | 서버 상태 |
| POST | `/upload/presign` | upload.py | 단건 presigned URL |
| POST | `/upload/multipart/initiate` | upload.py | 멀티파트 시작 |
| POST | `/upload/multipart/complete` | upload.py | 멀티파트 완료 |
| **POST** | **`/projects`** | projects.py | **프로젝트 생성 + 큐** |
| GET | `/projects` | projects.py | 목록 |
| GET | `/projects/{id}` | projects.py | 상세 (jobs+report) |
| POST | `/projects/{id}/retry` | projects.py | 재시도 |
| PUT | `/projects/{id}/extra-sources` | projects.py | 멀티캠 소스 등록 |
| POST | `/projects/{id}/multicam` | projects.py | 멀티캠 재처리 |
| DELETE | `/projects/{id}` | projects.py | 삭제 |
| GET | `/credits` | credits.py | 잔액 |
| GET | `/credits/transactions` | credits.py | 거래 내역 |
| GET | `/projects/{id}/download/{type}` | downloads.py | 결과물 URL |
| GET | `/projects/{id}/download/extra-source/{idx}` | downloads.py | 멀티캠 소스 URL |
| GET | `/projects/{id}/segments` | evaluations.py | 세그먼트+AI판단 |
| GET | `/projects/{id}/video-url` | evaluations.py | 프리뷰 스트림 URL |
| GET | `/projects/{id}/evaluation` | evaluations.py | 기존 평가 |
| POST | `/projects/{id}/evaluation` | evaluations.py | 평가 저장 |
| GET | `/projects/{id}/eval-report` | evaluations.py | AI vs Human 리포트 |

---

## 5. 파일 & 스토리지 경로

### R2 (Cloudflare)
```
eogum/
├── sources/{uuid}.mp4              ← 사용자 업로드 원본
└── results/{project_id}/
    ├── source.final.fcpxml         ← FCP XML
    ├── source.final.srt            ← 자막
    ├── source.report.md            ← 편집 리포트
    ├── source.avid.json            ← avid 프로젝트
    ├── storyline.json              ← 스토리 구조
    └── preview.mp4                 ← 480p 프리뷰
```

### Local Temp (처리 중에만 존재)
```
/tmp/eogum/{project_id}/
├── source.mp4                      ← R2에서 다운로드
├── extra_0.mp4                     ← 멀티캠 (있으면)
├── source.srt                      ← avid transcribe 출력
└── output/
    ├── storyline.json              ← transcript-overview
    ├── source.final.fcpxml         ← subtitle-cut/podcast-cut
    ├── source.final.srt
    ├── source.report.md
    ├── source.avid.json
    └── preview.mp4                 ← ffmpeg
```

---

## 6. 문제점 분석

### 요약 매트릭스

Codex CLI (gpt-5) 리뷰 2회 실행 결과: **Backend FAIL / Frontend FAIL**

| 심각도 | 건수 | 설명 |
|--------|------|------|
| CRITICAL | 3 | 데이터 유실/무결성 위험 |
| MAJOR | 16 | 안정성/UX 문제 |
| MINOR | 4 | 개선 사항 |

### CRITICAL — 데이터 유실/무결성

#### C1. In-memory job queue 유실
- **위치:** `job_runner.py:14` — `_queue: deque[str] = deque()`
- **문제:** 서버 재시작 시 큐 소멸. 실제로 프로젝트 `11d144f6`이 job 0개, status=failed
- **재현:** 프로젝트 enqueue → 서버 kill → 재시작 → startup recovery는 "failed"를 무시
- **해결:** projects.status="queued"를 큐로 사용. worker가 직접 DB에서 "queued" 프로젝트를 poll

#### C2. 크레딧 연산 비원자적
- **위치:** `credit.py:18-41` — `hold_credits()`
- **문제:** `get_balance()` (SELECT) → `update held_seconds` (UPDATE). 두 API 요청이 동시에 오면 둘 다 "잔액 충분" 판단 후 각각 hold → 초과 사용
- **코드:**
  ```python
  balance = get_balance(user_id)                    # ← 시점 A: balance=18000
  if balance["available_seconds"] < seconds: raise  # 통과
  db.table("credits").update({
      "held_seconds": balance["held_seconds"] + seconds,  # ← 시점 B: 다른 요청이 이미 hold 했을 수 있음
  }).eq("user_id", user_id).execute()
  ```
- **해결:** Supabase RPC (PostgreSQL function)로 SELECT + CHECK + UPDATE를 단일 트랜잭션 처리

#### ~~C3. Evaluation upsert 비원자적~~ ✅ 해결됨 (2026-03-07)
- **위치:** `evaluations.py`
- DB에 이미 UNIQUE(project_id, evaluator_id) 인덱스 존재. 코드를 `.upsert(on_conflict=...)` 로 변경 완료

---

### MAJOR — 안정성/UX

#### M1. retry/multicam이 active worker 미체크
- **위치:** `projects.py:71-104`, `projects.py:130-168`
- **문제:** retry가 old job DELETE + status="queued" + enqueue. 이때 worker가 동일 프로젝트를 처리 중이면 두 worker가 동시에 실행
- **해결:** enqueue 전에 `_queue`에 같은 project가 있는지, `_running`인 project와 같은지 체크. 또는 status를 CAS(Compare-And-Swap)로 변경

#### M2. Startup recovery가 failed(job 0개) 무시
- **위치:** `main.py:22-26`
- **문제:** `status IN ("queued", "processing")`만 복구. status="failed"이면서 job이 0개인 프로젝트(에러가 job 생성 전에 발생)는 영원히 stuck
- **해결:** `status="failed"` AND job 0개인 프로젝트도 "queued"로 복구

#### ~~M3. 다운로드 try-catch 없음~~ ✅ 해결됨
- handleDownload, handleDownloadExtraSource 모두 try-catch 적용 완료

#### M4. fetch timeout 없음
- **위치:** `lib/api.ts:3-24` — `apiFetch()`
- **문제:** AbortController 미사용. 서버 다운 시 fetch가 무한 대기
- **해결:** AbortController + 30초 timeout

#### ~~M5. alert() 사용~~ ✅ 해결됨
- 인라인 에러 배너 (setSaveError) 적용 완료

#### ~~M6. saving 플래그 미리셋~~ ✅ 해결됨
- early return 전에 setSaving(false) 호출하도록 수정 완료

#### ~~M7. getEvaluation 에러 전부 null~~ ✅ 해결됨
- 404만 null 반환, 나머지 rethrow 적용 완료

#### ~~M8. 업로드 실패 시 progress 미리셋~~ ✅ 해결됨
- catch에 `setUploadProgress(0)` 적용 완료

#### M9. duration 검증 없음
- **위치:** `dashboard/new/page.tsx:61`, `schemas.py:36`
- **문제:** 0초 or 극단적 길이(24h+) 허용 → 크레딧 0차감 or 처리 불가능
- **해결:** Frontend: duration > 0 && < 86400 체크. Backend: `source_duration_seconds: int = Field(gt=0, le=86400)`

#### M10. cut_type/settings 미검증
- **위치:** `schemas.py:32,38`
- **문제:** `cut_type: str` → 아무 문자열 허용. `settings: dict = {}` → 임의 JSON
- **해결:** `cut_type: Literal["subtitle_cut", "podcast_cut"]`, settings 스키마 정의

#### M11. SRT 경로 파싱 brittle
- **위치:** `avid.py:62-68`
- **문제:** stdout에서 "완료" + ".srt" 패턴 regex → avid 출력 형식 변경 시 깨짐
- **해결:** avid CLI에 `--json-output` 옵션 추가, structured output 반환

#### M12. glob 결과 순서 의존
- **위치:** `avid.py:178-195` — `_collect_results()`
- **문제:** `glob("{stem}*.fcpxml")[0]` → 여러 파일 있으면 잘못된 파일 선택
- **해결:** `.final.` 접미사 우선 or 최신 mtime 선택

#### M13. 리포트 파싱 regex 취약
- **위치:** `job_runner.py:204-220`
- **문제:** `"합계"` 한국어 패턴에 의존. 영어 리포트 시 cut_duration=0
- **해결:** avid.json의 structured data에서 cut_duration 직접 파싱

#### ~~M14. preview 다운로드 불가~~ ✅ 해결됨
- `_DOWNLOAD_TYPES`에 preview 추가 완료

#### M15. 멀티파트 실패 시 retry/abort 없음
- **위치:** `lib/api.ts:336-357` — `uploadFile()`
- **문제:** 파트 실패 시 throw만. R2에 미완성 multipart 잔류
- **해결:** part retry (3회) + 전체 실패 시 r2.abort_multipart_upload() 호출

#### M16. 폴링 에러 시 backoff 없음
- **위치:** `dashboard/page.tsx:256-260`
- **문제:** 서버 다운 시 10초마다 실패 요청 반복, 에러 배너만 표시
- **해결:** exponential backoff (10s → 20s → 40s, max 60s)

---

### MINOR

| ID | 위치 | 문제 |
|----|------|------|
| ~~m1~~ | ~~`config.py:25`~~ | ~~✅ 해결됨: `chalna_url` 기본값이 `"http://localhost:7861"`로 수정 완료~~ |
| m2 | `evaluations.py:90-110` | segment-decision 매칭 O(n×m). 대형 영상에서 느림 |
| m3 | `review/page.tsx` | 키보드 단축키 없음 (100+ 세그먼트를 마우스로만) |
| m4 | `projects/[id]/page.tsx` | 멀티캠 크레딧 비용 구체적 미표시 |

---

## 7. 수정 우선순위

### Phase 1 — 즉시 (각 5-15분, 코드 수정만)

| # | ID | 작업 | 파일 | 상태 |
|---|-----|------|------|------|
| 1 | M3 | 다운로드 try-catch | `projects/[id]/page.tsx` | ✅ |
| 2 | M5+M6 | alert()→인라인에러 + saving리셋 | `review/page.tsx` | ✅ |
| 3 | M8 | 업로드 실패 시 progress=0 | `new/page.tsx` | ✅ |
| 4 | M10 | cut_type Literal 타입 | `schemas.py` | |
| 5 | M14 | download types에 preview | `downloads.py` | ✅ |
| 6 | m1 | chalna_url 기본값 7861 | `config.py` | ✅ |
| 7 | M7 | getEvaluation 404만 null | `api.ts` | ✅ |
| 8 | M9 | duration 검증 추가 | `new/page.tsx` + `schemas.py` | |

> **추가 수정 (2026-03-07):**
> - C3: evaluation upsert → `.upsert(on_conflict=...)` 원자적 처리로 변경
> - `evaluations.py`: eogum_version 경로 수정 (avid_cli_path 기반 → `__file__` 기반)
> - `projects/[id]/page.tsx`: 다운로드 버튼에 report, project_json 추가
> - `projects/[id]/page.tsx`: 미사용 STATUS_CONFIG 항목(created, uploading) 제거
> - `projects/[id]/page.tsx`: JOB_TYPE_LABELS에서 실제 생성되지 않는 job type(transcribe, transcript_overview) 제거
> - `dashboard/page.tsx`: 미사용 STATUS_CONFIG 항목(created, uploading) 제거

### Phase 2 — 단기 (각 30분-2시간, 설계 변경)

| # | ID | 작업 | 파일 |
|---|-----|------|------|
| 1 | C1 | DB 기반 job queue | `job_runner.py`, `main.py` |
| 2 | M1 | retry시 active worker 체크 | `projects.py` |
| 3 | M4 | apiFetch AbortController | `api.ts` |
| 4 | M16 | 폴링 exponential backoff | `dashboard/page.tsx` 등 |
| 5 | M2 | startup recovery 보강 | `main.py` |
| 6 | M15 | 멀티파트 part retry + abort | `api.ts` |

### Phase 3 — 중기 (아키텍처 변경)

| # | ID | 작업 |
|---|-----|------|
| 1 | C2 | 크레딧 PostgreSQL function |
| ~~2~~ | ~~C3~~ | ~~Evaluation UNIQUE + upsert~~ ✅ 해결됨 |
| 3 | M11-13 | avid CLI structured output |
| 4 | — | Rate limiting 미들웨어 |
| 5 | — | Supabase Realtime (폴링 대체) |

---

## Appendix: Codex Review 결과

2026-02-22 실행. Backend/Frontend 양쪽 **FAIL**.

리뷰 로그: `codex-review-logs/review_20260222_180344.json` (backend), `review_20260222_180410.json` (frontend)
