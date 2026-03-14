# eogum API Runtime

> 최종 갱신: 2026-03-15
> 범위: `apps/api`, `apps/web`

이 문서는 `eogum` 프론트엔드와 백엔드가 현재 어떻게 분리되어 있고,
API 서버가 어떤 방식으로 서빙되는지 설명하는 운영 문서다.

## 1. 한 줄 요약

현재 구조는 아래와 같다.

```text
Browser
  -> Next.js Web (:3000 or Vercel)
      -> eogum API (:8000)
          -> Supabase
          -> Cloudflare R2
          -> avid-cli
          -> Chalna
```

중요한 점:

- 프론트엔드는 `avid` 를 직접 호출하지 않는다.
- 프론트엔드는 `eogum API` 만 호출한다.
- `avid-cli` 는 `eogum API` 내부에서 subprocess 로 실행된다.
- 브라우저가 R2 와 직접 통신하는 경우는 presigned upload / download URL 을 받은 뒤뿐이다.

## 2. Front / Back 경계

### 2.1 Frontend 책임

프론트엔드는 아래만 담당한다.

- 로그인 세션 유지
- 파일 선택 / YouTube URL 입력
- presigned upload 흐름 수행
- 프로젝트 목록 / 상세 / 리뷰 UI 렌더링
- `eogum API` 호출

프론트가 직접 하지 않는 것:

- Supabase service role 작업
- R2 credential 기반 업로드
- avid 엔진 실행
- 편집 결과 계산
- 리뷰 세그먼트 생성

핵심 파일:

- [apps/web/src/lib/api.ts](/home/jonhpark/workspace/eogum/apps/web/src/lib/api.ts)
- [apps/web/src/app/dashboard/new/page.tsx](/home/jonhpark/workspace/eogum/apps/web/src/app/dashboard/new/page.tsx)
- [apps/web/src/app/projects/[id]/page.tsx](/home/jonhpark/workspace/eogum/apps/web/src/app/projects/[id]/page.tsx)
- [apps/web/src/app/projects/[id]/review/page.tsx](/home/jonhpark/workspace/eogum/apps/web/src/app/projects/[id]/review/page.tsx)

### 2.2 Backend 책임

백엔드는 아래를 담당한다.

- Supabase JWT 검증
- presigned upload / download URL 발급
- 프로젝트 생성 / 상태 전이 / job queue
- avid CLI 실행
- Chalna, R2, Supabase 연동
- 리뷰 payload 생성 / 평가 저장 / 후처리 재실행

핵심 파일:

- [apps/api/src/eogum/main.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/main.py)
- [apps/api/src/eogum/routes/projects.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/projects.py)
- [apps/api/src/eogum/routes/evaluations.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/evaluations.py)
- [apps/api/src/eogum/services/job_runner.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/job_runner.py)
- [apps/api/src/eogum/services/avid.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/avid.py)

## 3. API 서버는 어떻게 서빙되는가

### 3.1 코드 기준 실행 엔트리

FastAPI 앱 엔트리는 [apps/api/src/eogum/main.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/main.py) 이다.

- 앱 객체: `eogum.main:app`
- 개발용 실행 함수: `eogum.main:run()`
- 표준 실행:
  - `uvicorn eogum.main:app --host 0.0.0.0 --port 8000`
  - 또는 `eogum-api`

### 3.2 프론트가 API 를 찾는 방식

프론트는 [apps/web/src/lib/api.ts](/home/jonhpark/workspace/eogum/apps/web/src/lib/api.ts) 에서 아래 규칙으로 API base URL 을 결정한다.

- `NEXT_PUBLIC_API_URL` 이 있으면 그 값을 사용
- 없으면 기본값 `http://localhost:8000/api/v1`

현재 로컬 프론트 `.env.local` 에서 확인된 값:

- `NEXT_PUBLIC_API_URL=http://192.168.0.3:8000/api/v1`

즉 현재 이 머신에서는 브라우저가 로컬 LAN 주소의 API 를 직접 보고 있다.

### 3.3 현재 머신에서 관측된 상태

2026-03-15 기준 이 머신에서 확인된 상태:

- `:3000` 에 Next.js 프론트가 떠 있음
- `:7861` 에 Chalna API 가 떠 있음
- `:8000` 에 `python -m eogum.main` 로 보이는 API 프로세스가 이미 떠 있음
- 동시에 `eogum-api.service` systemd 유닛도 `enabled`

하지만 systemd 유닛은 현재 정상 서빙 중이 아니라 crash-loop 상태다.
원인은 `:8000` 포트가 이미 수동 실행 프로세스에 의해 점유되어 있기 때문이다.

즉 지금 상태는 아래 둘이 충돌하고 있다.

1. 수동 실행 API
2. systemd `eogum-api.service`

운영 규칙:

- 로컬 개발 중에는 수동 실행 하나만 사용
- systemd 운영으로 전환할 때는 수동 실행 프로세스를 먼저 내리고 systemd 만 사용
- 둘을 동시에 켜지 않는다

## 4. systemd 구성

실제 등록된 유닛:

- `/etc/systemd/system/eogum-api.service`

샘플 파일:

- [apps/api/eogum-api.service](/home/jonhpark/workspace/eogum/apps/api/eogum-api.service)

현재 유닛 핵심:

- `WorkingDirectory=/home/jonhpark/workspace/eogum/apps/api`
- `ExecStart=/home/jonhpark/workspace/eogum/apps/api/.venv/bin/uvicorn eogum.main:app --host 0.0.0.0 --port 8000`
- `EnvironmentFile=/home/jonhpark/workspace/eogum/apps/api/.env`

즉 systemd 기준으로도 `eogum API` 는 별도 reverse proxy 없이 `:8000` 에 직접 뜨는 구조다.

## 5. 외부 노출 방식

의도된 외부 노출 경로는 아래와 같다.

```text
Browser
  -> eogum.sudoremove.com        (Web)
  -> api-eogum.sudoremove.com    (API)
```

문서 기준:

- 프론트: Vercel 또는 로컬 Next.js
- API: 로컬 머신 `:8000`
- 외부 노출: Cloudflare Tunnel 이 `api-eogum.sudoremove.com -> localhost:8000` 으로 전달

관련 문서:

- [docs/infra-setup.md](/home/jonhpark/workspace/eogum/docs/infra-setup.md)

## 6. OpenAPI 와 인간용 문서

FastAPI 기본 특성상 아래는 자동으로 제공된다.

- `/docs`
- `/openapi.json`

하지만 현재 프로젝트에서 더 중요한 문서는 자동 스키마보다 아래 두 가지다.

1. API 를 누가 호출하는가
   - 프론트
   - 외부 자동화
2. API 가 어떤 artifact 와 workflow 를 보장하는가
   - 업로드
   - 프로젝트 생성
   - 리뷰
   - 후처리
   - 다운로드

그래서 자동 OpenAPI 와 별도로 인간용 문서가 필요하다.

## 7. 지금 정리해야 할 운영 이슈

### 7.1 systemd vs 수동 실행 충돌

현재 가장 먼저 해결해야 할 운영 이슈다.

- 수동 `python -m eogum.main` 이 이미 `:8000` 사용
- `eogum-api.service` 도 같은 포트로 계속 재시작
- 결과적으로 systemd 는 실패 중

이건 코드 문제가 아니라 실행 방식 정리 문제다.

### 7.2 프론트와 백엔드 환경 변수 분리

프론트는 public env 만 가져야 한다.

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `NEXT_PUBLIC_API_URL`

백엔드는 private env 를 가진다.

- `SUPABASE_SERVICE_KEY`
- `R2_*`
- `AVID_BIN`
- `AVID_BACKEND_ROOT`
- `CHALNA_URL`
- 이메일 / provider 관련 설정

즉 프론트와 백엔드의 `.env` 역할을 섞지 않는 것이 중요하다.

## 8. 다음 문서

- [docs/eogum-api-reference.md](/home/jonhpark/workspace/eogum/docs/eogum-api-reference.md): 현재 API 표면
- [docs/eogum-avid-followup-plan.md](/home/jonhpark/workspace/eogum/docs/eogum-avid-followup-plan.md): avid follow-up 구현 계획
