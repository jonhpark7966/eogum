# 어검 (eogum) - Architecture

Auto Video Edit 온라인 서비스. 영상 소스를 업로드하면 자동으로 자막과 초벌 편집 프로젝트(FCPXML)가 생성된다.

## System Overview

```
┌─ Vercel ─────────────────────────────────┐
│  Next.js (eogum.sudoremove.com)          │
│  ├─ 랜딩/마케팅                          │
│  ├─ Supabase Auth (로그인/가입)          │
│  ├─ 대시보드 (프로젝트 목록)             │
│  ├─ 업로드 UI → R2 presigned URL         │
│  ├─ 설정 UI (cut type, language)         │
│  └─ 결과 페이지 (보고서 + 다운로드)      │
└──────────────┬───────────────────────────┘
               │ HTTPS API calls
┌──────────────▼───────────────────────────┐
│  홈 서버 (api-eogum.sudoremove.com)      │
│  Cloudflare Tunnel                       │
│  ┌───────────────────────────────────┐   │
│  │  FastAPI (어검 API)               │   │
│  │  ├─ Supabase JWT 검증             │   │
│  │  ├─ 크레딧 관리 (hold/deduct)     │   │
│  │  ├─ 프로젝트 CRUD                 │   │
│  │  ├─ R2 presigned URL 생성         │   │
│  │  ├─ Job Runner (Sequential)       │   │
│  │  └─ 이메일 알림 (완료/실패)       │   │
│  └───────────────────────────────────┘   │
│  ┌───────────────────────────────────┐   │
│  │  avid (auto-video-edit) 엔진      │   │
│  │  ├─ Chalna STT (localhost)        │   │
│  │  ├─ transcript-overview (Pass 1)  │   │
│  │  ├─ subtitle-cut / podcast-cut    │   │
│  │  ├─ FCPXML export                 │   │
│  │  ├─ claude CLI (subprocess)       │   │
│  │  ├─ codex CLI (subprocess)        │   │
│  │  └─ ffmpeg                        │   │
│  └───────────────────────────────────┘   │
└──────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────┐
│  External Services                        │
│  ├─ Supabase (Auth + PostgreSQL)         │
│  │   ├─ profiles, credits                │
│  │   ├─ projects, jobs, edit_reports     │
│  │   └─ RLS policies per user            │
│  ├─ Cloudflare R2 (Object Storage)       │
│  │   ├─ sources/ (원본 영상)             │
│  │   ├─ results/ (fcpxml, srt, report)   │
│  │   └─ Lifecycle: 1년 보관 후 삭제      │
│  └─ Resend / SES (이메일 알림)           │
└──────────────────────────────────────────┘
```

## Tech Stack

| Layer | Technology | Hosting |
|-------|-----------|---------|
| Frontend | Next.js 15 + React | Vercel |
| Auth | Supabase Auth | Supabase Cloud |
| Database | PostgreSQL | Supabase Cloud |
| API Server | FastAPI (Python) | 홈 서버 |
| Processing | avid (auto-video-edit) | 홈 서버 |
| STT | Chalna (자체 서비스) | 홈 서버 (GPU) |
| AI Analysis | claude CLI + codex CLI (subprocess) | 홈 서버 |
| Storage | Cloudflare R2 | Cloudflare |
| Email | Resend (또는 SES) | SaaS |
| Domain/CDN | Cloudflare | Cloudflare |
| Tunnel | Cloudflare Tunnel | 홈 서버 → Cloudflare |

## Domain Setup

- `eogum.sudoremove.com` → Vercel (프론트엔드)
- `api-eogum.sudoremove.com` → Cloudflare Tunnel → 홈 서버:8000

## Credit System

### Model
- 크레딧 단위: **초 (seconds)**
- 과금 기준: 영상 길이 (duration)
- 무료 티어: 가입 시 **18,000초 (5시간)** 부여
- B2B: 별도 계약

### Flow
```
1. 유저가 프로젝트 생성 요청
2. 영상 길이 확인 (ffprobe via R2 또는 업로드 시 메타데이터)
3. 크레딧 hold (balance에서 차감, held 상태로 기록)
4. 처리 시작
5a. 성공 → hold를 usage로 전환, 크레딧 확정 차감
5b. 실패 → hold 해제, 크레딧 복구
```

