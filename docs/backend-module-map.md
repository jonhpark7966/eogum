# 어검 백엔드 모듈 맵

> 최종 갱신: 2026-03-12
> 범위: `apps/api` 및 백엔드 런타임
> 원칙: 프론트엔드는 현재 소비자이며, 백엔드가 단독으로 동작 가능해야 한다.

## 1. 목적

이 문서는 백엔드 모듈 경계와 책임을 고정하기 위한 기준 문서다.

- 무엇을 어느 모듈이 소유하는지 정한다
- 어떤 방향의 의존성만 허용할지 정한다
- 리팩터링 중에도 API 동작을 유지하기 위한 안전한 분리 순서를 제공한다

관련 문서:

- [docs/avid-integration-spec.md](/home/jonhpark/workspace/eogum/docs/avid-integration-spec.md)
- [docs/avid-submodule-layout.md](/home/jonhpark/workspace/eogum/docs/avid-submodule-layout.md)
- [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md)

## 2. 현재 병목 지점

| 파일 | 현재 문제 | 우선 분리 대상 |
|------|-----------|----------------|
| [projects.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/projects.py) | 프로젝트 CRUD, credit check, queue enqueue, 멀티캠 재-export 스레드 시작이 한 파일에 섞여 있음 | `projects` 서비스, `processing.reexport` 서비스 |
| [job_runner.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/job_runner.py) | queue, worker, 상태 전이, credit hold/confirm, R2, avid, email, report 저장이 모두 결합됨 | `processing.worker`, `processing.report_parser`, `processing.artifacts` |
| [evaluations.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/evaluations.py) | 세그먼트 조합, 평가 저장, 버전 조회, 메트릭 계산이 한 파일에 있음 | `evaluations.segments`, `evaluations.service`, `evaluations.metrics` |
| [avid.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/avid.py) | CLI 실행, stdout 파싱, glob 결과 수집, Python import 기반 override 로직이 섞여 있음 | `adapters.avid_cli`, `processing.report_parser`, `processing.reexport` |
| [youtube.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/youtube.py) | 메타데이터 조회, in-memory task registry, worker 가 한 파일에 있음 | `youtube.service`, `youtube.task_registry`, `youtube.worker` |
| [credit.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/credit.py) | 잔액 계산과 ledger 기록은 있으나 repository 경계가 없음 | `credits.service`, `credits.repository` |

## 3. 목표 모듈 구조

### 3.1 비즈니스 모듈

| 모듈 | 책임 | 소유 데이터/상태 | 현재 진입 파일 |
|------|------|------------------|----------------|
| `auth` | JWT 검증, 현재 사용자 식별 | 인증 컨텍스트 | [auth.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/auth.py) |
| `projects` | 프로젝트 생성/조회/삭제, ownership 확인, extra source 메타데이터 관리 | `projects` 테이블의 메타데이터 영역 | [projects.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/projects.py) |
| `processing` | queue, worker, 상태 전이, artifact 등록, 재-export orchestration | `jobs`, `edit_reports`, `projects.status` | [job_runner.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/job_runner.py) |
| `evaluations` | 세그먼트 조회, 사람 평가 저장, 메트릭 계산 | `evaluations` 테이블 | [evaluations.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/evaluations.py) |
| `youtube` | URL 메타데이터 조회, 다운로드 task 시작/조회 | YouTube task lifecycle | [youtube.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/youtube.py) |
| `credits` | 잔액 조회, hold/confirm/release, 거래 기록 | `credits`, `credit_transactions` | [credits.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/credits.py) |
| `artifacts` | 결과물 key 해석, 다운로드 파일명 정책, presigned URL 발급 | 결과물 접근 정책 | [downloads.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/downloads.py) |

### 3.2 인프라 어댑터

| 어댑터 | 책임 | 현재 파일 |
|--------|------|----------|
| `db` | Supabase client 생성, repository 공통 접근점 | [database.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/database.py) |
| `storage` | R2 presign/download/upload/multipart | [r2.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/r2.py) |
| `avid_cli` | `avid-cli` subprocess 호출, manifest 해석, version/doctor/reexport 진입점 제공 | [avid.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/avid.py) |
| `notifications` | 이메일 발송 | [email.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/email.py) |
| `runtime` | app startup, recovery, wiring, 설정 | [main.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/main.py), [config.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/config.py) |

## 4. 허용 의존성 규칙

1. `routes` 는 HTTP 입출력, 인증, status code 변환만 담당한다.
2. 비즈니스 규칙은 `modules/*/service.py` 또는 그 하위 순수 로직 파일로 이동한다.
3. DB, R2, avid, email 같은 외부 I/O 는 adapter 또는 repository 를 통해서만 접근한다.
4. 한 route 가 다른 route 함수를 호출하면 안 된다.
5. adapter 는 FastAPI, `HTTPException`, `Request`, `Response` 를 몰라야 한다.
6. 순수 계산 로직은 subprocess, thread, DB client 를 직접 import 하지 않는다.
7. `avid` 는 `avid-cli` 로만 호출하고 Python import 는 금지한다.

## 5. 목표 패키지 레이아웃

```text
apps/api/src/eogum/
  api/
    routes/
      health.py
      projects.py
      credits.py
      downloads.py
      evaluations.py
      upload.py
      youtube.py
  core/
    config.py
    auth.py
  modules/
    projects/
      service.py
      repository.py
    processing/
      service.py
      worker.py
      reexport.py
      report_parser.py
      repository.py
    evaluations/
      service.py
      segments.py
      metrics.py
      versions.py
      repository.py
    youtube/
      service.py
      task_registry.py
      worker.py
    credits/
      service.py
      repository.py
    artifacts/
      service.py
  adapters/
    db.py
    r2.py
    avid_cli.py
    email.py
  models/
    schemas.py
```

## 6. 1차 분리 기준

1. `adapters.avid_cli`
2. `processing.reexport`
3. `evaluations.metrics`
4. `evaluations.segments`
5. `processing.report_parser`
6. `processing.worker`

이 순서는 `avid` 경계를 먼저 고정하고, 그 다음 순수 로직과 상태 전이를 정리하기 위한 것이다.

## 7. 모듈별 완료 정의

모듈 하나를 분리 완료라고 부르려면 아래를 만족해야 한다.

1. route 파일에서 핵심 비즈니스 로직이 제거되어 있어야 한다.
2. 외부 I/O 가 adapter 또는 repository 뒤에 있어야 한다.
3. 순수 계산 로직에 대한 unit test 가 있어야 한다.
4. 해당 API 변화가 있으면 integration test 가 있어야 한다.
5. 관련 문서가 새 구조를 반영해야 한다.
6. `avid` direct import 가 남아 있지 않아야 한다.

## 8. 백엔드 우선 원칙

어검은 프론트가 없어도 아래 흐름이 가능해야 한다.

1. 업로드 API 로 source 확보
2. 프로젝트 생성
3. 처리 상태 polling
4. 세그먼트 / 평가 조회 및 저장
5. 결과 다운로드

즉, 현재 리팩터링의 기준은 UI 가 아니라 API 와 worker 가 단독으로 이해 가능하고 검증 가능한가다.
