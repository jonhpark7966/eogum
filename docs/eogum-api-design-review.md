# eogum API Design Review

> 최종 갱신: 2026-03-16
> 기준 코드: `apps/api/src/eogum/routes/*`, `apps/api/src/eogum/models/schemas.py`, `apps/web/src/lib/api.ts`

이 문서는 현재 `eogum` 백엔드 API 를 세 가지 관점에서 다시 정리한다.

1. 프론트가 실제로 어떤 endpoint 를 쓰는가
2. 각 endpoint 가 어떤 파라미터와 의미를 가지는가
3. 여러 프론트가 붙는 것을 가정했을 때 어떤 이름과 리소스 모델이 더 맞는가

기존 참조 문서는 [docs/eogum-api-reference.md](/home/jonhpark/workspace/eogum/docs/eogum-api-reference.md) 이고,
이 문서는 그 위에 설계 검토를 덧붙인 문서다.

모든 API 는 기본 prefix `/api/v1` 를 가진다.

## 1. 도메인 기준으로 본 현재 API

현재 API 는 대략 아래 도메인으로 나뉜다.

- `upload`: R2 에 소스를 넣기 위한 전송 API
- `projects`: 편집 작업 단위와 상태 전이
- `evaluations`: 리뷰용 segment 조회와 사람 판단 저장
- `downloads`: 결과물 및 소스 다운로드
- `credits`: 사용량과 잔액
- `youtube`: 외부 소스 import
- `health`: 프로세스 생존 확인

문제는 현재 path 이름이 이 도메인과 실제 실행 의미를 항상 잘 반영하지 않는다는 점이다.
대표적으로 `POST /projects/{id}/multicam` 은 실제로 멀티캠 전용 API 가 아니라
평가 반영, 멀티캠 재구성 또는 제거, 최종 export 를 모두 포함하는 일반 재처리 API 다.

## 2. 인증 규칙

대부분 endpoint 는 아래 헤더가 필요하다.

```http
Authorization: Bearer <supabase access token>
```

예외:

- `GET /health`

## 3. Current Endpoint Inventory

### 3.1 Upload / Ingest

브라우저는 presigned URL 을 받은 뒤 R2 로 직접 `PUT` 한다.
즉 `multipart/initiate` 와 `multipart/complete` 사이에는 백엔드가 아닌 R2 direct upload 단계가 있다.

| Method | Path | Front 사용 | 요청 파라미터 | 주요 응답 | 현재 의미 | 설계 메모 |
| --- | --- | --- | --- | --- | --- | --- |
| `POST` | `/upload/presign` | 미사용 | body: `filename`, `content_type`, `size_bytes` | `upload_url`, `r2_key` | 단일 PUT 업로드용 presign 발급 | 작은 파일, 다른 클라이언트, server-to-server ingest 용으로는 유지 가치가 있다 |
| `POST` | `/upload/multipart/initiate` | 사용 | body: `filename`, `content_type`, `size_bytes` | `upload_id`, `r2_key`, `part_size`, `part_urls[]` | multipart 업로드 세션 생성 | project 생성과 분리돼 있어서 orphan upload 가능성이 있다 |
| `POST` | `/upload/multipart/complete` | 사용 | body: `r2_key`, `upload_id`, `parts[{part_number, etag}]` | `r2_key` | multipart 업로드 완료 | transport 완료일 뿐 project 생성은 아니다 |

현재 프론트는 파일 업로드와 project 생성을 분리해 수행한다.

- 파일 업로드 완료
- `POST /projects`

이 구조는 단순하지만, upload 성공 후 project 생성 실패 시 정리되지 않은 source object 가 남는다.

### 3.2 Projects / Lifecycle

