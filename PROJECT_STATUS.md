# 어검 (Eogum) 프로젝트 현황 분석

> 분석일: 2026-02-22
> 분석 대상: apps/api (FastAPI backend), apps/web (Next.js frontend)

---

## 1. 아키텍처 개요

```
[사용자 브라우저]
    │
    ├─ Next.js Frontend (Vercel)
    │   ├─ Supabase Auth (Google/GitHub OAuth)
    │   ├─ R2 Direct Upload (presigned URL)
    │   └─ API 호출 (Bearer JWT)
    │
    ├─ FastAPI Backend (로컬 서버 :8000)
    │   ├─ JWT 검증 (Supabase ES256)
    │   ├─ Supabase DB (프로젝트/잡/리포트)
    │   ├─ R2 Storage (소스/결과물)
    │   ├─ Job Runner (in-memory deque, 단일 스레드)
    │   └─ avid CLI 호출 (subprocess)
    │
    └─ External Services
        ├─ Chalna (STT, localhost:7861)
        ├─ avid CLI (auto-video-edit)
        ├─ Cloudflare R2 (파일 저장)
        ├─ Supabase (DB + Auth)
        └─ Resend (이메일)
```

---

## 2. 현재 DB 상태

### 프로젝트 (4개)
| Status | Name | ID (prefix) | Duration | Extra Sources |
|--------|------|-------------|----------|---------------|
| **failed** | PhysicalAI 용어집 | 11d144f6 | 6224s (~1h44m) | 0 |
| completed | leak2merge | 3c2a6d86 | - | 0 |
| completed | PhysicalAI 용어집, Qwen3.5... | a7445a5c | - | 0 |
| completed | 유튜브 같이 만들기 2 | 22200e46 | - | 0 |

### 잡 (3개) - 실패 프로젝트(11d144f6)에 잡 없음!
| Status | Project | Error |
|--------|---------|-------|
| completed | 3c2a6d86 | - |
| completed | a7445a5c | - |
| completed | 22200e46 | `[Errno 2] No such file or directory: 'python'` (but completed) |

### 크레딧 (3 유저)
- 모두 balance=18000, held=0 (정상)

---

## 3. 핵심 문제점

### 3.1 CRITICAL - Job Runner 안정성

#### (A) In-memory queue 유실
- **위치**: `services/job_runner.py`
- **문제**: `collections.deque`로 큐 관리 → 서버 재시작 시 큐 유실
- **영향**: 프로젝트 11d144f6처럼 job이 0개인데 failed 상태 발생
- **근본 원인**: retry/enqueue 후 서버가 재시작되면 worker thread가 죽고 DB만 "queued" 상태로 남음
- **현재 완화책**: startup recovery가 queued/processing 프로젝트를 re-enqueue
- **남은 문제**: startup recovery가 "failed" 상태는 건드리지 않음. 11d144f6는 worker에서 failed로 마킹된 후 retry 하면 다시 queued → 서버 재시작 → recovery가 다시 enqueue → 또 실패 사이클

#### (B) 단일 worker thread, 동시성 없음
- **문제**: 한 번에 1개 프로젝트만 처리. 1시간 44분 영상이면 뒤에 대기하는 프로젝트도 1시간+ 대기
- **영향**: 다중 사용자 동시 사용 시 심각한 병목

#### (C) Race condition - retry vs active worker
- **문제**: retry API가 old jobs 삭제 + status reset → 동시에 worker가 같은 프로젝트 처리 중이면 충돌
- **코드 경로**: `projects.py:retry_project()` → delete jobs → reset queued → enqueue
- **worker가 동시에**: job update → status update → 꼬임

### 3.2 HIGH - Frontend-Backend 상태 불일치

#### (A) 프로젝트 상태 표시 부정확
- **문제**: 프로젝트가 "queued"인데 job이 0개면 프론트에서 진행상황 표시 불가
- **원인**: job은 `_process_project()` 시작 시에야 생성됨. queued 상태에서는 job이 없음
- **UI 영향**: 대시보드에서 "대기 중" 뱃지만 보이고 구체적 정보 없음

