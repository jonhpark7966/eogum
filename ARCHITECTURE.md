# 어검 (Eogum) 아키텍처

> 최종 갱신: 2026-03-12
> 기준: 현재 저장소 구현

## 1. 시스템 개요

```text
[Browser]
  |
  +-- Next.js Web (Vercel or local)
  |     - Supabase OAuth / SSR session
  |     - R2 multipart direct upload
  |     - Project dashboard / detail / review
  |
  +-- FastAPI API (:8000)
        - JWT verification via Supabase JWKS
        - Supabase DB access
        - Cloudflare R2 upload/download
        - Background job runner
        - YouTube background downloader
        - avid CLI orchestration
        - Optional email notifications
  |
  +-- External services
        - Supabase (Auth + Postgres)
        - Cloudflare R2
        - Chalna STT
        - Resend
        - avid CLI / auto-video-edit repo
```

핵심 특성:

- 프론트엔드는 Next.js 16 + React 19 + Tailwind v4 기반이다.
- 백엔드는 FastAPI 단일 프로세스이며, 프로젝트 처리는 in-memory queue + background thread 로 돌아간다.
- 소스 입력은 두 가지다.
  - 파일 업로드: 브라우저에서 R2 로 멀티파트 직접 업로드
  - YouTube URL: 백엔드에서 `yt-dlp` 로 다운로드 후 R2 업로드
- 사람 평가 데이터는 Supabase `evaluations` 테이블에 저장되고, 이후 FCPXML 재-export 에 재사용할 수 있다.

## 2. 주요 사용자 플로우

### 2.1 로그인

1. 랜딩 페이지에서 Google 또는 GitHub 로그인
2. Supabase OAuth 완료 후 `/auth/callback`
3. 콜백 라우트가 세션 교환 후 `/dashboard` 로 리다이렉트
4. 미로그인 사용자는 `/` 와 `/auth/callback` 외 페이지 접근 시 middleware 에 의해 `/` 로 이동

관련 파일:

- [page.tsx](/home/jonhpark/workspace/eogum/apps/web/src/app/page.tsx)
- [route.ts](/home/jonhpark/workspace/eogum/apps/web/src/app/auth/callback/route.ts)
- [middleware.ts](/home/jonhpark/workspace/eogum/apps/web/src/middleware.ts)
- [middleware.ts](/home/jonhpark/workspace/eogum/apps/web/src/lib/supabase/middleware.ts)

### 2.2 프로젝트 생성

#### A. 파일 업로드 플로우

1. `/dashboard/new` 에서 파일 선택
2. 프론트가 브라우저에서 duration 메타데이터 추출
3. `POST /upload/multipart/initiate`
4. 브라우저가 presigned URL 로 R2 멀티파트 업로드
5. `POST /upload/multipart/complete`
6. `POST /projects`
7. 백엔드가 프로젝트를 `queued` 로 만들고 background queue 에 추가

#### B. YouTube URL 플로우

1. `/dashboard/new` 에서 `YouTube URL` 모드 선택
2. `POST /youtube/info` 로 메타데이터 확인
3. `POST /youtube/download` 으로 백그라운드 다운로드 시작
4. 프론트가 `GET /youtube/download/{task_id}` 를 폴링
5. 다운로드 완료 후 반환된 `r2_key` 로 `POST /projects`

현재 구현 메모:

- YouTube download task 자체는 DB 가 아니라 메모리에 저장된다.
- 프로젝트 생성 시 크레딧 부족이면 402를 반환한다.

관련 파일:

- [page.tsx](/home/jonhpark/workspace/eogum/apps/web/src/app/dashboard/new/page.tsx)
- [upload.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/upload.py)
- [youtube.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/youtube.py)
- [youtube.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/youtube.py)

### 2.3 프로세싱 파이프라인

프로젝트 생성 후 흐름:

1. `projects.status = queued`
2. `job_runner.enqueue(project_id)`
3. worker 가 프로젝트를 꺼내 `processing` 으로 변경
4. `jobs` 레코드 1개 생성
5. 크레딧 hold
6. 원본 소스 다운로드
7. `extra_sources` 가 있으면 함께 다운로드
8. avid `transcribe`
9. avid `transcript-overview --provider claude`
10. avid `subtitle-cut` 또는 `podcast-cut --provider claude`
11. `ffmpeg` 로 `preview.mp4` 생성 시도
12. 결과물 R2 업로드
13. `edit_reports` 저장
14. 크레딧 usage 확정
15. `jobs.status = completed`, `projects.status = completed`
16. 완료 이메일 전송 시도

실패 시:

1. hold 해제
2. `jobs.status = failed`
3. `projects.status = failed`
4. 실패 이메일 전송 시도
5. 임시 디렉터리 정리

startup recovery:

- 앱 시작 시 `queued` 또는 `processing` 프로젝트를 다시 `queued` 로 돌리고 재-enqueue 한다.
- 이 과정에서 `running` / `pending` jobs 와 `edit_reports` 를 지운다.