| Method | Path | Front 사용 | 요청 파라미터 | 주요 응답 | 현재 의미 | 설계 메모 |
| --- | --- | --- | --- | --- | --- | --- |
| `POST` | `/projects` | 사용 | body: `name`, `cut_type`, `language`, `source_r2_key`, `source_filename`, `source_duration_seconds`, `source_size_bytes`, `settings` | `ProjectResponse` | 프로젝트 row 생성 후 초기 처리 queue 등록 | `source_*` 필드가 평평하게 퍼져 있어 source abstraction 이 약하다 |
| `GET` | `/projects` | 사용 | 없음 | `ProjectResponse[]` | 내 프로젝트 목록 | 카드 렌더링에는 충분하지만 상태 enum 이 느슨하다 |
| `GET` | `/projects/{project_id}` | 사용 | path: `project_id` | `ProjectDetailResponse` | project row + jobs + report 조회 | UI 가 여기서 business rule 을 추론하고 있어 `available_actions` 같은 서버 계산 필드가 유용하다 |
| `POST` | `/projects/{project_id}/retry` | 사용 | path: `project_id` | `ProjectResponse` | 실패한 초기 처리 재시도 | command endpoint 로 의미가 비교적 명확하다 |
| `PUT` | `/projects/{project_id}/extra-sources` | 사용 | path: `project_id`, body: `extra_sources[{r2_key, filename, size_bytes, offset_ms?}]` | `ProjectResponse` | 원하는 extra source 목록 저장 | 실제로는 멀티캠 desired state 저장이다. 전체 replace 라는 점은 명확하지만 컬렉션 리소스 이름이 약하다 |
| `POST` | `/projects/{project_id}/multicam` | 사용 | path: `project_id` | `ProjectResponse` | 후처리 작업 queue 등록 | 이름이 틀렸다. 실제로는 `apply-evaluation`, `rebuild-multicam` 또는 `clear-extra-sources`, `export-project` 의 일반 재처리 entrypoint 다 |
| `DELETE` | `/projects/{project_id}` | 미사용 | path: `project_id` | 없음 (`204`) | 프로젝트 삭제 | 여러 프론트가 붙는 것을 생각하면 유지 가치가 높다 |

`POST /projects/{project_id}/multicam` 이 실제로 하는 일은 현재 저장된 desired state 를 materialize 하는 것이다.

- 저장된 evaluation 이 있으면 `apply-evaluation`
- 저장된 extra source 가 있으면 `rebuild-multicam`
- 저장된 extra source 는 없지만 기존 project JSON 에 extra source 가 있으면 `clear-extra-sources`
- 마지막에 항상 `export-project`

즉 이 endpoint 의 진짜 의미는 `멀티캠 적용` 이 아니라 `변경사항 적용` 또는 `재처리` 다.

### 3.3 Review / Evaluation

| Method | Path | Front 사용 | 요청 파라미터 | 주요 응답 | 현재 의미 | 설계 메모 |
| --- | --- | --- | --- | --- | --- | --- |
| `GET` | `/projects/{project_id}/segments` | 사용 | path: `project_id` | `schema_version`, `review_scope`, `join_strategy`, `segments[]`, `source_duration_ms` | `avid review-segments` 기반 리뷰 dataset 조회 | 이름만 보면 일반 segments 인데 실제로는 review-source payload 다 |
| `GET` | `/projects/{project_id}/video-url` | 사용 | path: `project_id` | `video_url`, `duration_ms` | preview stream URL 조회, 없으면 source fallback | 리소스 이름으로는 `/preview` 가 더 자연스럽다 |
| `GET` | `/projects/{project_id}/evaluation` | 사용 | path: `project_id` | `EvaluationResponse` | 현재 사용자 평가 불러오기 | 사실상 `my review override` 리소스다 |
| `POST` | `/projects/{project_id}/evaluation` | 사용 | path: `project_id`, body: review envelope + `segments[]` | `EvaluationResponse` | 평가 저장 또는 갱신 upsert | upsert 이므로 HTTP 의미상 `PUT` 이 더 가깝다 |
| `GET` | `/projects/{project_id}/eval-report` | 사용 | path: `project_id` | agreement, confusion, metrics, disagreements | 저장된 평가를 분석한 보고서 | 리뷰 리소스의 파생물이라 `/review/report` 로 읽히는 편이 맞다 |

현재 review 흐름은 두 단계로 나뉜다.

- `POST /evaluation`: 사람 판단을 저장
- `POST /multicam`: 저장된 판단을 결과물에 반영

이 분리는 기술적으로 맞지만, API 이름만 보면 저장과 반영이 같은 흐름처럼 보이지 않는다.

