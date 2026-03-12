# 어검 Web

어검의 Next.js 프론트엔드 앱이다.

기술 스택:

- Next.js 16
- React 19
- Tailwind CSS v4
- Supabase SSR Auth

## 주요 기능

- Google / GitHub OAuth 로그인
- 프로젝트 대시보드 및 크레딧 표시
- 파일 업로드 기반 프로젝트 생성
- YouTube URL 기반 프로젝트 생성
- 프로젝트 상세 페이지에서 결과 다운로드
- 멀티캠 추가 소스 업로드 / 제거 / 재-export
- 세그먼트 리뷰 및 eval 리포트 조회

## 필요한 환경 변수

로컬 개발 기준:

```bash
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

`NEXT_PUBLIC_API_URL` 이 없으면 기본값으로 `http://localhost:8000/api/v1` 을 사용한다.

## 시작 방법

```bash
cd apps/web
npm install
npm run dev
```

브라우저에서 `http://localhost:3000` 으로 접속한다.

## 주요 라우트

| Route | 설명 |
|------|------|
| `/` | 랜딩 페이지 및 OAuth 진입 |
| `/auth/callback` | Supabase OAuth callback |
| `/dashboard` | 프로젝트 목록 / 크레딧 |
| `/dashboard/new` | 새 프로젝트 생성 |
| `/projects/[id]` | 프로젝트 상세 / 다운로드 / 멀티캠 |
| `/projects/[id]/review` | 세그먼트 리뷰 |

## 인증 동작

- middleware 가 `/` 와 `/auth/callback` 외 경로를 보호한다.
- 미로그인 상태에서 보호 경로 접근 시 `/` 로 리다이렉트된다.

관련 파일:

- [middleware.ts](/home/jonhpark/workspace/eogum/apps/web/src/middleware.ts)
- [middleware.ts](/home/jonhpark/workspace/eogum/apps/web/src/lib/supabase/middleware.ts)
- [route.ts](/home/jonhpark/workspace/eogum/apps/web/src/app/auth/callback/route.ts)

## 백엔드와의 연동

프론트는 아래 API 흐름을 사용한다.

- 업로드: `/upload/multipart/*`
- 프로젝트: `/projects`
- 리뷰: `/projects/{id}/segments`, `/evaluation`, `/eval-report`
- YouTube: `/youtube/info`, `/youtube/download`, `/youtube/download/{task_id}`

API 타입과 호출 코드는 [api.ts](/home/jonhpark/workspace/eogum/apps/web/src/lib/api.ts) 참고.

## 현재 구현 메모

- 새 프로젝트 화면은 `파일 업로드` 와 `YouTube URL` 두 입력 모드를 지원한다.
- 프로젝트 상세 페이지는 `source`, `fcpxml`, `srt`, `report`, `project_json`, `storyline` 다운로드 버튼을 노출한다.
- 백엔드는 `preview` 다운로드도 지원하지만, 현재 프론트는 리뷰 플레이어용 스트리밍 URL 사용이 우선이다.
- `GET /evaluation` 이 404면 프론트에서는 정상적으로 `null` 로 취급한다.
