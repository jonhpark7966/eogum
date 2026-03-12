# 어검 백엔드 리팩터링 로드맵

> 최종 갱신: 2026-03-12
> 범위: `apps/api`
> 전제: 프론트엔드 개선은 뒤로 미루고, 백엔드 단독 운영 가능성을 먼저 높인다.

## 1. 원칙

1. API 명세를 가능한 한 유지한다.
2. 모듈 분리와 테스트 추가를 같은 단계에서 진행한다.
3. 순수 로직 추출이 먼저고, 폴더 이동은 그 다음이다.
4. 프론트 요구가 아니라 백엔드 상태 정합성과 운영 가능성을 기준으로 우선순위를 정한다.
5. `avid` 연동은 최종적으로 CLI-only + submodule 구조로 수렴한다.

## 2. 단계별 계획

### Phase 0. 문서 기준선 고정

산출물:

- [docs/backend-module-map.md](/home/jonhpark/workspace/eogum/docs/backend-module-map.md)
- [docs/backend-testing-strategy.md](/home/jonhpark/workspace/eogum/docs/backend-testing-strategy.md)
- [docs/avid-integration-spec.md](/home/jonhpark/workspace/eogum/docs/avid-integration-spec.md)
- [docs/avid-submodule-layout.md](/home/jonhpark/workspace/eogum/docs/avid-submodule-layout.md)
- [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md)
- 이 로드맵 문서

완료 기준:

- 백엔드 모듈 경계와 순서가 문서로 합의됨
- `avid` submodule 경로와 CLI 명세가 문서로 고정됨
- 프론트 작업이 현재 범위 밖임이 명시됨

### Phase 1. avid CLI-only 전환

목표:

- `eogum` 이 `avid` Python 모듈을 직접 import 하지 않도록 만든다.
- `avid` 를 submodule + CLI 명세 기반 외부 엔진으로 고정한다.

현재 대상:

- [avid.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/avid.py)
- [projects.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/projects.py#L130)

산출물:

- `adapters/avid_cli.py`
- `avid-cli version --json`
- `avid-cli doctor --json`
- `avid-cli reexport ...`
- manifest JSON 기반 결과 수집

테스트:

- `avid` 명세 테스트
- `version` / `doctor` live CLI smoke
- `reexport` integration test

### Phase 2. Evaluations 분리

목표:

- 평가 도메인을 순수 로직 중심으로 먼저 분리한다.

산출물:

- `evaluations/segments.py`
- `evaluations/metrics.py`
- `evaluations/service.py`
- `evaluations/versions.py`

테스트:

- segment merge unit test
- metric calculation unit test
- evaluation save / get integration test

### Phase 3. Processing 분리

목표:

- worker 와 처리 흐름을 백엔드의 핵심 모듈로 독립시킨다.

산출물:

- `processing/worker.py`
- `processing/service.py`
- `processing/reexport.py`
- `processing/report_parser.py`

테스트:

- report parser unit test
- 성공/실패 상태 전이 service test
- retry / re-export integration test

### Phase 4. Projects / Artifacts 분리

목표:

- 프로젝트 메타데이터 관리와 결과물 접근 정책을 분리한다.

산출물:

- `projects/service.py`
- `projects/repository.py`
- `artifacts/service.py`

테스트:

- create/list/detail/delete integration test
- download type validation unit/integration test
- extra_sources 상태 검증 integration test

### Phase 5. Credits 분리 및 정합성 강화

목표:

- 크레딧 계산과 ledger 기록을 별도 모듈로 고정한다.

산출물:

- `credits/service.py`
- `credits/repository.py`

테스트:

- available balance 계산 unit test
- hold / confirm / release service test
- credit API integration test

### Phase 6. YouTube 분리

목표:

- 메타데이터 조회, task registry, 다운로드 worker 를 분리한다.

산출물:

- `youtube/service.py`
- `youtube/task_registry.py`
- `youtube/worker.py`

테스트:

- info fetch 실패/성공 테스트
- task ownership integration test
- 다운로드 상태 전이 service test

### Phase 7. 런타임 하드닝

목표:

- 재시작 복구, worker 충돌 방지, smoke 검증을 추가한다.

산출물:

- startup recovery 범위 재정의
- active worker 충돌 방지 규칙
- API-only smoke 시나리오 문서 또는 스크립트

테스트:

- recovery service test
- API-only smoke test

## 3. 이번 라운드에서 하지 않을 것

- Next.js 페이지 컴포넌트 분리
- 프론트 API client 재구성
- 프론트 E2E 테스트

이들은 백엔드 모듈 경계와 API 명세가 안정된 뒤에 진행한다.

## 4. 각 단계의 완료 체크리스트

1. 대상 모듈 책임이 문서와 일치한다.
2. route 파일이 얇아졌다.
3. 핵심 순수 로직 unit test 가 추가됐다.
4. 해당 API 변화에 대한 integration test 가 추가됐다.
5. 기존 동작을 깨지 않았다는 수동 또는 자동 검증이 있다.
6. `avid` direct import 와 `sys.path` 조작이 제거됐다.

## 5. 추천 실제 작업 순서

1. `avid-submodule-layout` / `avid-cli-spec` 기준으로 backend adapter 정리
2. `avid-cli reexport` 추가
3. `evaluations.metrics` 추출 + unit test
4. `processing.report_parser` 추출 + unit test
5. `processing.reexport` 추출 + integration test
6. `projects` route slim-down
7. `credits` 정합성 강화
8. `youtube` 분리