### 3.4 Downloads / Artifacts

| Method | Path | Front 사용 | 요청 파라미터 | 주요 응답 | 현재 의미 | 설계 메모 |
| --- | --- | --- | --- | --- | --- | --- |
| `GET` | `/projects/{project_id}/download/{file_type}` | 사용 | path: `project_id`, `file_type` | `download_url`, `filename` | 결과 artifact 또는 source presign | stringly typed artifact access 다. `artifacts` 리소스로 읽는 편이 자연스럽다 |
| `GET` | `/projects/{project_id}/download/extra-source/{index}` | 사용 | path: `project_id`, `index` | `download_url`, `filename` | extra source presign | index 기반 주소는 여러 클라이언트가 수정하는 상황에서 불안정하다 |

현재 지원되는 `file_type`:

- `fcpxml`
- `srt`
- `report`
- `project_json`
- `storyline`
- `source`
- `preview`
- `sync_diagnostics`

현재 프론트가 실제로 요청하는 값:

- `source`
- `fcpxml`
- `srt`
- `report`
- `project_json`
- `storyline`

즉 `preview`, `sync_diagnostics` 는 백엔드 capability 로는 있으나 UI 에는 아직 노출되지 않는다.

### 3.5 Credits / Account

| Method | Path | Front 사용 | 요청 파라미터 | 주요 응답 | 현재 의미 | 설계 메모 |
| --- | --- | --- | --- | --- | --- | --- |
| `GET` | `/credits` | 사용 | 없음 | `balance_seconds`, `held_seconds`, `available_seconds` | 크레딧 잔액 조회 | 현재 dashboard 용으로 충분하다 |
| `GET` | `/credits/transactions` | 미사용 | query: `limit`, `offset` | `CreditTransactionResponse[]` | 거래 내역 조회 | billing UI 가 생기면 필요하다 |

### 3.6 YouTube Import

| Method | Path | Front 사용 | 요청 파라미터 | 주요 응답 | 현재 의미 | 설계 메모 |
| --- | --- | --- | --- | --- | --- | --- |
| `POST` | `/youtube/info` | 사용 | body: `url` | `title`, `duration_seconds`, `filesize_approx_bytes`, `thumbnail`, `uploader`, `upload_date` | 외부 영상 메타데이터 확인 | 향후 다른 import source 가 생기면 `imports/*` 로 일반화할 수 있다 |
| `POST` | `/youtube/download` | 사용 | body: `url` | `task_id`, `title`, `duration_seconds`, `filesize_approx_bytes` | YouTube download + R2 upload 작업 시작 | 비동기 import job 생성에 해당한다 |
| `GET` | `/youtube/download/{task_id}` | 사용 | path: `task_id` | `status`, `progress`, `error`, `r2_key`, `filename`, `duration_seconds`, `filesize_bytes` | import 진행률 polling | 실제 의미는 `YouTube import task` 조회다 |

### 3.7 System

| Method | Path | Front 사용 | 요청 파라미터 | 주요 응답 | 현재 의미 | 설계 메모 |
| --- | --- | --- | --- | --- | --- | --- |
| `GET` | `/health` | 미사용 | 없음 | `status`, `version` | 프로세스 liveness 확인 | `avid doctor`, R2, Supabase 를 검증하지 않으므로 readiness 로는 부족하다 |

## 4. Frontend Usage Summary

현재 프론트 기준으로 보면 API surface 는 아래 세 묶음으로 갈린다.

### 4.1 현재 실제 사용 중

- upload multipart 2종
- projects 6종
- downloads 2종
- evaluations 5종
- credits 1종
- youtube 3종

### 4.2 프론트 client 에 정의돼 있지만 현재 페이지에서 안 쓰는 것

- `POST /upload/presign`
- `DELETE /projects/{project_id}`

### 4.3 백엔드에 있지만 현재 프론트가 안 쓰는 것

- `GET /health`
- `GET /credits/transactions`
- artifact type `preview`
- artifact type `sync_diagnostics`

## 5. 현재 설계의 핵심 문제

### 5.1 Upload 와 Project Creation 이 분리돼 있다

현재 구조에서는 transport 성공과 domain 생성 성공이 따로 논다.

