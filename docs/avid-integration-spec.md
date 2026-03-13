# 어검 avid 연동 명세

> 최종 갱신: 2026-03-14
> 범위: `eogum` 백엔드와 `avid` 엔진 사이의 인터페이스
> 상태: 현재 운영 기준 문서

## 1. 핵심 결정

어검은 `avid` 를 **CLI-only 외부 엔진**으로 다룬다.

즉:

- `eogum` 백엔드는 `avid-cli` 를 subprocess 로 실행한다.
- `eogum` 백엔드는 `avid.*` Python 모듈을 직접 import 하지 않는다.
- `avid` 소스는 `third_party/auto-video-edit` submodule 로 고정한다.
- 초기 편집 workflow 와 후처리 workflow 모두 `avid-cli` 명령 조합으로만 실행한다.

## 2. 책임 경계

### 2.1 `eogum` 이 소유하는 것

- FastAPI 엔드포인트
- 인증, 사용자 권한
- 프로젝트 / job / evaluation / credit 상태
- R2 업로드/다운로드
- preview 생성
- `avid-cli` 실행 orchestration
- 실패 처리와 사용자 노출

### 2.2 `avid` 가 소유하는 것

- transcription 생성
- transcript overview 생성
- subtitle/podcast cut
- human evaluation override 적용
- extra source sync
- extra source 제거
- avid project JSON 형식
- FCPXML 생성

## 3. 허용/금지 규칙

허용:

- backend service 또는 adapter 에서 `avid-cli` 실행
- 결과는 JSON manifest 기준으로 읽기
- 초기 workflow 명령:
  - `version`, `doctor`, `transcribe`, `transcript-overview`, `subtitle-cut`, `podcast-cut`
- review workflow 명령:
  - `review-segments`
- 후처리 workflow 명령:
  - `apply-evaluation`, `rebuild-multicam`, `clear-extra-sources`, `export-project`
- `reexport` 는 deprecated compatibility command 로만 허용

금지:

- `from avid... import ...`
- `sys.path` 조작
- route 내부에서 `avid` 직접 호출
- stdout 문자열만 믿고 핵심 결과를 추론하는 방식

## 4. 현재 운영 메모

현재 코드는 아래 현실을 갖고 있다.

- submodule pointer 는 이미 저장소에 추가돼 있다
- 일부 로컬 환경은 여전히 `AVID_CLI_PATH` legacy fallback 에 의존할 수 있다
- `eogum` 초기 workflow 는 아직 provider 선택을 유연하게 노출하지 않는다
- deprecated `reexport` 는 남아 있지만 새 경로의 기준 명령이 아니다

목표는 이 문서와 아래 세부 문서를 기준으로 수렴하는 것이다.

## 5. 세부 문서

- [docs/avid-submodule-layout.md](/home/jonhpark/workspace/eogum/docs/avid-submodule-layout.md)
- [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md)
- [docs/backend-module-map.md](/home/jonhpark/workspace/eogum/docs/backend-module-map.md)
- [docs/backend-testing-strategy.md](/home/jonhpark/workspace/eogum/docs/backend-testing-strategy.md)
- [docs/backend-refactoring-roadmap.md](/home/jonhpark/workspace/eogum/docs/backend-refactoring-roadmap.md)