### credit_transactions.type
- `signup_bonus`: 가입 보너스 (+18000s)
- `purchase`: 결제 충전
- `hold`: 처리 시작 전 홀딩 (-N초)
- `hold_release`: 실패 시 홀딩 해제 (+N초)
- `usage`: 처리 완료 확정 (hold → usage 전환)
- `refund`: 수동 환불

## Database Schema

### profiles
| Column | Type | Note |
|--------|------|------|
| id | uuid PK | FK → auth.users |
| display_name | text | |
| plan | text | free / pro / enterprise |
| created_at | timestamptz | |

### credits
| Column | Type | Note |
|--------|------|------|
| user_id | uuid PK | FK → profiles |
| balance_seconds | integer | 사용 가능 잔액 |
| held_seconds | integer | 홀딩 중인 금액 |
| total_granted_seconds | integer | 총 부여된 크레딧 |
| updated_at | timestamptz | |

### credit_transactions
| Column | Type | Note |
|--------|------|------|
| id | uuid PK | |
| user_id | uuid | FK → profiles |
| amount_seconds | integer | +충전 / -사용 |
| type | text | signup_bonus/purchase/hold/hold_release/usage/refund |
| job_id | uuid | nullable |
| description | text | |
| created_at | timestamptz | |

### projects
| Column | Type | Note |
|--------|------|------|
| id | uuid PK | |
| user_id | uuid | FK → profiles |
| name | text | |
| status | text | created/uploading/processing/completed/failed |
| cut_type | text | subtitle_cut / podcast_cut |
| language | text | ko/en/ja 등 |
| source_r2_key | text | R2 object key |
| source_filename | text | 원본 파일명 |
| source_duration_seconds | integer | |
| source_size_bytes | bigint | |
| settings | jsonb | 추가 설정 (향후 확장) |
| created_at | timestamptz | |
| updated_at | timestamptz | |

### jobs
| Column | Type | Note |
|--------|------|------|
| id | uuid PK | |
| project_id | uuid | FK → projects |
| user_id | uuid | FK → profiles |
| type | text | transcribe/transcript_overview/subtitle_cut/podcast_cut |
| status | text | queued/running/completed/failed |
| progress | integer | 0-100 |
| result_r2_keys | jsonb | {fcpxml: "...", srt: "...", report: "..."} |
| error_message | text | nullable |
| started_at | timestamptz | |
| completed_at | timestamptz | |
| created_at | timestamptz | |

### edit_reports
| Column | Type | Note |
|--------|------|------|
| id | uuid PK | |
| project_id | uuid | FK → projects |
| total_duration_seconds | integer | |
| cut_duration_seconds | integer | |
| cut_percentage | real | |
| edit_summary | jsonb | reason별 count/duration |
| report_markdown | text | 전체 보고서 (웹 표시용) |
| created_at | timestamptz | |

## API Endpoints

### Auth (Supabase JWT 검증)
모든 API 요청에 `Authorization: Bearer <supabase_jwt>` 헤더 필요.

### Upload
```
POST /api/v1/upload/presign
  Body: { filename, content_type, size_bytes }
  Response: { upload_url, r2_key }
  → R2 presigned PUT URL 반환, 프론트에서 직접 업로드
```

### Projects
```
POST   /api/v1/projects          # 프로젝트 생성 + 잡 큐 등록
GET    /api/v1/projects          # 내 프로젝트 목록
GET    /api/v1/projects/:id      # 프로젝트 상세 (보고서 포함)
DELETE /api/v1/projects/:id      # 프로젝트 삭제
```

### Jobs
```
GET /api/v1/projects/:id/jobs         # 프로젝트의 잡 목록
GET /api/v1/projects/:id/jobs/:jobId  # 잡 상세
```

### Credits
```
GET  /api/v1/credits              # 잔액 조회
GET  /api/v1/credits/transactions # 거래 내역
```

### Downloads
```
GET /api/v1/projects/:id/download/:type  # type: fcpxml, srt, report
  → R2 presigned GET URL redirect
```

### Health
```
GET /api/v1/health   # 서버 상태
```

## Job Processing Flow