- `multipart/initiate`
- browser direct `PUT`
- `multipart/complete`
- `POST /projects`

이 구조의 결과:

- upload 성공 후 project 생성 실패 시 orphan source 가 남는다
- “업로드 실패” 와 “처리 시작 실패” 가 사용자에게 같은 에러처럼 보인다
- 나중에 ingest analytics 를 붙이기 어렵다

### 5.2 Project 가 `desired state` 와 `materialized output` 을 함께 안고 있다

현재 `projects.extra_sources` 와 `evaluations` 는 사용자가 원하는 상태를 저장하고,
`jobs.result_r2_keys` 는 그 상태를 반영한 실제 결과물을 가리킨다.

이 둘은 본질적으로 다른 층위다.

- desired state: review, multicam sources, offsets
- materialized state: fcpxml, srt, preview, sync diagnostics

API 이름이 이를 구분해 주지 않기 때문에 프론트가 “저장” 과 “적용” 을 헷갈린다.

### 5.3 `POST /projects/{id}/multicam` 이 너무 많은 일을 한다

현재 이름은 멀티캠이지만 실제 실행 범위는 아래다.

- evaluation 반영
- extra source 재구성
- extra source 제거
- 최종 export

이 endpoint 는 domain 명사보다 command 성격이 강하다.
따라서 멀티캠이라는 명사보다 `apply`, `reprocess`, `apply-changes` 같은 command 이름이 맞다.

### 5.4 Review API 가 엔진 리뷰 dataset 과 사람 저장 payload 를 섞어 놓는다

현재 review 페이지는 아래 두 데이터를 합쳐 쓴다.

- `GET /segments`: 엔진이 만든 review dataset
- `GET /evaluation`: 사용자가 저장한 override

이 모델 자체는 괜찮다.
하지만 path 이름이 이를 충분히 설명하지 못한다.

- `segments` 는 너무 일반적이다
- `evaluation` 은 실제로는 사람 review override 다
- `eval-report` 는 review 리소스의 파생 보고서다

### 5.5 Download API 가 artifact 리소스를 문자열과 index 로 표현한다

현재 artifact 는 두 종류로 섞여 있다.

- 결과물 artifact: `fcpxml`, `srt`, `storyline`, `project_json`, `preview`, `sync_diagnostics`
- 소스 asset: `source`, `extra-source/{index}`

이 방식의 문제:

- type safety 가 약하다
- 가능한 artifact 를 discover 할 방법이 없다
- extra source download 가 index 기반이라 stable identifier 가 없다

### 5.6 상태 모델과 capability 모델이 프론트로 충분히 전달되지 않는다

현재 프론트는 `status`, `jobs`, `extra_sources` 를 보고 스스로 버튼을 띄운다.
그래서 아래 같은 문제가 생긴다.

- `reprocess_failed` 를 특별 취급하지 못함
- 평가만 저장된 경우 apply 버튼 판단을 못 함
- preview, sync diagnostics 존재 여부를 알기 어려움

이런 규칙은 프론트가 추론하기보다 백엔드가 계산해서 내려주는 편이 낫다.

### 5.7 Health 가 readiness 를 보장하지 않는다

현재 `/health` 는 프로세스가 살아 있다는 것만 알려 준다.
하지만 실제 시스템 readiness 는 아래에 달려 있다.

- avid binary 존재
- provider 설정
- R2 접근
- Supabase 접근
- Chalna 접근

운영용 API 로는 `liveness` 와 `readiness` 를 나누는 편이 좋다.

### 5.8 API 이름과 별개로 상태 머신과 동시성도 정리해야 한다

path 이름을 정리해도 아래 두 문제를 같이 보지 않으면 여러 프론트에서 다시 문제가 생긴다.

- `reprocess_failed` 를 포함한 project 상태 머신이 문서화돼 있지 않다
- 재처리 시작 시 동시 요청 경쟁 조건을 막는 규칙이 API 계약에 드러나 있지 않다

최소한 API 문서에는 아래 상태를 명시해야 한다.

- `queued`
- `processing`
- `completed`
- `failed`
- `reprocess_failed`