관련 파일:

- [job_runner.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/job_runner.py)
- [avid.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/avid.py)
- [main.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/main.py)

### 2.4 결과 확인

프로젝트 상세 페이지(`/projects/{id}`)에서 제공하는 기능:

- 프로젝트 상태 표시
- 진행 중이면 job progress polling
- 편집 리포트 렌더링
- 결과 파일 다운로드
- 멀티캠 소스 업로드 / 제거 / 다운로드
- 리뷰 페이지 이동

백엔드가 지원하는 다운로드 타입:

- `source`
- `fcpxml`
- `srt`
- `report`
- `project_json`
- `storyline`
- `preview`

현재 프론트 UI 에서 노출하는 기본 다운로드 버튼:

- `source`
- `fcpxml`
- `srt`
- `report`
- `project_json`
- `storyline`

`preview` 는 백엔드 다운로드는 가능하지만, 현재 UI 에서는 별도 버튼보다 리뷰 플레이어용 `video-url` 사용이 중심이다.

관련 파일:

- [page.tsx](/home/jonhpark/workspace/eogum/apps/web/src/app/projects/[id]/page.tsx)
- [downloads.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/downloads.py)

### 2.5 세그먼트 리뷰 / 평가

리뷰 페이지(`/projects/{id}/review`) 흐름:

1. `GET /projects/{id}/segments`
2. `GET /projects/{id}/video-url`
3. `GET /projects/{id}/evaluation`
4. 사람이 keep / cut, reason, note 입력
5. `POST /projects/{id}/evaluation`
6. 필요할 때 `GET /projects/{id}/eval-report`

구현 포인트:

- 백엔드는 `evaluation` 이 없으면 404를 반환한다.
- 프론트는 이 404를 정상 상태로 처리하고 `null` 로 간주한다.
- 저장은 `project_id,evaluator_id` unique key 기반 atomic upsert 이다.
- `video-url` 은 preview 가 있으면 preview, 없으면 source 를 스트리밍한다.

관련 파일:

- [review/page.tsx](/home/jonhpark/workspace/eogum/apps/web/src/app/projects/[id]/review/page.tsx)
- [evaluations.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/evaluations.py)

### 2.6 평가 반영 재-export / 멀티캠

`POST /projects/{id}/multicam` 의 현재 의미는 단순 멀티캠 처리보다 넓다.

실행 조건:

- 프로젝트 상태가 `completed` 또는 `failed`
- 최신 completed job 이 존재
- `project_json` 결과물이 존재
- 평가 데이터 또는 `extra_sources` 중 하나 이상 존재

실행 내용:

1. 기존 `project_json` 다운로드
2. 평가 데이터가 있으면 human decision 을 avid `edit_decisions` 에 반영
3. 기존 extra source 가 avid project 에 있으면 먼저 제거
4. `extra_sources` 가 있으면 오디오 싱크 후 추가
5. 업데이트된 `project_json` 저장
6. FCPXML 재-export
7. 최신 completed job 의 `result_r2_keys.project_json` / `fcpxml` 갱신
8. 프로젝트 상태를 다시 `completed` 로 복구

실패 동작:

- 원래 completed 결과물이 남아 있다고 가정하고 프로젝트 상태를 `completed` 로 되돌린다.

관련 파일:

- [projects.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/projects.py)
- [avid.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/avid.py)

## 3. 데이터 모델 요약

| 테이블 | 주요 컬럼 | 설명 |
|--------|----------|------|
| `profiles` | `id`, `display_name`, `plan` | 사용자 프로필 |
| `credits` | `user_id`, `balance_seconds`, `held_seconds` | 사용자 크레딧 잔액 |
| `credit_transactions` | `user_id`, `amount_seconds`, `type`, `job_id` | 크레딧 사용 이력 |
| `projects` | `status`, `cut_type`, `language`, `source_*`, `settings`, `extra_sources` | 프로젝트 메타데이터 |
| `jobs` | `project_id`, `type`, `status`, `progress`, `result_r2_keys` | 처리 시도 단위 |
| `edit_reports` | `project_id`, `cut_duration_seconds`, `cut_percentage`, `report_markdown` | 편집 요약 |
| `evaluations` | `project_id`, `evaluator_id`, `avid_version`, `eogum_version`, `segments` | 사람 평가 결과 |

메모:

- `projects.extra_sources` 는 멀티캠 재-export 용 추가 소스 목록이다.
- 현재 코드는 processing attempt 당 `jobs` 행 1개만 만들며 `type` 에 `subtitle_cut` 또는 `podcast_cut` 이 저장된다.

## 4. API 엔드포인트 맵

모든 엔드포인트 prefix: `/api/v1`

