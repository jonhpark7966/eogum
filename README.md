# 어검 (Eogum)

어검은 영상 소스를 받아 AI 편집 결과물과 사람 평가를 관리하는 서비스다.
현재 저장소는 FastAPI 백엔드, Next.js 프론트엔드, Supabase/R2 인프라 문서를 포함한다.

문서 우선순위:

- [ARCHITECTURE.md](/home/jonhpark/workspace/eogum/ARCHITECTURE.md): 현재 런타임 구조와 사용자 플로우
- [docs/backend-module-map.md](/home/jonhpark/workspace/eogum/docs/backend-module-map.md): 백엔드 모듈 경계
- [docs/backend-testing-strategy.md](/home/jonhpark/workspace/eogum/docs/backend-testing-strategy.md): 백엔드 테스트 전략
- [docs/backend-refactoring-roadmap.md](/home/jonhpark/workspace/eogum/docs/backend-refactoring-roadmap.md): 백엔드 리팩터링 순서
- [docs/avid-integration-spec.md](/home/jonhpark/workspace/eogum/docs/avid-integration-spec.md): `avid` 연동 상위 명세
- [docs/avid-submodule-layout.md](/home/jonhpark/workspace/eogum/docs/avid-submodule-layout.md): `avid` submodule 목표 구조와 참조 규칙
- [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md): `avid-cli` 명령 명세
- [docs/infra-setup.md](/home/jonhpark/workspace/eogum/docs/infra-setup.md): 현재 코드 기준 인프라 셋업

## 저장소 구조

```text
eogum/
  apps/api
  apps/web
  docs
  supabase
  third_party/auto-video-edit   # 목표 구조 기준 git submodule
```

문서와 리팩터링 설계는 `third_party/auto-video-edit` 경로를 기준으로 한다.
현재 실제 submodule pointer 가 아직 추가되기 전이라면, 관련 문서는 목표 구조 문서로 읽으면 된다.

## Git 과 Submodule 운영 규칙

- 이 저장소는 `third_party/auto-video-edit` submodule 사용을 전제로 문서화한다.
- clone 시에는 `git clone --recurse-submodules <repo-url>` 를 사용한다.
- 기존 clone 에서는 `git submodule update --init --recursive` 를 먼저 실행한다.
- pull 이후에도 `git submodule update --init --recursive` 로 기록된 submodule commit 을 맞춘다.
- submodule 변경은 항상 의도적으로 review 해야 하므로 `git diff --submodule` 로 확인한다.
- `avid` 코드를 수정했다면 먼저 submodule 저장소에서 commit/push 하고, 그 다음 부모 저장소에서 submodule pointer 업데이트를 별도 commit 으로 남긴다.
- 부모 저장소와 submodule 변경을 무심코 한 commit 에 섞지 않는다.
- submodule 안에서 장기 작업을 할 때 detached HEAD 상태로 오래 작업하지 않는다. 필요한 branch 를 checkout 한 뒤 수정한다.

## `avid` 사용 원칙

- `eogum` 백엔드는 `avid` Python 모듈을 직접 import 하지 않는다.
- `eogum` 백엔드는 `avid-cli` 를 명시적으로 subprocess 로 실행한다.
- 어떤 바이너리를 어떤 경로에서 어떻게 호출할지는 [docs/avid-submodule-layout.md](/home/jonhpark/workspace/eogum/docs/avid-submodule-layout.md)에 적힌 규칙을 따른다.
- 어떤 명령을 어떤 인자와 출력 형식으로 기대하는지는 [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md)를 source of truth 로 삼는다.