```python
# Sequential job runner (single worker)
async def process_project(project_id):
    project = get_project(project_id)
    user = get_user(project.user_id)

    # 1. 크레딧 hold
    hold_credits(user.id, project.source_duration_seconds)

    try:
        # 2. R2에서 소스 다운로드 → temp/
        source_path = download_from_r2(project.source_r2_key)

        # 3. Chalna STT (localhost)
        srt_path = avid_transcribe(source_path, language=project.language)
        update_job_progress(job_id, 25)

        # 4. transcript-overview (Pass 1)
        storyline = avid_transcript_overview(srt_path)
        update_job_progress(job_id, 50)

        # 5. subtitle-cut or podcast-cut (Pass 2)
        result = avid_cut(source_path, srt_path, storyline, project.cut_type)
        update_job_progress(job_id, 75)

        # 6. 결과물 R2 업로드
        r2_keys = upload_results_to_r2(project_id, result)

        # 7. 보고서 DB 저장 (웹 표시용)
        save_edit_report(project_id, result.report)

        # 8. 크레딧 확정 차감
        confirm_credit_usage(user.id, project.source_duration_seconds)

        # 9. 이메일 알림
        send_completion_email(user.email, project)
        update_job_progress(job_id, 100)

    except Exception as e:
        # 실패 시 hold 해제
        release_credit_hold(user.id, project.source_duration_seconds)
        send_failure_email(user.email, project, error=str(e))
        mark_project_failed(project_id, str(e))
```

## Desktop App Compatibility (TODO)

데스크탑 앱은 어검 API 서버를 사용한다:
- **Auth/Credits**: 어검 API 호출 (동일)
- **소스**: 로컬 파일 (R2 업로드 안 함)
- **처리**: 미정 (로컬 vs 서버)

### Option A: 서버 처리 (영상 업로드)
- 데스크탑에서 서버로 영상 전송 → 서버에서 처리
- 유저는 앱만 설치하면 됨
- R2 대신 서버 직접 업로드 엔드포인트 필요

### Option B: 로컬 처리
- 유저 컴퓨터에서 avid 실행
- ffmpeg 번들 가능 (ffmpeg-static)
- claude/codex CLI 유저 설치 필요 → 진입 장벽 높음
- 크레딧 차감만 API 호출

→ 데스크탑 결정은 추후.

## Email Notifications

### 처리 완료
```
Subject: [어검] "{project_name}" 편집이 완료되었습니다
Body:
  - 프로젝트명
  - 원본 길이 / 컷 비율
  - 결과 페이지 링크
```

### 처리 실패
```
Subject: [어검] "{project_name}" 처리 중 오류가 발생했습니다
Body:
  - 프로젝트명
  - 오류 요약
  - 크레딧이 자동 복구되었다는 안내
  - 문의 링크
```

## File Structure

```
eogum/
├── apps/
│   ├── web/                    # Next.js frontend
│   │   ├── src/
│   │   │   ├── app/            # App Router pages
│   │   │   ├── components/     # React components
│   │   │   ├── lib/            # Supabase client, API client
│   │   │   └── hooks/          # Custom hooks
│   │   ├── public/
│   │   ├── next.config.js
│   │   ├── package.json
│   │   └── tsconfig.json
│   │
│   └── api/                    # FastAPI backend
│       ├── src/eogum/
│       │   ├── main.py         # FastAPI app
│       │   ├── config.py       # Settings (env-based)
│       │   ├── auth.py         # Supabase JWT verification
│       │   ├── routes/
│       │   │   ├── projects.py
│       │   │   ├── upload.py
│       │   │   ├── credits.py
│       │   │   ├── downloads.py
│       │   │   └── health.py
│       │   ├── services/
│       │   │   ├── credit.py   # Credit hold/deduct/release
│       │   │   ├── job_runner.py  # Sequential job processor
│       │   │   ├── r2.py       # R2 upload/download/presign
│       │   │   ├── email.py    # Email notifications
│       │   │   └── avid.py     # avid CLI/service wrapper
│       │   └── models/
│       │       └── schemas.py  # Pydantic request/response
│       ├── pyproject.toml
│       └── .env.example
│
├── supabase/
│   └── migrations/             # SQL migration files
│       └── 001_initial.sql
│
├── docs/
│   └── infra-setup.md          # R2, Tunnel, Vercel 설정 가이드
│
├── ARCHITECTURE.md             # 이 문서
├── TODO.md                     # 미결 사항
└── README.md
```