| Method | Path | 역할 |
|--------|------|------|
| GET | `/health` | 서버 상태 |
| POST | `/upload/presign` | 단건 presigned upload URL |
| POST | `/upload/multipart/initiate` | 멀티파트 업로드 시작 |
| POST | `/upload/multipart/complete` | 멀티파트 업로드 완료 |
| POST | `/projects` | 프로젝트 생성 및 queue 등록 |
| GET | `/projects` | 프로젝트 목록 |
| GET | `/projects/{id}` | 프로젝트 상세 |
| POST | `/projects/{id}/retry` | 실패 프로젝트 재시도 |
| PUT | `/projects/{id}/extra-sources` | 멀티캠 추가 소스 등록/수정 |
| POST | `/projects/{id}/multicam` | 평가 반영 재-export 및 멀티캠 처리 |
| DELETE | `/projects/{id}` | 프로젝트 삭제 |
| GET | `/credits` | 잔액 조회 |
| GET | `/credits/transactions` | 크레딧 거래 내역 |
| GET | `/projects/{id}/download/{type}` | 결과물 다운로드 URL |
| GET | `/projects/{id}/download/extra-source/{idx}` | 추가 소스 다운로드 URL |
| GET | `/projects/{id}/segments` | 세그먼트 + AI 판단 조회 |
| GET | `/projects/{id}/video-url` | 리뷰 플레이어용 스트리밍 URL |
| GET | `/projects/{id}/evaluation` | 기존 평가 조회 |
| POST | `/projects/{id}/evaluation` | 평가 저장 |
| GET | `/projects/{id}/eval-report` | AI vs Human 비교 리포트 |
| POST | `/youtube/info` | YouTube 메타데이터 조회 |
| POST | `/youtube/download` | YouTube 다운로드 시작 |
| GET | `/youtube/download/{task_id}` | YouTube 다운로드 상태 조회 |

## 5. 파일 및 스토리지 경로

### Cloudflare R2

```text
sources/{uuid}.{ext}
results/{project_id}/{artifact}
```

예상 artifact:

- `*.fcpxml`
- `*.srt`
- `*.report.md`
- `*.avid.json`
- `storyline.json`
- `preview.mp4`

### 로컬 임시 디렉터리

기본값: `/tmp/eogum`

```text
/tmp/eogum/{project_id}/
  source.ext
  extra_0.ext
  extra_1.ext
  *.srt
  output/
    *.fcpxml
    *.report.md
    *.avid.json
    storyline.json
    preview.mp4

/tmp/eogum/multicam_{project_id}/
  source.ext
  extra_0.ext
  output/
    *.fcpxml
    *.avid.json

/tmp/eogum/yt_{task_id}/
  downloaded-video.mp4
```

## 6. 현재 제약과 리스크

### 6.1 영속성

- 프로젝트 처리 queue 는 메모리에만 있다.
- YouTube download task 도 메모리에만 있다.
- 프로세스 재시작 시 startup recovery 는 프로젝트는 일부 복구하지만 YouTube task 는 복구하지 못한다.

### 6.2 동시성

- worker 는 1개뿐이라 동시에 1개 프로젝트만 처리한다.
- `retry` 와 `multicam` 에 active worker 충돌 방지 로직이 없다.

### 6.3 recovery 범위

- startup recovery 는 `queued`, `processing` 만 다룬다.
- `failed` 이면서 job 이 비정상적으로 비어 있는 프로젝트는 자동 복구 대상이 아니다.

### 6.4 입력 검증

- `source_duration_seconds`, `cut_type`, `settings` 검증이 아직 느슨하다.
- 프론트와 백엔드 모두 극단적인 입력값 방어가 충분하지 않다.

### 6.5 네트워크/업로드 내구성

- 프론트 `fetch` timeout 이 없다.
- 폴링 backoff 가 없다.
- 멀티파트 업로드는 part retry 및 abort cleanup 이 없다.

### 6.6 외부 도구 의존성

- avid CLI 출력과 결과 파일 경로 파싱이 일부 stdout/glob 규칙에 의존한다.
- 편집 리포트 파싱은 markdown 패턴에 의존한다.
- `ffmpeg`, `ffprobe`, `yt-dlp`, Chalna, avid `.venv` 가 모두 정상이어야 한다.

## 7. 참고 파일

- [main.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/main.py)
- [job_runner.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/job_runner.py)
- [avid.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/avid.py)
- [youtube.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/youtube.py)
- [api.ts](/home/jonhpark/workspace/eogum/apps/web/src/lib/api.ts)
- [dashboard/new/page.tsx](/home/jonhpark/workspace/eogum/apps/web/src/app/dashboard/new/page.tsx)
- [projects/[id]/page.tsx](/home/jonhpark/workspace/eogum/apps/web/src/app/projects/[id]/page.tsx)
- [projects/[id]/review/page.tsx](/home/jonhpark/workspace/eogum/apps/web/src/app/projects/[id]/review/page.tsx)