#### (B) Polling이 에러 시 조용히 멈춤
- **위치**: `dashboard/page.tsx`, `projects/[id]/page.tsx`
- **문제**: poll에서 에러 나면 catch에서 setError만 하고 interval은 계속 돌지만 데이터 갱신 안 됨
- **실제 영향**: 서버 재시작 중이면 사용자에게 적절한 피드백 없음

#### (C) 다운로드 에러 핸들링 없음
- **위치**: `projects/[id]/page.tsx` handleDownload, handleDownloadExtraSource
- **문제**: try-catch 없이 바로 window.open → API 에러 시 사용자 피드백 0

### 3.3 MEDIUM - avid CLI 연동 불안정

#### (A) SRT 경로 파싱이 brittle
- **위치**: `services/avid.py` lines 60-89
- **문제**: avid CLI stdout에서 "완료: /path/to/file.srt" 패턴을 regex로 파싱
- **위험**: avid CLI 출력 포맷 변경 시 즉시 깨짐
- **현재 fallback**: glob으로 `.srt` 파일 찾기 (여러 파일 있으면 잘못된 파일 선택 가능)

#### (B) Edit report 파싱 regex 취약
- **위치**: `job_runner.py` lines 204-220
- **문제**: `合計.*?(\d+):(\d+)\.(\d+)` 일본어 "합계" 패턴에 의존
- **위험**: avid 리포트 포맷 변경 시 cut_duration 파싱 실패 → 0으로 저장

#### (C) Timeout 관리
- **현재**: transcribe 7200s, overview/cut 1800s
- **문제**: 긴 영상에서 timeout 초과 가능 (이전 세션에서 600→1800으로 수정했지만 여전히 부족할 수 있음)

### 3.4 MEDIUM - UI/UX 문제

#### (A) alert() 사용
- **위치**: `review/page.tsx` lines 221, 238
- **문제**: 저장/리포트 실패 시 `alert()` 사용 → 네이티브 앱 느낌 깨짐

#### (B) 키보드 단축키 없음
- **영향**: 리뷰 페이지에서 100개+ 세그먼트를 하나씩 마우스 클릭으로만 평가

#### (C) 멀티캠 크레딧 비용 표시 없음
- **위치**: `projects/[id]/page.tsx`
- **문제**: "추가 크레딧이 차감됩니다" 메시지만 있고 얼마인지 표시 안 됨

### 3.5 LOW - 보안/성능

#### (A) Rate limiting 없음
- 모든 API 엔드포인트에 rate limit 없음
- presign/upload 남용 가능

#### (B) Settings 입력 검증 없음
- `ProjectCreate.settings`가 `dict = {}` → 임의 JSON 저장 가능
- avid CLI에 context로 전달되는 값이 sanitize 안 됨

#### (C) Evaluation upsert가 atomic하지 않음
- **위치**: `evaluations.py` line 234-254
- **문제**: check-then-insert 패턴 → 동시 요청 시 duplicate 가능

---

## 4. 파일별 상세 분석

### Backend (apps/api)

| 파일 | 줄 수 | 역할 | 주요 문제 |
|------|-------|------|-----------|
| `main.py` | ~50 | App init, startup recovery, CORS | recovery가 failed는 무시 |
| `config.py` | ~50 | 환경변수 로드 | CHALNA_URL 기본값 8001 (실제 7861) |
| `auth.py` | ~80 | JWT 검증 | 정상 |
| `schemas.py` | ~150 | Pydantic 모델 | settings 검증 없음 |
| `routes/upload.py` | ~100 | 업로드 presign/multipart | 정상 |
| `routes/projects.py` | ~200 | 프로젝트 CRUD | retry race condition |
| `routes/downloads.py` | ~100 | 다운로드 URL 생성 | 정상 |
| `routes/credits.py` | ~60 | 크레딧 조회 | 정상 |
| `routes/evaluations.py` | ~300 | 세그먼트 리뷰/평가 | upsert race condition |
| `services/job_runner.py` | ~250 | 잡 처리 파이프라인 | **핵심 문제 집중** |
| `services/avid.py` | ~200 | avid CLI 래퍼 | 출력 파싱 brittle |
| `services/r2.py` | ~150 | R2 스토리지 | 정상 |
| `services/credit.py` | ~100 | 크레딧 hold/confirm | 정상 |
| `services/email.py` | ~100 | 이메일 발송 | 미설정 시 graceful skip |