그리고 `POST /projects/{id}/apply` 같은 command endpoint 는
동일 project 에 대해 동시에 두 개 이상 진행될 수 없는 단일 실행 규칙을 가져야 한다.

### 5.9 설계 변경과 별개로 현재 운영 리스크가 있다

이 문서의 중심은 API shape 이지만, 실제 migration 전에 같이 고려해야 할 항목이 있다.

- in-memory queue 기반이라 프로세스 재시작 시 pending job 이 유실될 수 있다
- upload 완료 후 project 생성 실패 시 orphan R2 object 가 남는다
- artifact 가 latest completed job 에 묶여 있어 reprocess 중 일관성 규칙을 명확히 해야 한다

즉 API rename 만으로는 충분하지 않고,
job durability 와 artifact consistency 규칙도 함께 문서화해야 한다.

## 6. Target API Shape

핵심 원칙은 네 가지다.

1. resource 는 명사로, 실제 실행은 command endpoint 로 분리한다
2. desired state 저장과 apply 실행을 분리하되 이름으로 드러낸다
3. artifact 와 source 는 리소스로 다룬다
4. 프론트가 규칙을 추론하지 않도록 capability 와 pending change 를 서버가 계산한다

### 6.1 유지해도 되는 것

아래 endpoint 는 큰 방향은 괜찮다.

- `GET /projects`
- `GET /projects/{id}`
- `POST /projects`
- `POST /projects/{id}/retry`
- `DELETE /projects/{id}`
- `GET /credits`
- `GET /credits/transactions`

다만 `POST /projects` 의 request shape 는 다음처럼 nested source object 로 바꾸는 편이 확장에 강하다.

```json
{
  "name": "sample",
  "cut_type": "subtitle_cut",
  "language": "ko",
  "source": {
    "kind": "r2",
    "object_key": "sources/...",
    "filename": "sample.mp4",
    "duration_seconds": 123,
    "size_bytes": 456789
  },
  "settings": {
    "transcription_context": "..."
  }
}
```

### 6.2 바꾸는 편이 맞는 것

| 현재 | 제안 | 이유 |
| --- | --- | --- |
| `PUT /projects/{id}/extra-sources` | `PUT /projects/{id}/multicam-config` 또는 `PUT /projects/{id}/sources/secondary` | 현재 endpoint 는 멀티캠 desired state 전체 replace 이기 때문이다 |
| `POST /projects/{id}/multicam` | `POST /projects/{id}/apply` 또는 `POST /projects/{id}/reprocess` | 실제 의미가 멀티캠이 아니라 변경사항 적용이기 때문이다 |
| `GET /projects/{id}/segments` | `GET /projects/{id}/review/segments` | 리뷰용 engine dataset 임을 path 에 드러내야 한다 |
| `GET /projects/{id}/video-url` | `GET /projects/{id}/preview` | 실제로 preview stream 을 주는 endpoint 다 |
| `GET /projects/{id}/evaluation` | `GET /projects/{id}/review` | 현재 사용자 review override 리소스에 가깝다 |
| `POST /projects/{id}/evaluation` | `PUT /projects/{id}/review` | upsert 이고 idempotent semantics 가 더 잘 맞는다 |
| `GET /projects/{id}/eval-report` | `GET /projects/{id}/review/report` | review 의 파생 보고서다 |
| `GET /projects/{id}/download/{file_type}` | `GET /projects/{id}/artifacts/{artifact_type}` | 결과물 artifact 리소스로 읽는 편이 명확하다 |
| `GET /projects/{id}/download/extra-source/{index}` | `GET /projects/{id}/sources/secondary/{source_id}` | index 대신 stable identifier 가 필요하다 |
| `POST /youtube/download` + `GET /youtube/download/{task_id}` | `POST /imports/youtube` + `GET /imports/{task_id}` | YouTube 는 import job 의 한 종류로 보는 편이 확장에 강하다 |

### 6.3 새 응답 필드로 추가할 가치가 큰 것

`GET /projects/{id}` 또는 별도 summary endpoint 에 아래 필드를 추가하는 편이 좋다.

- `available_artifacts`
- `available_actions`
- `pending_changes.review`
- `pending_changes.multicam`
- `last_error`
- `active_job`
- `last_completed_job_id`

예시:

