# 어검 (Eogum)

어검은 영상 소스를 받아 AI 편집 결과물과 사람 평가를 관리하는 서비스다.
현재 저장소는 FastAPI 백엔드, Next.js 프론트엔드, Supabase/R2 인프라 문서를 포함한다.

문서 우선순위:

- [docs/eogum-api-runtime.md](/home/jonhpark/workspace/eogum/docs/eogum-api-runtime.md): 프론트/백 분리와 API 런타임 구조
- [docs/eogum-api-reference.md](/home/jonhpark/workspace/eogum/docs/eogum-api-reference.md): 현재 API 표면과 프론트 소비 경로
- [ARCHITECTURE.md](/home/jonhpark/workspace/eogum/ARCHITECTURE.md): 현재 런타임 구조와 사용자 플로우
- [WORK_IN_PROGRESS.md](/home/jonhpark/workspace/eogum/WORK_IN_PROGRESS.md): 현재 진행 중인 백엔드 리팩터링 작업
- [docs/backend-module-map.md](/home/jonhpark/workspace/eogum/docs/backend-module-map.md): 백엔드 모듈 경계
- [docs/backend-testing-strategy.md](/home/jonhpark/workspace/eogum/docs/backend-testing-strategy.md): 백엔드 테스트 전략
- [docs/backend-refactoring-roadmap.md](/home/jonhpark/workspace/eogum/docs/backend-refactoring-roadmap.md): 백엔드 리팩터링 순서
- [docs/avid-integration-spec.md](/home/jonhpark/workspace/eogum/docs/avid-integration-spec.md): `avid` 연동 상위 명세
- [docs/avid-runtime-layout.md](/home/jonhpark/workspace/eogum/docs/avid-runtime-layout.md): `avid` sibling checkout 구조와 참조 규칙
- [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md): `avid-cli` 명령 명세
- [docs/infra-setup.md](/home/jonhpark/workspace/eogum/docs/infra-setup.md): 현재 코드 기준 인프라 셋업

## 저장소 구조

```text
eogum/
  apps/api
  apps/web
  docs
  supabase
```

`avid` 런타임은 Eogum 저장소 밖의 sibling checkout 을 기준으로 한다.

```text
workspace/
  eogum/
  auto-video-edit/
    apps/backend/.venv/bin/avid-cli
```

## Git 운영 규칙

- `eogum` 과 `auto-video-edit` 는 별도 repository 로 관리한다.
- `eogum` 은 `auto-video-edit` 의 Python 모듈을 직접 import 하지 않고 `avid-cli` 만 subprocess 로 실행한다.
- `avid` 코드를 수정했다면 `/home/jonhpark/workspace/auto-video-edit` 에서 별도 commit 으로 관리한다.
- `eogum` 쪽 변경과 `avid` 쪽 변경은 commit 을 분리해 추적한다.

## `avid` 사용 원칙

- `eogum` 백엔드는 `avid` Python 모듈을 직접 import 하지 않는다.
- `eogum` 백엔드는 `avid-cli` 를 명시적으로 subprocess 로 실행한다.
- 어떤 바이너리를 어떤 경로에서 어떻게 호출할지는 [docs/avid-runtime-layout.md](/home/jonhpark/workspace/eogum/docs/avid-runtime-layout.md)에 적힌 규칙을 따른다.
- 어떤 명령을 어떤 인자와 출력 형식으로 기대하는지는 [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md)를 source of truth 로 삼는다.
