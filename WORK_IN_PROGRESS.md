# Work In Progress

> 최종 갱신: 2026-03-12
> 범위: backend-first 리팩터링

## 현재 목표

- `eogum` 과 `avid` 경계를 CLI-only 로 고정
- backend 에서 `avid` direct import 제거
- 이후 테스트 작성이 가능한 모듈 경계 정리

## 진행 중인 작업

1. `avid-cli` 에 `version`, `doctor`, `reexport`, `--json` / `--manifest-out` 추가
2. `eogum` backend 가 `AVID_BACKEND_ROOT` / `AVID_BIN` 을 명시적으로 사용하도록 전환
3. `/projects/{id}/multicam` 경로를 CLI 재호출 기반으로 변경
4. 다음 단계 테스트를 위한 adapter 경계 고정

## 이번 라운드에서 끝내려는 것

- `python -m avid.cli` 직접 조립 제거
- route 내부 `sys.path` 조작과 `from avid... import ...` 제거
- CLI 결과물 위치를 stdout 추론 대신 JSON 결과로 읽는 기반 추가

## 다음 작업

- `avid-cli` adapter 명세 테스트 초안 추가
- `evaluations` / `processing` 단위 테스트 시작
- 실제 git submodule pointer 추가 및 bootstrap 절차 검증

## 메모

- 문서는 `third_party/auto-video-edit` submodule 구조를 기준으로 유지한다.
- 실제 submodule pointer 가 아직 없으면 현재 코드는 legacy sibling repo 경로를 임시 fallback 으로 사용한다.
