# 어검 avid Runtime 구조

> 최종 갱신: 2026-06-11
> 범위: `eogum` 저장소에서 sibling `auto-video-edit` checkout 을 avid 런타임으로 사용하는 방식
> 상태: 현재 운영 기준 문서

## 1. 목적

이 문서는 `eogum` 이 `avid` 를 Python import 로 느슨하게 참조하지 않고,
sibling repository 의 명시적 CLI 실행으로 고정하기 위한 기준 문서다.

핵심 목표:

- `eogum` 과 `auto-video-edit` 의 소유권과 commit history 를 분리한다.
- `eogum` 백엔드가 `avid` 내부 Python 구조를 몰라도 되게 만든다.
- `avid` 참조 경로와 실행 바이너리 경로를 문서와 env 로 고정한다.

## 2. 표준 디렉터리 구조

운영/개발 기준 구조는 아래와 같다.

```text
workspace/
  eogum/
    apps/
      api/
      web/
    docs/
    supabase/
  auto-video-edit/
    apps/
      backend/
        src/avid
        .venv/
```

`avid` 백엔드 표준 경로:

- `/home/jonhpark/workspace/auto-video-edit/apps/backend`

`avid-cli` 표준 경로:

- `/home/jonhpark/workspace/auto-video-edit/apps/backend/.venv/bin/avid-cli`

## 3. `eogum` 백엔드가 참조해야 하는 정확한 경로

로컬 기본값은 `eogum` repository 의 parent directory 에 있는 sibling repo 를 기준으로 계산한다.

```text
AVID_BACKEND_ROOT=/home/jonhpark/workspace/auto-video-edit/apps/backend
AVID_BIN=/home/jonhpark/workspace/auto-video-edit/apps/backend/.venv/bin/avid-cli
```

핵심 규칙:

- subprocess `cwd` 는 항상 `AVID_BACKEND_ROOT`
- 실행 파일은 항상 `AVID_BIN`
- `AVID_CLI_PATH` 는 deprecated 이며 경로 결정에 사용하지 않는다
- `python -m avid.cli` 형태를 직접 만들지 않는다
- `sys.path` 나 `PYTHONPATH` 를 억지로 조작하지 않는다
- `avid.*` Python 모듈을 직접 import 하지 않는다

즉, `eogum` 이 `avid` 와 맺는 인터페이스는 파일 경로 + CLI 명령뿐이다.

## 4. `eogum` 백엔드의 호출 방식

백엔드는 아래 형태로만 `avid` 를 실행해야 한다.

```text
subprocess.run(
  [AVID_BIN, <subcommand>, ...],
  cwd=AVID_BACKEND_ROOT,
  ...
)
```

허용:

- `avid-cli transcribe ...`
- `avid-cli transcript-overview ...`
- `avid-cli subtitle-cut ...`
- `avid-cli podcast-cut ...`
- `avid-cli review-segments ...`
- `avid-cli apply-evaluation ...`
- `avid-cli rebuild-multicam ...`
- `avid-cli clear-extra-sources ...`
- `avid-cli export-project ...`
- `avid-cli version --json`
- `avid-cli doctor --json`
- `avid-cli reexport ...` 는 compatibility 전용

금지:

- `from avid... import ...`
- `sys.path.insert(... avid/src ...)`
- route 내부 direct import
- backend 가 avid project model 내부 구조를 직접 수정하는 방식

## 5. `.venv` 와 실행 환경

운영 기준:

- `.venv` 는 `/home/jonhpark/workspace/auto-video-edit/apps/backend/.venv` 에 생성한다.
- 이 `.venv` 는 local/runtime artifact 이며 git tracked 대상이 아니다.
- backend startup 시 최소한 `AVID_BIN version --json` 과 `AVID_BIN doctor --json` 으로 실행 가능 여부를 확인하는 것이 좋다.

## 6. Git 운영 주의

- `eogum` 과 `auto-video-edit` 는 별도 repo 로 commit 한다.
- `avid` 쪽 수정은 `/home/jonhpark/workspace/auto-video-edit` 에서 review/commit 한다.
- `eogum` 쪽은 어떤 `AVID_BIN` 을 호출하는지 env/config/docs 로만 고정한다.
- `avid` 명령이나 출력 명세가 바뀌면 [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md)도 같이 검토한다.

## 7. 관련 문서

- 상위 원칙: [docs/avid-integration-spec.md](/home/jonhpark/workspace/eogum/docs/avid-integration-spec.md)
- CLI 명령 명세: [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md)
- 백엔드 모듈 경계: [docs/backend-module-map.md](/home/jonhpark/workspace/eogum/docs/backend-module-map.md)