```json
{
  "status": "completed",
  "available_actions": ["open-review", "apply", "retry-reprocess"],
  "pending_changes": {
    "review": true,
    "multicam": false
  },
  "available_artifacts": ["source", "fcpxml", "srt", "preview", "project_json"]
}
```

이 정보가 있으면 프론트는 business rule 을 중복 구현하지 않아도 된다.

### 6.4 `apply` endpoint 의 권장 shape

현재 구조를 최대한 살리면서 이름과 의미를 바로잡으려면 `POST /projects/{id}/apply` 가 가장 무난하다.

요청 body 는 처음에는 optional 로 두고,
기본값은 서버가 자동으로 step 을 계산하게 하면 된다.
첫 버전은 `auto` 만 지원하는 편이 맞다.

```json
{
  "mode": "auto"
}
```

고급 클라이언트용 explicit mode 는 나중에 필요할 때 추가하면 된다.
초기 migration 에서는 surface 를 넓히지 않는 편이 안전하다.

응답은 최소한 아래를 포함하는 편이 좋다.

```json
{
  "project_id": "p_123",
  "job_id": "j_456",
  "status": "processing",
  "planned_steps": ["apply-evaluation", "export-project"]
}
```

중요한 점은 이 endpoint 가 “저장된 desired state 를 실제 결과물에 반영하는 command” 라는 사실을
path 와 response 둘 다에서 드러내야 한다는 점이다.

### 6.5 Upload / Import 의 장기 방향

현재 코드를 크게 깨지 않으려면 당장은 upload API 를 유지해도 된다.
다만 장기적으로는 아래 둘 중 하나로 정리하는 편이 좋다.

1. `uploads` 리소스를 일반화한다
2. `project source ingest` 를 project 생성 플로우와 묶는다

권장 방향은 1번이다.

- `POST /uploads/multipart`
- `POST /uploads/{upload_id}/complete`
- `POST /projects` with uploaded object reference

이 형태는 web, admin tool, batch importer, mobile client 모두에서 재사용하기 쉽다.

외부 import 는 별도 도메인으로 유지하는 편이 좋다.

- `POST /imports/youtube`
- `GET /imports/{task_id}`

## 7. Migration Order

현실적인 순서는 아래가 맞다.

1. `reprocess_failed` 를 포함한 상태 머신과 단일 실행 규칙을 먼저 문서화한다
2. `multicam` endpoint 의 의미를 `apply` 로 재정의하고 alias 를 추가한다
3. review path 를 `review/*` 로 정리하고 기존 path 는 호환용 alias 로 남긴다
4. artifact path 를 `artifacts/*` 로 추가하고 기존 `download/*` 는 유지한다
5. `GET /projects/{id}` 에 `available_actions`, `pending_changes`, `available_artifacts` 를 추가한다
6. extra source 에 stable identifier 를 붙이고 index download 를 걷어낸다
7. orphan upload 정리 전략과 upload / import API 일반화를 함께 넣는다

alias 는 영구 방치하지 말고 deprecation 계획을 같이 둬야 한다.

- 새 path 추가 시 old path 응답에 deprecation 표식을 남긴다
- 프론트 migration 완료 후 제거 날짜를 문서에 적는다
- 최소 1개 release 동안은 old path 를 유지한다

## 8. 지금 바로 손대기 좋은 범위

현재 코드와 UI 문제를 같이 고려하면, 첫 변경 세트는 아래가 가장 안전하다.

### 8.1 API 명확화

- `POST /projects/{id}/apply` 추가
- 기존 `POST /projects/{id}/multicam` 은 내부적으로 같은 구현을 호출하는 alias 로 유지
- 응답에 `planned_steps` 추가

### 8.2 Review naming 정리

- `GET /projects/{id}/review/segments`
- `GET /projects/{id}/review`
- `PUT /projects/{id}/review`
- `GET /projects/{id}/review/report`
- 기존 endpoint 는 alias 로 유지

### 8.3 Detail summary 강화

- `GET /projects/{id}` 에 `pending_changes`, `available_actions`, `available_artifacts` 추가

이 세 가지를 먼저 하면 프론트가 현재 겪는 혼란의 대부분이 줄어든다.
