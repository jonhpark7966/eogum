# 어검 avid Submodule 목표 구조

> 최종 갱신: 2026-03-12
> 범위: `eogum` 저장소에서 `auto-video-edit` 를 submodule 로 사용하는 방식
> 상태: 목표 구조 문서. 현재 코드 전체가 아직 이 구조로 옮겨진 것은 아니다.

## 1. 목적

이 문서는 `avid` 를 sibling repo 나 Python import 로 느슨하게 참조하지 않고,
`eogum` 저장소 안의 명시적 submodule + 명시적 CLI 호출로 고정하기 위한 기준 문서다.

핵심 목표:

- `avid` 버전을 git pointer 로 고정한다.
- `eogum` 백엔드가 `avid` 내부 Python 구조를 몰라도 되게 만든다.
- `avid` 참조 경로와 실행 바이너리 경로를 문서로 고정한다.

## 2. 표준 디렉터리 구조

목표 구조는 아래를 기준으로 한다.

```text
eogum/
  apps/
    api/
    web/
  docs/
  supabase/
  third_party/
    auto-video-edit/           # git submodule
      apps/
        backend/
          src/avid
          .venv/               # 로컬 실행용, git tracked 아님
```

submodule 표준 경로:

- `third_party/auto-video-edit`

`avid` 백엔드 표준 경로:

- `third_party/auto-video-edit/apps/backend`

## 3. Submodule 추가/초기화 명령

canonical add 명령:

```bash
git submodule add git@github.com:jonhpark7966/auto-video-edit.git third_party/auto-video-edit
git submodule update --init --recursive
```

clone 기준:

```bash
git clone --recurse-submodules <eogum-repo-url>
```

기존 clone 기준:

```bash
git submodule update --init --recursive
```

## 4. `eogum` 백엔드가 참조해야 하는 정확한 경로

목표 구조에서 `eogum` 백엔드는 아래 세 경로를 기준으로 삼는다.

```text
AVID_SUBMODULE_ROOT=/path/to/eogum/third_party/auto-video-edit
AVID_BACKEND_ROOT=/path/to/eogum/third_party/auto-video-edit/apps/backend
AVID_BIN=/path/to/eogum/third_party/auto-video-edit/apps/backend/.venv/bin/avid-cli
```

핵심 규칙:

- subprocess `cwd` 는 항상 `AVID_BACKEND_ROOT`
- 실행 파일은 항상 `AVID_BIN`
- `python -m avid.cli` 형태를 직접 만들지 않는다
- `sys.path` 나 `PYTHONPATH` 를 억지로 조작하지 않는다
- `avid.*` Python 모듈을 직접 import 하지 않는다

즉, `eogum` 이 `avid` 와 맺는 인터페이스는 파일 경로 + CLI 명령뿐이다.

## 5. `eogum` 백엔드의 호출 방식

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
- `avid-cli reexport ...`
- `avid-cli version --json`
- `avid-cli doctor --json`

금지:

- `from avid... import ...`
- `sys.path.insert(... avid/src ...)`
- route 내부 direct import
- backend 가 avid project model 내부 구조를 직접 수정하는 방식

## 6. `.venv` 와 실행 환경

submodule 은 소스만 버전 고정한다.
`.venv` 는 submodule 이 자동으로 제공하지 않는다.

운영 기준:

- `.venv` 는 `third_party/auto-video-edit/apps/backend/.venv` 에 생성한다.
- 이 `.venv` 는 local/runtime artifact 이며 git tracked 대상이 아니다.
- backend startup 시 최소한 `AVID_BIN version --json` 과 `AVID_BIN doctor --json` 으로 실행 가능 여부를 확인하는 것이 좋다.

## 7. Git 운영 주의

- submodule pointer 변경은 종속성 버전 업데이트와 같다.
- `avid` 저장소 내용 수정과 `eogum` 코드 수정을 동시에 해도 되지만, commit 은 가급적 분리한다.
- `eogum` 쪽 commit message 와 `avid` 쪽 commit message 를 함께 추적할 수 있어야 한다.
- `avid` 를 업데이트할 때는 어떤 subcommand 나 출력 명세가 바뀌는지 [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md)도 같이 검토해야 한다.

## 8. 관련 문서

- 상위 원칙: [docs/avid-integration-spec.md](/home/jonhpark/workspace/eogum/docs/avid-integration-spec.md)
- CLI 명령 명세: [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md)
- 백엔드 모듈 경계: [docs/backend-module-map.md](/home/jonhpark/workspace/eogum/docs/backend-module-map.md)
