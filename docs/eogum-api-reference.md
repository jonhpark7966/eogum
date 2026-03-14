# eogum API Reference

> 최종 갱신: 2026-03-15
> 기준 코드: `apps/api/src/eogum/routes/*`

이 문서는 현재 `eogum` API 표면을 프론트엔드 소비 관점에서 정리한 문서다.
상세 구현 흐름은 [ARCHITECTURE.md](/home/jonhpark/workspace/eogum/ARCHITECTURE.md),
런타임/서빙 구조는 [docs/eogum-api-runtime.md](/home/jonhpark/workspace/eogum/docs/eogum-api-runtime.md) 를 본다.

모든 API 는 기본 prefix `/api/v1` 를 가진다.

## 1. 인증

대부분 엔드포인트는 `Authorization: Bearer <supabase access token>` 이 필요하다.

예외:

- `GET /api/v1/health`

## 2. Health

### `GET /health`

용도:

- 프로세스 생존 확인

응답:

- `status`
- `version`

## 3. Upload

라우트 파일:

- [upload.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/upload.py)

### `POST /upload/presign`

용도:

- 단일 PUT 업로드용 presigned URL 발급

주요 입력:

- `filename`
- `content_type`

주요 응답:

- `upload_url`
- `r2_key`

### `POST /upload/multipart/initiate`

용도:

- 대용량 업로드용 multipart presign 발급

주요 입력:

- `filename`
- `content_type`
- `size_bytes`

주요 응답:

- `upload_id`
- `r2_key`
- `part_size`
- `part_urls[]`

### `POST /upload/multipart/complete`

용도:

- multipart 업로드 완료

주요 입력:

- `r2_key`
- `upload_id`
- `parts[]`

## 4. Projects

라우트 파일:

- [projects.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/projects.py)

### `POST /projects`

용도:

- 프로젝트 생성 및 초기 queue 등록

주요 입력:

- `name`
- `cut_type`
- `language`
- `source_r2_key`
- `source_filename`
- `source_duration_seconds`
- `source_size_bytes`
- `settings`

주요 동작:

- credits 확인
- `projects.status = queued`
- background queue 등록

### `GET /projects`

용도:

- 내 프로젝트 목록 조회

### `GET /projects/{project_id}`

용도:

- 프로젝트 상세

포함 정보:

- project row
- jobs
- report

### `POST /projects/{project_id}/retry`

용도:

- 실패한 프로젝트 재처리

제약:

- `failed` 상태만 허용

### `PUT /projects/{project_id}/extra-sources`

용도:

- 멀티캠 extra source 목록 저장

입력:

- `extra_sources[]`

현재 payload 요소:

- `r2_key`
- `filename`
- `size_bytes`
- `offset_ms` optional

### `POST /projects/{project_id}/multicam`

용도:

- 후처리 workflow 실행

실제 의미:

- `apply-evaluation`
- `rebuild-multicam` 또는 `clear-extra-sources`
- `export-project`

즉 이름은 `multicam` 이지만 실제로는 평가 반영과 최종 export 재생성까지 포함하는 후처리 진입점이다.

### `DELETE /projects/{project_id}`

용도:

- 프로젝트 삭제

## 5. Downloads

라우트 파일:

- [downloads.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/downloads.py)

### `GET /projects/{project_id}/download/source`

용도:

- 원본 source 다운로드 URL 발급

### `GET /projects/{project_id}/download/extra-source/{index}`

용도:

- extra source 다운로드 URL 발급

### `GET /projects/{project_id}/download/{file_type}`

지원 타입:

- `fcpxml`
- `srt`
- `report`
- `project_json`
- `storyline`
- `source`
- `preview`

주의:

- 현재 엔진이 남기는 `sync_diagnostics` artifact 는 문서/계획상 반영 대상이지만, 상위 서비스 반영은 follow-up 작업 범위다.

## 6. Credits

라우트 파일:

- [credits.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/credits.py)

### `GET /credits`

용도:

- 현재 크레딧 잔액 조회

### `GET /credits/transactions`

용도:

- 크레딧 거래 내역 조회

쿼리:

- `limit`
- `offset`

## 7. Evaluations / Review

라우트 파일:

- [evaluations.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/evaluations.py)

### `GET /projects/{project_id}/segments`

용도:

- `avid-cli review-segments` payload 반환

주요 응답:

- `schema_version`
- `review_scope`
- `join_strategy`
- `segments[]`
- `source_duration_ms`

현재 의미:

- `segments[]` 는 engine-native review payload 다
- `eogum` 이 별도 overlap merge 를 재구현하지 않는 것이 목표 구조다

### `GET /projects/{project_id}/video-url`

용도:

- 리뷰 플레이어용 source / preview stream URL 발급

### `GET /projects/{project_id}/evaluation`

용도:

- 현재 사용자 평가 조회

특징:

- 없으면 `404`

### `POST /projects/{project_id}/evaluation`

용도:

- 현재 사용자 평가 저장

주요 입력:

- `schema_version`
- `review_scope`
- `join_strategy`
- `segments[]`

현재 방향:

- `segments[]` 는 `avid-cli review-segments` payload 와 최대한 같은 shape 를 유지해야 한다

### `GET /projects/{project_id}/eval-report`

용도:

- AI vs human 비교 리포트

## 8. YouTube

라우트 파일:

- [youtube.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/youtube.py)

### `POST /youtube/info`

용도:

- URL 메타데이터 조회

### `POST /youtube/download`

용도:

- 백그라운드 다운로드 시작

### `GET /youtube/download/{task_id}`

용도:

- 다운로드 진행 상태 폴링

주의:

- 현재 task registry 는 메모리 기반이다

## 9. 프론트와 직접 연결되는 핵심 엔드포인트

실제 프론트 화면 기준으로 보면 이 순서가 중요하다.

### 프로젝트 생성

1. `POST /upload/multipart/initiate`
2. `POST /upload/multipart/complete`
3. `POST /projects`

또는

1. `POST /youtube/info`
2. `POST /youtube/download`
3. `GET /youtube/download/{task_id}`
4. `POST /projects`

### 프로젝트 상세

1. `GET /projects`
2. `GET /projects/{project_id}`
3. `GET /projects/{project_id}/download/{file_type}`

### 리뷰

1. `GET /projects/{project_id}/segments`
2. `GET /projects/{project_id}/video-url`
3. `GET /projects/{project_id}/evaluation`
4. `POST /projects/{project_id}/evaluation`
5. `GET /projects/{project_id}/eval-report`

### 후처리

1. `PUT /projects/{project_id}/extra-sources`
2. `POST /projects/{project_id}/multicam`

## 10. 문서 우선순위

현재 API 관련 문서 우선순위는 아래와 같다.

1. [docs/eogum-api-runtime.md](/home/jonhpark/workspace/eogum/docs/eogum-api-runtime.md)
2. [docs/eogum-api-reference.md](/home/jonhpark/workspace/eogum/docs/eogum-api-reference.md)
3. [ARCHITECTURE.md](/home/jonhpark/workspace/eogum/ARCHITECTURE.md)
4. FastAPI `/docs`