### Frontend (apps/web)

| 파일 | 줄 수 | 역할 | 주요 문제 |
|------|-------|------|-----------|
| `page.tsx` (landing) | 468 | 랜딩 페이지 | 정상 |
| `dashboard/page.tsx` | 358 | 대시보드 | 폴링 에러 처리 미흡 |
| `dashboard/new/page.tsx` | 251 | 프로젝트 생성 | 비디오 duration 검증 없음 |
| `projects/[id]/page.tsx` | 587 | 프로젝트 상세 | **다운로드 에러 무시** |
| `projects/[id]/review/page.tsx` | 627 | 세그먼트 리뷰 | alert() 사용, 단축키 없음 |
| `lib/api.ts` | 363 | API 클라이언트 | timeout 없음 |
| `lib/supabase/*` | ~80 | Supabase 클라이언트 | 정상 |

---

## 5. 프로세싱 파이프라인 플로우

```
[업로드] ──→ [프로젝트 생성] ──→ [큐 대기] ──→ [처리 시작]
                                                    │
                                    ┌───────────────┤
                                    ▼               ▼
                            [크레딧 홀드]    [소스 다운로드 (R2→로컬)]
                                    │               │
                                    ▼               ▼
                            [Chalna 전사] ──→ [SRT 생성] (progress: 30%)
                                                    │
                                                    ▼
                            [transcript-overview] ──→ [storyline.json] (50%)
                                                    │
                                                    ▼
                            [subtitle/podcast cut] ──→ [FCPXML+SRT+Report] (75%)
                                                    │
                                                    ▼
                            [프리뷰 생성 (ffmpeg)] ──→ [preview.mp4]
                                                    │
                                                    ▼
                            [R2 업로드 (결과물)] (85%)
                                                    │
                                                    ▼
                            [Edit Report 저장] ──→ [크레딧 확정] ──→ [완료]
                                                                      │
                                                                      ▼
                                                              [이메일 발송]

실패 시:
    [크레딧 홀드 해제] → [job/project failed] → [실패 이메일] → [temp 정리]
```

---

## 6. 해결 우선순위 제안

### P0 (즉시)
1. **다운로드 에러 핸들링** - try-catch 추가 (5분)
2. **config.py CHALNA_URL 기본값** 수정 - 8001→7861 (1분)
3. **alert() → 인라인 에러** 교체 (15분)

### P1 (단기)
4. **Job runner retry race condition** 수정 - enqueue 전 active worker 확인
5. **Startup recovery** 보강 - failed 프로젝트도 retry 옵션 제공
6. **API timeout** 추가 - AbortController + fetch timeout
7. **avid CLI 출력 파싱** 개선 - structured output 사용

### P2 (중기)
8. **Job queue 영속화** - DB 기반 큐 또는 Redis
9. **실시간 업데이트** - Supabase Realtime 도입
10. **키보드 단축키** - 리뷰 페이지
11. **Rate limiting** 추가

### P3 (장기)
12. **Worker pool** - 동시 처리
13. **Pagination** - 대시보드
14. **Settings schema** 검증

---

## 7. avid CLI 동작 확인 상태

| 기능 | CLI 직접 실행 | API 경유 | 비고 |
|------|:---:|:---:|------|
| transcribe | OK | OK | Chalna 포트 수정 후 정상 |
| transcript-overview | OK | OK | timeout 1800s로 수정 |
| subtitle-cut | OK | OK | - |
| podcast-cut | OK | ? | 테스트 필요 |
| multicam (extra-source) | OK | ? | 테스트 필요 |
| preview 생성 | OK | OK | 실패해도 job은 완료 |

---

## 8. 기술 스택 정리

### Backend
- Python 3.12, FastAPI, Uvicorn
- supabase-py, boto3 (R2), resend
- subprocess로 avid CLI 호출

### Frontend
- Next.js 16.1.6, React 19, TypeScript
- Tailwind CSS v4
- @supabase/ssr, @supabase/supabase-js
- react-markdown, remark-gfm

### External
- Supabase (DB + Auth + Storage metadata)
- Cloudflare R2 (파일 저장)
- Chalna (STT, RTX 5090)
- avid CLI (자동 편집)
- Resend (이메일, 미설정)
