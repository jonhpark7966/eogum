# 어검 (Eogum) 프로젝트 상태

> 기준 코드: 2026-03-12
> 이 문서는 현재 저장소 구현 상태를 요약한 snapshot 이다.
> 라이브 운영 환경이나 실제 DB 상태를 조회한 보고서는 아니다.

## 1. 현재 구현된 기능

| 영역 | 상태 | 메모 |
|------|------|------|
| OAuth 로그인 | 구현됨 | Google / GitHub, Supabase SSR 세션 |
| 파일 업로드 | 구현됨 | R2 멀티파트 직접 업로드 |
| YouTube URL 입력 | 구현됨 | `yt-dlp` 백그라운드 다운로드 후 R2 업로드 |
| 프로젝트 처리 | 구현됨 | `subtitle_cut`, `podcast_cut` |
| 크레딧 hold / usage | 구현됨 | 프로젝트 시작 전 hold, 완료 시 확정 |
| 편집 리포트 저장 | 구현됨 | `edit_reports` 테이블 저장 |
| 프리뷰 생성 | 구현됨 | `ffmpeg` 로 `preview.mp4` 생성 시도 |
| 결과 다운로드 | 구현됨 | source / fcpxml / srt / report / project_json / storyline / preview 지원 |
| 세그먼트 리뷰 | 구현됨 | keep/cut + reason + note 저장 |
| eval 리포트 | 구현됨 | confusion matrix, precision, recall, F1 |
| 평가 반영 재-export | 구현됨 | human override 를 avid project 에 반영 |
| 멀티캠 재-export | 구현됨 | extra source 업로드, 오디오 싱크 후 FCPXML 재생성 |
| 이메일 알림 | 선택 구현 | Resend 미설정 시 skip |

## 2. 현재 UI 기준 사용자 흐름

### Web

- `/` : 랜딩 페이지 + OAuth 진입
- `/dashboard` : 프로젝트 목록, 상태, 크레딧
- `/dashboard/new` : 파일 업로드 또는 YouTube URL 로 프로젝트 생성
- `/projects/{id}` : 상태 확인, 결과 다운로드, 멀티캠 소스 관리, 리뷰 진입
- `/projects/{id}/review` : 세그먼트 평가 및 eval 리포트 조회

### API / Worker

- `POST /projects` 로 생성된 프로젝트는 즉시 queue 에 들어간다.
- worker 는 source 다운로드 -> avid 처리 -> preview 생성 -> 결과 업로드 순으로 진행한다.
- startup 시 `queued` / `processing` 프로젝트는 재-enqueue 된다.

## 3. 현재 운영 전제 조건

필수 외부 의존성:

- Supabase
- Cloudflare R2
- avid 저장소 + avid `.venv`
- Chalna
- `ffmpeg`
- `ffprobe`
- `yt-dlp`

선택 의존성:

- Resend

환경 변수 기준:

- API: [apps/api/.env.example](/home/jonhpark/workspace/eogum/apps/api/.env.example)
- Web: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL`

## 4. 최근 코드 기준 반영된 내용

- 평가 저장은 atomic upsert 로 처리된다.
- 프로젝트 상세 페이지 다운로드는 try/catch 로 에러를 사용자에게 노출한다.
- 리뷰 페이지는 `alert()` 대신 인라인 에러 배너를 사용한다.
- 백엔드는 `preview` 다운로드 타입을 지원한다.
- `/projects/{id}/multicam` 은 이제 멀티캠 전용이 아니라 평가 반영 재-export 도 수행한다.
- 새 프로젝트 페이지는 `YouTube URL` 소스 모드를 지원한다.
- `CHALNA_URL` 기본값은 `http://localhost:7861` 이다.

## 5. 현재 남아 있는 주요 제약

### 5.1 영속성

- 프로젝트 queue 가 메모리에만 있다.
- YouTube download task 도 메모리에만 있다.

### 5.2 동시성

- worker 가 하나라서 동시에 1개 프로젝트만 처리한다.
- `retry` / `multicam` 요청 시 active worker 와의 충돌 방지가 없다.

### 5.3 recovery

- startup recovery 는 `queued`, `processing` 만 다룬다.
- `failed` 상태에서 job 이 비정상적으로 비어 있는 케이스는 자동 복구하지 않는다.

### 5.4 입력 검증

- 프로젝트 duration 검증이 아직 느슨하다.
- `cut_type`, `settings` 도 더 엄격한 스키마가 필요하다.

### 5.5 네트워크 내구성

- 프론트 API timeout 이 없다.
- 폴링 backoff 가 없다.
- 멀티파트 업로드 part retry / abort cleanup 이 없다.

### 5.6 외부 출력 파싱

- avid stdout 기반 SRT 경로 탐지
- glob 기반 결과물 수집
- markdown 기반 edit report 파싱

이 부분은 upstream 형식 변경에 취약하다.

## 6. 권장 다음 작업

1. queue 와 YouTube task 를 DB 또는 별도 job system 으로 영속화
2. `retry` / `multicam` 실행 전에 active worker 충돌 방지
3. startup recovery 범위를 보강
4. duration / cut_type / settings 검증 강화
5. 프론트 fetch timeout, polling backoff, multipart retry 추가
6. avid 결과 파싱을 structured output 중심으로 전환
